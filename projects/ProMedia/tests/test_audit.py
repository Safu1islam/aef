"""T-013 — audit covers attempts, not just successes."""

from __future__ import annotations

import pytest

from promedia.core.registry import invoke
from promedia.errors import Forbidden
from tests.conftest import declaration_original


def test_authority_operations_audited(operator_ctx):
    """AC-1."""
    invoke(operator_ctx, "connect-account", {"platform": "x", "handle": "me", "secret": None})
    entries = invoke(operator_ctx, "audit", {"limit": 10})["entries"]
    connect = [e for e in entries if e["operation"] == "connect-account"]
    assert connect
    assert connect[0]["outcome"] == "allowed"
    assert connect[0]["principal"] == "operator"


def test_denials_are_audited(agent_ctx):
    """AC-2: the question after an incident is what was attempted."""
    with pytest.raises(Forbidden):
        invoke(agent_ctx, "connect-account", {"platform": "x", "handle": "me"})
    entries = invoke(agent_ctx, "audit", {"limit": 10})["entries"]
    denied = [e for e in entries if e["outcome"] == "denied"]
    assert denied
    assert denied[0]["operation"] == "connect-account"
    assert denied[0]["principal"] == "agent"


def test_mutating_agent_operations_audited(agent_ctx, media_file):
    invoke(
        agent_ctx, "ingest", {"source_path": str(media_file), "declaration": declaration_original()}
    )
    entries = invoke(agent_ctx, "audit", {"limit": 10})["entries"]
    assert any(e["operation"] == "ingest" and e["outcome"] == "allowed" for e in entries)


def test_read_operations_not_audited(agent_ctx):
    """Auditing reads would bury the signal that matters."""
    invoke(agent_ctx, "status", {})
    entries = invoke(agent_ctx, "audit", {"limit": 10})["entries"]
    assert not any(e["operation"] == "status" for e in entries)
