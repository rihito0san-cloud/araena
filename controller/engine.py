#!/usr/bin/env python3
"""
engine.py -- orchestration: read state, feed workers, submit verified proofs.

Runs as one background thread. It never holds a private key (the KeyStore does)
and never decides to spend without the ledger's cap and the selected-signer
rules being satisfied. When no key is loaded it reports that it is waiting for
the operator -- it does not claim to be mining.

Challenge turnover is the normal operating condition here: prevWork and the
anchor roll every block, and a proof stays valid only while its anchorBlock is
inside the contract's anchor window. So the loop re-issues jobs on state change
rather than letting workers grind a dead challenge.
"""
import threading
import time

from protocol import Contract, effective_bits, expected_hashes, zero_bits
from submit import Submitter, SubmitError

# Re-issue a job when the challenge is older than this many blocks. Real mints
# land with an anchor lag of roughly 20-130 blocks against a 250-block window,
# so this leaves room to submit while keeping work current.
JOB_REFRESH_BLOCKS = 60
POLL_SECONDS = 2.0
MINER_IDLE_SLEEP = 1.0


class Engine:
    def __init__(self, contract, ledger, keystore, fleet, submitter=None,
                 autostart=False):
        self.c = contract
        self.ledger = ledger
        self.keys = keystore
        self.fleet = fleet
        self.sub = submitter or Submitter(contract, ledger, keystore)

        self._thread = None
        self._stop = threading.Event()
        self._lock = threading.RLock()

        self.running = False
        self.paused = False
        self.autosubmit = bool(autostart)

        self.status = {}
        self.chain_error = ""
        self.chain_age = 0.0
        self.last_status_at = 0.0
        self.last_challenge = None
        self.last_job_block = 0
        self.solutions = 0
        self.last_solution = None
        self.submissions = 0
        self.last_submission = None
        self.mints = 0
        self.last_mint = None

    # ------------------------------------------------------------- control
    def start(self):
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop.clear()
            self.running = True
            self._thread = threading.Thread(target=self._run, daemon=True,
                                            name="engine")
            self._thread.start()

    def stop(self):
        self._stop.set()
        with self._lock:
            self.running = False
        if self._thread:
            self._thread.join(timeout=10)

    def pause(self, on):
        """Pause stops new work and submissions; receipts keep being watched."""
        with self._lock:
            self.paused = bool(on)
        if on:
            for w in self.fleet.all():
                w.stop_job()

    def set_autosubmit(self, on):
        with self._lock:
            self.autosubmit = bool(on)

    # --------------------------------------------------------------- state
    def _read_state(self):
        st = self.c.mining_status()
        with self._lock:
            self.status = st
            self.last_status_at = time.time()
            self.chain_error = ""
        return st

    def _challenge_key(self, st):
        return (st["prevWork"], st["anchor"], st["anchorBlock"], st["target"])

    def _issue_jobs(self, st):
        """Give every idle-ish worker the current challenge and a fresh prefix."""
        signer = self.keys.selected
        if not signer:
            return 0
        issued = 0
        for w in self.fleet.all():
            if w.removed or not w.alive:
                continue
            try:
                w.issue_job(signer, st["prevWork"], st["anchor"],
                            st["target"], st["anchorBlock"])
                issued += 1
            except Exception as exc:
                w.error = f"job issue failed: {exc}"
        with self._lock:
            self.last_job_block = st["blockNumber"]
        return issued

    def _collect_proofs(self, st):
        proofs = []
        for w in self.fleet.all():
            p = w.take_verified()
            if not p:
                continue
            self.solutions += 1
            self.last_solution = {
                "worker": w.worker_id,
                "zero_bits": p["zero_bits"],
                "digest": p["digest"].hex(),
                "anchorBlock": p["anchorBlock"],
                "at": p["found_at"],
            }
            # Only a proof for the challenge we are still pursuing is useful.
            if p["prevWork"] != st["prevWork"] or p["anchor"] != st["anchor"]:
                self.ledger.event("proof", "discarded proof for a stale challenge")
                continue
            proofs.append(p)
        return proofs

    def _submit(self, proof):
        try:
            res = self.sub.submit(proof)
        except SubmitError as exc:
            self.ledger.event("submit_error", f"[{exc.kind}] {exc.message}")
            return None
        except Exception as exc:
            self.ledger.event("submit_error", f"[unexpected] {exc}")
            return None

        self.submissions += 1
        self.last_submission = res
        if not self.autosubmit:
            return res

        # Reconcile on a worker thread so receipt waiting never blocks mining.
        threading.Thread(
            target=self._finish, args=(res,), daemon=True,
            name=f"receipt-{res['tx_hash'][:10]}").start()
        return res

    def _finish(self, res):
        try:
            out = self.sub.reconcile(res["row_id"], res["tx_hash"],
                                     res["wallet"], res["value_wei"])
        except Exception as exc:
            self.ledger.event("receipt_error", str(exc))
            return
        if out.get("stage") == "mint_confirmed":
            self.mints += 1
            self.last_mint = out

    # ---------------------------------------------------------------- loop
    def _run(self):
        while not self._stop.is_set():
            try:
                st = self._read_state()
            except Exception as exc:
                with self._lock:
                    self.chain_error = str(exc)
                    self.chain_age = time.time() - self.last_status_at
                self._stop.wait(POLL_SECONDS * 2)
                continue

            self.chain_age = time.time() - self.last_status_at

            if not self.paused:
                need_job = (
                    self.last_challenge != self._challenge_key(st)
                    or st["blockNumber"] - self.last_job_block > JOB_REFRESH_BLOCKS
                )
                if need_job and self.keys.selected:
                    self._issue_jobs(st)
                    self.last_challenge = self._challenge_key(st)

                for proof in self._collect_proofs(st):
                    self._submit(proof)
            else:
                # Paused: keep draining so the queue does not grow, but drop.
                for w in self.fleet.all():
                    w.take_verified()

            self._stop.wait(POLL_SECONDS)

    # ------------------------------------------------------------ snapshot
    def snapshot(self):
        fleet = self.fleet.snapshot()
        st = dict(self.status)
        target = st.get("target", 0)
        hps = fleet["fresh_hashrate"]
        bits = effective_bits(target) if target else None
        exp = expected_hashes(target) if target else None
        eta = (exp / hps) if (exp and hps > 0) else None

        signer = self.keys.selected
        if signer and not self.keys.is_loaded(signer):
            signer_state = "waiting_for_key"
        elif signer:
            signer_state = "loaded"
        else:
            signer_state = "none"

        # Drop bytes from the public snapshot.
        safe_status = {k: (v.hex() if isinstance(v, (bytes, bytearray)) else v)
                       for k, v in st.items()}

        return {
            "running": self.running,
            "paused": self.paused,
            "autosubmit": self.autosubmit,
            "signer": signer,
            "signer_state": signer_state,
            "fleet": fleet,
            "status": safe_status,
            "target_bits": bits,
            "expected_hashes": exp,
            "eta_seconds": eta,
            "chain_error": self.chain_error,
            "chain_age": self.chain_age,
            "solutions": self.solutions,
            "last_solution": self.last_solution,
            "submissions": self.submissions,
            "last_submission": self.last_submission,
            "mints": self.mints,
            "last_mint": self.last_mint,
            "budget": self.ledger.snapshot(),
        }
