"""
Worker intake: proof verification and job bookkeeping.

The security property under test is that a worker only ever *proposes*. A
digest that does not match our own Keccak is counted and dropped, so a broken
or malicious GPU costs hashrate and never reaches the submitter.
"""
import os
import sys
import time

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "controller"))

from fleet import Fleet, Worker, WorkerError  # noqa: E402
from mp_reference import keccak256  # noqa: E402

MINER = "0x" + "11" * 20
PREV = bytes.fromhex("22" * 32)
ANCHOR = bytes.fromhex("33" * 32)
PREFIX = bytes.fromhex("44" * 24)
# A trivially satisfiable target so any digest qualifies; the verification we
# are testing is about the digest, not the difficulty.
TARGET = (1 << 256) - 1
ANCHOR_BLOCK = 63039232


def make_worker():
    return Worker("test#0", "/bin/true", 0)


def job_state(w):
    w.job = {
        "job_id": 1, "miner": MINER, "prevWork": PREV, "anchor": ANCHOR,
        "target": TARGET, "anchorBlock": ANCHOR_BLOCK, "prefix": PREFIX,
        "issued_at": time.time(),
    }
    return w


def found_line(nonce, digest, anchor_block=ANCHOR_BLOCK):
    return "FOUND {} {} {}".format(nonce.hex(), digest.hex(), anchor_block)


def test_valid_proof_is_published_after_local_recomputation():
    w = job_state(make_worker())
    nonce = PREFIX + (7).to_bytes(8, "big")
    digest = keccak256(bytes.fromhex(MINER[2:]) + PREV + ANCHOR + nonce)
    w._handle(found_line(nonce, digest))
    p = w.take_verified()
    assert p is not None
    assert p["nonce"] == nonce
    assert p["digest"] == digest
    assert p["job_id"] == 1


def test_lying_digest_is_counted_but_never_published():
    """The core untrusted-GPU guarantee."""
    w = job_state(make_worker())
    nonce = PREFIX + (7).to_bytes(8, "big")
    fake = b"\x00" * 32  # claims a perfect hash it did not compute
    w._handle(found_line(nonce, fake))
    assert w.take_verified() is None
    assert "does not verify" in w.error
    assert w.solutions == 1  # counted, not trusted


def test_correct_digest_above_target_is_rejected():
    w = make_worker()
    w.job = {"job_id": 1, "miner": MINER, "prevWork": PREV, "anchor": ANCHOR,
             "target": 1 << 128, "anchorBlock": ANCHOR_BLOCK, "prefix": PREFIX,
             "issued_at": time.time()}
    nonce = PREFIX + (1).to_bytes(8, "big")
    digest = keccak256(bytes.fromhex(MINER[2:]) + PREV + ANCHOR + nonce)
    if int.from_bytes(digest, "big") <= (1 << 128):
        pytest.skip("astronomically unlikely: digest happened to satisfy target")
    w._handle(found_line(nonce, digest))
    assert w.take_verified() is None
    assert "above target" in w.error


def test_proof_from_a_different_prefix_is_dropped():
    w = job_state(make_worker())
    other = bytes.fromhex("99" * 24) + (3).to_bytes(8, "big")
    digest = keccak256(bytes.fromhex(MINER[2:]) + PREV + ANCHOR + other)
    w._handle(found_line(other, digest))
    assert w.take_verified() is None  # not this job's prefix space


def test_proof_from_a_superseded_challenge_is_dropped():
    w = job_state(make_worker())
    nonce = PREFIX + (2).to_bytes(8, "big")
    digest = keccak256(bytes.fromhex(MINER[2:]) + PREV + ANCHOR + nonce)
    # The challenge moved on before the proof arrived.
    w._handle(found_line(nonce, digest, anchor_block=ANCHOR_BLOCK + 1))
    assert w.take_verified() is None


def test_malformed_found_does_not_crash_the_reader():
    w = job_state(make_worker())
    for bad in ["FOUND", "FOUND zz zz 1", "FOUND " + "ab" * 32 + " cd 1",
                "FOUND " + "ab" * 31 + " " + "cd" * 32 + " 1"]:
        w._handle(bad)
    assert w.take_verified() is None


def test_selftest_failure_marks_worker_invalid():
    w = make_worker()
    w._handle("SELFTEST failed 3 vectors")
    assert w.selftest_ok is False
    assert w.state == "invalid"
    w._handle("READY host dev 1 1")
    assert w.state == "invalid"  # READY must not clear a failed self-test


def test_selftest_ok_allows_ready():
    w = make_worker()
    w._handle("SELFTEST ok 8 vectors")
    w._handle("READY cuda RTX 4090 sm_89 8192 4")
    assert w.selftest_ok is True
    assert w.state == "idle"
    assert w.backend == "cuda"


def test_rate_marks_hashing_and_staleness_clears():
    w = make_worker()
    w._handle("SELFTEST ok 8 vectors")
    w._handle("READY host cpu 1 1")
    w._handle("RATE 1234567 5000000 12")
    assert w.state == "hashing"
    assert w.hashrate == 1234567
    assert w.fresh is True
    w.last_seen = time.time() - 30  # telemetry went quiet
    assert w.fresh is False


def test_issue_job_sends_fresh_prefix_each_time():
    """Reusing a prefix would re-search space already covered."""
    sent = []
    w = make_worker()
    w.send = lambda line: sent.append(line)
    w.issue_job(MINER, PREV, ANCHOR, TARGET, ANCHOR_BLOCK)
    w.issue_job(MINER, PREV, ANCHOR, TARGET, ANCHOR_BLOCK)
    assert len(sent) == 2
    p1 = sent[0].split()[5]
    p2 = sent[1].split()[5]
    assert p1 != p2
    assert len(p1) == 48 and len(p2) == 48
    # The job line carries hex without 0x and the decimal anchor block.
    assert not p1.startswith("0x")
    assert sent[0].split()[6] == str(ANCHOR_BLOCK)


def test_take_verified_is_one_shot():
    w = job_state(make_worker())
    nonce = PREFIX + (9).to_bytes(8, "big")
    digest = keccak256(bytes.fromhex(MINER[2:]) + PREV + ANCHOR + nonce)
    w._handle(found_line(nonce, digest))
    assert w.take_verified() is not None
    assert w.take_verified() is None


def test_fleet_rejects_duplicate_worker_ids():
    f = Fleet("/bin/true")
    f.add("local#0", 0)
    with pytest.raises(WorkerError):
        f.add("local#0", 0)


def test_fleet_counts_only_fresh_hashrate():
    f = Fleet("/bin/true")
    a = f.add("local#0", 0)
    b = f.add("local#1", 1)
    for w, rate, fresh in ((a, 100.0, True), (b, 5000.0, False)):
        w.state = "hashing"
        w.hashrate = rate
        w.last_seen = time.time() if fresh else time.time() - 60
    assert f.fresh_hashrate() == 100.0
    assert f.fresh_count if hasattr(f, "fresh_count") else len(f.fresh_workers()) == 1


def test_removed_worker_does_not_restart():
    f = Fleet("/bin/true")
    w = f.add("local#0", 0)
    f.remove("local#0")
    assert f.get("local#0") is None
    assert w.removed is True
    with pytest.raises(WorkerError):
        w.start()
