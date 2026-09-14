"""
Independent reference implementations used by the test suite.

This module is deliberately NOT imported by the controller. The point of a
verification harness is that it does not share code with the thing it checks,
so the Keccak here is a from-spec implementation validated against
pycryptodome, and the RPC helpers are hand-rolled rather than the controller's
protocol module. Three independent authorities agree on every proof:

  1. this module's from-spec Keccak
  2. pycryptodome's Keccak
  3. the contract's own pure workFor() view
"""
import json
import urllib.request

RPC = "https://rpc.minerpotatos.xyz"
CONTRACT = "0xb0db77c5d6ed578189609ecc72d25699a79f785b"
CHAIN_ID = 4663

# ---------------------------------------------------------------- Keccak-256
_RC = [
    0x0000000000000001, 0x0000000000008082, 0x800000000000808A,
    0x8000000080008000, 0x000000000000808B, 0x0000000080000001,
    0x8000000080008081, 0x8000000000008009, 0x000000000000008A,
    0x0000000000000088, 0x0000000080008009, 0x000000008000000A,
    0x000000008000808B, 0x800000000000008B, 0x8000000000008089,
    0x8000000000008003, 0x8000000000008002, 0x8000000000000080,
    0x000000000000800A, 0x800000008000000A, 0x8000000080008081,
    0x8000000000008080, 0x0000000080000001, 0x8000000080008008,
]
# rho offsets indexed [x][y]
_R = [
    [0, 36, 3, 41, 18],
    [1, 44, 10, 45, 2],
    [62, 6, 43, 15, 61],
    [28, 55, 25, 21, 56],
    [27, 20, 39, 8, 14],
]
_M = (1 << 64) - 1
_RATE = 136


def _rotl(x, n):
    n %= 64
    return ((x << n) | (x >> (64 - n))) & _M if n else x


def _keccakf(a):
    for rnd in range(24):
        c = [a[x] ^ a[x + 5] ^ a[x + 10] ^ a[x + 15] ^ a[x + 20] for x in range(5)]
        d = [c[(x + 4) % 5] ^ _rotl(c[(x + 1) % 5], 1) for x in range(5)]
        a = [a[i] ^ d[i % 5] for i in range(25)]
        b = [0] * 25
        for x in range(5):
            for y in range(5):
                # pi sends lane (x,y) to index y + 5*((2x+3y) mod 5).
                # Swapping these index arguments yields a stable, fast,
                # entirely wrong hash -- see keccak.h for the same warning.
                b[y + 5 * ((2 * x + 3 * y) % 5)] = _rotl(a[y * 5 + x], _R[x][y])
        for x in range(5):
            for y in range(5):
                a[y * 5 + x] = b[y * 5 + x] ^ (
                    (~b[y * 5 + ((x + 1) % 5)] & _M) & b[y * 5 + ((x + 2) % 5)])
        a[0] ^= _RC[rnd]
    return a


def keccak256(msg: bytes) -> bytes:
    pad = bytearray(msg)
    pad.append(0x01)
    while len(pad) % _RATE:
        pad.append(0)
    pad[-1] |= 0x80
    a = [0] * 25
    for off in range(0, len(pad), _RATE):
        blk = pad[off:off + _RATE]
        for i in range(_RATE // 8):
            a[i] ^= int.from_bytes(blk[i * 8:i * 8 + 8], "little")
        a = _keccakf(a)
    return b"".join(a[i].to_bytes(8, "little") for i in range(4))


def selector(sig: str) -> str:
    return keccak256(sig.encode()).hex()[:8]


# ------------------------------------------------------------------- RPC
def _rpc(method, params, block="latest"):
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    req = urllib.request.Request(
        RPC, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=25) as r:
        out = json.loads(r.read())
    if "error" in out:
        raise RuntimeError(out["error"])
    return out["result"]


def block_number() -> int:
    return int(_rpc("eth_blockNumber", []), 16)


def _call(data: str, block: str) -> str:
    return _rpc("eth_call", [{"to": CONTRACT, "data": data}, block])


_STATUS_FIELDS = [
    "anchorBlock", "anchor", "prevWork", "target", "difficulty", "price",
    "supply", "maxSupply", "targetInterval", "lastMintAt", "startTime",
    "anchorWindow", "burstLeft", "burstReadyAt", "chainTime", "blockNumber",
    "epoch", "floorBits", "streak", "activeSupply", "epochEndsAt", "vault",
    "redeemValue", "epochPrice",
]


def mining_status(block: str = None):
    """Returns (status_dict, block_tag). Reads a single pinned block."""
    if block is None:
        block = hex(block_number() - 1)
    raw = _call("0x" + selector("miningStatus()"), block)
    words = [raw[2 + i * 64:2 + (i + 1) * 64] for i in range((len(raw) - 2) // 64)]
    if len(words) != 24:
        raise RuntimeError(f"miningStatus returned {len(words)} words, expected 24")
    st = {}
    for i, name in enumerate(_STATUS_FIELDS):
        v = int(words[i], 16)
        st[name] = v.to_bytes(32, "big") if name in ("anchor", "prevWork") else v
    return st, block


def work_for(miner: bytes, prev_work: bytes, anchor: bytes, nonce: int,
             block: str = "latest") -> bytes:
    """The contract's own pure preimage hash -- the authority on the layout."""
    data = "0x" + selector("workFor(address,bytes32,bytes32,uint256)")
    data += miner.hex().rjust(64, "0")
    data += prev_work.hex() + anchor.hex() + nonce.to_bytes(32, "big").hex()
    return bytes.fromhex(_call(data, block)[2:])


def effective_bits(target: int) -> float:
    return 256 - target.bit_length() if target else 256.0
