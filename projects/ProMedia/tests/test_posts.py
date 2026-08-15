"""T-011 — the authority gate end to end (F-2)."""

from __future__ import annotations

import pytest

from promedia.core.principal import agent, operator
from promedia.core.registry import Context, invoke
from promedia.errors import ApprovalRequired, Forbidden, RightsBlocked
from tests.conftest import declaration_original, declaration_uncleared, make_config


@pytest.fixture
def sim_config(tmp_path):
    """Simulation on, so the slice can be exercised without credentials."""
    return make_config(tmp_path, **{"publishing.allow_simulation": True})


@pytest.fixture
def sim_conn(sim_config):
    from promedia.core import db

    connection = db.connect(sim_config.db_path)
    db.apply_schema(connection)
    yield connection
    connection.close()


@pytest.fixture
def op_ctx(sim_config, sim_conn):
    return Context(config=sim_config, conn=sim_conn, principal=operator("op"))


@pytest.fixture
def ag_ctx(sim_config, sim_conn):
    return Context(config=sim_config, conn=sim_conn, principal=agent("ag"))


def _ready_post(op_ctx, ag_ctx, media_file, declaration=None):
    account = invoke(op_ctx, "connect-account", {"platform": "x", "handle": "me", "secret": "tok"})
    asset = invoke(
        ag_ctx,
        "ingest",
        {"source_path": str(media_file), "declaration": declaration or declaration_original()},
    )
    # The operator attests the declaration the agent proposed; only then can a
    # permitting rule fire.
    invoke(op_ctx, "attest-declaration", {"asset_id": asset["asset_id"]})
    invoke(ag_ctx, "determine-rights", {"asset_id": asset["asset_id"]})
    invoke(ag_ctx, "seal-provenance", {"asset_id": asset["asset_id"]})
    post = invoke(
        ag_ctx,
        "queue-post",
        {"account_id": account["account_id"], "asset_id": asset["asset_id"], "body": "hello"},
    )
    return post["post_id"]


def test_agent_can_queue_cannot_approve(op_ctx, ag_ctx, media_file):
    """AC-1: and the denial is audited."""
    post_id = _ready_post(op_ctx, ag_ctx, media_file)
    with pytest.raises(Forbidden):
        invoke(ag_ctx, "approve-post", {"post_id": post_id})

    audit = invoke(ag_ctx, "audit", {"limit": 20})["entries"]
    denials = [e for e in audit if e["operation"] == "approve-post" and e["outcome"] == "denied"]
    assert denials, "a denied authority attempt must be audited"


def test_agent_cannot_publish(op_ctx, ag_ctx, media_file):
    post_id = _ready_post(op_ctx, ag_ctx, media_file)
    invoke(op_ctx, "approve-post", {"post_id": post_id})
    with pytest.raises(Forbidden):
        invoke(ag_ctx, "publish-post", {"post_id": post_id})
    assert invoke(ag_ctx, "publications", {})["count"] == 0


def test_publish_requires_approval(op_ctx, ag_ctx, media_file):
    """AC-2: even the operator cannot publish an unapproved post."""
    post_id = _ready_post(op_ctx, ag_ctx, media_file)
    with pytest.raises(ApprovalRequired):
        invoke(op_ctx, "publish-post", {"post_id": post_id})


def test_publish_requires_permitted_verdict(op_ctx, ag_ctx, media_file):
    """AC-3: server-side refusal, not a hidden button."""
    post_id = _ready_post(op_ctx, ag_ctx, media_file, declaration=declaration_uncleared())
    with pytest.raises(RightsBlocked) as excinfo:
        invoke(op_ctx, "approve-post", {"post_id": post_id})
    assert excinfo.value.detail["verdict"] == "BLOCKED"


def test_publication_recorded(op_ctx, ag_ctx, media_file):
    """AC-4."""
    post_id = _ready_post(op_ctx, ag_ctx, media_file)
    invoke(op_ctx, "approve-post", {"post_id": post_id})
    result = invoke(op_ctx, "publish-post", {"post_id": post_id})

    assert result["platform"] == "x"
    assert result["platform_post_id"]
    assert result["simulated"] is True
    assert result["provenance_id"]

    publications = invoke(op_ctx, "publications", {})["publications"]
    assert len(publications) == 1
    assert publications[0]["simulated"] is True
    assert publications[0]["content_hash"]


def test_publish_is_idempotent(op_ctx, ag_ctx, media_file):
    """AC-5: a double post is not recoverable once seen."""
    post_id = _ready_post(op_ctx, ag_ctx, media_file)
    invoke(op_ctx, "approve-post", {"post_id": post_id})
    first = invoke(op_ctx, "publish-post", {"post_id": post_id})
    second = invoke(op_ctx, "publish-post", {"post_id": post_id})

    assert second["already_published"] is True
    assert second["publication_id"] == first["publication_id"]
    assert invoke(op_ctx, "publications", {})["count"] == 1


def test_publish_requires_sealed_provenance(op_ctx, ag_ctx, media_file):
    account = invoke(op_ctx, "connect-account", {"platform": "x", "handle": "me2", "secret": "t"})
    asset = invoke(
        ag_ctx, "ingest", {"source_path": str(media_file), "declaration": declaration_original()}
    )
    invoke(op_ctx, "attest-declaration", {"asset_id": asset["asset_id"]})
    invoke(ag_ctx, "determine-rights", {"asset_id": asset["asset_id"]})
    post = invoke(
        ag_ctx,
        "queue-post",
        {"account_id": account["account_id"], "asset_id": asset["asset_id"], "body": "x"},
    )
    invoke(op_ctx, "approve-post", {"post_id": post["post_id"]})
    with pytest.raises(RightsBlocked, match="provenance"):
        invoke(op_ctx, "publish-post", {"post_id": post["post_id"]})


def test_decision_context_is_complete(op_ctx, ag_ctx, media_file):
    """The operator must see the whole basis before authorising."""
    post_id = _ready_post(op_ctx, ag_ctx, media_file)
    d = invoke(ag_ctx, "post", {"post_id": post_id})
    assert d["account"]["platform"] == "x"
    assert d["rights"]["verdict"] == "PERMITTED"
    assert d["rights"]["ruleset_version"] == "1.0.0"
    assert d["asset"]["content_hash"]
    assert d["provenance_sealed"] is True
    assert d["approvable"] is True
    assert "SIMULATED" in (d["warning"] or "").upper()


def test_rejection_blocks_publication(op_ctx, ag_ctx, media_file):
    post_id = _ready_post(op_ctx, ag_ctx, media_file)
    invoke(op_ctx, "approve-post", {"post_id": post_id, "decision": "rejected"})
    with pytest.raises(ApprovalRequired):
        invoke(op_ctx, "publish-post", {"post_id": post_id})
