"""T-043 — renders count against the storage ledger.

DR-006 makes the ledger the SOLE source of truth for the F-7 100 GB ceiling.
Before this task a render wrote real bytes to disk that the ledger never
learned about, so every render ever produced counted as zero — editing is the
workload that produces the most bytes in this system, so the gap grew fastest
exactly where it mattered most.

Three things this file has to prove, matching the task's acceptance criteria:

  AC-1: a render reserves a PROJECTED output size before ffmpeg starts, and a
        render that would breach the ceiling is refused before any encoding
        time is spent — not after a slow encode discovers the problem.
  AC-2: a failed or timed-out render releases its reservation. Leaks are the
        real failure mode here (T-007 AC-5's reasoning, sharper: a render
        runs for minutes and can time out, which is more likely than a
        crashed ingest).
  AC-3: deleting a render returns its committed bytes to the ledger.

Split into two groups: pure ledger/estimation tests that need no ffmpeg at
all (fast, always run), and real end-to-end tests through the OPERATION layer
that need genuine encodes to exercise a genuine crash path rather than a
mocked one.
"""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from promedia.core import db, projects as projects_layer, storage
from promedia.core.db import iso
from promedia.core.media import ffmpeg
from promedia.core.media.edl import EDL, Clip
from promedia.core.media.ffmpeg import RenderFailed
from promedia.core.principal import agent
from promedia.core.registry import Context, invoke
from promedia.errors import CeilingExceeded, NotFound, ValidationError
from tests.conftest import attest, declaration_original, make_config

needs_ffmpeg = pytest.mark.skipif(not ffmpeg.available(), reason="ffmpeg not installed")


# --- pure ledger and estimation tests (no ffmpeg needed) ---------------------


def test_projected_render_bytes_uses_the_configured_bitrate_and_margin(config):
    """The projection formula itself: duration x bitrate x margin, from
    configuration — never a literal (protocol 05)."""
    table = config.get("media", "estimated_bitrate_bytes_per_second")
    margin = config.get("media", "render_size_safety_margin")
    projected = storage.projected_render_bytes(config, duration_seconds=10.0, quality="balanced")
    assert projected == math.ceil(10.0 * table["balanced"] * margin)


def test_projected_render_bytes_falls_back_for_an_unrecognised_quality(config):
    """An unknown quality string must not crash the estimator — compile_render
    raises the authoritative ValidationError moments later; this just needs to
    not under-estimate in the meantime."""
    table = config.get("media", "estimated_bitrate_bytes_per_second")
    margin = config.get("media", "render_size_safety_margin")
    projected = storage.projected_render_bytes(config, duration_seconds=5.0, quality="nonsense")
    assert projected == math.ceil(5.0 * max(table.values()) * margin)


def test_projected_render_bytes_never_goes_negative(config):
    assert storage.projected_render_bytes(config, duration_seconds=0.0, quality="fast") == 0


def test_projected_duration_uses_the_edls_own_end_when_given(conn, config):
    """A clip with an explicit end needs no probed duration at all."""
    ctx = Context(config=config, conn=conn, principal=agent("t"))
    _insert_bare_asset(conn, "as_x", duration_seconds=None)
    document = EDL(aspect="landscape_720",
                    clips=[Clip(asset_id="as_x", start=2.0, end=5.0, speed=1.0)])
    seconds, known = projects_layer._projected_render_seconds(ctx, document)
    assert known is True
    assert seconds == pytest.approx(3.0)


def test_projected_duration_uses_the_probed_source_duration_when_available(conn, config):
    ctx = Context(config=config, conn=conn, principal=agent("t"))
    _insert_bare_asset(conn, "as_x", duration_seconds=12.0)
    document = EDL(aspect="landscape_720",
                    clips=[Clip(asset_id="as_x", start=0.0, end=None, speed=1.0)])
    seconds, known = projects_layer._projected_render_seconds(ctx, document)
    assert known is True
    assert seconds == pytest.approx(12.0)


def test_projected_duration_falls_back_when_the_source_was_never_probed(conn, config):
    """A-15 residue: probe_status 'unavailable'/'failed' leaves duration_seconds
    NULL. An unbounded clip against that source cannot know its real length, so
    a configured, generous per-clip guess stands in — and the caller is told
    the estimate is not a measurement (`known` is False)."""
    ctx = Context(config=config, conn=conn, principal=agent("t"))
    _insert_bare_asset(conn, "as_x", duration_seconds=None)
    document = EDL(aspect="landscape_720",
                    clips=[Clip(asset_id="as_x", start=0.0, end=None, speed=2.0)])
    seconds, known = projects_layer._projected_render_seconds(ctx, document)
    fallback = float(config.get("media", "unknown_clip_duration_seconds"))
    assert known is False
    assert seconds == pytest.approx(fallback / 2.0)  # speed halves the on-timeline duration


def test_reserve_projected_refused_at_ceiling(tmp_path, conn):
    """The shared admission-control path a render now goes through too."""
    cfg = make_config(tmp_path, **{"storage.ceiling_bytes": 1000})  # refuse at 850
    storage.reserve_projected(conn, cfg, projected=400, kind="derivative")
    with pytest.raises(CeilingExceeded) as excinfo:
        storage.reserve_projected(conn, cfg, projected=500, kind="derivative")
    assert excinfo.value.detail["shortfall_bytes"] == 50


def test_reserve_projected_accepts_a_caller_supplied_id(tmp_path, conn):
    """Renders reserve under their own render_id (T-043) so the reservation
    can be found again by id alone when the render is deleted."""
    cfg = make_config(tmp_path, **{"storage.ceiling_bytes": 10_000})
    reservation_id = storage.reserve_projected(
        conn, cfg, projected=500, kind="derivative", reservation_id="rnd_explicit"
    )
    assert reservation_id == "rnd_explicit"


def test_commit_with_no_asset_id_is_a_derivative_with_no_asset(tmp_path, conn):
    """A render reservation fits the existing ledger row shape exactly as the
    task predicted: kind='derivative', asset_id NULL."""
    cfg = make_config(tmp_path, **{"storage.ceiling_bytes": 10_000})
    reservation_id = storage.reserve_projected(conn, cfg, projected=500, kind="derivative")
    storage.commit(conn, reservation_id, actual_bytes=480)
    row = conn.execute(
        "SELECT asset_id, kind, bytes, state FROM storage_ledger WHERE id = ?", (reservation_id,)
    ).fetchone()
    assert row["asset_id"] is None
    assert row["kind"] == "derivative"
    assert row["bytes"] == 480
    assert row["state"] == "committed"


def test_commit_reconciles_an_overestimate_down(tmp_path, conn):
    """AC-1's honesty requirement: the ledger converges on truth. A reservation
    that overshot returns its slack the moment the real size is known."""
    cfg = make_config(tmp_path, **{"storage.ceiling_bytes": 10_000})
    reservation_id = storage.reserve_projected(conn, cfg, projected=5000, kind="derivative")
    assert storage.usage(conn)["total_bytes"] == 5000
    storage.commit(conn, reservation_id, actual_bytes=1200)  # real output much smaller
    assert storage.usage(conn)["total_bytes"] == 1200


def test_commit_reconciles_an_underestimate_up(tmp_path, conn):
    """The other failure direction: a badly-wrong LOW estimate is corrected to
    the true figure at commit rather than quietly staying wrong forever."""
    cfg = make_config(tmp_path, **{"storage.ceiling_bytes": 10_000})
    reservation_id = storage.reserve_projected(conn, cfg, projected=500, kind="derivative")
    assert storage.usage(conn)["total_bytes"] == 500
    storage.commit(conn, reservation_id, actual_bytes=4000)  # real output much bigger
    assert storage.usage(conn)["total_bytes"] == 4000


def test_free_returns_committed_bytes_to_the_pool(tmp_path, conn):
    """AC-3's core mechanism, exercised directly against the ledger."""
    cfg = make_config(tmp_path, **{"storage.ceiling_bytes": 10_000})
    reservation_id = storage.reserve_projected(conn, cfg, projected=500, kind="derivative")
    storage.commit(conn, reservation_id, actual_bytes=480)
    assert storage.usage(conn)["committed_bytes"] == 480
    assert storage.free(conn, reservation_id) == "freed"
    assert storage.usage(conn)["committed_bytes"] == 0


def test_free_is_idempotent(tmp_path, conn):
    cfg = make_config(tmp_path, **{"storage.ceiling_bytes": 10_000})
    reservation_id = storage.reserve_projected(conn, cfg, projected=500, kind="derivative")
    storage.commit(conn, reservation_id, actual_bytes=480)
    assert storage.free(conn, reservation_id) == "freed"
    assert storage.free(conn, reservation_id) == "already_released"


def test_free_reports_missing_rather_than_raising(tmp_path, conn):
    """A render made before T-043 shipped was never reserved at all — deleting
    one must not be treated as ledger drift."""
    cfg = make_config(tmp_path, **{"storage.ceiling_bytes": 10_000})
    assert storage.free(conn, "rnd_never_existed") == "missing"


def test_free_reports_a_still_reserved_row_rather_than_touching_it(tmp_path, conn):
    """A row in any state but 'committed' is not this call's to change — the
    same scoping discipline `release` already applies to 'reserved' rows
    (finding B4), mirrored here for the deletion path."""
    cfg = make_config(tmp_path, **{"storage.ceiling_bytes": 10_000})
    reservation_id = storage.reserve_projected(conn, cfg, projected=500, kind="derivative")
    assert storage.free(conn, reservation_id) == "reserved"
    assert storage.usage(conn)["reserved_bytes"] == 500  # untouched


def _insert_bare_asset(conn, asset_id: str, *, duration_seconds: float | None) -> None:
    conn.execute(
        "INSERT INTO assets (id, content_hash, byte_size, original_filename, mime_type,"
        " duration_seconds, probe_status, derived_from, state, ingested_at, object_path)"
        " VALUES (?, ?, ?, ?, NULL, ?, ?, NULL, 'stored', ?, ?)",
        (
            asset_id, f"hash_{asset_id}", 100, f"{asset_id}.mp4",
            duration_seconds, "ok" if duration_seconds is not None else "unavailable",
            iso(), f"/nowhere/{asset_id}.mp4",
        ),
    )
    # T-044: render() now checks rights before anything else, including the
    # pure-estimation callers of this helper that never go near render(). A
    # bare asset with no rights_verdicts row is ESCALATE (NO_VERDICT_YET), so
    # any test that DOES route this asset through render-project needs it to
    # be PERMITTED already, exactly like a real ingest + determine-rights
    # would leave it — inserted directly here, matching this helper's own
    # 'bare row' style, rather than through the real operation.
    conn.execute(
        "INSERT INTO rights_verdicts (id, asset_id, verdict, matched_rule, reasons, ruleset,"
        " ruleset_version, jurisdiction, evidence_digest, decided_at, decided_by)"
        " VALUES (?, ?, 'PERMITTED', 'TEST_SEED', '[]', 'test', '1', 'n/a', 'n/a', ?, 'test')",
        (f"vd_{asset_id}", asset_id, iso()),
    )


# --- real end-to-end tests, through the operation layer -----------------------


@pytest.fixture
def render_env(tmp_path, monkeypatch):
    monkeypatch.setenv("PROMEDIA_CREDENTIAL_STORE", str(tmp_path / "creds.json"))
    cfg = make_config(tmp_path)
    conn = db.connect(cfg.db_path)
    db.apply_schema(conn)
    yield cfg, Context(config=cfg, conn=conn, principal=agent("ag"), agent_id="agent-a")
    conn.close()


@pytest.fixture(scope="module")
def real_media(tmp_path_factory):
    """Genuine decodable media. conftest's ``media_file`` writes placeholder
    bytes that ffmpeg cannot decode — fine for ingest tests, useless for a
    render (a previous task's finding, repeated here so it is not
    rediscovered)."""
    if not ffmpeg.available():
        pytest.skip("ffmpeg not installed")
    out = tmp_path_factory.mktemp("rndstorage") / "src.mp4"
    ffmpeg.run([
        "-f", "lavfi", "-i", "testsrc=size=640x480:rate=25:duration=4",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=4",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-shortest", str(out),
    ], timeout_seconds=180)
    return out


@pytest.fixture
def source_asset(render_env, real_media):
    """PERMITTED and render-ready (T-044): render-project now refuses
    anything short of that before it does any work, so a fixture meant to
    feed real renders has to clear the same gate a real one would."""
    cfg, ctx = render_env
    asset_id = invoke(ctx, "ingest", {
        "source_path": str(real_media), "declaration": declaration_original(),
    })["asset_id"]
    attest(ctx, asset_id)  # also runs determine-rights (tests/conftest.py)
    return asset_id


def edl_with_clip(asset_id: str, **kw) -> dict:
    base = {"aspect": "landscape_720", "clips": [{"asset_id": asset_id, "start": 0, "end": 2}]}
    base.update(kw)
    return base


@needs_ffmpeg
def test_render_reserves_before_encoding_and_commits_the_real_size_after(render_env, source_asset):
    """AC-1's second half, and DR-006's convergence requirement, both in one
    real render: what the ledger gains equals exactly what was produced."""
    cfg, ctx = render_env
    pid = invoke(ctx, "create-project", {"title": "P"})["project_id"]
    invoke(ctx, "set-edl", {"project_id": pid, "edl": edl_with_clip(source_asset)})

    before = storage.usage(ctx.conn)["committed_bytes"]
    result = invoke(ctx, "render-project", {"project_id": pid, "quality": "fast"})
    after = storage.usage(ctx.conn)["committed_bytes"]

    assert result["storage"]["committed_bytes"] == result["byte_size"]
    assert after - before == result["byte_size"]
    assert result["storage"]["projected_bytes"] > 0
    assert result["storage"]["duration_measured"] is True


def test_a_render_that_would_breach_the_ceiling_is_refused_before_ffmpeg_starts(
    render_env, monkeypatch, tmp_path
):
    """AC-1's sharpest sentence: 'refusing after a 4-minute encode is not
    admission control.' ffmpeg is monkeypatched to explode if it is ever
    reached, proving the refusal happens strictly before it — and this needs
    no real ffmpeg at all, since the point is that it is never invoked."""
    cfg, ctx = render_env
    placeholder = tmp_path / "placeholder.mp4"
    placeholder.write_bytes(b"not real media; never decoded in this test")
    _insert_bare_asset(ctx.conn, "as_ceiling", duration_seconds=5.0)
    ctx.conn.execute("UPDATE assets SET object_path = ? WHERE id = 'as_ceiling'", (str(placeholder),))

    pid = invoke(ctx, "create-project", {"title": "P"})["project_id"]
    invoke(ctx, "set-edl", {"project_id": pid, "edl": edl_with_clip("as_ceiling", end=5)})

    cfg.values["storage"]["ceiling_bytes"] = 1000  # refuse at 850; any real render dwarfs this

    import promedia.core.media.render as render_engine

    def boom(*_args, **_kwargs):
        raise AssertionError("ffmpeg must never run once admission control has refused")

    monkeypatch.setattr(render_engine, "compile_render", boom)
    monkeypatch.setattr(render_engine, "execute", boom)

    with pytest.raises(CeilingExceeded):
        invoke(ctx, "render-project", {"project_id": pid, "quality": "fast"})

    assert storage.usage(ctx.conn)["total_bytes"] == 0, "a refused reservation must leak nothing"


@needs_ffmpeg
def test_a_render_that_fails_to_compile_releases_its_reservation(render_env, source_asset):
    """AC-2, the compile-time failure path: an unrecognised quality fails
    AFTER the reservation is taken (compile_render is what validates it), so
    this is a real test of the release-on-failure path, not a no-op."""
    cfg, ctx = render_env
    pid = invoke(ctx, "create-project", {"title": "P"})["project_id"]
    invoke(ctx, "set-edl", {"project_id": pid, "edl": edl_with_clip(source_asset)})

    before = storage.usage(ctx.conn)["total_bytes"]
    with pytest.raises(ValidationError):
        invoke(ctx, "render-project", {"project_id": pid, "quality": "not-a-real-quality"})
    after = storage.usage(ctx.conn)["total_bytes"]
    assert after == before, "a compile-time failure must not leak the reservation it took"


@needs_ffmpeg
def test_a_render_that_times_out_releases_its_reservation(render_env, source_asset):
    """AC-2's sharpest case: 'a render runs for minutes and can time out —
    that is the same hazard [as a crashed ingest], more likely.' This is a
    REAL ffmpeg process actually killed by the timeout, not a mock."""
    cfg, ctx = render_env
    pid = invoke(ctx, "create-project", {"title": "P"})["project_id"]
    invoke(ctx, "set-edl", {"project_id": pid, "edl": edl_with_clip(source_asset)})

    cfg.values["media"]["render_timeout_seconds"] = 0.01  # guaranteed to expire mid-encode

    before = storage.usage(ctx.conn)["total_bytes"]
    with pytest.raises(RenderFailed):
        invoke(ctx, "render-project", {"project_id": pid, "quality": "fast"})
    after = storage.usage(ctx.conn)["total_bytes"]
    assert after == before, "a timed-out render must not leak the reservation it took"


@needs_ffmpeg
def test_deleting_a_render_returns_its_bytes_to_the_ledger(render_env, source_asset):
    """AC-3, end to end: render for real, delete it, watch the ledger move
    back down by exactly what it moved up by."""
    cfg, ctx = render_env
    pid = invoke(ctx, "create-project", {"title": "P"})["project_id"]
    invoke(ctx, "set-edl", {"project_id": pid, "edl": edl_with_clip(source_asset)})
    result = invoke(ctx, "render-project", {"project_id": pid, "quality": "fast"})
    render_id = result["render_id"]
    committed = result["storage"]["committed_bytes"]
    output_path = Path(result["output_path"])
    assert output_path.is_file()

    before = storage.usage(ctx.conn)["committed_bytes"]
    outcome = projects_layer.delete_render(ctx, render_id=render_id)
    after = storage.usage(ctx.conn)["committed_bytes"]

    assert outcome["ledger_state"] == "freed"
    assert outcome["bytes_freed"] == committed
    assert before - after == committed
    assert outcome["file_deleted"] is True
    assert not output_path.exists()
    assert invoke(ctx, "renders", {"project_id": pid})["count"] == 0


def test_deleting_a_render_with_no_reservation_on_record_still_succeeds(render_env):
    """A render catalogued before T-043 shipped was never reserved. Deleting
    one must succeed rather than treating the absent reservation as an error
    — 'missing' is an expected case here, not drift (see storage.free)."""
    cfg, ctx = render_env
    pid = invoke(ctx, "create-project", {"title": "P"})["project_id"]
    legacy_path = cfg.data_dir / "legacy.mp4"
    legacy_path.parent.mkdir(parents=True, exist_ok=True)
    legacy_path.write_bytes(b"a render from before this task existed")
    ctx.conn.execute(
        "INSERT INTO renders (id, project_id, edl_version, output_path, quality,"
        " width, height, duration_seconds, byte_size, substitutions, rendered_by, rendered_at)"
        " VALUES ('rnd_legacy', ?, 1, ?, 'fast', 1280, 720, 2.0, 12345, NULL, 'ag', ?)",
        (pid, str(legacy_path), iso()),
    )

    outcome = projects_layer.delete_render(ctx, render_id="rnd_legacy")

    assert outcome["ledger_state"] == "missing"
    assert outcome["bytes_freed"] == 0
    assert outcome["file_deleted"] is True
    assert not legacy_path.exists()


def test_deleting_an_unknown_render_is_not_found(render_env):
    cfg, ctx = render_env
    with pytest.raises(NotFound):
        projects_layer.delete_render(ctx, render_id="rnd_does_not_exist")


# --- R-006: delete-render is now a registered, dual-surface operation ---------


@needs_ffmpeg
def test_delete_render_is_reachable_as_a_registered_operation(render_env, source_asset):
    """The gap R-006 named directly: AC-3 was implemented and tested against
    the projects module, but neither surface could call it. This goes through
    invoke(), exactly as the CLI and the web UI do."""
    cfg, ctx = render_env
    pid = invoke(ctx, "create-project", {"title": "P"})["project_id"]
    invoke(ctx, "set-edl", {"project_id": pid, "edl": edl_with_clip(source_asset)})
    result = invoke(ctx, "render-project", {"project_id": pid, "quality": "fast"})
    render_id = result["render_id"]
    output_path = Path(result["output_path"])
    assert output_path.is_file()

    outcome = invoke(ctx, "delete-render", {"project_id": pid, "render_id": render_id})

    assert outcome["ok"] is True
    assert outcome["ledger_state"] == "freed"
    assert not output_path.exists()
    assert invoke(ctx, "renders", {"project_id": pid})["count"] == 0


def test_delete_render_takes_the_project_lock(render_env):
    """C-19, proven the way tests/test_locking.py proves it elsewhere: a lock
    already held on the project refuses the call before anything is deleted."""
    cfg, ctx = render_env
    pid = invoke(ctx, "create-project", {"title": "P"})["project_id"]
    legacy_path = cfg.data_dir / "legacy.mp4"
    legacy_path.parent.mkdir(parents=True, exist_ok=True)
    legacy_path.write_bytes(b"a render from before this task existed")
    ctx.conn.execute(
        "INSERT INTO renders (id, project_id, edl_version, output_path, quality,"
        " width, height, duration_seconds, byte_size, substitutions, rendered_by, rendered_at)"
        " VALUES ('rnd_locked', ?, 1, ?, 'fast', 1280, 720, 2.0, 12345, NULL, 'ag', ?)",
        (pid, str(legacy_path), iso()),
    )
    db.acquire_lock(
        ctx.conn, "project", pid, task_id="held", agent="agent-beta", model="m", ttl_minutes=30,
    )

    from promedia.errors import EntityLocked

    with pytest.raises(EntityLocked):
        invoke(ctx, "delete-render", {"project_id": pid, "render_id": "rnd_locked"})

    assert legacy_path.exists(), "a refused call must not have deleted the file"
    assert invoke(ctx, "renders", {"project_id": pid})["count"] == 1


def test_delete_render_refuses_a_render_id_from_a_different_project(render_env):
    """Defence in depth for the operation surface: project_id and render_id
    are two independent parameters, and nothing stops a caller from supplying
    a render_id that belongs to a different project than the one it locked."""
    cfg, ctx = render_env
    pid_a = invoke(ctx, "create-project", {"title": "A"})["project_id"]
    pid_b = invoke(ctx, "create-project", {"title": "B"})["project_id"]
    render_output = cfg.data_dir / "cross-project.mp4"
    render_output.parent.mkdir(parents=True, exist_ok=True)
    render_output.write_bytes(b"belongs to project A")
    ctx.conn.execute(
        "INSERT INTO renders (id, project_id, edl_version, output_path, quality,"
        " width, height, duration_seconds, byte_size, substitutions, rendered_by, rendered_at)"
        " VALUES ('rnd_a', ?, 1, ?, 'fast', 1280, 720, 2.0, 12345, NULL, 'ag', ?)",
        (pid_a, str(render_output), iso()),
    )

    with pytest.raises(ValidationError):
        invoke(ctx, "delete-render", {"project_id": pid_b, "render_id": "rnd_a"})

    assert render_output.exists(), "a project_id/render_id mismatch must not delete anything"
    assert invoke(ctx, "renders", {"project_id": pid_a})["count"] == 1
