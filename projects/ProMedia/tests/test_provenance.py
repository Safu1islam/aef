"""T-010 — provenance survives the media it describes (F-8)."""

from __future__ import annotations

import json

import pytest

from promedia.core import provenance
from promedia.core.registry import invoke
from promedia.errors import IntegrityError, NotFound
from tests.conftest import attest, declaration_original


def _sealed_asset(ctx, media_file):
    asset_id = invoke(
        ctx, "ingest", {"source_path": str(media_file), "declaration": declaration_original()}
    )["asset_id"]
    attest(ctx, asset_id)
    sealed = invoke(ctx, "seal-provenance", {"asset_id": asset_id})
    return asset_id, sealed["provenance_id"]


def test_record_is_self_contained(agent_ctx, media_file):
    """AC-1: embeds everything; references no filesystem path."""
    asset_id, provenance_id = _sealed_asset(agent_ctx, media_file)
    record = invoke(agent_ctx, "provenance", {"provenance_id": provenance_id})
    payload = record["payload"]

    assert payload["content_hash"]
    assert payload["declaration"]["authorship"] == "operator_original"
    assert payload["verdict"]["verdict"] == "PERMITTED"
    assert payload["verdict"]["ruleset_version"] == "1.0.0"
    assert "evidence" in payload

    # No filesystem path anywhere in the record.
    serialised = json.dumps(payload)
    assert "object_path" not in serialised
    assert ".mp4" not in serialised.replace(payload["original_filename"], "")


def test_provenance_survives_asset_deletion(agent_ctx, media_file, config):
    """AC-2 — the property the whole design exists for.

    Delete the stored object and the asset row, exactly as retention will, then
    read the record back and verify it.
    """
    asset_id, provenance_id = _sealed_asset(agent_ctx, media_file)

    asset = agent_ctx.conn.execute(
        "SELECT object_path FROM assets WHERE id = ?", (asset_id,)
    ).fetchone()
    from pathlib import Path

    Path(asset["object_path"]).unlink()
    agent_ctx.conn.execute("DELETE FROM assets WHERE id = ?", (asset_id,))

    assert agent_ctx.conn.execute(
        "SELECT COUNT(*) AS n FROM assets WHERE id = ?", (asset_id,)
    ).fetchone()["n"] == 0

    record = invoke(agent_ctx, "provenance", {"provenance_id": provenance_id})
    assert record["integrity_verified"] is True
    assert record["payload"]["verdict"]["verdict"] == "PERMITTED"
    assert record["payload"]["declaration"]["authorship"] == "operator_original"


def test_tamper_detected(agent_ctx, media_file):
    """AC-3."""
    _, provenance_id = _sealed_asset(agent_ctx, media_file)
    row = agent_ctx.conn.execute(
        "SELECT payload FROM provenance_records WHERE id = ?", (provenance_id,)
    ).fetchone()
    tampered = json.loads(row["payload"])
    tampered["verdict"]["verdict"] = "PERMITTED"
    tampered["declaration"]["authorship"] = "definitely mine"
    agent_ctx.conn.execute(
        "UPDATE provenance_records SET payload = ? WHERE id = ?",
        (json.dumps(tampered, sort_keys=True, separators=(",", ":")), provenance_id),
    )
    with pytest.raises(IntegrityError):
        provenance.read(agent_ctx.conn, provenance_id)
    assert invoke(agent_ctx, "verify-provenance", {"provenance_id": provenance_id})["integrity_verified"] is False


def test_seal_requires_a_verdict(agent_ctx, media_file):
    asset_id = invoke(
        agent_ctx, "ingest", {"source_path": str(media_file), "declaration": declaration_original()}
    )["asset_id"]
    with pytest.raises(NotFound, match="verdict"):
        invoke(agent_ctx, "seal-provenance", {"asset_id": asset_id})
