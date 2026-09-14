#!/usr/bin/env python3
"""
fleet.py -- worker lifecycle and proof intake.

One worker process per validated device. Workers are handed a public address,
a challenge and a fresh random prefix; they return candidate proofs. Every
returned proof is recomputed locally before the engine is allowed to see it, so
a broken or lying worker costs hashrate, never a bad transaction.

Two transports are supported:
  local  -- spawn the binary as a child process (normal case: the controller
            runs on the rented machine itself)
  ssh    -- run the binary on a remote host over a pinned SSH channel

The reference design hot-added devices but never implemented hot removal, so
removal here explicitly stops the worker, closes its pipes and suppresses
reconnection.
"""
import os
import secrets
import subprocess
import threading
import time

from protocol import build_preimage, work_of, zero_bits

# A worker whose last telemetry is older than this is not counted as hashing.
FRESH_SECONDS = 5.0


class WorkerError(RuntimeError):
    pass


class Worker:
    """One device, one process, one job at a time."""

    def __init__(self, worker_id, binary, device_index, args=None,
                 label=""):
        self.worker_id = worker_id
        self.binary = binary
        self.device_index = device_index
        self.args = list(args or [])
        self.label = label or worker_id

        self.proc = None
        self._lock = threading.RLock()
        self._reader = None

        self.state = "stopped"
        self.backend = ""
        self.device_name = ""
        self.threads = 0
        self.hashrate = 0.0
        self.total_hashes = 0
        self.best_bits = 0
        self.last_seen = 0.0
        self.selftest_ok = False
        self.error = ""
        self.solutions = 0
        self.removed = False

        # Job bookkeeping: a proof must be attributable to the job that
        # produced it, so a proof from a replaced job can be discarded.
        self.job = None
        self.job_id = 0

    # ------------------------------------------------------------- process
    def start(self):
        with self._lock:
            if self.proc and self.proc.poll() is None:
                return
            if self.removed:
                raise WorkerError(f"{self.worker_id} was removed")
            cmd = [self.binary, "--device", str(self.device_index)] + self.args
            self.proc = subprocess.Popen(
                cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True, bufsize=1,
                cwd=os.path.dirname(os.path.abspath(self.binary)) or None)
            self.state = "starting"
            self.error = ""
            self._reader = threading.Thread(target=self._read_loop,
                                            daemon=True,
                                            name=f"reader-{self.worker_id}")
            self._reader.start()

    def stop(self, suppress_reconnect=True):
        with self._lock:
            if suppress_reconnect:
                self.removed = True
            self.state = "stopping"
            proc = self.proc
            if proc is None:
                self.state = "stopped"
                return
            try:
                if proc.poll() is None:
                    try:
                        proc.stdin.write("QUIT\n")
                        proc.stdin.flush()
                    except Exception:
                        pass
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        proc.terminate()
                        try:
                            proc.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            proc.kill()
            finally:
                for stream in (proc.stdin, proc.stdout):
                    try:
                        stream.close()
                    except Exception:
                        pass
                self.proc = None
                self.state = "stopped"
                self.hashrate = 0.0

    @property
    def alive(self):
        with self._lock:
            return self.proc is not None and self.proc.poll() is None

    @property
    def fresh(self):
        return (self.state == "hashing" and self.hashrate > 0
                and (time.time() - self.last_seen) <= FRESH_SECONDS)

    # --------------------------------------------------------------- input
    def send(self, line):
        with self._lock:
            proc = self.proc
            if proc is None or proc.poll() is not None:
                raise WorkerError(f"{self.worker_id} is not running")
            proc.stdin.write(line + "\n")
            proc.stdin.flush()

    def issue_job(self, miner, prev_work, anchor, target, anchor_block,
                  prefix=None):
        """Send a JOB with a fresh random prefix and record the job identity."""
        prefix = prefix or secrets.token_bytes(24)
        if len(prefix) != 24:
            raise WorkerError("prefix must be 24 bytes")
        with self._lock:
            self.job_id += 1
            self.job = {
                "job_id": self.job_id,
                "miner": miner,
                "prevWork": prev_work,
                "anchor": anchor,
                "target": int(target),
                "anchorBlock": int(anchor_block),
                "prefix": prefix,
                "issued_at": time.time(),
            }
            self.send("JOB {} {} {} {} {} {}".format(
                miner[2:] if miner.startswith("0x") else miner,
                prev_work.hex(), anchor.hex(),
                int(target).to_bytes(32, "big").hex(), prefix.hex(),
                int(anchor_block)))
            return self.job_id

    def stop_job(self):
        with self._lock:
            self.job = None
            try:
                self.send("STOP")
            except WorkerError:
                pass

    # -------------------------------------------------------------- output
    def _read_loop(self):
        proc = self.proc
        if proc is None:
            return
        for raw in proc.stdout:
            line = raw.strip()
            if not line:
                continue
            try:
                self._handle(line)
            except Exception as exc:  # a bad line must not kill the reader
                self.error = f"parse error: {exc}"
        # stdout closed: the worker is gone.
        with self._lock:
            if self.state != "stopping":
                self.state = "exited"
                self.hashrate = 0.0

    def _handle(self, line):
        parts = line.split()
        kind = parts[0]
        with self._lock:
            self.last_seen = time.time()
            if kind == "SELFTEST":
                self.selftest_ok = len(parts) >= 2 and parts[1] == "ok"
                if not self.selftest_ok:
                    self.error = "self-test failed"
                    self.state = "invalid"
            elif kind == "READY":
                self.backend = parts[1] if len(parts) > 1 else ""
                self.device_name = parts[2] if len(parts) > 2 else ""
                try:
                    self.threads = int(parts[3]) if len(parts) > 3 else 0
                except ValueError:
                    self.threads = 0
                if self.state != "invalid":
                    self.state = "idle"
            elif kind == "RATE":
                try:
                    self.hashrate = float(parts[1])
                    self.total_hashes = int(parts[2])
                    self.best_bits = int(parts[3]) if len(parts) > 3 else 0
                except (ValueError, IndexError):
                    return
                self.state = "hashing"
            elif kind == "FOUND":
                self.solutions += 1
                self._on_found(parts)
            elif kind == "ERROR":
                self.error = " ".join(parts[1:])
                # A malformed-line complaint is not fatal; a self-test failure
                # already set state=invalid above.
            elif kind == "BYE":
                self.state = "exited"
                self.hashrate = 0.0

    def _on_found(self, parts):
        """Verify the proof locally before publishing it.

        This is the check that makes an untrusted GPU safe to run: the worker
        only proposes, and a digest that does not match our own Keccak is
        counted and dropped rather than forwarded.
        """
        if len(parts) < 4:
            self.error = "malformed FOUND"
            return
        try:
            nonce = bytes.fromhex(parts[1])
            digest = bytes.fromhex(parts[2])
            anchor_block = int(parts[3])
        except ValueError:
            self.error = "malformed FOUND hex"
            return
        job = self.job
        if job is None:
            return  # proof for a job we already replaced
        if anchor_block != job["anchorBlock"]:
            return  # stale proof from a superseded challenge
        if len(nonce) != 32 or nonce[:24] != job["prefix"]:
            return  # not from this job's prefix space
        pre = build_preimage(job["miner"], job["prevWork"], job["anchor"], nonce)
        if len(pre) != 116:
            return
        if work_of(job["miner"], job["prevWork"], job["anchor"], nonce) != digest:
            # Counted, not trusted: a lying or buggy worker loses speed only.
            self.error = "worker returned a digest that does not verify"
            return
        if int.from_bytes(digest, "big") > job["target"]:
            self.error = "worker returned a digest above target"
            return
        self.verified_found = {
            "job_id": job["job_id"],
            "miner": job["miner"],
            "prevWork": job["prevWork"],
            "anchor": job["anchor"],
            "target": job["target"],
            "anchorBlock": anchor_block,
            "nonce": nonce,
            "digest": digest,
            "zero_bits": zero_bits(digest),
            "found_at": time.time(),
        }

    # ------------------------------------------------------------- snapshot
    def take_verified(self):
        with self._lock:
            out = getattr(self, "verified_found", None)
            self.verified_found = None
            return out

    def snapshot(self):
        with self._lock:
            return {
                "worker_id": self.worker_id,
                "label": self.label,
                "device_index": self.device_index,
                "state": self.state,
                "backend": self.backend,
                "device_name": self.device_name,
                "threads": self.threads,
                "hashrate": self.hashrate,
                "fresh": self.fresh,
                "total_hashes": self.total_hashes,
                "best_bits": self.best_bits,
                "solutions": self.solutions,
                "selftest_ok": self.selftest_ok,
                "last_seen": self.last_seen,
                "error": self.error,
                "alive": self.alive,
                "removed": self.removed,
            }


class Fleet:
    """Owns the worker set and the hot add/remove rules."""

    def __init__(self, binary, extra_args=None):
        self.binary = binary
        self.extra_args = list(extra_args or [])
        self._workers = {}
        self._lock = threading.RLock()

    def worker_id_for(self, host, port, device_index):
        """Stable identity: host, port and device index together."""
        return f"{host}:{port}#{device_index}"

    def add(self, worker_id, device_index, label=""):
        with self._lock:
            if worker_id in self._workers:
                raise WorkerError(f"duplicate worker id {worker_id}")
            w = Worker(worker_id, self.binary, device_index,
                       args=self.extra_args, label=label)
            self._workers[worker_id] = w
            return w

    def remove(self, worker_id):
        """Hot removal: stop, close, and suppress any reconnect."""
        with self._lock:
            w = self._workers.pop(worker_id, None)
        if w:
            w.stop(suppress_reconnect=True)

    def get(self, worker_id):
        with self._lock:
            return self._workers.get(worker_id)

    def all(self):
        with self._lock:
            return list(self._workers.values())

    def start_all(self):
        for w in self.all():
            try:
                w.start()
            except WorkerError as exc:
                w.error = str(exc)

    def stop_all(self):
        for w in self.all():
            w.stop(suppress_reconnect=False)

    def fresh_hashrate(self):
        """Aggregate rate over workers with fresh telemetry only.

        Counting a disconnected device at its last reported rate is how a
        dashboard ends up claiming a hashrate that no longer exists.
        """
        return sum(w.hashrate for w in self.all() if w.fresh)

    def fresh_workers(self):
        return [w for w in self.all() if w.fresh]

    def snapshot(self):
        return {
            "workers": [w.snapshot() for w in self.all()],
            "fresh_hashrate": self.fresh_hashrate(),
            "fresh_count": len(self.fresh_workers()),
        }
