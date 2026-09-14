#!/usr/bin/env python3
"""
budget.py -- shared spending ledger and cap enforcement.

One SQLite file holds the cap, cumulative spend, and per-transaction
reservations. Every financial value is an integer number of wei, stored as a
decimal string so nothing is ever rounded by a float.

The invariant enforced before any broadcast:

    spent + reserved + new_maximum_liability <= cap
    maximum_liability = payable_value + gas_limit * max_fee_per_gas

Changing the cap never resets spent or reserved, and a cap below existing
obligations is rejected. The cap is set by the operator at activation; nothing
in this module invents one, and a default is never treated as authorization.
"""
import os
import sqlite3
import threading
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS wallets (
  address   TEXT PRIMARY KEY,
  label     TEXT NOT NULL DEFAULT '',
  added_at  REAL NOT NULL,
  is_signer INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS txs (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  tx_hash        TEXT,
  wallet         TEXT NOT NULL,
  nonce          INTEGER,
  value_wei      TEXT NOT NULL,
  gas_limit      INTEGER NOT NULL,
  max_fee_wei    TEXT NOT NULL,
  reserve_wei    TEXT NOT NULL,
  spent_wei      TEXT NOT NULL DEFAULT '0',
  state          TEXT NOT NULL,
  token_id       INTEGER,
  proof_nonce    TEXT,
  anchor_block   INTEGER,
  signed_raw     TEXT,
  note           TEXT NOT NULL DEFAULT '',
  created_at     REAL NOT NULL,
  updated_at     REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS txs_state ON txs(state);
CREATE TABLE IF NOT EXISTS events (
  id        INTEGER PRIMARY KEY AUTOINCREMENT,
  ts        REAL NOT NULL,
  kind      TEXT NOT NULL,
  message   TEXT NOT NULL
);
"""

# A transaction is "reserved" until it reaches a terminal state.
OPEN_STATES = ("reserved", "broadcast", "pending")


class BudgetError(RuntimeError):
    pass


class Ledger:
    """Thread-safe ledger. One connection, guarded by a lock.

    SQLite is used in serialized mode with a single connection because the
    controller does bounded, low-rate writes and correctness of the atomic
    reserve/release matters far more than write concurrency.
    """

    def __init__(self, path):
        self.path = path
        self._lock = threading.RLock()
        new = not os.path.exists(path)
        self.db = sqlite3.connect(path, check_same_thread=False,
                                  isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA foreign_keys=ON")
        with self._lock:
            self.db.executescript(SCHEMA)
            if new:
                # cap starts at 0: the system cannot spend until the operator
                # sets a limit explicitly.
                self._set("cap_wei", "0")

    # ------------------------------------------------------------ settings
    def _set(self, key, value):
        self.db.execute(
            "INSERT INTO settings(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, str(value)))

    def _get(self, key, default=None):
        r = self.db.execute("SELECT value FROM settings WHERE key=?",
                            (key,)).fetchone()
        return r["value"] if r else default

    @property
    def cap(self):
        with self._lock:
            return int(self._get("cap_wei", "0"))

    def set_cap(self, cap_wei: int):
        """Set the spending ceiling. Refuses to lower it below obligations."""
        cap_wei = int(cap_wei)
        if cap_wei < 0:
            raise BudgetError("cap cannot be negative")
        with self._lock:
            spent = self.spent
            reserved = self.reserved
            if cap_wei < spent + reserved:
                raise BudgetError(
                    f"cap {cap_wei} is below existing obligations "
                    f"(spent {spent} + reserved {reserved}); "
                    "resolve open transactions first")
            self._set("cap_wei", cap_wei)
            self.event("cap", f"cap set to {cap_wei} wei")

    # ------------------------------------------------------------- amounts
    @property
    def spent(self):
        with self._lock:
            r = self.db.execute(
                "SELECT COALESCE(SUM(CAST(spent_wei AS INTEGER)),0) s "
                "FROM txs").fetchone()
            return int(r["s"])

    @property
    def reserved(self):
        with self._lock:
            q = "SELECT COALESCE(SUM(CAST(reserve_wei AS INTEGER)),0) s FROM txs"
            ph = ",".join("?" * len(OPEN_STATES))
            r = self.db.execute(q + f" WHERE state IN ({ph})",
                                OPEN_STATES).fetchone()
            return int(r["s"])

    def remaining(self):
        with self._lock:
            return self.cap - self.spent - self.reserved

    def snapshot(self):
        with self._lock:
            cap, spent, reserved = self.cap, self.spent, self.reserved
            return {"cap_wei": cap, "spent_wei": spent,
                    "reserved_wei": reserved,
                    "remaining_wei": cap - spent - reserved,
                    "cap_set": cap > 0}

    # -------------------------------------------------------- reservations
    def reserve(self, *, wallet, value_wei, gas_limit, max_fee_wei,
                proof_nonce=None, anchor_block=None, nonce=None,
                signed_raw=None, note=""):
        """Atomically reserve liability and persist the intent.

        Returns the row id. Raises BudgetError when the cap would be exceeded.
        The reservation is written BEFORE broadcast so a crash between the two
        can never leave unaccounted spend behind.
        """
        value_wei = int(value_wei)
        gas_limit = int(gas_limit)
        max_fee_wei = int(max_fee_wei)
        liability = value_wei + gas_limit * max_fee_wei
        now = time.time()
        with self._lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                cap = self.cap
                if cap <= 0:
                    raise BudgetError(
                        "no spending cap configured; refusing to reserve")
                spent = self.spent
                reserved = self.reserved
                if spent + reserved + liability > cap:
                    raise BudgetError(
                        f"cap exceeded: spent {spent} + reserved {reserved} + "
                        f"liability {liability} > cap {cap}")
                cur = self.db.execute(
                    "INSERT INTO txs(tx_hash,wallet,nonce,value_wei,gas_limit,"
                    "max_fee_wei,reserve_wei,state,proof_nonce,anchor_block,"
                    "signed_raw,note,created_at,updated_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (None, wallet.lower(), nonce, str(value_wei), gas_limit,
                     str(max_fee_wei), str(liability), "reserved", proof_nonce,
                     anchor_block, signed_raw, note, now, now))
                row_id = cur.lastrowid
                self.db.execute("COMMIT")
            except Exception:
                self.db.execute("ROLLBACK")
                raise
        return row_id

    def mark_broadcast(self, row_id, tx_hash, wallet_nonce=None,
                       signed_raw=None):
        now = time.time()
        with self._lock:
            self.db.execute(
                "UPDATE txs SET tx_hash=?, state='broadcast', nonce="
                "COALESCE(?,nonce), signed_raw=COALESCE(?,signed_raw), "
                "updated_at=? WHERE id=?",
                (tx_hash, wallet_nonce, signed_raw, now, row_id))

    def settle_success(self, row_id, *, gas_used_wei, value_wei, token_id=None):
        """Release the reservation exactly once and book actual cost."""
        now = time.time()
        with self._lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                row = self.db.execute("SELECT state FROM txs WHERE id=?",
                                      (row_id,)).fetchone()
                if row is None:
                    raise BudgetError(f"no tx row {row_id}")
                if row["state"] not in OPEN_STATES:
                    # Idempotent: a replayed receipt must not double-book.
                    self.db.execute("ROLLBACK")
                    return False
                self.db.execute(
                    "UPDATE txs SET state='confirmed', reserve_wei='0', "
                    "spent_wei=?, token_id=?, updated_at=? WHERE id=?",
                    (str(int(gas_used_wei) + int(value_wei)), token_id, now,
                     row_id))
                self.db.execute("COMMIT")
            except Exception:
                self.db.execute("ROLLBACK")
                raise
        return True

    def settle_failure(self, row_id, *, gas_used_wei, note=""):
        """A reverted tx still consumed gas: book gas only, keep value out."""
        now = time.time()
        with self._lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                row = self.db.execute("SELECT state FROM txs WHERE id=?",
                                      (row_id,)).fetchone()
                if row is None:
                    raise BudgetError(f"no tx row {row_id}")
                if row["state"] not in OPEN_STATES:
                    self.db.execute("ROLLBACK")
                    return False
                self.db.execute(
                    "UPDATE txs SET state='failed', reserve_wei='0', "
                    "spent_wei=?, note=?, updated_at=? WHERE id=?",
                    (str(int(gas_used_wei)), note, now, row_id))
                self.db.execute("COMMIT")
            except Exception:
                self.db.execute("ROLLBACK")
                raise
        return True

    def release(self, row_id, note=""):
        """Drop a reservation for a transaction that was never broadcast."""
        now = time.time()
        with self._lock:
            self.db.execute(
                "UPDATE txs SET state='released', reserve_wei='0', note=?, "
                "updated_at=? WHERE id=? AND state='reserved'",
                (note, now, row_id))

    def drop_tx(self, row_id, note=""):
        """Mark a transaction dropped (superseded / not included)."""
        now = time.time()
        with self._lock:
            self.db.execute(
                "UPDATE txs SET state='dropped', reserve_wei='0', note=?, "
                "updated_at=? WHERE id=? AND state IN ('reserved','broadcast',"
                "'pending')", (note, now, row_id))

    def open_txs(self):
        with self._lock:
            ph = ",".join("?" * len(OPEN_STATES))
            return [dict(r) for r in self.db.execute(
                f"SELECT * FROM txs WHERE state IN ({ph}) ORDER BY id",
                OPEN_STATES)]

    def recent_txs(self, limit=50):
        with self._lock:
            return [dict(r) for r in self.db.execute(
                "SELECT id,tx_hash,wallet,nonce,value_wei,state,token_id,note,"
                "created_at,updated_at FROM txs ORDER BY id DESC LIMIT ?",
                (limit,))]

    def pending_by_wallet(self, wallet):
        with self._lock:
            ph = ",".join("?" * len(OPEN_STATES))
            return [dict(r) for r in self.db.execute(
                f"SELECT * FROM txs WHERE wallet=? AND state IN ({ph})",
                (wallet.lower(), *OPEN_STATES))]

    # ------------------------------------------------------------- wallets
    def add_wallet(self, address, label=""):
        with self._lock:
            self.db.execute(
                "INSERT INTO wallets(address,label,added_at) VALUES(?,?,?) "
                "ON CONFLICT(address) DO UPDATE SET label=excluded.label",
                (address.lower(), label, time.time()))

    def wallets(self):
        with self._lock:
            return [dict(r) for r in self.db.execute(
                "SELECT * FROM wallets ORDER BY added_at")]

    def set_active_signer(self, address):
        with self._lock:
            self._set("active_signer", address.lower())

    @property
    def active_signer(self):
        with self._lock:
            v = self._get("active_signer")
            return v.lower() if v else None

    def clear_active_signer(self):
        with self._lock:
            self._set("active_signer", "")

    # -------------------------------------------------------------- events
    def event(self, kind, message):
        with self._lock:
            self.db.execute("INSERT INTO events(ts,kind,message) VALUES(?,?,?)",
                            (time.time(), kind, message))

    def events(self, limit=100):
        with self._lock:
            return [dict(r) for r in self.db.execute(
                "SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,))]

    def close(self):
        with self._lock:
            self.db.close()
