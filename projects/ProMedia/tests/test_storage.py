"""T-007 — admission control against the hard ceiling (F-7)."""

from __future__ import annotations

import pytest

from promedia.core import storage
from promedia.core.db import iso, now
from promedia.errors import CeilingExceeded
from tests.conftest import GB, make_config


def test_projection_includes_derivatives(config):
    """AC-2: the sizing failure is admitting source bytes and forgetting renders."""
    assert storage.projected_bytes(config, 1000) == 1500  # multiplier 0.5


def test_reservation_refused_at_ceiling(tmp_path, conn):
    """AC-1: refusal reports the shortfall so the caller knows what to free."""
    cfg = make_config(tmp_path, **{"storage.ceiling_bytes": 1000})  # refuse at 850
    storage.reserve(conn, cfg, master_bytes=400)  # projects to 600
    with pytest.raises(CeilingExceeded) as excinfo:
        storage.reserve(conn, cfg, master_bytes=400)  # would total 1200 > 850
    detail = excinfo.value.detail
    assert detail["shortfall_bytes"] == 350
    assert detail["projected_bytes"] == 600


def test_peak_batch_sizing_case(tmp_path, conn):
    """AC-3: the case project.md identified — 20 files of 1.5 GB.

    Confirms the derived figure in the constraints table: ~45 GB, ~45% of the
    ceiling, admitted but consuming nearly half of it in one batch.
    """
    cfg = make_config(tmp_path)
    for _ in range(20):
        storage.reserve(conn, cfg, master_bytes=int(1.5 * GB))
    usage = storage.usage(conn)
    gb_used = usage["total_bytes"] / GB
    assert 44.0 < gb_used < 46.0, f"expected ~45 GB, got {gb_used:.1f} GB"
    assert usage["total_bytes"] < cfg.refuse_bytes


def test_batch_beyond_ceiling_is_refused(tmp_path, conn):
    """The same batch at a 4x derivative multiplier does not fit — A-3 is load-bearing."""
    cfg = make_config(tmp_path, **{"storage.derivative_multiplier": 4.0})
    admitted = 0
    with pytest.raises(CeilingExceeded):
        for _ in range(20):
            storage.reserve(conn, cfg, master_bytes=int(1.5 * GB))
            admitted += 1
    assert admitted < 20, "a 4x multiplier must exhaust the ceiling before 20 files"


def test_refused_ingest_is_queued_and_retryable(tmp_path, conn, media_file):
    """AC-4: F-7 says queue or refuse, never discard."""
    cfg = make_config(tmp_path, **{"storage.ceiling_bytes": 100})
    from promedia.core.registry import Context
    from promedia.core.principal import agent

    ctx = Context(config=cfg, conn=conn, principal=agent("t"))
    from promedia.core import ingest as ingest_layer

    with pytest.raises(CeilingExceeded) as excinfo:
        ingest_layer.ingest_file(
            ctx,
            source_path=str(media_file),
            declaration={"authorship": "operator_original", "third_party_material": []},
        )
    assert excinfo.value.detail["queued"] is True
    queued = storage.queued(conn)
    assert len(queued) == 1
    assert queued[0]["source_path"] == str(media_file)
    assert queued[0]["shortfall_bytes"] > 0


def test_expired_reservation_reclaimed(tmp_path, conn):
    """AC-5: a crashed ingest must not consume quota permanently."""
    cfg = make_config(tmp_path, **{"storage.ceiling_bytes": 1000})
    reservation = storage.reserve(conn, cfg, master_bytes=400)
    assert storage.usage(conn)["total_bytes"] == 600
    conn.execute(
        "UPDATE storage_ledger SET expires_at = ? WHERE id = ?",
        (iso(now().replace(year=2000)), reservation),
    )
    assert storage.usage(conn)["total_bytes"] == 0  # expired excluded from usage
    assert storage.reclaim_expired(conn) == 1


def test_status_reports_state_transitions(tmp_path, conn):
    cfg = make_config(tmp_path, **{"storage.ceiling_bytes": 1000})
    assert storage.status(conn, cfg)["state"] == "ok"
    storage.reserve(conn, cfg, master_bytes=500)  # 750 -> above warn (700)
    assert storage.status(conn, cfg)["state"] == "warning"


def test_release_frees_quota(tmp_path, conn):
    cfg = make_config(tmp_path, **{"storage.ceiling_bytes": 1000})
    reservation = storage.reserve(conn, cfg, master_bytes=400)
    storage.release(conn, reservation)
    assert storage.usage(conn)["total_bytes"] == 0
