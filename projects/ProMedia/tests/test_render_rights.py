"""T-044 — rights and media-availability gate before render (and publish).

Before this task ``projects.render()`` checked that every source asset an EDL
referenced EXISTED and had usable media on disk, and nothing else. It never
asked whether the footage was allowed to be used at all: an edit composed
from BLOCKED, ESCALATE, or simply never-graded material rendered
successfully, every time. F-4 is the constraint that makes this sharp —
"transforming material never makes unusable material usable" — and an editor
is exactly that transformation machine, so it was the most direct laundering
route left open in the system.

Four things this file has to prove, matching the task's acceptance criteria:

  AC-1: rendering an EDL that references a BLOCKED or ESCALATE asset is
        refused, naming the offending asset.
  AC-2: a rendered output inherits the most restrictive verdict of its
        sources, and cannot be published if any source is not PERMITTED.
  AC-3: rendering from an asset whose media is absent or deleted raises
        MediaUnavailable rather than producing a partial video.
  AC-4: a render is registered as a derivative of its sources, so the
        existing ancestry machinery governs it.

AC-3's mechanism (``_resolve_sources``) already existed before this task —
what T-044 added there is only the ORDER (rights checked first, so a legal
refusal is never masked by a coincidental availability one). It is still
proven directly here, in the file this task owns, rather than left to read as
inherited from tests/test_projects.py.
"""

from __future__ import annotations

import pytest

from promedia.core import db
from promedia.core import projects as projects_layer
from promedia.core import rights as rights_layer
from promedia.core.media import ffmpeg
from promedia.core.principal import agent, operator
from promedia.core.registry import Context, invoke
from promedia.errors import MediaUnavailable, NotFound, RightsBlocked
from tests.conftest import attest, declaration_original, make_config

needs_ffmpeg = pytest.mark.skipif(not ffmpeg.available(), reason="ffmpeg not installed")


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("PROMEDIA_CREDENTIAL_STORE", str(tmp_path / "creds.json"))
    cfg = make_config(tmp_path)
    conn = db.connect(cfg.db_path)
    db.apply_schema(conn)
    yield cfg, Context(config=cfg, conn=conn, principal=agent("ag"), agent_id="agent-a")
    conn.close()


@pytest.fixture(scope="module")
def real_media(tmp_path_factory):
    """Genuine decodable media — a render actually decodes its sources, so
    conftest's placeholder-bytes ``media_file`` cannot stand in here."""
    if not ffmpeg.available():
        pytest.skip("ffmpeg not installed")
    out = tmp_path_factory.mktemp("renderrights") / "src.mp4"
    ffmpeg.run([
        "-f", "lavfi", "-i", "testsrc=size=640x480:rate=25:duration=4",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=4",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-shortest", str(out),
    ], timeout_seconds=180)
    return out


def _ingest(ctx, media_path) -> str:
    return invoke(ctx, "ingest", {
        "source_path": str(media_path), "declaration": declaration_original(),
    })["asset_id"]


@pytest.fixture
def permitted_asset(env, real_media):
    cfg, ctx = env
    asset_id = _ingest(ctx, real_media)
    attest(ctx, asset_id)  # operator-attests and runs determine-rights -> PERMITTED
    return asset_id


def an_edl(asset_id, **kw) -> dict:
    base = {"aspect": "landscape_720", "clips": [{"asset_id": asset_id, "start": 0, "end": 1}]}
    base.update(kw)
    return base


def _block(ctx, asset_id: str) -> None:
    """Re-declare an already-ingested asset as unlicensed third-party
    material and re-determine — THIRD_PARTY_MATERIAL_UNCLEARED (the shipped
    conservative-1.0.0 ruleset) blocks this outright, no evidence needed."""
    ctx.conn.execute(
        "UPDATE rights_declarations SET authorship = 'third_party',"
        " third_party_material = '[\"unlicensed clip\"]' WHERE asset_id = ?",
        (asset_id,),
    )
    op_ctx = Context(config=ctx.config, conn=ctx.conn, principal=operator("op"))
    invoke(op_ctx, "determine-rights", {"asset_id": asset_id})


# --- AC-1: BLOCKED or ESCALATE is refused, naming the offending asset --------


def test_render_refuses_a_blocked_source_naming_it(env, permitted_asset):
    cfg, ctx = env
    _block(ctx, permitted_asset)
    assert invoke(
        Context(config=cfg, conn=ctx.conn, principal=operator("op")),
        "rights", {"asset_id": permitted_asset},
    )["verdict"] == "BLOCKED"

    pid = invoke(ctx, "create-project", {"title": "P"})["project_id"]
    invoke(ctx, "set-edl", {"project_id": pid, "edl": an_edl(permitted_asset)})

    with pytest.raises(RightsBlocked) as excinfo:
        invoke(ctx, "render-project", {"project_id": pid})
    assert excinfo.value.detail["asset_id"] == permitted_asset
    assert excinfo.value.detail["verdict"] == "BLOCKED"
    # AC-1 is a refusal, not a partial success: nothing rendered.
    assert invoke(ctx, "renders", {"project_id": pid})["count"] == 0


def test_render_refuses_a_never_graded_source(env, real_media):
    """ESCALATE by the most direct route: an asset ingested but never run
    through determine-rights at all. effective_verdict's own NO_VERDICT_YET
    maps to ESCALATE, which AC-1 names explicitly alongside BLOCKED."""
    cfg, ctx = env
    ungraded = _ingest(ctx, real_media)

    pid = invoke(ctx, "create-project", {"title": "P"})["project_id"]
    invoke(ctx, "set-edl", {"project_id": pid, "edl": an_edl(ungraded)})

    with pytest.raises(RightsBlocked) as excinfo:
        invoke(ctx, "render-project", {"project_id": pid})
    assert excinfo.value.detail["asset_id"] == ungraded
    assert excinfo.value.detail["verdict"] == "ESCALATE"
    assert excinfo.value.detail["matched_rule"] == "NO_VERDICT_YET"


def test_render_refuses_an_agent_declared_but_unattested_source(env, real_media):
    """The other ESCALATE route: a real verdict was computed, and it is
    still ESCALATE, because DECLARATION_NOT_OPERATOR_ATTESTED fires before any
    permitting rule can (F-2 — an agent's own declaration cannot self-permit).
    """
    cfg, ctx = env
    asset_id = _ingest(ctx, real_media)
    invoke(ctx, "determine-rights", {"asset_id": asset_id})  # as the agent; never attested
    assert invoke(ctx, "rights", {"asset_id": asset_id})["verdict"] == "ESCALATE"

    pid = invoke(ctx, "create-project", {"title": "P"})["project_id"]
    invoke(ctx, "set-edl", {"project_id": pid, "edl": an_edl(asset_id)})

    with pytest.raises(RightsBlocked) as excinfo:
        invoke(ctx, "render-project", {"project_id": pid})
    assert excinfo.value.detail["verdict"] == "ESCALATE"


def test_render_refuses_when_only_the_audio_track_is_blocked(env, permitted_asset, real_media):
    """The gate covers every source an EDL depends on (edl.asset_ids()), not
    only the video clips — an audio bed is just as real a laundering route."""
    cfg, ctx = env
    music = _ingest(ctx, real_media)
    _block(ctx, music)

    pid = invoke(ctx, "create-project", {"title": "P"})["project_id"]
    edl = an_edl(permitted_asset, audio=[{"asset_id": music, "start": 0}])
    invoke(ctx, "set-edl", {"project_id": pid, "edl": edl})

    with pytest.raises(RightsBlocked) as excinfo:
        invoke(ctx, "render-project", {"project_id": pid})
    assert excinfo.value.detail["asset_id"] == music


def test_render_naming_a_nonexistent_asset_is_not_found_not_rights_blocked(env):
    """A wrong id is a caller mistake, not a rights question — the more
    specific NotFound must win over the newer, broader rights gate (T-044's
    _require_assets_exist runs first, ahead of _check_rights)."""
    cfg, ctx = env
    pid = invoke(ctx, "create-project", {"title": "P"})["project_id"]
    invoke(ctx, "set-edl", {"project_id": pid, "edl": an_edl("as_does_not_exist")})
    with pytest.raises(NotFound):
        invoke(ctx, "render-project", {"project_id": pid})


# --- AC-2: the render inherits the worst verdict; publish stays gated -------


@needs_ffmpeg
def test_a_permitted_render_reports_the_inherited_verdict(env, permitted_asset):
    cfg, ctx = env
    pid = invoke(ctx, "create-project", {"title": "P"})["project_id"]
    invoke(ctx, "set-edl", {"project_id": pid, "edl": an_edl(permitted_asset)})

    result = invoke(ctx, "render-project", {"project_id": pid, "quality": "fast"})

    assert result["rights"]["verdict"] == "PERMITTED"
    assert result["rights"]["asset_id"] == result["render_id"]


@needs_ffmpeg
def test_a_render_can_be_queued_and_approved_like_any_other_asset(env, permitted_asset):
    """The point of AC-4's ancestry-machinery wiring, proven end to end:
    posts.py needed ZERO changes to govern a render, because the render's
    output is a real row in the same assets table every other post already
    reads its rights verdict from."""
    cfg, ctx = env
    op_ctx = Context(config=cfg, conn=ctx.conn, principal=operator("op"))
    account = invoke(op_ctx, "connect-account", {"platform": "x", "handle": "me", "secret": "t"})

    pid = invoke(ctx, "create-project", {"title": "P"})["project_id"]
    invoke(ctx, "set-edl", {"project_id": pid, "edl": an_edl(permitted_asset)})
    result = invoke(ctx, "render-project", {"project_id": pid, "quality": "fast"})
    render_asset_id = result["rights"]["asset_id"]

    post = invoke(ctx, "queue-post", {
        "account_id": account["account_id"], "asset_id": render_asset_id, "body": "hello",
    })
    approved = invoke(op_ctx, "approve-post", {"post_id": post["post_id"]})
    assert approved["status"] == "approved"


@needs_ffmpeg
def test_a_render_cannot_be_approved_if_its_governing_source_degrades_later(
    env, permitted_asset
):
    """AC-2's sharpest sentence, and AC-4's real test: 'cannot be published
    if any source is not PERMITTED' has to hold even when the source was
    PERMITTED at render time and only degraded afterwards — otherwise a
    render taken before a re-grade is a permanent loophole. Proven by
    RE-GRADING the source AFTER a successful render and showing the render's
    OWN effective_verdict moves with it, live, through the existing ancestry
    chain (rights.ancestry() / effective_verdict()) — not by re-checking a
    value frozen at render time."""
    cfg, ctx = env
    pid = invoke(ctx, "create-project", {"title": "P"})["project_id"]
    invoke(ctx, "set-edl", {"project_id": pid, "edl": an_edl(permitted_asset)})
    result = invoke(ctx, "render-project", {"project_id": pid, "quality": "fast"})
    render_asset_id = result["rights"]["asset_id"]

    op_ctx = Context(config=cfg, conn=ctx.conn, principal=operator("op"))
    account = invoke(op_ctx, "connect-account", {"platform": "x", "handle": "me", "secret": "t"})
    post = invoke(ctx, "queue-post", {
        "account_id": account["account_id"], "asset_id": render_asset_id, "body": "hello",
    })

    _block(ctx, permitted_asset)  # the SOURCE degrades after the render exists

    assert rights_layer.effective_verdict(ctx, render_asset_id)["verdict"] == "BLOCKED", (
        "the render's effective verdict must move with its governing source"
    )
    with pytest.raises(RightsBlocked) as excinfo:
        invoke(op_ctx, "approve-post", {"post_id": post["post_id"]})
    assert excinfo.value.detail["verdict"] == "BLOCKED"


@needs_ffmpeg
def test_render_registers_a_derivative_with_the_governing_source_as_parent(
    env, permitted_asset
):
    """AC-4's literal wording: the render is a real derivative row, not a
    side-channel record — same shape as any other derived asset."""
    cfg, ctx = env
    pid = invoke(ctx, "create-project", {"title": "P"})["project_id"]
    invoke(ctx, "set-edl", {"project_id": pid, "edl": an_edl(permitted_asset)})
    result = invoke(ctx, "render-project", {"project_id": pid, "quality": "fast"})
    render_asset_id = result["rights"]["asset_id"]

    row = ctx.conn.execute(
        "SELECT derived_from, state, content_hash FROM assets WHERE id = ?", (render_asset_id,)
    ).fetchone()
    assert row is not None, "a render must leave a real row in the assets table (AC-4)"
    assert row["derived_from"] == permitted_asset
    assert row["state"] == "stored"
    assert permitted_asset in rights_layer.ancestry(ctx, render_asset_id)
    # Evidence recorded for the full source list, not just the governing one.
    sources_recorded = ctx.conn.execute(
        "SELECT body FROM evidence WHERE asset_id = ? AND kind = 'render_source'",
        (render_asset_id,),
    ).fetchall()
    assert len(sources_recorded) == 1  # one clip, one distinct source


@needs_ffmpeg
def test_deleting_a_render_marks_its_derivative_asset_deleted(env, permitted_asset):
    """The reverse consistency check: once the bytes are gone,
    media_state()/media_available() must say so for the derivative asset too
    — otherwise a post already queued against it would pass the availability
    gate for media that no longer exists (the same phantom-asset hazard T-029
    closed for ingested masters)."""
    cfg, ctx = env
    pid = invoke(ctx, "create-project", {"title": "P"})["project_id"]
    invoke(ctx, "set-edl", {"project_id": pid, "edl": an_edl(permitted_asset)})
    result = invoke(ctx, "render-project", {"project_id": pid, "quality": "fast"})
    render_asset_id = result["rights"]["asset_id"]

    invoke(ctx, "delete-render", {"project_id": pid, "render_id": result["render_id"]})

    assert rights_layer.media_state(ctx, render_asset_id) == "deleted"


def test_register_render_asset_deduplicates_identical_content(env, permitted_asset, tmp_path):
    """Direct unit test of the dedup branch, independent of whether two real
    ffmpeg renders ever happen to produce byte-identical output: two renders
    of literally the same bytes must not collide on assets.content_hash
    (UNIQUE) and must not silently duplicate a derivative asset."""
    cfg, ctx = env
    from promedia.core.db import iso

    output = tmp_path / "same_bytes.mp4"
    output.write_bytes(b"identical rendered bytes for the dedup test")
    verdict = {"verdict": "PERMITTED", "evaluated_source": permitted_asset, "source_asset": permitted_asset}
    from promedia.core.media.edl import EDL, Clip

    document = EDL(clips=[Clip(asset_id=permitted_asset, start=0, end=1)])
    at = iso()

    first_id = projects_layer._register_render_asset(
        ctx, render_id="rnd_dup_a", project_title="P", document=document,
        output_path=output, result={"byte_size": len(output.read_bytes())}, verdict=verdict, at=at,
    )
    second_id = projects_layer._register_render_asset(
        ctx, render_id="rnd_dup_b", project_title="P", document=document,
        output_path=output, result={"byte_size": len(output.read_bytes())}, verdict=verdict, at=at,
    )

    assert first_id == "rnd_dup_a"
    assert second_id == "rnd_dup_a", "identical bytes must resolve to the FIRST registered asset"
    assert ctx.conn.execute(
        "SELECT COUNT(*) AS n FROM assets WHERE id = 'rnd_dup_b'"
    ).fetchone()["n"] == 0


# --- AC-3: absent or deleted media raises MediaUnavailable, not a partial file


def test_rendering_deleted_media_raises_media_unavailable(env, permitted_asset):
    cfg, ctx = env
    pid = invoke(ctx, "create-project", {"title": "P"})["project_id"]
    invoke(ctx, "set-edl", {"project_id": pid, "edl": an_edl(permitted_asset)})
    ctx.conn.execute(
        "UPDATE assets SET state = 'deleted', object_path = NULL WHERE id = ?",
        (permitted_asset,),
    )

    with pytest.raises(MediaUnavailable) as excinfo:
        invoke(ctx, "render-project", {"project_id": pid})
    assert excinfo.value.detail["asset_id"] == permitted_asset


def test_rights_are_checked_before_media_availability(env, permitted_asset):
    """Ordering, proven directly: an asset that is BOTH blocked and deleted
    must fail with RightsBlocked, never MediaUnavailable — a legal refusal
    must never be masked by a coincidental logistical one on the same call."""
    cfg, ctx = env
    _block(ctx, permitted_asset)
    ctx.conn.execute(
        "UPDATE assets SET state = 'deleted', object_path = NULL WHERE id = ?",
        (permitted_asset,),
    )
    pid = invoke(ctx, "create-project", {"title": "P"})["project_id"]
    invoke(ctx, "set-edl", {"project_id": pid, "edl": an_edl(permitted_asset)})

    with pytest.raises(RightsBlocked):
        invoke(ctx, "render-project", {"project_id": pid})
