"""T-053/T-055 — the Pro Media v2 rich client's backend surface.

Two things, deliberately separate: serving the built SPA at /studio (a static
bundle plus a catch-all for client-side routing), and the one genuinely new
route this phase added, /media/{asset_id}/file — needed for the editor's
source monitor, and security-sensitive for the same reason render_file
already is (T-049): the path must come from the database, never the URL.

The SPA itself calls only /api/op/* and /api/ops, which tests/test_parity.py
and tests/test_ops_forms.py already cover exhaustively; this file does not
re-test that surface, only the two things this phase actually added.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from promedia.core.registry import invoke
from tests.conftest import attest
from tests.test_ops_forms import agent_client, env, ingest_as_agent, operator_client
from tests.test_projects import real_media

__all__ = ["env"]

FRONTEND_DIST = Path(__file__).resolve().parents[1] / "promedia" / "web" / "frontend" / "dist"


# --- /studio: serving the built SPA ---------------------------------------------


@pytest.mark.skipif(not (FRONTEND_DIST / "index.html").is_file(),
                     reason="frontend not built (npm run build) in this environment")
def test_studio_serves_the_built_shell(env):
    cfg, ctx, store = env
    response = agent_client(cfg, store).get("/studio")
    assert response.status_code == 200
    assert '<div id="app">' in response.text
    assert "/studio/assets/" in response.text


@pytest.mark.skipif(not (FRONTEND_DIST / "index.html").is_file(),
                     reason="frontend not built (npm run build) in this environment")
def test_studio_deep_paths_serve_the_same_shell(env):
    """The SPA owns client-side routing; any /studio/* path must reach it
    rather than 404ing, or a page refresh on e.g. /studio/projects would
    break — the exact 'no dead links' rule T-053's AC-2 pins for the menu."""
    cfg, ctx, store = env
    direct = agent_client(cfg, store).get("/studio")
    deep = agent_client(cfg, store).get("/studio/projects/some-project-id")
    assert deep.status_code == 200
    assert deep.text == direct.text


@pytest.mark.skipif(not (FRONTEND_DIST / "index.html").is_file(),
                     reason="frontend not built (npm run build) in this environment")
def test_studio_calendar_route_serves_the_same_shell(env):
    """T-070: /calendar is a client-only route (DR-022, no new backend
    surface) — this pins that it actually reaches the SPA shell rather than
    404ing, the same 'no dead links' guarantee as the projects deep path
    above."""
    cfg, ctx, store = env
    direct = agent_client(cfg, store).get("/studio")
    calendar = agent_client(cfg, store).get("/studio/calendar")
    assert calendar.status_code == 200
    assert calendar.text == direct.text


@pytest.mark.skipif(not (FRONTEND_DIST / "index.html").is_file(),
                     reason="frontend not built (npm run build) in this environment")
def test_studio_serves_the_real_built_assets(env):
    cfg, ctx, store = env
    shell = agent_client(cfg, store).get("/studio")
    # Extract one real asset path straight out of the served shell rather
    # than hardcoding a hashed filename that changes on every build.
    import re

    match = re.search(r'/studio/assets/[\w.\-]+\.js', shell.text)
    assert match, "the built shell references no JS asset under /studio/assets/"
    asset_response = agent_client(cfg, store).get(match.group(0))
    assert asset_response.status_code == 200


@pytest.mark.skipif(not (FRONTEND_DIST / "index.html").is_file(),
                     reason="frontend not built (npm run build) in this environment")
def test_studio_token_bootstrap_grants_operator_authority(env):
    """Mirrors '/'s exact bootstrap (T-053 AC-1): the SAME operator-token
    cookie, no second auth mechanism. Verified by checking the principal the
    server actually resolves afterwards, not by trusting the redirect alone."""
    cfg, ctx, store = env
    client = agent_client(cfg, store, follow_redirects=False)
    bootstrap = client.get(f"/studio?token={store.operator_token()}")
    assert bootstrap.status_code == 303
    assert bootstrap.headers["location"] == "/studio"

    status = client.post("/api/op/status", json={})
    assert status.status_code == 200
    assert status.json()["principal"]["kind"] == "operator"


def test_studio_without_a_build_reports_not_built_rather_than_crashing(env, monkeypatch):
    """Sabotage-style: forces the 'not built' branch regardless of this
    environment's actual state, so the fallback itself is verified rather
    than only ever exercised by accident on a machine with no build yet."""
    import promedia.web.app as app_module

    cfg, ctx, store = env
    original = Path.is_file

    def fake_is_file(self):
        if self.name == "index.html" and "frontend" in self.parts:
            return False
        return original(self)

    monkeypatch.setattr(Path, "is_file", fake_is_file)
    from promedia.web.app import create_app
    from fastapi.testclient import TestClient

    app = create_app(cfg, store=store)
    client = TestClient(app)
    response = client.get("/studio")
    assert response.status_code == 503
    assert response.json()["error"] == "NOT_BUILT"


# --- /media/{asset_id}/file: the new source-preview route ----------------------


def test_media_file_serves_a_stored_assets_bytes(env, real_media):
    cfg, ctx, store = env
    asset_id = ingest_as_agent(ctx, real_media)
    response = agent_client(cfg, store).get(f"/media/{asset_id}/file")
    assert response.status_code == 200
    assert int(response.headers["content-length"]) == real_media.stat().st_size


def test_media_file_refuses_when_media_is_not_stored(env, real_media):
    cfg, ctx, store = env
    asset_id = ingest_as_agent(ctx, real_media)
    ctx.conn.execute("UPDATE assets SET state = 'deleted' WHERE id = ?", (asset_id,))
    ctx.conn.commit()
    response = agent_client(cfg, store).get(f"/media/{asset_id}/file")
    assert response.status_code == 400  # MediaUnavailable's default mapping
    assert "MEDIA_UNAVAILABLE" in response.text


def test_media_file_404s_for_an_unknown_asset(env):
    cfg, ctx, store = env
    response = agent_client(cfg, store).get("/media/as_does_not_exist/file")
    assert response.status_code == 404


def test_media_file_path_comes_from_the_database_not_the_url(env, real_media):
    """The directory-traversal check render_file already had (T-049): the
    route takes only an id, and the served path is whatever the asset row
    actually records — a request cannot smuggle a different path in."""
    cfg, ctx, store = env
    asset_id = ingest_as_agent(ctx, real_media)
    # No route accepts a path segment at all; the only way to reach a
    # DIFFERENT file would be forging the id, which resolves through the
    # `asset` operation exactly like every other reader of this table.
    response = agent_client(cfg, store).get(f"/media/{asset_id}/../../../../etc/passwd")
    # Starlette normalises the path before routing; this either 404s (no
    # such route) or resolves back to the same, legitimate asset file.
    assert response.status_code in (200, 404)
    if response.status_code == 200:
        assert int(response.headers["content-length"]) == real_media.stat().st_size


# --- T-056: diff-project-versions, and reject-as-a-new-version -----------------
#
# "Accept" needs no operation of its own — the newer version is already
# current the moment set-edl wrote it, which is what test_reject_appends_a_new_version
# _equal_to_the_earlier_one below proves by contrast: THAT is the one that
# actually has to write something.


def _project_at(ctx, title="P"):
    from promedia.core.registry import Context as _Context
    from promedia.core.principal import agent as _agent

    as_agent = _Context(config=ctx.config, conn=ctx.conn, principal=_agent("diff-test-agent"))
    return as_agent, invoke(as_agent, "create-project", {"title": title})["project_id"]


def test_diff_names_a_clip_added_a_trim_and_an_effect_change(env):
    cfg, ctx, store = env
    as_agent, pid = _project_at(ctx)
    invoke(as_agent, "set-edl", {
        "project_id": pid,
        "edl": {"aspect": "landscape", "clips": [{"asset_id": "as_one", "start": 0, "end": 5}]},
    })
    invoke(as_agent, "set-edl", {
        "project_id": pid,
        "edl": {"aspect": "landscape", "clips": [
            {"asset_id": "as_one", "start": 0, "end": 5, "effect": "grayscale"},
            {"asset_id": "as_two", "start": 0, "end": 3},
        ]},
    })

    diff = invoke(as_agent, "diff-project-versions", {"project_id": pid, "from_version": 2, "to_version": 3})
    assert diff["identical"] is False
    kinds = {c["kind"] for c in diff["changes"]}
    assert "clip_effect_changed" in kinds
    assert "clip_added" in kinds
    assert any(c["kind"] == "clip_added" and c["asset_id"] == "as_two" for c in diff["changes"])


def test_diff_reports_seconds_trimmed_using_the_assets_real_probed_duration(env, real_media):
    """Not an invented number: the before/after seconds come from the same
    duration_seconds ffprobe recorded at ingest (A-15), the same source
    render's own storage projection reads (T-043)."""
    cfg, ctx, store = env
    asset_id = ingest_as_agent(ctx, real_media)
    as_agent, pid = _project_at(ctx)
    invoke(as_agent, "set-edl", {
        "project_id": pid,
        "edl": {"aspect": "landscape", "clips": [{"asset_id": asset_id, "start": 0, "end": 1}]},
    })
    invoke(as_agent, "set-edl", {
        "project_id": pid,
        "edl": {"aspect": "landscape", "clips": [{"asset_id": asset_id, "start": 0, "end": 3}]},
    })

    diff = invoke(as_agent, "diff-project-versions", {"project_id": pid, "from_version": 2, "to_version": 3})
    trims = [c for c in diff["changes"] if c["kind"] == "clip_trimmed"]
    assert len(trims) == 1
    assert trims[0]["before_seconds"] == pytest.approx(1.0, abs=0.01)
    assert trims[0]["after_seconds"] == pytest.approx(3.0, abs=0.01)
    assert "lengthened" in trims[0]["detail"]


def test_diff_names_removed_clips_captions_and_audio_tracks(env):
    cfg, ctx, store = env
    as_agent, pid = _project_at(ctx)
    invoke(as_agent, "set-edl", {
        "project_id": pid,
        "edl": {
            "aspect": "landscape",
            "clips": [
                {"asset_id": "as_one", "start": 0, "end": 5},
                {"asset_id": "as_two", "start": 0, "end": 5},
            ],
            "text": [{"text": "hello", "start": 0, "end": 2}],
            "audio": [{"asset_id": "as_music", "start": 0}],
        },
    })
    invoke(as_agent, "set-edl", {
        "project_id": pid,
        "edl": {"aspect": "landscape", "clips": [{"asset_id": "as_one", "start": 0, "end": 5}]},
    })

    diff = invoke(as_agent, "diff-project-versions", {"project_id": pid, "from_version": 2, "to_version": 3})
    kinds = {c["kind"] for c in diff["changes"]}
    assert "clip_removed" in kinds
    assert "caption_removed" in kinds
    assert "audio_track_removed" in kinds


def test_diff_reports_a_pure_reorder_as_moved_not_as_removed_and_added(env):
    """Independent-review finding (T-056): difflib's raw opcodes alone read a
    clip that only changed position as 'removed, then a different one
    added' — an actively false story about footage that never left the
    timeline. AC-1 names 'reordered' explicitly; this is the case that must
    not silently become a fabricated delete+insert pair."""
    cfg, ctx, store = env
    as_agent, pid = _project_at(ctx)
    invoke(as_agent, "set-edl", {
        "project_id": pid,
        "edl": {"aspect": "landscape", "clips": [
            {"asset_id": "as_one", "start": 0, "end": 5},
            {"asset_id": "as_two", "start": 0, "end": 5},
            {"asset_id": "as_three", "start": 0, "end": 5},
        ]},
    })
    invoke(as_agent, "set-edl", {  # as_three moved from last to first; nothing else changed
        "project_id": pid,
        "edl": {"aspect": "landscape", "clips": [
            {"asset_id": "as_three", "start": 0, "end": 5},
            {"asset_id": "as_one", "start": 0, "end": 5},
            {"asset_id": "as_two", "start": 0, "end": 5},
        ]},
    })

    diff = invoke(as_agent, "diff-project-versions", {"project_id": pid, "from_version": 2, "to_version": 3})
    assert diff["changes"] == [{
        "kind": "clip_reordered", "asset_id": "as_three",
        "from_position": 3, "to_position": 1,
        "detail": "clip (as_three) moved from position 3 to position 1",
    }]
    kinds = {c["kind"] for c in diff["changes"]}
    assert "clip_removed" not in kinds
    assert "clip_added" not in kinds


def test_diff_does_not_claim_a_reorder_when_the_moved_clip_also_changed(env):
    """A clip that both moved AND was re-trimmed is not honestly a pure
    'reorder' — it is deliberately left as remove+add rather than a move
    that silently hides a content change too."""
    cfg, ctx, store = env
    as_agent, pid = _project_at(ctx)
    invoke(as_agent, "set-edl", {
        "project_id": pid,
        "edl": {"aspect": "landscape", "clips": [
            {"asset_id": "as_one", "start": 0, "end": 5},
            {"asset_id": "as_two", "start": 0, "end": 5},
        ]},
    })
    invoke(as_agent, "set-edl", {  # as_two moved AND its end point changed
        "project_id": pid,
        "edl": {"aspect": "landscape", "clips": [
            {"asset_id": "as_two", "start": 0, "end": 9},
            {"asset_id": "as_one", "start": 0, "end": 5},
        ]},
    })

    diff = invoke(as_agent, "diff-project-versions", {"project_id": pid, "from_version": 2, "to_version": 3})
    kinds = {c["kind"] for c in diff["changes"]}
    assert "clip_reordered" not in kinds
    assert "clip_removed" in kinds
    assert "clip_added" in kinds


def test_diff_names_a_transition_duration_change_without_the_dissolve_dissolve_bug(env):
    """Independent-review finding (T-056): before the fix, changing ONLY
    transition_duration (transition_in unchanged) reported 'transition
    changed from dissolve to dissolve' — before/after named the wrong
    field."""
    cfg, ctx, store = env
    as_agent, pid = _project_at(ctx)
    invoke(as_agent, "set-edl", {
        "project_id": pid,
        "edl": {"aspect": "landscape", "clips": [
            {"asset_id": "as_one", "start": 0, "end": 5},
            {"asset_id": "as_two", "start": 0, "end": 5,
             "transition_in": "dissolve", "transition_duration": 0.5},
        ]},
    })
    invoke(as_agent, "set-edl", {
        "project_id": pid,
        "edl": {"aspect": "landscape", "clips": [
            {"asset_id": "as_one", "start": 0, "end": 5},
            {"asset_id": "as_two", "start": 0, "end": 5,
             "transition_in": "dissolve", "transition_duration": 1.5},
        ]},
    })

    diff = invoke(as_agent, "diff-project-versions", {"project_id": pid, "from_version": 2, "to_version": 3})
    duration_changes = [c for c in diff["changes"] if c["kind"] == "clip_transition_duration_changed"]
    assert len(duration_changes) == 1
    assert duration_changes[0]["before"] == 0.5
    assert duration_changes[0]["after"] == 1.5
    assert "dissolve' transition duration changed from 0.5s to 1.5s" in duration_changes[0]["detail"]
    assert not any(c["kind"] == "clip_transition_changed" for c in diff["changes"])


def test_diff_names_an_aspect_change(env):
    cfg, ctx, store = env
    as_agent, pid = _project_at(ctx)
    invoke(as_agent, "set-edl", {
        "project_id": pid,
        "edl": {"aspect": "landscape", "clips": [{"asset_id": "as_one", "start": 0, "end": 5}]},
    })
    invoke(as_agent, "set-edl", {
        "project_id": pid,
        "edl": {"aspect": "vertical", "clips": [{"asset_id": "as_one", "start": 0, "end": 5}]},
    })

    diff = invoke(as_agent, "diff-project-versions", {"project_id": pid, "from_version": 2, "to_version": 3})
    aspect_changes = [c for c in diff["changes"] if c["kind"] == "aspect_changed"]
    assert aspect_changes == [
        {"kind": "aspect_changed", "before": "landscape", "after": "vertical",
         "detail": "output frame changed from 'landscape' to 'vertical'"}
    ]


def test_diff_between_a_version_and_itself_is_identical(env):
    cfg, ctx, store = env
    as_agent, pid = _project_at(ctx)
    invoke(as_agent, "set-edl", {
        "project_id": pid,
        "edl": {"aspect": "landscape", "clips": [{"asset_id": "as_one", "start": 0, "end": 5}]},
    })
    diff = invoke(as_agent, "diff-project-versions", {"project_id": pid, "from_version": 2, "to_version": 2})
    assert diff == {
        "ok": True, "project_id": pid, "from_version": 2, "to_version": 2,
        "changes": [], "count": 0, "identical": True,
    }


def test_diff_of_an_unknown_version_is_not_found(env):
    cfg, ctx, store = env
    as_agent, pid = _project_at(ctx)
    with pytest.raises(Exception) as excinfo:
        invoke(as_agent, "diff-project-versions", {"project_id": pid, "from_version": 1, "to_version": 99})
    assert "NotFound" in type(excinfo.value).__name__ or "no version" in str(excinfo.value)


def test_diff_project_versions_is_read_only_and_agent_callable(env):
    """No lock is taken (it declares no entity) and an agent principal (not
    just the operator) can call it — reviewing a diff is not itself drafting,
    editing, or publishing (F-2)."""
    from promedia.core.registry import load_operations

    op = load_operations()["diff-project-versions"]
    assert op.mutates is False
    assert op.entity is None
    assert op.authority == "agent"


def test_reject_appends_a_new_version_equal_to_the_earlier_one(env):
    """T-056 AC-2: reject is not a delete and not a mutation of history — it
    is an ordinary set-edl call carrying the OLDER version's own document,
    pinned to the current version so it cannot silently clobber a third
    version that landed in between (R-010). Proven by reading the resulting
    version back and comparing it to the original, not by trusting the call
    succeeded."""
    cfg, ctx, store = env
    as_agent, pid = _project_at(ctx)
    original_edl = {"aspect": "landscape", "clips": [{"asset_id": "as_one", "start": 0, "end": 5}]}
    invoke(as_agent, "set-edl", {"project_id": pid, "edl": original_edl})  # v2
    invoke(as_agent, "set-edl", {  # v3, the one to reject
        "project_id": pid,
        "edl": {"aspect": "landscape", "clips": [
            {"asset_id": "as_one", "start": 0, "end": 5},
            {"asset_id": "as_two", "start": 0, "end": 5},
        ]},
    })

    v2 = invoke(as_agent, "project", {"project_id": pid, "version": 2})
    reject = invoke(as_agent, "set-edl", {
        "project_id": pid, "edl": v2["edl"], "note": "rejected v3", "expected_version": 3,
    })
    assert reject["edl_version"] == 4
    assert reject["previous_version"] == 3

    restored = invoke(as_agent, "project", {"project_id": pid, "version": 4})
    assert restored["edl"] == v2["edl"]
    # History is append-only: v3 (the rejected version) still reads back
    # unchanged, exactly as T-042's append-only guarantee requires.
    v3 = invoke(as_agent, "project", {"project_id": pid, "version": 3})
    assert len(v3["edl"]["clips"]) == 2

    history = invoke(as_agent, "project-versions", {"project_id": pid})
    assert history["count"] == 4
