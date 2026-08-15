"""Credential store (DR-008).

The decision that matters is separation, not the cipher: credentials live
outside the repository and outside the application database, so they are absent
from every backup, export, provenance artefact and agent-readable path.

Since T-022 the at-rest form is Windows DPAPI (per-user OS encryption), which
was the backend DR-008 named and fabrication F-002 tracked. It is a swap behind
this interface, exactly as that record predicted — the store's callers did not
change.
"""

from __future__ import annotations

import base64
import json
import os
import secrets
import stat
from pathlib import Path
from typing import Any

from ..errors import ConfigurationError, NotFound
from . import dpapi

ENV_STORE_PATH = "PROMEDIA_CREDENTIAL_STORE"
# Forces a backend rather than choosing by platform. Exists so a test can pin
# the plaintext path on Windows and so an operator on a machine where DPAPI is
# broken has a documented way out — not as a convenience default.
ENV_BACKEND = "PROMEDIA_CREDENTIAL_BACKEND"
OPERATOR_TOKEN_KEY = "operator_token"
REDACTED = "<redacted>"

# On-disk shape. Version 1 was a flat {ref: plaintext} dict with no envelope;
# it is still READ, so an existing store keeps working and is re-encrypted on
# the next write (see _read/_write).
STORE_VERSION = 2
BACKEND_DPAPI = "dpapi"
BACKEND_PLAINTEXT = "plaintext"


def default_backend() -> str:
    """DPAPI where the OS provides it, plaintext where it does not.

    Chosen by platform rather than by configuration because the wrong answer is
    not a preference, it is a silent downgrade: a store that believed it was
    encrypted while writing clear text would be worse than one that never
    claimed to be. The env override is explicit and is recorded in the file
    itself, so what a store IS can always be read off it.
    """
    forced = os.environ.get(ENV_BACKEND)
    if forced:
        if forced not in (BACKEND_DPAPI, BACKEND_PLAINTEXT):
            raise ConfigurationError(
                f"unknown credential backend '{forced}'",
                variable=ENV_BACKEND,
                supported=[BACKEND_DPAPI, BACKEND_PLAINTEXT],
            )
        if forced == BACKEND_DPAPI and not dpapi.available():
            raise ConfigurationError(
                "DPAPI was requested but this platform does not provide it",
                variable=ENV_BACKEND,
            )
        return forced
    return BACKEND_DPAPI if dpapi.available() else BACKEND_PLAINTEXT


def default_store_path() -> Path:
    """Outside the repository, always.

    Placed under the user profile rather than the project so that cloning,
    zipping or backing up the repo cannot carry credentials with it.
    """
    override = os.environ.get(ENV_STORE_PATH)
    if override:
        return Path(override)
    base = os.environ.get("APPDATA") or os.path.expanduser("~/.config")
    return Path(base) / "ProMedia" / "credentials.json"


class CredentialStore:
    """Key/value secret store. Values are never returned to any surface."""

    def __init__(self, path: Path | None = None, *, backend: str | None = None) -> None:
        self.path = path or default_store_path()
        self.backend = backend or default_backend()

    # --- internals ---
    def _load(self) -> dict[str, Any]:
        if not self.path.is_file():
            return {"version": STORE_VERSION, "backend": self.backend, "entries": {}}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except ValueError as exc:
            raise ConfigurationError(
                f"credential store at {self.path} is not valid JSON", path=str(self.path)
            ) from exc
        if not isinstance(raw, dict) or "entries" not in raw:
            # Version 1: a flat {ref: plaintext} mapping. Read as plaintext
            # whatever this store's backend is — the bytes on disk decide how
            # they must be decoded, not our preference. Re-encrypted on the next
            # write, so an existing store upgrades by being used.
            return {"version": 1, "backend": BACKEND_PLAINTEXT, "entries": dict(raw)}
        return raw

    def _read(self) -> dict[str, Any]:
        """Every stored secret, in the clear, for callers inside this process."""
        loaded = self._load()
        stored_backend = loaded.get("backend", BACKEND_PLAINTEXT)
        entries = loaded.get("entries", {})
        if stored_backend != BACKEND_DPAPI:
            return {ref: str(value) for ref, value in entries.items()}
        out: dict[str, Any] = {}
        for ref, value in entries.items():
            try:
                out[ref] = dpapi.unprotect(base64.b64decode(value))
            except (OSError, ValueError) as exc:
                # A blob this user cannot decrypt is the control WORKING — the
                # store was copied from another account. Reported as a
                # configuration failure naming the reference, never swallowed
                # into a missing-credential answer, which would send the
                # operator to re-enter a credential that is already there.
                raise ConfigurationError(
                    f"credential '{ref}' cannot be decrypted by this Windows user;"
                    " the store was created under a different account",
                    path=str(self.path),
                    ref=ref,
                ) from exc
        return out

    def _write(self, data: dict[str, Any]) -> None:
        if self.backend == BACKEND_DPAPI:
            entries = {
                ref: base64.b64encode(dpapi.protect(str(value))).decode("ascii")
                for ref, value in data.items()
            }
        else:
            entries = {ref: str(value) for ref, value in data.items()}
        payload = {"version": STORE_VERSION, "backend": self.backend, "entries": entries}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        # Defence in depth, not the control. DPAPI is what protects the bytes;
        # the ACL is what keeps another local account from reading the file at
        # all, and the user profile is the real boundary.
        try:
            self.path.chmod(stat.S_IRUSR | stat.S_IWUSR)
        except OSError:  # pragma: no cover
            pass

    # --- public ---
    def put(self, ref: str, value: str) -> str:
        data = self._read()
        data[ref] = value
        self._write(data)
        return ref

    def get(self, ref: str) -> str:
        data = self._read()
        if ref not in data:
            raise NotFound(f"no credential stored under '{ref}'", ref=ref)
        return str(data[ref])

    def has(self, ref: str) -> bool:
        return ref in self._read()

    def delete(self, ref: str) -> bool:
        data = self._read()
        if ref not in data:
            return False
        del data[ref]
        self._write(data)
        return True

    def refs(self) -> list[str]:
        """Reference names only. Never values — this is what surfaces may show."""
        return sorted(self._read())

    # --- operator token (F-2) ---
    def operator_token(self) -> str | None:
        data = self._read()
        value = data.get(OPERATOR_TOKEN_KEY)
        return str(value) if value else None

    def ensure_operator_token(self) -> str:
        existing = self.operator_token()
        if existing:
            return existing
        token = secrets.token_urlsafe(32)
        self.put(OPERATOR_TOKEN_KEY, token)
        return token
