"""T-008 — ingest, content addressing, honest metadata."""

from __future__ import annotations

import hashlib

import pytest

from promedia.core import storage
from promedia.core.registry import invoke
from promedia.errors import NotFound, ValidationError
from tests.conftest import declaration_original


def test_ingest_stores_content_addressed(agent_ctx, media_file, config):
    """AC-1: identity is the content, not the path (F-8)."""
    expected = hashlib.sha256(media_file.read_bytes()).hexdigest()
    result = invoke(
        agent_ctx, "ingest", {"source_path": str(media_file), "declaration": declaration_original()}
    )
    assert result["content_hash"] == expected
    stored = config.object_root / expected[0:2] / expected[2:4] / expected
    assert stored.is_file()
    assert stored.read_bytes() == media_file.read_bytes()


def test_duplicate_ingest_does_not_double_count(agent_ctx, media_file):
    """AC-2: dedup falls out of content addressing, and must not consume quota twice."""
    first = invoke(
        agent_ctx, "ingest", {"source_path": str(media_file), "declaration": declaration_original()}
    )
    usage_after_first = storage.usage(agent_ctx.conn)["total_bytes"]

    second = invoke(
        agent_ctx, "ingest", {"source_path": str(media_file), "declaration": declaration_original()}
    )
    assert second["duplicate"] is True
    assert second["asset_id"] == first["asset_id"]
    assert storage.usage(agent_ctx.conn)["total_bytes"] == usage_after_first


def test_ingest_requires_rights_declaration(agent_ctx, media_file):
    """AC-3: an asset that cannot be evaluated must never become publishable."""
    with pytest.raises(ValidationError) as excinfo:
        invoke(agent_ctx, "ingest", {"source_path": str(media_file)})
    assert excinfo.value.detail["parameter"] == "declaration"


def test_ingest_rejects_bad_authorship(agent_ctx, media_file):
    with pytest.raises(ValidationError) as excinfo:
        invoke(
            agent_ctx,
            "ingest",
            {"source_path": str(media_file), "declaration": {"authorship": "probably mine"}},
        )
    assert excinfo.value.detail["parameter"] == "declaration.authorship"


def test_missing_probe_records_unavailable_not_guess(agent_ctx, media_file):
    """AC-4: ffprobe is absent here. A guessed duration would be a fabrication."""
    result = invoke(
        agent_ctx, "ingest", {"source_path": str(media_file), "declaration": declaration_original()}
    )
    assert result["probe_status"] in {"unavailable", "failed", "ok"}
    if result["probe_status"] != "ok":
        assert result["duration_seconds"] is None, "duration must be null, never invented"


def test_failed_ingest_releases_reservation(agent_ctx, tmp_path):
    """AC-5: a failure must not leak quota."""
    missing = tmp_path / "not-there.mp4"
    with pytest.raises(NotFound):
        invoke(
            agent_ctx,
            "ingest",
            {"source_path": str(missing), "declaration": declaration_original()},
        )
    assert storage.usage(agent_ctx.conn)["total_bytes"] == 0


def test_ingest_records_declaration(agent_ctx, media_file):
    result = invoke(
        agent_ctx, "ingest", {"source_path": str(media_file), "declaration": declaration_original()}
    )
    detail = invoke(agent_ctx, "asset", {"asset_id": result["asset_id"]})
    assert detail["declaration"]["authorship"] == "operator_original"
    assert detail["declaration"]["declared_by"] == "test-agent"


def test_agent_may_ingest(agent_ctx, media_file):
    """F-2 permits agents to ingest — it has no external effect."""
    result = invoke(
        agent_ctx, "ingest", {"source_path": str(media_file), "declaration": declaration_original()}
    )
    assert result["ok"] is True
