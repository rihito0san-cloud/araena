#!/usr/bin/env python3
"""
dashboard.py -- read-only status view.

A separate process with a separate token. It deliberately does NOT import
signer.py, does NOT decrypt SSH credentials, and rejects every mutation
request. If the dashboard is compromised or its token leaks, the worst case is
that someone can read public state.

State comes from the controller over loopback; chain reads come from the RPC.
An RPC failure renders as "unknown/stale", never as a zero balance.
"""
import json
import os
import secrets
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from protocol import CONTRACT, CHAIN_ID, Contract, effective_bits, expected_hashes

HERE = os.path.dirname(os.path.abspath(__file__))
HOST = "127.0.0.1"
PORT = 8903
STATE_TTL = 15.0
MAX_BODY = 2048


class Reader:
    """Caches controller state and chain reads; never mutates anything."""

    def __init__(self, controller_url, controller_token):
        self.url = controller_url.rstrip("/")
        self.token = controller_token
        self.contract = Contract()
        self._lock = threading.Lock()
        self._state = None
        self._state_at = 0.0
        self._chain = None
        self._chain_at = 0.0
        self._thread = threading.Thread(target=self._poll, daemon=True)
        self._thread.start()

    def _get_controller(self):
        req = urllib.request.Request(
            self.url + "/api/state",
            headers={"X-Panel-Token": self.token})
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())

    def _poll(self):
        while True:
            try:
                st = self._get_controller()
                with self._lock:
                    self._state, self._state_at = st, time.time()
            except Exception as exc:
                with self._lock:
                    if self._state is not None:
                        self._state["_read_error"] = str(exc)
            time.sleep(2.0)

    # ---------------------------------------------------------- chain reads
    def chain(self):
        with self._lock:
            if self._chain and time.time() - self._chain_at < STATE_TTL:
                return dict(self._chain)
        out = {"error": None}
        try:
            st = self.contract.mining_status()
            out["status"] = {k: (v.hex() if isinstance(v, (bytes, bytearray)) else v)
                             for k, v in st.items()}
            out["block_number"] = st["blockNumber"]
        except Exception as exc:
            out["error"] = str(exc)
            with self._lock:
                if self._chain:
                    stale = dict(self._chain)
                    stale["error"] = str(exc)
                    stale["stale"] = True
                    return stale
        wallets = []
        state = self.state() or {}
        for w in state.get("wallets", []) or []:
            row = dict(w)
            try:
                row["balance_wei"] = self.contract.balance(w["address"])
                row["balance_state"] = "ok"
            except Exception:
                row["balance_wei"], row["balance_state"] = None, "unknown"
            try:
                row["nft_count"] = self.contract.balance_of(w["address"])
                row["nft_state"] = "ok"
            except Exception:
                row["nft_count"], row["nft_state"] = None, "unknown"
            wallets.append(row)
        out["wallets"] = wallets
        with self._lock:
            self._chain, self._chain_at = out, time.time()
        return dict(out)

    def state(self):
        with self._lock:
            if not self._state:
                return None
            out = dict(self._state)
            out["_state_age"] = time.time() - self._state_at
            return out

    def snapshot(self):
        st = self.state() or {}
        ch = self.chain()
        status = ch.get("status") or st.get("status") or {}
        target = status.get("target", 0)
        hps = (st.get("fleet") or {}).get("fresh_hashrate", 0)
        exp = expected_hashes(target) if target else None
        return {
            "chain_id": CHAIN_ID,
            "contract": CONTRACT,
            "controller": {
                "reachable": st.get("running") is not None,
                "state_age": st.get("_state_age"),
                "read_error": st.get("_read_error"),
                "running": st.get("running"),
                "paused": st.get("paused"),
                "autosubmit": st.get("autosubmit"),
            },
            "fleet": st.get("fleet") or {"workers": [], "fresh_hashrate": 0,
                                         "fresh_count": 0},
            "status": status,
            "target_bits": effective_bits(target) if target else None,
            "expected_hashes": exp,
            "eta_seconds": (exp / hps) if (exp and hps > 0) else None,
            "wallets": ch.get("wallets") or st.get("wallets") or [],
            "budget": st.get("budget") or {},
            "signer": st.get("signer"),
            "signer_state": st.get("signer_state"),
            "mints": st.get("mints", 0),
            "last_mint": st.get("last_mint"),
            "solutions": st.get("solutions", 0),
            "last_solution": st.get("last_solution"),
            "events": (st.get("events") or [])[:25],
            "chain_error": ch.get("error"),
            "stale": bool(ch.get("stale")),
        }


class Handler(BaseHTTPRequestHandler):
    reader: Reader = None
    token: str = None
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass

    def _send(self, code, payload, ctype="application/json"):
        body = payload.encode() if isinstance(payload, str) \
            else json.dumps(payload).encode()
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

    def do_GET(self):
        host = self.headers.get("Host", "")
        if host and not host.startswith("127.0.0.1:"):
            return self._send(403, {"error": "bad host"})
        if self.path == "/" or self.path.startswith("/index"):
            with open(os.path.join(HERE, "dashboard.html")) as f:
                return self._send(200, f.read(), "text/html; charset=utf-8")
        if self.headers.get("Authorization", "").replace("Bearer ", "") != self.token:
            return self._send(401, {"error": "unauthorized"})
        if self.path.startswith("/api/status"):
            try:
                return self._send(200, self.reader.snapshot())
            except Exception as exc:
                return self._send(500, {"error": str(exc)})
        return self._send(404, {"error": "not found"})

    # Read-only by construction: no mutation route exists to call.
    def do_POST(self):
        self._send(405, {"error": "this service is read-only"})

    def do_PUT(self):
        self._send(405, {"error": "this service is read-only"})

    def do_DELETE(self):
        self._send(405, {"error": "this service is read-only"})


def main():
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--controller", default=f"http://{HOST}:8901")
    ap.add_argument("--controller-token", default=os.environ.get("MP_PANEL_TOKEN", ""))
    ap.add_argument("--port", type=int, default=PORT)
    args = ap.parse_args()

    if not args.controller_token:
        raise SystemExit(
            "a controller token is required: --controller-token or MP_PANEL_TOKEN.\n"
            "The dashboard never shares the controller's privileges, but it does\n"
            "need to read its state.")

    Handler.reader = Reader(args.controller, args.controller_token)
    Handler.token = secrets.token_urlsafe(24)
    httpd = ThreadingHTTPServer((HOST, args.port), Handler)
    httpd.daemon_threads = True
    print("MinerPotatos dashboard (read-only)")
    print(f"  open http://{HOST}:{args.port}/#token={Handler.token}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
