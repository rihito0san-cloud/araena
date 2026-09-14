#!/usr/bin/env python3
"""
signer.py -- memory-only transaction signer.

Private keys enter through the authenticated panel, live only in this process,
and are never written to disk, logged, or sent to a worker. Importing this
module is the trust boundary: the read-only dashboard must not import it.

Nothing here decides whether to spend. That is the engine's job, against the
ledger's cap and the selected-signer rules.
"""
import threading

from eth_account import Account
from eth_account.messages import encode_defunct

from protocol import CHAIN_ID


class SignerError(RuntimeError):
    pass


class KeyStore:
    """Holds decrypted private keys in process memory, keyed by address."""

    def __init__(self):
        self._keys = {}          # address (lowercase) -> private key bytes
        self._lock = threading.RLock()
        self._selected = None

    # ------------------------------------------------------------- loading
    def import_key(self, private_key) -> str:
        """Import a key from an exact-length hex string or raw bytes.

        Returns the public address. The caller is responsible for clearing its
        own copy of the input (the panel wipes the form field and never keeps
        the value in browser storage).
        """
        if isinstance(private_key, str):
            pk = private_key.strip()
            if pk.startswith("0x"):
                pk = pk[2:]
            if len(pk) != 64:
                raise SignerError(
                    "private key must be 32 bytes (64 hex characters); "
                    "a seed phrase or keystore file is not accepted here")
            try:
                key_bytes = bytes.fromhex(pk)
            except ValueError as exc:
                raise SignerError(f"private key is not valid hex: {exc}")
        elif isinstance(private_key, (bytes, bytearray)):
            if len(private_key) != 32:
                raise SignerError("private key must be exactly 32 bytes")
            key_bytes = bytes(private_key)
        else:
            raise SignerError("unsupported private key type")

        acct = Account.from_key(key_bytes)
        addr = acct.address.lower()
        with self._lock:
            self._keys[addr] = key_bytes
        return acct.address

    def forget(self, address):
        """Drop the key but KEEP the selection.

        Clearing the selection here would collapse two different states into
        one: "no signer chosen" and "signer chosen, key not entered yet". The
        second must stay visible so the panel can report *Waiting for selected
        signer* instead of silently looking idle.
        """
        with self._lock:
            self._keys.pop(address.lower(), None)

    def forget_all(self):
        """Clear every key, again preserving the selection for the same reason."""
        with self._lock:
            self._keys.clear()

    # ----------------------------------------------------------- selection
    def select(self, address):
        """Select the signer that new jobs and submissions may use.

        Selecting an address whose key is not loaded is allowed (the engine
        then reports "waiting for selected signer"), but it never causes a
        fallback to a different wallet.
        """
        addr = address.lower()
        with self._lock:
            self._selected = addr
        return addr

    def clear_selection(self):
        with self._lock:
            self._selected = None

    @property
    def selected(self):
        with self._lock:
            return self._selected

    def is_loaded(self, address):
        with self._lock:
            return address.lower() in self._keys

    def loaded_addresses(self):
        with self._lock:
            return sorted(self._keys.keys())

    def selected_is_loaded(self):
        with self._lock:
            return self._selected is not None and self._selected in self._keys

    # ------------------------------------------------------------- signing
    def sign_transaction(self, tx: dict) -> bytes:
        """Sign an EIP-1559 or legacy transaction dict; returns raw bytes.

        Refuses to sign for any address other than the selected one. This is
        what makes "deselect then flush the queue" actually hold: a proof mined
        for address A cannot be broadcast as address B, and a queued proof for
        a deselected address cannot be signed at all.
        """
        sender = (tx.get("from") or "").lower()
        with self._lock:
            if self._selected is None:
                raise SignerError("no signer selected")
            if sender != self._selected:
                raise SignerError(
                    f"refusing to sign for {sender}: selected signer is "
                    f"{self._selected}")
            key = self._keys.get(sender)
            if key is None:
                raise SignerError(
                    f"selected signer {sender} is not loaded; "
                    "waiting for the operator to enter the key")
        tx = dict(tx)
        tx.pop("from", None)
        tx.setdefault("chainId", CHAIN_ID)
        signed = Account.from_key(key).sign_transaction(tx)
        # eth_account exposes the blob differently across versions.
        raw = getattr(signed, "rawTransaction", None)
        if raw is None:
            raw = getattr(signed, "raw_transaction", None)
        if raw is None:
            raise SignerError("signer returned no raw transaction")
        return bytes(raw)

    def sign_message(self, message: str) -> bytes:
        """Sign arbitrary text (login-style proofs). Never used for spending."""
        with self._lock:
            if self._selected is None:
                raise SignerError("no signer selected")
            key = self._keys.get(self._selected)
            if key is None:
                raise SignerError("selected signer is not loaded")
        return Account.from_key(key).sign_message(
            encode_defunct(text=message)).signature
