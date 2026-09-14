"""
Submission lifecycle, exercised against mocks.

These tests must not spend funds to prove error branches. The fake contract
lets each test choose exactly which stage fails, so the retry classification
is checked directly: price churn is repairable, a moved challenge is not, and a
status=1 receipt without a mint event is a failure rather than ownership.
"""
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "controller"))

from budget import Ledger  # noqa: E402
from protocol import keccak256, work_of  # noqa: E402
from signer import KeyStore  # noqa: E402
from submit import Submitter, SubmitError  # noqa: E402

MINER = "0x" + "11" * 20
PREV = bytes.fromhex("22" * 32)
ANCHOR = bytes.fromhex("33" * 32)
PREFIX = bytes.fromhex("44" * 24)
ANCHOR_BLOCK = 63039232
BLOCK = 63039300
PRICE = 8 * 10 ** 15
GAS_PRICE = 10 ** 9
# Trivial target: the lifecycle is what is under test, not the difficulty.
TARGET = (1 << 256) - 1


def make_proof(nonce_counter=7, miner=MINER, prev=PREV, anchor=ANCHOR):
    nonce = PREFIX + nonce_counter.to_bytes(8, "big")
    digest = work_of(miner, prev, anchor, nonce)
    return {"miner": miner, "prevWork": prev, "anchor": anchor,
            "target": TARGET, "anchorBlock": ANCHOR_BLOCK,
            "nonce": nonce, "digest": digest, "job_id": 1}


class FakeRpc:
    """Programmable RPC: set the failure you want, assert what was sent."""

    def __init__(self):
        self.calls = []
        self.revert = None
        self.estimate = 250_000
        self.balance = 10 ** 18
        self.nonce_pending = 3
        self.nonce_latest = 3
        self.broadcast_error = None
        self.sent_hash = "0x" + "ab" * 32
        self.receipt = None
        self.block_number = BLOCK

    def call(self, method, params):
        self.calls.append((method, params))
        if method == "eth_chainId":
            return hex(4663)
        if method == "eth_blockNumber":
            return hex(self.block_number)
        if method == "eth_gasPrice":
            return hex(GAS_PRICE)
        if method == "eth_getBlockByNumber":
            return {"baseFeePerGas": hex(GAS_PRICE), "number": hex(self.block_number),
                    "transactions": []}
        if method == "eth_maxPriorityFeePerGas":
            return hex(0)
        if method == "eth_getBalance":
            return hex(self.balance)
        if method == "eth_getTransactionCount":
            return hex(self.nonce_pending if params[1] == "pending"
                       else self.nonce_latest)
        if method == "eth_call":
            if self.revert:
                raise RuntimeError(self.revert)
            return "0x" + "00" * 32
        if method == "eth_estimateGas":
            return hex(self.estimate)
        if method == "eth_sendRawTransaction":
            if self.broadcast_error:
                raise RuntimeError(self.broadcast_error)
            return self.sent_hash
        if method == "eth_getTransactionReceipt":
            return self.receipt
        raise AssertionError(f"unexpected RPC call {method}")


class FakeContract:
    def __init__(self, rpc=None, status=None):
        self.rpc = rpc or FakeRpc()
        self.status = status or self._default_status()
        self.mine_calls = []

    def _default_status(self):
        return {"anchorBlock": ANCHOR_BLOCK, "anchor": ANCHOR, "prevWork": PREV,
                "target": TARGET, "difficulty": 1, "price": PRICE, "supply": 100,
                "maxSupply": 3333, "targetInterval": 45, "lastMintAt": 0,
                "startTime": 0, "anchorWindow": 250, "burstLeft": 0,
                "burstReadyAt": 0, "chainTime": 0, "blockNumber": BLOCK,
                "epoch": 3, "floorBits": 29, "streak": 50, "activeSupply": 100,
                "epochEndsAt": 0, "vault": 0, "redeemValue": 0,
                "epochPrice": PRICE, "_block": hex(BLOCK)}

    def mining_status(self, block=None):
        return dict(self.status)

    def work_for(self, miner, prev, anchor, nonce, block="latest"):
        return work_of(miner, prev, anchor, nonce.to_bytes(32, "big"))

    def balance(self, addr, block="latest"):
        return self.rpc.balance

    def tx_count(self, addr, block="latest"):
        return (self.rpc.nonce_pending if block == "pending"
                else self.rpc.nonce_latest)

    def block_number(self):
        return self.rpc.block_number

    def send_raw(self, raw):
        return self.rpc.call("eth_sendRawTransaction", [raw.hex()])

    def receipt(self, txhash):
        return self.rpc.call("eth_getTransactionReceipt", [txhash])


@pytest.fixture()
def env(tmp_path):
    rpc = FakeRpc()
    contract = FakeContract(rpc)
    ledger = Ledger(str(tmp_path / "t.sqlite"))
    ledger.set_cap(10 ** 18)
    keys = KeyStore()
    # A deterministic throwaway key: never used against a real chain here.
    keys.import_key("0x" + "11" * 31 + "11")
    addr = keys.loaded_addresses()[0]
    keys.select(addr)
    sub = Submitter(contract, ledger, keys)
    yield {"rpc": rpc, "contract": contract, "ledger": ledger, "keys": keys,
           "sub": sub, "signer": addr}
    ledger.close()


def signer_proof(env):
    return make_proof(miner=env["signer"])


def test_happy_path_reaches_broadcast_and_books_reservation(env):
    res = env["sub"].submit(signer_proof(env))
    assert res["stage"] == "broadcast"
    assert res["tx_hash"] == "0x" + "ab" * 32
    assert res["value_wei"] == PRICE
    rows = env["ledger"].open_txs()
    assert len(rows) == 1
    assert rows[0]["tx_hash"] == res["tx_hash"]
    assert rows[0]["signed_raw"]  # recoverable after a restart


def test_digest_mismatch_is_refused_before_any_rpc(env):
    p = signer_proof(env)
    p["digest"] = b"\x00" * 32
    before = len(env["rpc"].calls)
    with pytest.raises(SubmitError) as exc:
        env["sub"].submit(p)
    assert exc.value.kind == "bad_proof"
    assert len(env["rpc"].calls) == before  # nothing left the process


def test_proof_for_another_address_cannot_be_signed(env):
    """The miner address is inside the preimage, so it cannot be swapped."""
    other = "0x" + "99" * 20
    with pytest.raises(SubmitError) as exc:
        env["sub"].submit(make_proof(miner=other))
    assert exc.value.kind == "signer_mismatch"


def test_deselected_signer_blocks_queued_proofs(env):
    env["keys"].clear_selection()
    with pytest.raises(SubmitError) as exc:
        env["sub"].submit(signer_proof(env))
    assert exc.value.kind == "no_signer"


def test_selected_signer_without_key_does_not_fall_back(env):
    env["keys"].forget(env["signer"])
    with pytest.raises(SubmitError) as exc:
        env["sub"].submit(signer_proof(env))
    assert exc.value.kind == "signer_not_loaded"


def test_moved_prevwork_is_not_repairable(env):
    p = signer_proof(env)
    env["contract"].status["prevWork"] = bytes.fromhex("ee" * 32)
    with pytest.raises(SubmitError) as exc:
        env["sub"].submit(p)
    assert exc.value.kind == "challenge_changed"


def test_expired_anchor_is_dropped(env):
    p = signer_proof(env)
    env["contract"].status["blockNumber"] = ANCHOR_BLOCK + 999
    with pytest.raises(SubmitError) as exc:
        env["sub"].submit(p)
    assert exc.value.kind == "anchor_expired"


def test_anchor_inside_window_is_allowed(env):
    """Real mints land 20-130 blocks after their anchor, window is 250."""
    p = signer_proof(env)
    env["contract"].status["blockNumber"] = ANCHOR_BLOCK + 120
    res = env["sub"].submit(p)
    assert res["stage"] == "broadcast"


def test_price_reread_every_attempt_after_underpaid(env):
    p = signer_proof(env)
    env["rpc"].revert = "execution reverted: Underpaid"
    with pytest.raises(SubmitError) as exc:
        env["sub"].submit(p)
    assert exc.value.kind == "price_churn"
    # It retried rather than giving up on the first revert.
    assert len(env["ledger"].events(50)) >= 1


def test_badsolution_revert_is_terminal(env):
    p = signer_proof(env)
    env["rpc"].revert = "execution reverted: BadSolution"
    with pytest.raises(SubmitError) as exc:
        env["sub"].submit(p)
    assert exc.value.kind == "bad_solution"


def test_soldout_revert_is_terminal(env):
    env["rpc"].revert = "execution reverted: SoldOut"
    with pytest.raises(SubmitError) as exc:
        env["sub"].submit(signer_proof(env))
    assert exc.value.kind == "sold_out"


def test_unfunded_wallet_is_refused_and_reservation_released(env):
    env["rpc"].balance = 10 ** 12
    with pytest.raises(SubmitError) as exc:
        env["sub"].submit(signer_proof(env))
    assert exc.value.kind == "unfunded"
    assert env["ledger"].reserved == 0


def test_cap_exceeded_refuses_and_reserves_nothing(env):
    env["ledger"].set_cap(PRICE + 10 ** 12)  # below price plus gas liability
    with pytest.raises(SubmitError) as exc:
        env["sub"].submit(signer_proof(env))
    assert exc.value.kind == "budget"
    assert env["ledger"].reserved == 0


def test_external_pending_nonce_blocks_submission(env):
    env["rpc"].nonce_pending = 5
    env["rpc"].nonce_latest = 3
    with pytest.raises(SubmitError) as exc:
        env["sub"].submit(signer_proof(env))
    assert exc.value.kind == "external_pending"


def test_lost_broadcast_response_is_reconciled(env):
    env["rpc"].broadcast_error = "request timed out"
    with pytest.raises(SubmitError) as exc:
        env["sub"].submit(signer_proof(env))
    assert exc.value.kind == "broadcast"
    # The reservation is not silently kept open as "reserved" forever.
    assert env["ledger"].reserved == 0


def test_already_known_continues_without_duplicate(env):
    env["rpc"].broadcast_error = "already known"
    with pytest.raises(SubmitError):
        env["sub"].submit(signer_proof(env))
    # Only one row was ever created for this proof.
    assert len(env["ledger"].recent_txs(10)) == 1


def _receipt(status=1, mint_to=None, token_id=101, gas_used=200_000):
    logs = []
    if mint_to:
        topic = "0x" + keccak256(b"Transfer(address,address,uint256)").hex()
        logs.append({
            "address": "0xb0db77c5d6ed578189609ecc72d25699a79f785b",
            "topics": [topic,
                       "0x" + "00" * 32,
                       "0x" + mint_to[2:].rjust(64, "0"),
                       "0x" + hex(token_id)[2:].rjust(64, "0")],
            "data": "0x",
        })
    return {"status": hex(status), "gasUsed": hex(gas_used),
            "effectiveGasPrice": hex(GAS_PRICE), "blockNumber": hex(BLOCK),
            "logs": logs}


def test_receipt_with_mint_event_confirms_ownership(env):
    res = env["sub"].submit(signer_proof(env))
    env["rpc"].receipt = _receipt(1, mint_to=res["wallet"])
    env["rpc"].block_number = BLOCK + 5  # past the finality wait
    out = env["sub"].reconcile(res["row_id"], res["tx_hash"], res["wallet"],
                               res["value_wei"])
    assert out["stage"] == "mint_confirmed"
    assert out["token_id"] == 101
    assert env["ledger"].spent == res["value_wei"] + 200_000 * GAS_PRICE


def test_status_one_without_mint_event_is_not_ownership(env):
    """Gas was spent and no NFT was received: that is a failure."""
    res = env["sub"].submit(signer_proof(env))
    env["rpc"].receipt = _receipt(1, mint_to=None)
    env["rpc"].block_number = BLOCK + 5
    out = env["sub"].reconcile(res["row_id"], res["tx_hash"], res["wallet"],
                               res["value_wei"])
    assert out["stage"] == "no_mint_event"
    # Only gas is booked; the price came back.
    assert env["ledger"].spent == 200_000 * GAS_PRICE


def test_mint_to_the_wrong_address_is_not_ours(env):
    res = env["sub"].submit(signer_proof(env))
    env["rpc"].receipt = _receipt(1, mint_to="0x" + "77" * 20)
    env["rpc"].block_number = BLOCK + 5
    out = env["sub"].reconcile(res["row_id"], res["tx_hash"], res["wallet"],
                               res["value_wei"])
    assert out["stage"] == "no_mint_event"


def test_reverted_receipt_books_gas_only(env):
    res = env["sub"].submit(signer_proof(env))
    env["rpc"].receipt = _receipt(0, gas_used=180_000)
    env["rpc"].block_number = BLOCK + 5
    out = env["sub"].reconcile(res["row_id"], res["tx_hash"], res["wallet"],
                               res["value_wei"])
    assert out["stage"] == "failed"
    assert env["ledger"].spent == 180_000 * GAS_PRICE


def test_receipt_processing_is_idempotent(env):
    res = env["sub"].submit(signer_proof(env))
    env["rpc"].receipt = _receipt(1, mint_to=res["wallet"])
    env["rpc"].block_number = BLOCK + 5
    first = env["sub"].reconcile(res["row_id"], res["tx_hash"], res["wallet"],
                                 res["value_wei"])
    spent_after_first = env["ledger"].spent
    second = env["sub"].reconcile(res["row_id"], res["tx_hash"], res["wallet"],
                                  res["value_wei"])
    assert first["stage"] == "mint_confirmed"
    assert second["booked"] is False
    assert env["ledger"].spent == spent_after_first


def test_restart_reconciles_open_rows_without_duplicating(env):
    res = env["sub"].submit(signer_proof(env))
    # Simulate a restart: a fresh Submitter over the same ledger.
    sub2 = Submitter(env["contract"], env["ledger"], env["keys"])
    env["rpc"].receipt = _receipt(1, mint_to=res["wallet"])
    env["rpc"].block_number = BLOCK + 5
    out = sub2.reconcile_open()
    assert len(out) == 1
    assert out[0]["stage"] == "mint_confirmed"
    assert len(env["ledger"].open_txs()) == 0


def test_reserved_but_never_broadcast_is_released_on_restart(env):
    row = env["ledger"].reserve(wallet=env["signer"], value_wei=PRICE,
                                gas_limit=300_000, max_fee_wei=GAS_PRICE)
    sub2 = Submitter(env["contract"], env["ledger"], env["keys"])
    out = sub2.reconcile_open()
    assert out[0]["stage"] == "released"
    assert env["ledger"].reserved == 0
