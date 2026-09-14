#!/usr/bin/env python3
"""
End-to-end worker protocol harness.

Drives a compiled worker binary the way the controller will: keep the pipe
open, send one JOB, read lines until FOUND, verify the returned proof against
an independent Keccak implementation and (optionally) against the contract's
own pure workFor() view, then send QUIT.

Usage:
  python3 tests/harness.py --binary path/to/miner [--live] [--easy]

  --easy  use a trivially satisfiable target so the pipeline can be proved
          without GPU-class hashrate
  --live  additionally cross-check the digest against the live contract

Piping "JOB ...\\nQUIT\\n" into the worker is NOT a valid test: the worker
correctly drains buffered commands before hashing, so it would consume QUIT and
exit without ever searching.
"""
import argparse
import os
import secrets
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from Crypto.Hash import keccak  # noqa: E402

from mp_reference import keccak256, mining_status, work_for  # noqa: E402


def build_preimage(miner, prev_work, anchor, nonce):
    return miner + prev_work + anchor + nonce


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--binary", required=True)
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--easy", action="store_true")
    ap.add_argument("--timeout", type=float, default=120.0)
    args = ap.parse_args()

    miner = bytes.fromhex("1111111122222222333333334444444455555555")
    prefix = secrets.token_bytes(24)

    if args.live:
        st, block = mining_status()
        prev_work, anchor = st["prevWork"], st["anchor"]
        anchor_block = st["anchorBlock"]
        target = st["target"]
        print(f"live challenge: block={block} anchorBlock={anchor_block} "
              f"target={256 - target.bit_length()} bits")
        if args.easy:
            target = (1 << 256) - 1
            print("  (overridden with an all-ones target for --easy)")
    else:
        prev_work = bytes.fromhex("00" * 32)
        anchor = bytes.fromhex("11" * 32)
        anchor_block = 63039232
        target = (1 << 256) - 1 if args.easy else (1 << 200)

    job = "JOB {} {} {} {} {} {}\n".format(
        miner.hex(), prev_work.hex(), anchor.hex(),
        target.to_bytes(32, "big").hex(), prefix.hex(), anchor_block)

    p = subprocess.Popen([args.binary], stdin=subprocess.PIPE,
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         text=True, bufsize=1)
    p.stdin.write(job)
    p.stdin.flush()

    deadline = time.time() + args.timeout
    found = None
    saw_selftest = saw_ready = False
    rates = 0
    try:
        while time.time() < deadline:
            line = p.stdout.readline().strip()
            if not line:
                if p.poll() is not None:
                    break
                continue
            print("  worker:", line[:120])
            if line.startswith("SELFTEST ok"):
                saw_selftest = True
            elif line.startswith("READY"):
                saw_ready = True
            elif line.startswith("RATE"):
                rates += 1
            elif line.startswith("FOUND"):
                found = line.split()
                break
            elif line.startswith("ERROR"):
                print("FAIL: worker reported an error")
                return 1
    finally:
        try:
            p.stdin.write("QUIT\n")
            p.stdin.flush()
            p.wait(timeout=5)
        except Exception:
            p.kill()

    ok = True
    def check(label, cond):
        nonlocal ok
        ok = ok and bool(cond)
        print(f"  [{'ok' if cond else 'FAIL'}] {label}")

    print("\nprotocol")
    check("worker ran its self-test", saw_selftest)
    check("worker reported READY", saw_ready)

    if not found:
        print("\nno solution within the timeout "
              "(expected for a hard target without a GPU)")
        return 0 if not args.easy else 1

    nonce = bytes.fromhex(found[1])
    digest = bytes.fromhex(found[2])
    echoed_anchor = int(found[3])

    print("\nproof")
    py_keccak = keccak256(build_preimage(miner, prev_work, anchor, nonce))
    ref_keccak = keccak.new(digest_bits=256)
    ref_keccak.update(build_preimage(miner, prev_work, anchor, nonce))
    check("worker digest == our C keccak path", digest == py_keccak)
    check("worker digest == pycryptodome keccak", digest == ref_keccak.digest())
    check("preimage length is 116", len(build_preimage(miner, prev_work, anchor, nonce)) == 116)
    check("nonce carries the job prefix", nonce[:24] == prefix)
    check("anchorBlock echoed unchanged", echoed_anchor == anchor_block)
    check("digest <= target", int.from_bytes(digest, "big") <= target)

    if args.live:
        cf = work_for(miner, prev_work, anchor, int.from_bytes(nonce, "big"))
        check("worker digest == contract workFor()", digest == cf)

    print("\nRESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
