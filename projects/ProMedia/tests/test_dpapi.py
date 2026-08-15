"""T-022 — the DPAPI credential backend, retiring fabrication F-002.

DR-008's decision was SEPARATION: credentials live outside the repository and
outside the application database, so they are absent from every backup, export
and agent-readable path. That was implemented in T-006 and is unchanged here.
What was missing is the at-rest half — the file was plaintext JSON, registered
as F-002 rather than quietly accepted.

These tests cover the three ways an encryption-at-rest change goes wrong:

* it does not actually encrypt (the defect);
* it encrypts under the MACHINE rather than the USER, which reads as encrypted
  and protects nothing against another account on the same box;
* it silently degrades to plaintext when the OS call is unavailable, so the
  store believes it is encrypted while writing secrets in the clear.

The third is the one that would be invisible in production, so it is asserted
directly rather than inferred from the first.
"""

from __future__ import annotations

import base64
import json
import sys

import pytest

from promedia.core import dpapi
from promedia.core.credentials import (
    BACKEND_DPAPI,
    BACKEND_PLAINTEXT,
    ENV_BACKEND,
    STORE_VERSION,
    CredentialStore,
    default_backend,
)
from promedia.errors import ConfigurationError

WINDOWS_ONLY = pytest.mark.skipif(sys.platform != "win32", reason="DPAPI is a Windows facility")

CANARY = "canary-secret-value-9x7-do-not-leak"


@pytest.fixture
def store_path(tmp_path):
    return tmp_path / "creds.json"


def _on_disk(path) -> str:
    return path.read_text(encoding="utf-8")


# --- the binding itself -------------------------------------------------------


@WINDOWS_ONLY
def test_protect_roundtrips():
    assert dpapi.unprotect(dpapi.protect(CANARY)) == CANARY


@WINDOWS_ONLY
def test_ciphertext_does_not_contain_the_plaintext():
    assert CANARY.encode("utf-8") not in dpapi.protect(CANARY)


@WINDOWS_ONLY
def test_ciphertext_is_not_deterministic():
    """Two protects of one value differ. A constant blob would suggest ECB-ish
    behaviour or a no-op wrapper, and would leak equality between accounts."""
    assert dpapi.protect(CANARY) != dpapi.protect(CANARY)


@WINDOWS_ONLY
def test_a_blob_from_different_entropy_cannot_be_decrypted(monkeypatch):
    """The application scoping actually participates in the key.

    This is the closest reachable proxy for AC-1's cross-account case: change
    one input to the key derivation and decryption must FAIL rather than return
    something. If entropy were ignored, this would round-trip.
    """
    blob = dpapi.protect(CANARY)
    monkeypatch.setattr(dpapi, "ENTROPY", b"a-different-application")
    with pytest.raises(OSError):
        dpapi.unprotect(blob)


@WINDOWS_ONLY
def test_local_machine_scope_is_not_used():
    """AC-1's real subject, asserted on the source.

    The machine-wide flag (0x4) binds ciphertext to the MACHINE, so any account
    on it could decrypt — exactly the protection this task exists to provide,
    removed. It cannot be detected from a blob at runtime without a second
    Windows account, so the dwFlags argument is pinned instead.

    Read from the AST, not the source text: this module explains in prose why
    the flag is not used, and a text scan reports that explanation as the
    offence. (The same trap caught tests/test_hygiene.py's literal scan.)
    """
    import ast
    from pathlib import Path

    tree = ast.parse(Path(dpapi.__file__).read_text(encoding="utf-8"))
    crypt_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and len(node.args) == 7 and not node.keywords
    ]
    assert crypt_calls, "no 7-argument DPAPI call found; this test is no longer looking at it"
    for call in crypt_calls:
        flags = call.args[5]  # dwFlags
        assert isinstance(flags, ast.Constant) and flags.value == 0, (
            f"dwFlags is {ast.dump(flags)}, not the literal 0 — "
            "a non-zero value here may be CRYPTPROTECT_LOCAL_MACHINE"
        )


# --- the store, end to end ----------------------------------------------------


@WINDOWS_ONLY
def test_the_secret_is_not_on_disk_in_the_clear(store_path):
    """F-002's replacement condition, stated as an assertion."""
    CredentialStore(store_path).put("x:me", CANARY)
    assert CANARY not in _on_disk(store_path)


@WINDOWS_ONLY
def test_the_operator_token_is_encrypted_too(store_path):
    """It grants publish authority over every account — the highest-value
    secret in the store, and the easiest one to forget is in there."""
    token = CredentialStore(store_path).ensure_operator_token()
    assert len(token) > 20
    assert token not in _on_disk(store_path)
    assert CredentialStore(store_path).operator_token() == token


@WINDOWS_ONLY
def test_the_store_records_what_it_is(store_path):
    """A store must be readable as encrypted or not, from the file itself."""
    CredentialStore(store_path).put("x:me", CANARY)
    payload = json.loads(_on_disk(store_path))
    assert payload["backend"] == BACKEND_DPAPI
    assert payload["version"] == STORE_VERSION
    assert list(payload["entries"]) == ["x:me"]


@WINDOWS_ONLY
def test_every_store_operation_still_works_encrypted(store_path):
    """The interface DR-008 promised would not change."""
    store = CredentialStore(store_path)
    store.put("x:me", CANARY)
    store.put("linkedin:me", "second")
    assert store.get("x:me") == CANARY
    assert store.has("x:me") is True
    assert store.refs() == ["linkedin:me", "x:me"]
    assert store.delete("x:me") is True
    assert store.has("x:me") is False
    assert CredentialStore(store_path).get("linkedin:me") == "second"


@WINDOWS_ONLY
def test_a_rotated_credential_leaves_no_earlier_plaintext(store_path):
    """Rewriting must not append; the old value must be gone from the file."""
    store = CredentialStore(store_path)
    store.put("x:me", "first-value-aaa")
    store.put("x:me", "second-value-bbb")
    disk = _on_disk(store_path)
    assert "first-value-aaa" not in disk and "second-value-bbb" not in disk
    assert store.get("x:me") == "second-value-bbb"


# --- migration from the v1 plaintext file -------------------------------------


@WINDOWS_ONLY
def test_an_existing_plaintext_store_is_still_readable(store_path):
    """The operator's real store predates this change and must not be stranded."""
    store_path.write_text(json.dumps({"x:me": CANARY, "operator_token": "tok"}), encoding="utf-8")
    store = CredentialStore(store_path)
    assert store.get("x:me") == CANARY
    assert store.operator_token() == "tok"


@WINDOWS_ONLY
def test_using_a_plaintext_store_upgrades_it(store_path):
    """Upgrade by use, not by a migration command nobody runs."""
    store_path.write_text(json.dumps({"x:me": CANARY}), encoding="utf-8")
    store = CredentialStore(store_path)
    store.put("linkedin:me", "another")

    disk = _on_disk(store_path)
    assert CANARY not in disk, "the pre-existing secret stayed in the clear after a write"
    assert json.loads(disk)["backend"] == BACKEND_DPAPI
    assert store.get("x:me") == CANARY  # and it survived the upgrade


# --- the silent-downgrade direction -------------------------------------------


def test_an_unknown_forced_backend_is_refused(monkeypatch, store_path):
    monkeypatch.setenv(ENV_BACKEND, "rot13")
    with pytest.raises(ConfigurationError):
        default_backend()


@WINDOWS_ONLY
def test_the_default_backend_is_dpapi_on_windows(monkeypatch):
    monkeypatch.delenv(ENV_BACKEND, raising=False)
    assert default_backend() == BACKEND_DPAPI


def test_dpapi_cannot_be_forced_where_it_does_not_exist(monkeypatch):
    """The silent-downgrade guard.

    A backend that fell back to plaintext when the OS call was missing would
    leave the store reporting itself encrypted while writing clear text. It
    refuses instead.
    """
    monkeypatch.setenv(ENV_BACKEND, BACKEND_DPAPI)
    monkeypatch.setattr(dpapi, "available", lambda: False)
    with pytest.raises(ConfigurationError):
        default_backend()


def test_protect_raises_rather_than_returning_plaintext(monkeypatch):
    monkeypatch.setattr(dpapi, "available", lambda: False)
    with pytest.raises(dpapi.DpapiUnavailable):
        dpapi.protect(CANARY)


@WINDOWS_ONLY
def test_a_foreign_blob_is_reported_not_treated_as_missing(store_path):
    """AC-1's failure path: a store copied from another Windows account.

    Simulated by corrupting the blob, which is the same code path a foreign
    ciphertext takes — DPAPI refuses both. The distinction that matters is that
    it must NOT come back as 'no credential stored', which would send the
    operator to re-enter a credential that is present and intact.
    """
    CredentialStore(store_path).put("x:me", CANARY)
    payload = json.loads(_on_disk(store_path))
    blob = bytearray(base64.b64decode(payload["entries"]["x:me"]))
    blob[-1] ^= 0xFF
    payload["entries"]["x:me"] = base64.b64encode(bytes(blob)).decode("ascii")
    store_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ConfigurationError) as excinfo:
        CredentialStore(store_path).get("x:me")
    assert "x:me" in str(excinfo.value)
    assert "different account" in str(excinfo.value)


# --- the plaintext backend is still reachable and still honest ----------------


def test_the_plaintext_backend_says_so_on_disk(store_path):
    """Kept as the documented escape hatch, and it must not pretend."""
    store = CredentialStore(store_path, backend=BACKEND_PLAINTEXT)
    store.put("x:me", CANARY)
    payload = json.loads(_on_disk(store_path))
    assert payload["backend"] == BACKEND_PLAINTEXT
    assert CANARY in _on_disk(store_path), "a plaintext store must not look encrypted"
    assert store.get("x:me") == CANARY
