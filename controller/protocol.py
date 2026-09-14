#!/usr/bin/env python3
"""
protocol.py -- MinerPotatos contract adapter.

Everything the controller needs to read chain state and to build a mine()
transaction. This module owns no keys and never signs; signing lives in the
signer module, which is imported by nobody else.

Pinned identifiers, re-verified against the live chain on 2026-09-14:

  chain id      4663 (0x1237)
  contract      0xb0db77c5d6ed578189609ecc72d25699a79f785b
  bytecode      keccak256(runtime) = ec77f042...116c7553
  submission    mine(uint256 nonce, uint256 anchorBlock), payable

The ABI in abi.json was extracted from the project frontend and checked against
live calls. It is a reconstruction, not a verified-source audit.
"""
import json
import os
import time
import urllib.error
import urllib.request

from Crypto.Hash import keccak
from eth_abi import decode as abi_decode
from eth_abi import encode as abi_encode

HERE = os.path.dirname(os.path.abspath(__file__))

CHAIN_ID = 4663
CONTRACT = "0xb0db77c5d6ed578189609ecc72d25699a79f785b"
# Matches the frontend's VITE_DEPLOY_BLOCK; used only to bound log scans.
DEPLOY_BLOCK = 62985004
RPCS = ["https://rpc.minerpotatos.xyz", "https://rpc.mainnet.chain.robinhood.com/"]

# The 24 MiningStatus struct fields, in ABI order. Decoding positionally
# against this list is what keeps a tuple-order change visible as an error
# instead of silently reassigning fields.
STATUS_FIELDS = [
    "anchorBlock", "anchor", "prevWork", "target", "difficulty", "price",
    "supply", "maxSupply", "targetInterval", "lastMintAt", "startTime",
    "anchorWindow", "burstLeft", "burstReadyAt", "chainTime", "blockNumber",
    "epoch", "floorBits", "streak", "activeSupply", "epochEndsAt", "vault",
    "redeemValue", "epochPrice",
]
_BYTES32_FIELDS = ("anchor", "prevWork")

ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"


def keccak256(data: bytes) -> bytes:
    k = keccak.new(digest_bits=256)
    k.update(data)
    return k.digest()


def selector(sig: str) -> str:
    return keccak256(sig.encode()).hex()[:8]


def abi():
    with open(os.path.join(HERE, "abi.json")) as f:
        return json.load(f)


def _transfer_topic():
    return "0x" + keccak256(b"Transfer(address,address,uint256)").hex()


def _mined_topic():
    return "0x" + keccak256(
        b"Mined(uint256,address,bytes32,bytes32,uint256,uint256,uint8,uint8,uint256)"
    ).hex()


class RpcError(RuntimeError):
    pass


class Rpc:
    """Minimal JSON-RPC client with bounded retries across the RPC list."""

    def __init__(self, urls=None, timeout=20, retries=3):
        self.urls = list(urls or RPCS)
        self.timeout = timeout
        self.retries = retries
        self.last_error = None
        self._id = 0

    def call(self, method, params):
        last = None
        for attempt in range(self.retries):
            for url in self.urls:
                try:
                    return self._one(url, method, params)
                except Exception as exc:  # network, HTTP or JSON-RPC error
                    last = exc
                    self.last_error = f"{url}: {exc}"
            time.sleep(0.4 * (attempt + 1))
        raise RpcError(f"{method} failed after {self.retries} rounds: {last}")

    def _one(self, url, method, params):
        self._id += 1
        payload = {"jsonrpc": "2.0", "id": self._id,
                   "method": method, "params": params}
        req = urllib.request.Request(
            url, data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            out = json.loads(r.read())
        if "error" in out:
            raise RpcError(out["error"])
        return out.get("result")


def hexbytes(b: bytes) -> str:
    return "0x" + b.hex()


def unhex(s: str) -> bytes:
    return bytes.fromhex(s[2:] if s.startswith("0x") else s)


def pad_addr(addr: str) -> str:
    a = addr[2:] if addr.startswith("0x") else addr
    if len(a) != 40:
        raise ValueError(f"bad address: {addr}")
    return a.lower().rjust(64, "0")


# ------------------------------------------------------------------- reads
class Contract:
    def __init__(self, rpc=None, address=CONTRACT):
        self.rpc = rpc or Rpc()
        self.address = address

    def _call(self, sig, types, values, block="latest"):
        data = "0x" + selector(sig) + abi_encode(types, values).hex()
        return self.rpc.call("eth_call",
                             [{"to": self.address, "data": data}, block])

    # --- identity checks: run these before anything spends -----------------
    def chain_id(self):
        return int(self.rpc.call("eth_chainId", []), 16)

    def code(self, block="latest"):
        return unhex(self.rpc.call("eth_getCode", [self.address, block]))

    def code_hash(self, block="latest"):
        return keccak256(self.code(block))

    def block_number(self):
        return int(self.rpc.call("eth_blockNumber", []), 16)

    def block_hash(self, n):
        r = self.rpc.call("eth_getBlockByNumber", [hex(n), False])
        return unhex(r["hash"]) if r else None

    def gas_price(self):
        return int(self.rpc.call("eth_gasPrice", []), 16)

    def balance(self, addr, block="latest"):
        return int(self.rpc.call("eth_getBalance", [addr, block]), 16)

    def tx_count(self, addr, block="latest"):
        return int(self.rpc.call("eth_getTransactionCount", [addr, block]), 16)

    def send_raw(self, raw: bytes):
        return self.rpc.call("eth_sendRawTransaction", [hexbytes(raw)])

    def receipt(self, txhash):
        return self.rpc.call("eth_getTransactionReceipt", [txhash])

    def get_transaction(self, txhash):
        return self.rpc.call("eth_getTransactionByHash", [txhash])

    # --- mining state ------------------------------------------------------
    def mining_status(self, block=None):
        """Decoded MiningStatus plus the block it was read at.

        Always read state from ONE pinned block: prevWork and the anchor roll
        every block on this contract, so mixing reads from two blocks produces
        a challenge that was never live.
        """
        if block is None:
            block = hex(self.block_number() - 1)
        raw = self._call("miningStatus()", [], [], block)
        body = raw[2:] if raw.startswith("0x") else raw
        words = [body[i * 64:(i + 1) * 64] for i in range(len(body) // 64)]
        if len(words) != len(STATUS_FIELDS):
            raise RpcError(
                f"miningStatus returned {len(words)} words, "
                f"expected {len(STATUS_FIELDS)}; ABI has drifted")
        st = {}
        for name, w in zip(STATUS_FIELDS, words):
            v = int(w, 16)
            st[name] = v.to_bytes(32, "big") if name in _BYTES32_FIELDS else v
        st["_block"] = block
        return st

    def work_for(self, miner: str, prev_work: bytes, anchor: bytes, nonce: int,
                 block="latest"):
        """The contract's own pure preimage hash -- the authority on layout."""
        out = self._call("workFor(address,bytes32,bytes32,uint256)",
                         ["address", "bytes32", "bytes32", "uint256"],
                         [miner, prev_work, anchor, nonce], block)
        return unhex(out)

    def mint_price(self, block="latest"):
        return int(self._call("mintPrice()", [], [], block), 16)

    def balance_of(self, owner, block="latest"):
        return int(self._call("balanceOf(address)", ["address"], [owner],
                              block), 16)

    def owner_of(self, token_id, block="latest"):
        return "0x" + self._call("ownerOf(uint256)", ["uint256"], [token_id],
                                 block)[-40:]

    def logs(self, from_block, to_block, topics=None):
        params = {"fromBlock": hex(from_block), "toBlock": hex(to_block),
                  "address": self.address}
        if topics:
            params["topics"] = topics
        return self.rpc.call("eth_getLogs", [params]) or []

    def mined_events(self, from_block, to_block):
        return self.logs(from_block, to_block, [_mined_topic()])

    def transfers_to(self, addr, from_block, to_block):
        return self.logs(from_block, to_block,
                         [_transfer_topic(), None, pad_addr(addr)])


# ------------------------------------------------------------------ proofs
def build_preimage(miner: str, prev_work: bytes, anchor: bytes,
                   nonce: bytes) -> bytes:
    """The 116-byte preimage, exactly as the chain hashes it.

        miner address  20 bytes  packed, NOT ABI-padded to a 32-byte word
        prevWork       32 bytes
        anchor         32 bytes  blockhash(anchorBlock)
        nonce          32 bytes  big-endian uint256

    Padding this to 116 bytes is what makes a single Keccak rate block enough.
    """
    pre = unhex(miner) + prev_work + anchor + nonce
    if len(pre) != 116:
        raise ValueError(f"preimage must be 116 bytes, got {len(pre)}")
    return pre


def work_of(miner: str, prev_work: bytes, anchor: bytes, nonce: bytes) -> bytes:
    return keccak256(build_preimage(miner, prev_work, anchor, nonce))


def is_solution(miner: str, prev_work: bytes, anchor: bytes, nonce: bytes,
                target: int) -> bool:
    """True when keccak256(preimage) <= target.

    The comparison is inclusive. The site's own browser worker accepts
    digest <= target, and rejecting an equal digest would throw away a proof
    the contract would have accepted.
    """
    return int.from_bytes(work_of(miner, prev_work, anchor, nonce), "big") <= target


def effective_bits(target: int) -> float:
    """log2(2^256 / target): how many bits the target demands."""
    if target <= 0:
        return 256.0
    return 256 - target.bit_length()


def expected_hashes(target: int) -> float:
    return (2 ** 256) / target if target > 0 else float("inf")


def zero_bits(digest: bytes) -> int:
    n = 0
    for b in digest:
        if b == 0:
            n += 8
            continue
        v = b
        while not (v & 0x80):
            v <<= 1
            n += 1
        break
    return n


# ------------------------------------------------------------- tx building
def mine_calldata(nonce: int, anchor_block: int) -> str:
    return "0x" + selector("mine(uint256,uint256)") + abi_encode(
        ["uint256", "uint256"], [nonce, anchor_block]).hex()


def encode_transfer_receipt(receipt):
    """Decode Transfer logs from a receipt into (from, to, tokenId) triples."""
    out = []
    topic = _transfer_topic()[2:]
    for lg in receipt.get("logs", []) or []:
        if not lg.get("topics") or lg["topics"][0][2:] != topic:
            continue
        if lg["address"].lower() != CONTRACT.lower():
            continue
        t = lg["topics"]
        if len(t) < 4:
            continue
        frm = "0x" + t[1][-40:]
        to = "0x" + t[2][-40:]
        token_id = int(t[3], 16)
        out.append((frm, to, token_id))
    return out


def decode_mint_from_receipt(receipt, expect_miner: str):
    """A confirmed mint must show a Transfer from the zero address to us.

    A status=1 receipt without that event is not NFT ownership: gas was spent
    and nothing was received. Callers must treat that as a failure.
    """
    for frm, to, token_id in encode_transfer_receipt(receipt):
        if frm == ZERO_ADDRESS and to.lower() == expect_miner.lower():
            return token_id
    return None
