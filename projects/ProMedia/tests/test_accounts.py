"""T-006 AC-3 — account connection records a reference, never a secret."""

from __future__ import annotations

import json

import pytest

from promedia.core.credentials import REDACTED, CredentialStore
from promedia.core.registry import Context, invoke
from promedia.core.principal import operator
from promedia.errors import Forbidden, ValidationError


@pytest.fixture
def op_ctx(config, conn, store, monkeypatch):
    monkeypatch.setenv("PROMEDIA_CREDENTIAL_STORE", str(store.path))
    return Context(config=config, conn=conn, principal=operator("op"))


def test_connect_account_records_reference_only(op_ctx, store):
    """AC-3: platform, handle and a credential REFERENCE — never the secret."""
    result = invoke(
        op_ctx, "connect-account", {"platform": "x", "handle": "myhandle", "secret": "s3cr3t-value"}
    )
    assert result["platform"] == "x"
    assert result["handle"] == "myhandle"
    assert result["credential_ref"] == "x:myhandle"
    assert result["credential_value"] == REDACTED
    assert result["status"] == "connected"
    assert "s3cr3t-value" not in json.dumps(result)

    row = op_ctx.conn.execute("SELECT * FROM accounts WHERE id = ?", (result["account_id"],)).fetchone()
    assert row["credential_ref"] == "x:myhandle"
    assert "s3cr3t-value" not in json.dumps(dict(row))

    # The secret is in the store, outside the database.
    assert CredentialStore(store.path).get("x:myhandle") == "s3cr3t-value"


def test_connect_without_secret_is_recorded_but_cannot_publish(op_ctx):
    result = invoke(op_ctx, "connect-account", {"platform": "linkedin", "handle": "me"})
    assert result["status"] == "error"
    assert "cannot publish" in result["note"]


def test_unsupported_platform_rejected(op_ctx):
    with pytest.raises(ValidationError) as excinfo:
        invoke(op_ctx, "connect-account", {"platform": "myspace", "handle": "me"})
    assert "x" in excinfo.value.detail["supported"]


def test_agent_cannot_connect_account(agent_ctx):
    """F-2: connecting an account establishes publishing capability."""
    with pytest.raises(Forbidden):
        invoke(agent_ctx, "connect-account", {"platform": "x", "handle": "me", "secret": "s"})
