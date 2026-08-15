"""T-029 — the phantom asset: media deleted, records intact (I9).

Two defects, one root cause. Retention deletes the bytes and leaves the asset
row, the declaration, the verdict and the sealed provenance behind — which is
correct and required (F-8). Everything downstream then had to decide what to do
with an asset that is fully documented and completely absent, and two places
decided wrong:

  * ``ingest`` matched a re-ingested file on content_hash alone and returned
    ok=True, duplicate=True, "identical bytes already ingested; storage not
    double-counted" — for a row whose state was 'deleted', whose object_path was
    NULL, with nothing on disk and zero bytes accounted;
  * ``determine-rights`` returned PERMITTED, and nothing downstream refused, so
    a phantom asset could be walked to publication.

The tests below fix the boundary in place: what must still work after deletion
(reading provenance, reading and re-deriving the verdict) and what must now
refuse (re-ingest, approve, publish).
"""

from __future__ import annotations

import pytest

from promedia.core import storage
from promedia.core.db import iso
from promedia.core.principal import agent, operator
from promedia.core.registry import Context, invoke
from promedia.errors import MediaUnavailable
from tests.conftest import declaration_original, make_config


@pytest.fixture
def sim_config(tmp_path):
    """Simulation on, so the publish path is reachable without credentials."""
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


def _retention_delete(ctx, asset_id: str) -> None:
    """Do exactly what the retention policy will do, and nothing more.

    There is no retention operation yet (project.md section 10 defines the
    policy; no task has implemented it), so the state it produces is written
    directly. This is NOT a fabrication and is not registered as one: no fake
    value is invented and no assertion is satisfied by invented data. It is the
    real database put into a real state that the schema explicitly provides for
    -- assets.state CHECK IN ('stored','deleted'), object_path nullable
    "because retention deletes the bytes while the row may linger".
    tests/test_provenance.py already does the same thing for the same reason.

    Three effects, because retention has three:
      1. the bytes leave the disk;
      2. the row is marked deleted and loses its object_path;
      3. the committed reservation is released -- otherwise deleting media
         would not give the quota back, and the 100 GB ceiling (F-7) would
         ratchet down for ever.
    """
    from pathlib import Path

    row = ctx.conn.execute(
        "SELECT object_path FROM assets WHERE id = ?", (asset_id,)
    ).fetchone()
    assert row is not None and row["object_path"], "asset must be stored before deleting it"
    Path(row["object_path"]).unlink()
    ctx.conn.execute(
        "UPDATE assets SET state = 'deleted', object_path = NULL WHERE id = ?", (asset_id,)
    )
    ctx.conn.execute(
        "UPDATE storage_ledger SET state = 'released', released_at = ?"
        " WHERE asset_id = ? AND state = 'committed'",
        (iso(), asset_id),
    )


def _published_ready_asset(op_ctx, ag_ctx, media_file):
    """An asset that is genuinely PERMITTED, sealed, and queued for publication."""
    account = invoke(op_ctx, "connect-account", {"platform": "x", "handle": "me", "secret": "tok"})
    asset = invoke(
        ag_ctx, "ingest", {"source_path": str(media_file), "declaration": declaration_original()}
    )
    asset_id = asset["asset_id"]
    invoke(op_ctx, "attest-declaration", {"asset_id": asset_id})
    verdict = invoke(ag_ctx, "determine-rights", {"asset_id": asset_id})
    assert verdict["verdict"] == "PERMITTED", "fixture must start from a genuinely clean asset"
    invoke(ag_ctx, "seal-provenance", {"asset_id": asset_id})
    post = invoke(
        ag_ctx,
        "queue-post",
        {"account_id": account["account_id"], "asset_id": asset_id, "body": "hello"},
    )
    return asset_id, post["post_id"]


# --- defect 1: re-ingest of a deleted asset ----------------------------------


def test_reingest_of_deleted_asset_is_refused_not_reported_as_duplicate(
    op_ctx, ag_ctx, media_file
):
    """The reassuring message WAS the defect.

    Before the fix this returned ok=True, duplicate=True with the note
    "identical bytes already ingested; storage not double-counted", having
    restored nothing at all.
    """
    asset_id, _ = _published_ready_asset(op_ctx, ag_ctx, media_file)
    _retention_delete(ag_ctx, asset_id)

    with pytest.raises(MediaUnavailable) as excinfo:
        invoke(
            ag_ctx,
            "ingest",
            {"source_path": str(media_file), "declaration": declaration_original()},
        )

    detail = excinfo.value.detail
    assert detail["asset_state"] == "deleted"
    assert detail["asset_id"] == asset_id
    assert excinfo.value.code == "MEDIA_UNAVAILABLE"
    # The refusal must name a real remedy, not merely fail.
    assert detail["remedy"]


def test_refused_reingest_restores_nothing_and_says_so(op_ctx, ag_ctx, media_file):
    """A refusal that quietly half-restored would be the same lie in reverse."""
    asset_id, _ = _published_ready_asset(op_ctx, ag_ctx, media_file)
    _retention_delete(ag_ctx, asset_id)

    with pytest.raises(MediaUnavailable):
        invoke(
            ag_ctx,
            "ingest",
            {"source_path": str(media_file), "declaration": declaration_original()},
        )

    row = ag_ctx.conn.execute(
        "SELECT state, object_path FROM assets WHERE id = ?", (asset_id,)
    ).fetchone()
    assert row["state"] == "deleted"
    assert row["object_path"] is None
    assert list(ag_ctx.config.object_root.rglob("*")) == [] or not any(
        p.is_file() for p in ag_ctx.config.object_root.rglob("*")
    )


def test_refused_reingest_leaks_no_quota(op_ctx, ag_ctx, media_file):
    """F-7. A reservation is taken before hashing, so the refusal must give it back.

    This is the half that would have bitten a RESTORE implementation from the
    other side: restoring without re-reserving puts bytes on disk that the
    ledger does not know about, which is precisely the drift LedgerDrift exists
    to catch.
    """
    asset_id, _ = _published_ready_asset(op_ctx, ag_ctx, media_file)
    _retention_delete(ag_ctx, asset_id)

    before = storage.usage(ag_ctx.conn)
    assert before["total_bytes"] == 0, "retention must return the quota"

    with pytest.raises(MediaUnavailable):
        invoke(
            ag_ctx,
            "ingest",
            {"source_path": str(media_file), "declaration": declaration_original()},
        )

    after = storage.usage(ag_ctx.conn)
    assert after == before
    stranded = ag_ctx.conn.execute(
        "SELECT COUNT(*) AS n FROM storage_ledger WHERE state = 'reserved'"
    ).fetchone()
    assert stranded["n"] == 0, "the refusal must not strand a reservation"


def test_duplicate_of_a_stored_asset_still_dedupes(ag_ctx, media_file):
    """The fix must refuse deleted assets WITHOUT breaking real deduplication."""
    first = invoke(
        ag_ctx, "ingest", {"source_path": str(media_file), "declaration": declaration_original()}
    )
    usage_after_first = storage.usage(ag_ctx.conn)["total_bytes"]

    second = invoke(
        ag_ctx, "ingest", {"source_path": str(media_file), "declaration": declaration_original()}
    )
    assert second["duplicate"] is True
    assert second["asset_id"] == first["asset_id"]
    assert second["asset_state"] == "stored"
    assert storage.usage(ag_ctx.conn)["total_bytes"] == usage_after_first


# --- defect 2: the phantom verdict -------------------------------------------


def test_verdict_survives_deletion_and_stays_permitted(op_ctx, ag_ctx, media_file):
    """The line, from the side that must NOT change.

    F-8 requires the rights record to outlive the media; C-20 requires the same
    asset, evidence and ruleset version to yield the identical verdict for ever.
    Media existence is not evidence, so deleting a file must not turn PERMITTED
    into BLOCKED. What changes is that the result now SAYS the media is gone.
    """
    asset_id, _ = _published_ready_asset(op_ctx, ag_ctx, media_file)
    before = invoke(ag_ctx, "determine-rights", {"asset_id": asset_id})
    _retention_delete(ag_ctx, asset_id)
    after = invoke(ag_ctx, "determine-rights", {"asset_id": asset_id})

    assert after["verdict"] == before["verdict"] == "PERMITTED", "C-20: the verdict is unchanged"
    assert after["evidence_digest"] == before["evidence_digest"]
    assert after["media_available"] is False
    assert after["media_state"] == "deleted"
    assert after["publication_blocked"] is True
    assert "publication is refused" in after["note"]


def test_verdict_row_is_not_stamped_with_availability(op_ctx, ag_ctx, media_file):
    """Availability is reported, never recorded. Recording it would break C-20."""
    asset_id, _ = _published_ready_asset(op_ctx, ag_ctx, media_file)
    _retention_delete(ag_ctx, asset_id)
    invoke(ag_ctx, "determine-rights", {"asset_id": asset_id})

    rows = ag_ctx.conn.execute(
        "SELECT verdict FROM rights_verdicts WHERE asset_id = ?", (asset_id,)
    ).fetchall()
    assert {r["verdict"] for r in rows} == {"PERMITTED"}


def test_provenance_still_readable_after_deletion(op_ctx, ag_ctx, media_file):
    """The line, from the side that must keep working (F-8).

    Refusing to READ a sealed record because the media is gone would break the
    single thing the record exists for.
    """
    asset_id, _ = _published_ready_asset(op_ctx, ag_ctx, media_file)
    prov = ag_ctx.conn.execute(
        "SELECT id FROM provenance_records WHERE asset_id = ?", (asset_id,)
    ).fetchone()
    _retention_delete(ag_ctx, asset_id)

    record = invoke(ag_ctx, "provenance", {"provenance_id": prov["id"]})
    assert record["ok"] is True
    assert record["integrity_verified"] is True
    assert record["payload"]["verdict"]["verdict"] == "PERMITTED"
    assert invoke(ag_ctx, "asset", {"asset_id": asset_id})["asset"]["state"] == "deleted"


def test_approve_refuses_a_phantom_asset(op_ctx, ag_ctx, media_file):
    """Before the fix the operator could approve the publication of nothing."""
    asset_id, post_id = _published_ready_asset(op_ctx, ag_ctx, media_file)
    _retention_delete(ag_ctx, asset_id)

    with pytest.raises(MediaUnavailable) as excinfo:
        invoke(op_ctx, "approve-post", {"post_id": post_id})

    assert excinfo.value.detail["asset_state"] == "deleted"
    # Named as an availability problem, never as a rights problem.
    assert excinfo.value.code == "MEDIA_UNAVAILABLE"
    assert excinfo.value.detail["verdict"] == "PERMITTED"
    status = ag_ctx.conn.execute(
        "SELECT status FROM posts WHERE id = ?", (post_id,)
    ).fetchone()["status"]
    assert status == "queued"


def test_publish_refuses_when_retention_fires_after_approval(op_ctx, ag_ctx, media_file):
    """Approval records authorisation, not that the bytes are still there.

    Retention can fire in the window between approving and publishing, so the
    check is repeated at the gate rather than trusted from approval time.
    """
    asset_id, post_id = _published_ready_asset(op_ctx, ag_ctx, media_file)
    invoke(op_ctx, "approve-post", {"post_id": post_id})
    _retention_delete(ag_ctx, asset_id)

    with pytest.raises(MediaUnavailable) as excinfo:
        invoke(op_ctx, "publish-post", {"post_id": post_id})

    assert excinfo.value.detail["asset_state"] == "deleted"
    assert invoke(ag_ctx, "publications", {})["count"] == 0
    # Refused BEFORE the claim, so the post is retryable rather than stranded
    # in 'publishing' with no way back.
    status = ag_ctx.conn.execute(
        "SELECT status FROM posts WHERE id = ?", (post_id,)
    ).fetchone()["status"]
    assert status == "approved"


def test_decision_context_does_not_offer_a_control_the_server_will_refuse(
    op_ctx, ag_ctx, media_file
):
    asset_id, post_id = _published_ready_asset(op_ctx, ag_ctx, media_file)
    _retention_delete(ag_ctx, asset_id)

    d = invoke(op_ctx, "post", {"post_id": post_id})
    assert d["media_available"] is False
    assert d["media_state"] == "deleted"
    assert d["approvable"] is False
    # And the reason shown is availability, not a degraded rights verdict.
    assert d["rights"]["verdict"] == "PERMITTED"


def test_rights_report_agrees_with_the_gate(op_ctx, ag_ctx, media_file):
    """Finding N4's rule, applied to the new gate.

    A reporting operation that disagrees with the gate is worse than no
    reporting operation at all.
    """
    asset_id, _ = _published_ready_asset(op_ctx, ag_ctx, media_file)
    _retention_delete(ag_ctx, asset_id)

    report = invoke(ag_ctx, "rights", {"asset_id": asset_id})
    assert report["verdict"] == "PERMITTED"
    assert report["media_available"] is False
    assert report["publishable"] is False
    assert "publication is refused" in report["note"]
