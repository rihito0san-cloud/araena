#!/usr/bin/env python3
"""
panel.py -- authenticated local control API and management UI.

Binds 127.0.0.1 only. A random token is generated per start and printed in a
URL fragment (never in a query string, so it stays out of access logs and
referrers). Requests must carry it in X-Panel-Token.

This process is the only one that may import signer.py. The dashboard is a
separate process with a separate read-only token.
"""
import json
import os
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from budget import Ledger
from engine import Engine
from fleet import Fleet
from protocol import CONTRACT, CHAIN_ID, Contract
from signer import KeyStore
from submit import Submitter

HERE = os.path.dirname(os.path.abspath(__file__))
HOST = "127.0.0.1"
PORT = 8901
MAX_BODY = 8192
ACTIVE_SIGNER_FILE = os.path.join(HERE, "active-signer.json")


class Panel:
    def __init__(self, binary, db_path=None, worker_args=None,
                 devices=None, rpcs=None):
        self.token = secrets.token_urlsafe(24)
        self.ledger = Ledger(db_path or os.path.join(HERE, "launch.sqlite"))
        self.keys = KeyStore()
        self.contract = Contract()
        self.fleet = Fleet(binary, extra_args=worker_args)
        self.submitter = Submitter(self.contract, self.ledger, self.keys)
        self.engine = Engine(self.contract, self.ledger, self.keys,
                             self.fleet, submitter=self.submitter)
        self.devices = list(devices or [])
        self._lock = threading.RLock()
        self._load_active_signer()

    # ------------------------------------------------------ signer selection
    def _load_active_signer(self):
        """Restore the selected PUBLIC address. Never a key."""
        try:
            with open(ACTIVE_SIGNER_FILE) as f:
                data = json.load(f)
            addr = (data.get("address") or "").lower()
            if addr.startswith("0x") and len(addr) == 42:
                self.keys.select(addr)
                self.ledger.set_active_signer(addr)
        except FileNotFoundError:
            pass
        except Exception as exc:
            self.ledger.event("signer", f"could not read selection: {exc}")

    def _save_active_signer(self, address):
        tmp = ACTIVE_SIGNER_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump({"address": address.lower(),
                       "updated_at": time.time()}, f, indent=1)
        os.replace(tmp, ACTIVE_SIGNER_FILE)  # atomic

    def set_active_signer(self, address):
        """Serialized against submission by the ledger/engine locks."""
        addr = address.lower()
        with self._lock:
            self.keys.select(addr)
            self.ledger.set_active_signer(addr)
            self.ledger.add_wallet(addr, "selected signer")
            self._save_active_signer(addr)
            self.ledger.event("signer", f"active signer set to {addr}")
        return {"address": addr, "key_loaded": self.keys.is_loaded(addr)}

    def clear_active_signer(self):
        with self._lock:
            self.keys.clear_selection()
            self.ledger.clear_active_signer()
            try:
                os.remove(ACTIVE_SIGNER_FILE)
            except FileNotFoundError:
                pass
            self.ledger.event("signer", "active signer cleared")
        return {"address": None}

    # ------------------------------------------------------------- devices
    def enroll_device(self, device_index, label=""):
        wid = f"local#{device_index}"
        with self._lock:
            if self.fleet.get(wid):
                return {"worker_id": wid, "note": "already enrolled"}
            w = self.fleet.add(wid, device_index, label=label or f"GPU {device_index}")
            w.start()
        return {"worker_id": wid}

    def remove_device(self, worker_id):
        self.fleet.remove(worker_id)
        return {"removed": worker_id}

    # --------------------------------------------------------------- state
    def state(self):
        snap = self.engine.snapshot()
        snap["wallets"] = self._wallet_rows()
        snap["txs"] = self.ledger.recent_txs(30)
        snap["events"] = self.ledger.events(40)
        snap["loaded_keys"] = self.keys.loaded_addresses()
        snap["contract"] = CONTRACT
        snap["chain_id"] = CHAIN_ID
        return snap

    def _wallet_rows(self):
        rows = []
        selected = self.keys.selected
        for w in self.ledger.wallets():
            addr = w["address"]
            try:
                bal = self.contract.balance(addr)
                bal_state = "ok"
            except Exception:
                # An RPC failure must never render as a zero balance.
                bal, bal_state = None, "unknown"
            try:
                nfts = self.contract.balance_of(addr)
                nft_state = "ok"
            except Exception:
                nfts, nft_state = None, "unknown"
            rows.append({
                "address": addr,
                "label": w["label"],
                "balance_wei": bal,
                "balance_state": bal_state,
                "nft_count": nfts,
                "nft_state": nft_state,
                "key_loaded": self.keys.is_loaded(addr),
                "is_selected": addr == selected,
            })
        return rows


class Handler(BaseHTTPRequestHandler):
    panel: Panel = None
    protocol_version = "HTTP/1.1"

    # ------------------------------------------------------------- plumbing
    def log_message(self, fmt, *args):
        pass  # never log tokens or key material

    def _origin_ok(self):
        host = self.headers.get("Host", "")
        origin = self.headers.get("Origin")
        allowed_host = f"{HOST}:{PORT}"
        if host and host != allowed_host and not host.startswith("127.0.0.1:"):
            return False
        if origin is not None and origin not in (
                f"http://{allowed_host}", "http://127.0.0.1", "null"):
            return False
        return True

    def _authorized(self):
        return self.headers.get("X-Panel-Token", "") == self.panel.token

    def _send(self, code, payload, ctype="application/json"):
        body = json.dumps(payload).encode() if ctype == "application/json" \
            else payload.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'none'; script-src 'unsafe-inline'; "
            "style-src 'unsafe-inline'; connect-src 'self'; img-src data:")
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        try:
            n = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            return None
        if n <= 0:
            return {}
        if n > MAX_BODY:
            return None
        raw = self.rfile.read(n)
        try:
            return json.loads(raw)
        except Exception:
            return None

    # -------------------------------------------------------------- routes
    def do_GET(self):
        if not self._origin_ok():
            return self._send(403, {"error": "bad origin"})
        if self.path == "/" or self.path.startswith("/index"):
            with open(os.path.join(HERE, "panel.html")) as f:
                return self._send(200, f.read(), "text/html; charset=utf-8")
        if not self._authorized():
            return self._send(401, {"error": "unauthorized"})
        if self.path.startswith("/api/state"):
            try:
                return self._send(200, self.panel.state())
            except Exception as exc:
                return self._send(500, {"error": str(exc)})
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        if not self._origin_ok():
            return self._send(403, {"error": "bad origin"})
        if not self._authorized():
            return self._send(401, {"error": "unauthorized"})
        body = self._body()
        if body is None:
            return self._send(400, {"error": "bad or oversized body"})
        try:
            return self._dispatch(body)
        except Exception as exc:
            return self._send(400, {"error": str(exc)})

    def _dispatch(self, body):
        p = self.path
        panel = self.panel

        if p == "/api/import-key":
            key = body.get("private_key", "")
            if not key:
                return self._send(400, {"error": "missing private_key"})
            addr = panel.keys.import_key(key)
            # The caller's copy is transient; we keep only the derived address.
            panel.ledger.add_wallet(addr.lower(), "imported")
            panel.ledger.event("key", f"key imported for {addr}")
            return self._send(200, {"address": addr,
                                    "selected": panel.keys.selected == addr.lower()})

        if p == "/api/active-signer":
            addr = body.get("address", "")
            if not addr:
                panel.clear_active_signer()
                return self._send(200, {"address": None})
            return self._send(200, panel.set_active_signer(addr))

        if p == "/api/cap":
            cap = body.get("cap_wei")
            if cap is None:
                eth = body.get("cap_eth")
                if eth is None:
                    return self._send(400, {"error": "cap required"})
                cap = int(float(eth) * 10 ** 18)
            panel.ledger.set_cap(int(cap))
            return self._send(200, panel.ledger.snapshot())

        if p == "/api/enroll":
            idx = body.get("device")
            if idx is None:
                return self._send(400, {"error": "device index required"})
            return self._send(200, panel.enroll_device(int(idx),
                                                      body.get("label", "")))

        if p == "/api/remove":
            wid = body.get("worker_id", "")
            if not wid:
                return self._send(400, {"error": "worker_id required"})
            return self._send(200, panel.remove_device(wid))

        if p == "/api/mining":
            on = bool(body.get("start"))
            if on:
                panel.engine.start()
                panel.engine.pause(False)
            else:
                panel.engine.pause(True)
            return self._send(200, {"running": panel.engine.running,
                                    "paused": panel.engine.paused})

        if p == "/api/autosubmit":
            panel.engine.set_autosubmit(bool(body.get("on")))
            return self._send(200, {"autosubmit": panel.engine.autosubmit})

        if p == "/api/submit":
            # Manual one-shot: take the freshest verified proof and submit it.
            proof = None
            for w in panel.fleet.all():
                proof = w.take_verified()
                if proof:
                    break
            if not proof:
                return self._send(409, {"error": "no verified proof available"})
            res = panel.engine._submit(proof)
            if not res:
                return self._send(409, {"error": "submission refused; see events"})
            return self._send(200, res)

        if p == "/api/reconcile":
            return self._send(200, {"results": panel.submitter.reconcile_open()})

        if p == "/api/forget-keys":
            panel.keys.forget_all()
            panel.ledger.event("key", "all in-memory keys cleared")
            return self._send(200, {"cleared": True})

        return self._send(404, {"error": "not found"})


def serve(panel, host=HOST, port=PORT):
    Handler.panel = panel
    httpd = ThreadingHTTPServer((host, port), Handler)
    httpd.daemon_threads = True
    return httpd


def main():
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--binary", required=True,
                    help="path to the compiled worker (miner or miner-host)")
    ap.add_argument("--device", type=int, action="append", default=[],
                    help="GPU index to enroll at startup (repeatable)")
    ap.add_argument("--db", default=None)
    ap.add_argument("--port", type=int, default=PORT)
    args = ap.parse_args()

    panel = Panel(args.binary, db_path=args.db)
    for d in args.device:
        panel.enroll_device(d)
    httpd = serve(panel, port=args.port)
    url = f"http://{HOST}:{args.port}/#token={panel.token}"
    print("MinerPotatos controller")
    print(f"  chain    {CHAIN_ID}")
    print(f"  contract {CONTRACT}")
    print(f"  open     {url}")
    print("  the token is only in the URL fragment; keep this terminal private")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        panel.engine.stop()
        panel.fleet.stop_all()


if __name__ == "__main__":
    main()
