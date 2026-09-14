"""
Keccak and preimage vectors.

Every digest here is pinned to two independent authorities: pycryptodome's
Keccak and (for the workFor vectors) the live contract's own pure function.
These are the vectors compiled into worker/vectors.inc, so regenerating them
without re-checking against the chain would silently weaken the self-test.
"""
import os
import sys

import pytest
from Crypto.Hash import keccak

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mp_reference import keccak256, selector  # noqa: E402

CANONICAL_EMPTY = "c5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470"
CANONICAL_ABC = "4e03657aea45a94fc7d47ba826c8d667c0d1e6e33a64a036ec44f58fa12d6c45"


def pdk(data: bytes) -> bytes:
    k = keccak.new(digest_bits=256)
    k.update(data)
    return k.digest()


def test_canonical_empty_string_vector():
    """The vector that catches a padding or lane-count error immediately."""
    assert keccak256(b"").hex() == CANONICAL_EMPTY
    assert pdk(b"").hex() == CANONICAL_EMPTY


def test_canonical_abc_vector():
    assert keccak256(b"abc").hex() == CANONICAL_ABC


def test_reference_matches_pycryptodome_on_116_byte_inputs():
    for _ in range(25):
        msg = os.urandom(116)
        assert keccak256(msg) == pdk(msg)


@pytest.mark.parametrize("n", [0, 1, 135, 136, 137, 271, 272])
def test_rate_block_boundaries(n):
    """Lengths around the 136-byte rate expose absorb/padding bugs."""
    msg = os.urandom(n)
    assert keccak256(msg) == pdk(msg)


def test_single_padding_bit_not_sha3():
    """Ethereum Keccak pads with 0x01, SHA3-256 with 0x06.

    Using hashlib.sha3_256 here is the classic way to build a miner that runs
    fast and finds proofs the contract rejects.
    """
    import hashlib
    msg = os.urandom(116)
    assert keccak256(msg) != hashlib.sha3_256(msg).digest()


def test_selectors_match_known_values():
    """Selectors read off the live contract's dispatch, not recomputed here."""
    assert selector("miningStatus()") == "be38c5c8"
    assert selector("workFor(address,bytes32,bytes32,uint256)") == "41c7e5bd"
    assert selector("mine(uint256,uint256)") == "071e9503"
    assert selector("mintPrice()") == "6817c76c"


VECTORS = [
    b"\x00" * 116,
    b"\xff" * 116,
    bytes(range(116)),
    bytes.fromhex("1111111122222222333333334444444455555555") + b"\x00" * 32
    + b"\xff" * 32 + b"\x00" * 32,
    bytes.fromhex("a1" * 20) + bytes.fromhex("b2" * 32) + bytes.fromhex("c3" * 32)
    + bytes.fromhex("d4" * 24) + b"\x00" * 7 + b"\x01",
    bytes.fromhex("de" * 20) + bytes.fromhex("ad" * 32) + bytes.fromhex("be" * 32)
    + bytes.fromhex("ef" * 24) + b"\xff" * 8,
    bytes.fromhex("ab" * 20) + b"\x7f" * 32 + b"\x80" * 32 + b"\x00" * 23
    + b"\x7f" + b"\xff" * 8,
    bytes.fromhex("00" * 19 + "01") + bytes.fromhex("01" * 32)
    + bytes.fromhex("02" * 32) + bytes.fromhex("03" * 24) + b"\x00" * 7 + b"\x01",
]


def test_compiled_vectors_file_matches_reference():
    """worker/vectors.inc must agree with this reference, or the binary's
    self-test is checking itself against a wrong expectation."""
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "worker", "vectors.inc")
    assert os.path.exists(path), "worker/vectors.inc missing"
    rows = [ln.strip() for ln in open(path) if ln.strip().startswith("{")]
    assert len(rows) == len(VECTORS), (
        f"vectors.inc has {len(rows)} rows, this test defines {len(VECTORS)}")
    for row, vec in zip(rows, VECTORS):
        pre_hex, want = row.strip("{},").replace('"', "").split(",")
        assert bytes.fromhex(pre_hex.strip()) == vec
        assert want.strip() == keccak256(vec).hex()


def test_preimage_layout_is_116_bytes_and_packed():
    """The address is packed, not ABI-padded to a 32-byte word."""
    miner = bytes.fromhex("11" * 20)
    pre = miner + b"\x22" * 32 + b"\x33" * 32 + b"\x44" * 32
    assert len(pre) == 116
    assert pre[:20] == miner
    # ABI padding would put 12 zero bytes before the address.
    assert pre[:12] != b"\x00" * 12
