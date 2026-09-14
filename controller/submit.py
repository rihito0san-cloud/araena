#!/usr/bin/env python3
"""
submit.py -- the transaction lifecycle for one proof.

This is the only place that spends money, and it is deliberately paranoid. The
order matters: verify, re-read state, re-simulate, re-check budget, reserve,
sign, broadcast, reconcile. A successful simulation is not a mint, a broadcast
is not an inclusion, and a status=1 receipt is not ownership until the mint
Transfer event from the zero address is present.

Distinct outcomes, never reported as interchangeable:
  found / simulated / signed / broadcast / included / mint_confirmed / failed
"""
import time

from eth_utils import to_checksum_address

from protocol import (
    CONTRACT,
    Contract,
    RpcError,
    build_preimage,
    decode_mint_from_receipt,
    effective_bits,
    is_solution,
    mine_calldata,
    work_of,
)


class SubmitError(RuntimeError):
    """A proof could not be submitted. `kind` drives the retry policy."""

    def __init__(self, kind, message):
        super().__init__(message)
        self.kind = kind
        self.message = message


# Retry policy: price movement is repairable, a moved challenge is not.
PRICE_RETRIES = 3
GAS_MARGIN_NUM, GAS_MARGIN_DEN = 13, 10     # 1.3x estimate
GAS_MARGIN_CAP = 2_000_000                  # never reserve absurd gas
MIN_PRIO_FEE = 1                            # chain reports 0 priority fee
FINALITY_BLOCKS = 2                         # wait beyond inclusion
DEFAULT_GAS_LIMIT = 400_000                 # fallback if estimation fails


class FeeModel:
    """EIP-1559 fees read from the chain, with a legacy fallback."""

    def __init__(self, contract: Contract):
        self.c = contract

    def read(self):
        rpc = self.c.rpc
        block = rpc.call("eth_getBlockByNumber", ["latest", False])
        base = int(block["baseFeePerGas"], 16) if block.get("baseFeePerGas") else None
        gas_price = int(rpc.call("eth_gasPrice", []), 16)
        if base is None:
            return {"legacy": True, "gasPrice": gas_price}
        try:
            prio = int(rpc.call("eth_maxPriorityFeePerGas", []), 16)
        except Exception:
            prio = 0
        prio = max(prio, MIN_PRIO_FEE)
        return {
            "legacy": False,
            "baseFee": base,
            "priorityFee": prio,
            # Cap at 2x base + priority: bounded, and generous enough to ride
            # a couple of busy blocks without an unbounded liability.
            "maxFeePerGas": base * 2 + prio,
        }


class Submitter:
    def __init__(self, contract, ledger, keystore, fees=None, log=None):
        self.c = contract
        self.ledger = ledger
        self.keys = keystore
        self.fees = fees or FeeModel(contract)
        self.log = log or (lambda kind, msg: ledger.event(kind, msg))

    # ------------------------------------------------------------ helpers
    def _next_nonce(self, wallet):
        """Nonce from the chain, cross-checked against our own open rows.

        An unexplained external pending transaction means something else is
        spending from this wallet; guessing a nonce here produces a stuck or
        dropped transaction, so submission is blocked instead.
        """
        pending = int(self.c.tx_count(wallet, "pending"))
        latest = int(self.c.tx_count(wallet, "latest"))
        ours = self.ledger.pending_by_wallet(wallet)
        if pending > latest:
            raise SubmitError(
                "external_pending",
                f"wallet has {pending - latest} external pending tx; "
                "resolve it before submitting")
        if ours:
            raise SubmitError(
                "own_pending",
                f"{len(ours)} of our own transactions are still open; "
                "one globally pending transaction at a time")
        return latest

    def _simulate(self, wallet, nonce_value, anchor_block, value_wei):
        """eth_call the real mine() with the real value. Returns revert text."""
        data = mine_calldata(nonce_value, anchor_block)
        params = {"from": wallet, "to": CONTRACT, "data": data,
                  "value": hex(int(value_wei))}
        try:
            self.c.rpc.call("eth_call", [params, "latest"])
            return None
        except RpcError as exc:
            return str(exc)
        except Exception as exc:
            return str(exc)

    def _estimate_gas(self, wallet, nonce_value, anchor_block, value_wei):
        data = mine_calldata(nonce_value, anchor_block)
        params = {"from": wallet, "to": CONTRACT, "data": data,
                  "value": hex(int(value_wei))}
        try:
            est = int(self.c.rpc.call("eth_estimateGas", [params, "latest"]), 16)
        except Exception:
            return None
        return est

    # --------------------------------------------------------------- main
    def submit(self, proof):
        """Take a locally verified proof through to broadcast.

        Returns a dict describing the outcome. Raises SubmitError with a kind
        the caller can classify.
        """
        miner = proof["miner"]
        nonce_bytes = proof["nonce"]
        anchor_block = int(proof["anchorBlock"])
        nonce_value = int.from_bytes(nonce_bytes, "big")

        # 1. Recompute the digest ourselves. Never trust the worker's word.
        digest = work_of(miner, proof["prevWork"], proof["anchor"], nonce_bytes)
        if len(build_preimage(miner, proof["prevWork"], proof["anchor"],
                              nonce_bytes)) != 116:
            raise SubmitError("bad_proof", "preimage is not 116 bytes")
        if digest != proof["digest"]:
            raise SubmitError("bad_proof", "digest does not match our Keccak")

        # 2. The selected signer must be loaded and must be this proof's miner.
        selected = self.keys.selected
        if not selected:
            raise SubmitError("no_signer", "no signer selected")
        if not self.keys.selected_is_loaded():
            raise SubmitError("signer_not_loaded",
                              f"selected signer {selected} is not loaded")
        if miner.lower() != selected.lower():
            # A proof mined for address A cannot be submitted as address B:
            # the address is inside the hash preimage.
            raise SubmitError("signer_mismatch",
                              f"proof belongs to {miner}, signer is {selected}")

        row_id = None
        attempts = 0
        last_error = "unknown"

        while attempts < PRICE_RETRIES:
            attempts += 1

            # 3. Fresh state, pinned to one block.
            st = self.c.mining_status()
            block_tag = st["_block"]
            if st["prevWork"] != proof["prevWork"]:
                raise SubmitError("challenge_changed",
                                  "prevWork moved; this proof is stale")
            # The anchor window check is unconditional. It used to be nested
            # inside "anchor changed", which meant a proof whose anchor was
            # still the contract's current one skipped the check entirely --
            # and a stale anchor is exactly the case that must be caught.
            lag = st["blockNumber"] - anchor_block
            if lag > int(st["anchorWindow"]):
                raise SubmitError(
                    "anchor_expired",
                    f"anchor lag {lag} exceeds window {st['anchorWindow']}")
            if not is_solution(miner, proof["prevWork"], proof["anchor"],
                               nonce_bytes, st["target"]):
                raise SubmitError("target_changed",
                                  "proof no longer meets the current target")
            if st["supply"] >= st["maxSupply"]:
                raise SubmitError("sold_out", "collection is sold out")

            # 4. Price is re-read every attempt; it is never hardcoded.
            value_wei = int(st["price"])
            if value_wei <= 0:
                raise SubmitError("bad_price", f"implausible price {value_wei}")

            # 5. Nonce and fees.
            wallet_nonce = self._next_nonce(miner)
            fee = self.fees.read()

            # 6. Simulate the actual call with the actual value.
            revert = self._simulate(miner, nonce_value, anchor_block, value_wei)
            if revert:
                kind = "revert"
                for marker, k in (("SoldOut", "sold_out"),
                                  ("NotStarted", "not_started"),
                                  ("AnchorExpired", "anchor_expired"),
                                  ("AnchorInFuture", "anchor_early"),
                                  ("BlockFull", "block_full"),
                                  ("Underpaid", "underpaid"),
                                  ("BadSolution", "bad_solution")):
                    if marker.lower() in revert.lower():
                        kind = k
                        break
                # Underpaid means the price moved under us: repairable by
                # re-reading it on the next loop pass.
                if kind == "underpaid":
                    last_error = revert
                    continue
                raise SubmitError(kind, revert)

            # 7. Gas, with a bounded margin.
            est = self._estimate_gas(miner, nonce_value, anchor_block, value_wei)
            if est is None:
                gas_limit = DEFAULT_GAS_LIMIT
            else:
                gas_limit = min(est * GAS_MARGIN_NUM // GAS_MARGIN_DEN,
                                max(est + 60_000, GAS_MARGIN_CAP))
                gas_limit = max(gas_limit, est + 10_000)

            if fee["legacy"]:
                max_fee = int(fee["gasPrice"])
            else:
                max_fee = int(fee["maxFeePerGas"])
            liability = value_wei + gas_limit * max_fee

            # 8. The wallet must actually cover price plus worst-case gas.
            bal = int(self.c.balance(miner))
            if bal < liability:
                raise SubmitError(
                    "unfunded",
                    f"balance {bal} wei < liability {liability} wei")

            # 9. Reserve against the shared cap and persist intent BEFORE
            #    broadcast, in one transaction.
            tx_dict = {
                # eth_account >= 0.11 rejects a non-checksummed `to`, so the
                # constant must be normalized here rather than signed as-is.
                "to": to_checksum_address(CONTRACT),
                "from": miner,
                "nonce": wallet_nonce,
                "value": value_wei,
                "gas": gas_limit,
                "data": mine_calldata(nonce_value, anchor_block),
            }
            if fee["legacy"]:
                tx_dict["gasPrice"] = int(fee["gasPrice"])
            else:
                tx_dict["maxFeePerGas"] = int(fee["maxFeePerGas"])
                tx_dict["maxPriorityFeePerGas"] = int(fee["priorityFee"])
                tx_dict["type"] = 2

            try:
                row_id = self.ledger.reserve(
                    wallet=miner, value_wei=value_wei, gas_limit=gas_limit,
                    max_fee_wei=max_fee,
                    proof_nonce=nonce_bytes.hex(), anchor_block=anchor_block,
                    nonce=wallet_nonce,
                    note=f"bits={effective_bits(st['target']):.2f}")
            except Exception as exc:
                raise SubmitError("budget", str(exc))

            try:
                raw = self.keys.sign_transaction(tx_dict)
            except Exception as exc:
                self.ledger.release(row_id, f"signing failed: {exc}")
                raise SubmitError("signing", str(exc))

            self.ledger.mark_broadcast(row_id, None, wallet_nonce, raw.hex())

            # 10. Broadcast, then reconcile by nonce even if the RPC times out.
            try:
                tx_hash = self.c.send_raw(raw)
            except Exception as exc:
                text = str(exc)
                if "already known" in text.lower():
                    self.log("broadcast", "transaction already known")
                    tx_hash = self._find_hash_by_nonce(miner, wallet_nonce)
                    if not tx_hash:
                        self.ledger.drop_tx(row_id, "already known, hash not found")
                        raise SubmitError("broadcast", text)
                else:
                    self.ledger.drop_tx(row_id, f"broadcast failed: {text}")
                    raise SubmitError("broadcast", text)

            self.ledger.mark_broadcast(row_id, tx_hash, wallet_nonce, raw.hex())
            self.log("broadcast",
                     f"tx {tx_hash} nonce {wallet_nonce} value {value_wei} wei")
            return {
                "stage": "broadcast",
                "row_id": row_id,
                "tx_hash": tx_hash,
                "wallet": miner,
                "nonce": wallet_nonce,
                "value_wei": value_wei,
                "gas_limit": gas_limit,
                "anchor_block": anchor_block,
                "attempts": attempts,
            }

        raise SubmitError("price_churn",
                          f"price kept moving ({attempts} attempts): {last_error}")

    def _find_hash_by_nonce(self, wallet, nonce):
        """Reconcile a broadcast whose response we lost."""
        latest = int(self.c.tx_count(wallet, "latest"))
        if nonce < latest:
            block = self.c.block_number()
            # Scan a short recent range for our own transaction.
            for b in range(block, max(block - 64, 0), -1):
                data = self.c.rpc.call("eth_getBlockByNumber", [hex(b), True])
                for tx in data.get("transactions", []) or []:
                    if (tx.get("from", "").lower() == wallet.lower()
                            and int(tx["nonce"], 16) == nonce):
                        return tx["hash"]
        return None

    # ----------------------------------------------------------- receipts
    def wait_receipt(self, tx_hash, timeout=180, poll=2.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            r = self.c.receipt(tx_hash)
            if r:
                return r
            time.sleep(poll)
        return None

    def reconcile(self, row_id, tx_hash, wallet, value_wei,
                  wait_blocks=FINALITY_BLOCKS, timeout=300):
        """Watch a broadcast transaction to a terminal, booked outcome.

        A receipt with status=1 but no mint Transfer from the zero address to
        our wallet is NOT ownership. Gas was spent, nothing was received, and
        that must be recorded as a failure rather than a mint.
        """
        receipt = self.wait_receipt(tx_hash, timeout=timeout)
        if receipt is None:
            self.log("receipt", f"{tx_hash} still unresolved; reservation kept")
            return {"stage": "unresolved", "row_id": row_id,
                    "tx_hash": tx_hash}

        status = int(receipt.get("status", "0x0"), 16)
        gas_used = int(receipt["gasUsed"], 16)
        eff = receipt.get("effectiveGasPrice")
        gas_price = int(eff, 16) if eff else int(
            self.c.rpc.call("eth_gasPrice", []), 16)
        gas_cost = gas_used * gas_price

        included_at = int(receipt["blockNumber"], 16)
        # Wait beyond inclusion before calling it final.
        deadline = time.time() + 120
        while time.time() < deadline:
            if self.c.block_number() >= included_at + wait_blocks:
                break
            time.sleep(2)

        if status != 1:
            changed = self.ledger.settle_failure(
                row_id, gas_used_wei=gas_cost,
                note=f"reverted, gas {gas_cost} wei")
            self.log("failed",
                     f"{tx_hash} reverted; consumed {gas_cost} wei of gas")
            return {"stage": "failed", "row_id": row_id, "tx_hash": tx_hash,
                    "gas_used_wei": gas_cost, "booked": changed}

        token_id = decode_mint_from_receipt(receipt, wallet)
        if token_id is None:
            # status=1 but no mint event: do not claim an NFT we do not have.
            changed = self.ledger.settle_failure(
                row_id, gas_used_wei=gas_cost,
                note="status=1 but no mint Transfer from zero address")
            self.log("failed",
                     f"{tx_hash} succeeded without a mint event; not ownership")
            return {"stage": "no_mint_event", "row_id": row_id,
                    "tx_hash": tx_hash, "gas_used_wei": gas_cost,
                    "booked": changed}

        changed = self.ledger.settle_success(
            row_id, gas_used_wei=gas_cost, value_wei=value_wei,
            token_id=token_id)
        self.log("minted",
                 f"token {token_id} minted to {wallet} via {tx_hash}")
        return {"stage": "mint_confirmed", "row_id": row_id,
                "tx_hash": tx_hash, "token_id": token_id,
                "gas_used_wei": gas_cost, "booked": changed}

    def reconcile_open(self):
        """Re-attach to transactions left open by a restart.

        Restart must never duplicate a pending transaction or silently drop
        its reservation, so every open row is driven to a terminal state here.
        """
        out = []
        for row in self.ledger.open_txs():
            if not row.get("tx_hash"):
                # Reserved but never broadcast: safe to release.
                self.ledger.release(row["id"], "never broadcast before restart")
                out.append({"row_id": row["id"], "stage": "released"})
                continue
            out.append(self.reconcile(row["id"], row["tx_hash"], row["wallet"],
                                      int(row["value_wei"])))
        return out
