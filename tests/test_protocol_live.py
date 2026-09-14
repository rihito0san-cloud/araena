"""
Live contract checks.

These hit the real RPC, so they are marked and skipped when the network is
unreachable -- a deployment box without internet must still be able to run the
rest of the suite. They are the tests that would catch the contract being
redeployed, the ABI drifting, or the preimage layout changing.
"""
import os
import secrets
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "controller"))

import protocol as P  # noqa: E402

live = pytest.mark.skipif(
    os.environ.get("MP_SKIP_LIVE") == "1",
    reason="MP_SKIP_LIVE=1")


@pytest.fixture(scope="module")
def contract():
    try:
        c = P.Contract()
        c.chain_id()
        return c
    except Exception as exc:
        pytest.skip(f"live RPC unreachable: {exc}")


@live
def test_chain_id(contract):
    assert contract.chain_id() == 4663


@live
def test_contract_has_code(contract):
    assert len(contract.code()) > 1000


@live
def test_mining_status_decodes_24_fields(contract):
    st = contract.mining_status()
    for field in P.STATUS_FIELDS:
        assert field in st, f"missing {field}"
    assert isinstance(st["anchor"], bytes) and len(st["anchor"]) == 32
    assert isinstance(st["prevWork"], bytes) and len(st["prevWork"]) == 32
    assert st["maxSupply"] > 0
    assert st["price"] > 0, "a zero price would mean the decode is wrong"
    assert st["anchorWindow"] > 0
    assert st["blockNumber"] > P.DEPLOY_BLOCK


@live
def test_anchor_is_the_blockhash_of_anchor_block(contract):
    """The anchor must come from the contract, never a substituted blockhash."""
    st = contract.mining_status()
    assert st["anchor"] == contract.block_hash(st["anchorBlock"])


@live
def test_local_preimage_matches_contract_work_for(contract):
    """The decisive layout check, on state from one pinned block."""
    st = contract.mining_status()
    block = st["_block"]
    for _ in range(3):
        miner = "0x" + secrets.token_hex(20)
        nonce = secrets.token_bytes(32)
        local = P.work_of(miner, st["prevWork"], st["anchor"], nonce)
        remote = contract.work_for(miner, st["prevWork"], st["anchor"],
                                   int.from_bytes(nonce, "big"), block)
        assert local == remote
        assert len(P.build_preimage(miner, st["prevWork"], st["anchor"],
                                    nonce)) == 116


@live
def test_mint_price_agrees_with_status(contract):
    st = contract.mining_status()
    assert contract.mint_price(st["_block"]) == st["price"]


@live
def test_selectors_are_in_the_dispatch_table(contract):
    """A redeployed contract would answer these differently or not at all."""
    block = contract.block_number()
    assert contract.mint_price(hex(block)) >= 0


@live
def test_recent_mints_respect_the_anchor_window(contract):
    """Empirical basis for the submission deadline: real mints land with an
    anchor lag well inside the window."""
    topic = "0x" + P.keccak256(
        b"Mined(uint256,address,bytes32,bytes32,uint256,uint256,uint8,uint8,uint256)"
    ).hex()
    top = contract.block_number()
    logs = contract.logs(max(top - 20000, P.DEPLOY_BLOCK), top, [topic])
    if not logs:
        pytest.skip("no recent Mined events to sample")
    window = contract.mining_status()["anchorWindow"]
    for lg in logs[-10:]:
        mined_in = int(lg["blockNumber"], 16)
        anchor_block = int(lg["data"][2:][3 * 64:4 * 64], 16)
        assert 0 <= mined_in - anchor_block <= window


@live
def test_solution_predicate_accepts_a_real_proof(contract):
    """Build a proof against a trivially easy target and confirm the predicate
    and the contract agree on validity."""
    st = contract.mining_status()
    miner = "0x" + secrets.token_hex(20)
    nonce = secrets.token_bytes(32)
    digest = P.work_of(miner, st["prevWork"], st["anchor"], nonce)
    easy = (1 << 256) - 1
    assert P.is_solution(miner, st["prevWork"], st["anchor"], nonce, easy)
    # And the digest we computed is the one the contract would compute.
    assert contract.work_for(miner, st["prevWork"], st["anchor"],
                             int.from_bytes(nonce, "big"),
                             st["_block"]) == digest
    # A target below the digest must be rejected.
    hard = int.from_bytes(digest, "big") - 1
    assert not P.is_solution(miner, st["prevWork"], st["anchor"], nonce, hard)


@live
def test_effective_bits_is_sane(contract):
    st = contract.mining_status()
    bits = P.effective_bits(st["target"])
    assert 0 < bits < 256
    assert P.expected_hashes(st["target"]) > 1
