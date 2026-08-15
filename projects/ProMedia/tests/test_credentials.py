"""T-006 — credential separation (DR-008)."""

from __future__ import annotations

import json
from pathlib import Path

from promedia.core.credentials import REDACTED, CredentialStore
from promedia.core.principal import agent, resolve
from promedia.core.registry import Context, invoke

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_store_is_outside_repo(monkeypatch):
    """AC-2: credentials must never sit in a tree agents are told to read."""
    monkeypatch.delenv("PROMEDIA_CREDENTIAL_STORE", raising=False)
    from promedia.core import credentials

    path = credentials.default_store_path().resolve()
    assert REPO_ROOT not in path.parents and path != REPO_ROOT


def test_no_secret_in_any_output(config, conn, store, monkeypatch):
    """AC-1: not in the response, not in the database, not in the audit log."""
    monkeypatch.setenv("PROMEDIA_CREDENTIAL_STORE", str(store.path))
    from promedia.core import principal as principal_module

    ctx = Context(config=config, conn=conn, principal=principal_module.operator("op"))
    secret = "super-secret-token-value-9x7"
    result = invoke(ctx, "connect-account", {"platform": "x", "handle": "me", "secret": secret})

    assert secret not in json.dumps(result)
    assert result["credential_value"] == REDACTED

    listing = invoke(ctx, "list-accounts", {})
    assert secret not in json.dumps(listing)

    audit = invoke(ctx, "audit", {"limit": 50})
    assert secret not in json.dumps(audit)

    dump = "".join(str(row) for row in conn.iterdump())
    assert secret not in dump, "a secret reached the database, which is the backup artefact"


def test_store_roundtrip(store):
    store.put("x:me", "value")
    assert store.get("x:me") == "value"
    assert store.has("x:me")
    assert store.refs() == ["x:me"]
    assert store.delete("x:me") is True
    assert store.has("x:me") is False


def test_operator_token_generated_once(store):
    first = store.ensure_operator_token()
    assert store.ensure_operator_token() == first
    assert len(first) > 20


def test_resolve_requires_matching_token():
    assert resolve("abc", "abc").is_operator is True
    assert resolve("abc", "different").is_operator is False
    assert resolve(None, "abc").is_operator is False
    # No token configured means operator authority is unavailable — the safe
    # direction. It must not fail open.
    assert resolve("anything", None).is_operator is False


def test_refs_never_expose_values(store):
    store.put("x:me", "secret-value")
    assert "secret-value" not in json.dumps(store.refs())
