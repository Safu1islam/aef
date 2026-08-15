"""T-051/T-052, plus the T-049 test debt this repo's own finding named.

T-049 shipped /projects, /projects/{id} and /renders/{id}/file with ZERO test
coverage — verified only against a running server by hand. That gap is closed
here first, then T-051's structured clip editor and T-052's dashboard rework
build on top of it.
"""

from __future__ import annotations

import json

import pytest

from promedia.core.registry import invoke
from tests.conftest import attest
from tests.test_ops_forms import agent_client, env, ingest_as_agent, operator_client
from tests.test_projects import real_media

__all__ = ["env"]


def _create_project(client, title="A project") -> str:
    response = client.post("/projects", data={"title": title})
    return response.headers["location"].rsplit("/", 1)[-1]


# --- T-049 gap: /projects, /projects/{id} ---------------------------------------


def test_projects_index_lists_created_projects(env):
    cfg, ctx, store = env
    invoke(ctx, "create-project", {"title": "Existing"})
    response = agent_client(cfg, store).get("/projects")
    assert response.status_code == 200
    assert "Existing" in response.text
    assert "<script" not in response.text


def test_creating_a_project_redirects_to_it(env):
    cfg, ctx, store = env
    response = agent_client(cfg, store, follow_redirects=False).post(
        "/projects", data={"title": "New one"}
    )
    assert response.status_code == 303
    project_id = response.headers["location"].rsplit("/", 1)[-1]
    assert invoke(ctx, "project", {"project_id": project_id})["title"] == "New one"


def test_project_detail_shows_the_edit_and_media_list(env, media_file):
    cfg, ctx, store = env
    asset_id = ingest_as_agent(ctx, media_file)
    project_id = _create_project(agent_client(cfg, store, follow_redirects=False))
    response = agent_client(cfg, store).get(f"/projects/{project_id}")
    assert response.status_code == 200
    assert asset_id in response.text
    assert "<script" not in response.text


def test_project_json_edl_route_still_works(env, media_file):
    """The escape hatch the brief requires be kept, verified end to end."""
    cfg, ctx, store = env
    asset_id = ingest_as_agent(ctx, media_file)
    project_id = _create_project(agent_client(cfg, store, follow_redirects=False))
    edl = json.dumps({
        "aspect": "landscape",
        "clips": [{"asset_id": asset_id, "start": 0, "end": 1}],
        "text": [], "audio": [],
    })
    response = agent_client(cfg, store, follow_redirects=False).post(
        f"/projects/{project_id}/edl", data={"edl": edl, "note": "via JSON"}
    )
    assert response.status_code == 303
    current = invoke(ctx, "project", {"project_id": project_id})
    assert current["edl_version"] == 2
    assert current["edl"]["clips"][0]["asset_id"] == asset_id


# --- the cross-origin fix (finding, fixed while touching this file) ------------


def test_project_mutations_refuse_a_foreign_origin(env, media_file):
    """The finding: create-project/set-edl/render-project are agent-authority,
    so none of them needed the operator's cookie to run — a page on another
    origin could have auto-submitted a form with no authentication at all
    before this was routed through guarded()."""
    cfg, ctx, store = env
    client = agent_client(cfg, store, follow_redirects=False)
    response = client.post(
        "/projects", data={"title": "forged"}, headers={"Origin": "http://evil.example"}
    )
    assert response.status_code == 403
    assert invoke(ctx, "list-projects", {})["count"] == 0


def test_set_edl_also_refuses_a_foreign_origin(env, media_file):
    cfg, ctx, store = env
    project_id = _create_project(agent_client(cfg, store, follow_redirects=False))
    before = invoke(ctx, "project", {"project_id": project_id})["edl_version"]
    client = agent_client(cfg, store, follow_redirects=False)
    response = client.post(
        f"/projects/{project_id}/edl",
        data={"edl": "{}", "note": "forged"},
        headers={"Origin": "http://evil.example"},
    )
    assert response.status_code == 403
    assert invoke(ctx, "project", {"project_id": project_id})["edl_version"] == before


# --- T-051: structured per-clip editing -----------------------------------------


def _clip_form(index, asset_id, **overrides):
    # index is the numeric slot for an existing clip (0, 1, ...) or the
    # literal string "new" for the trailing add-a-clip row — position only
    # makes sense for the former, and defaults to "last" for a new clip.
    default_position = str(index + 1) if isinstance(index, int) else "99"
    base = {
        f"clip-{index}-asset_id": asset_id,
        f"clip-{index}-start": "0",
        f"clip-{index}-end": "",
        f"clip-{index}-speed": "1",
        f"clip-{index}-effect": "none",
        f"clip-{index}-transition_in": "cut",
        f"clip-{index}-transition_duration": "0.5",
        f"clip-{index}-volume": "1",
        f"clip-{index}-position": default_position,
    }
    base.update({f"clip-{index}-{k}": v for k, v in overrides.items()})
    return base


def test_adding_a_clip_through_the_new_row(env, media_file):
    """AC-1: a clip added without touching JSON."""
    cfg, ctx, store = env
    asset_id = ingest_as_agent(ctx, media_file)
    project_id = _create_project(agent_client(cfg, store, follow_redirects=False))

    form = _clip_form("new", asset_id, start="1", end="4")
    response = agent_client(cfg, store, follow_redirects=False).post(
        f"/projects/{project_id}/clips", data=form
    )
    assert response.status_code == 303
    current = invoke(ctx, "project", {"project_id": project_id})
    assert len(current["edl"]["clips"]) == 1
    assert current["edl"]["clips"][0]["start"] == 1.0
    assert current["edl"]["clips"][0]["end"] == 4.0


def test_trimming_an_existing_clip(env, media_file):
    cfg, ctx, store = env
    asset_id = ingest_as_agent(ctx, media_file)
    project_id = _create_project(agent_client(cfg, store, follow_redirects=False))
    invoke(ctx, "set-edl", {
        "project_id": project_id,
        "edl": {"aspect": "landscape", "clips": [{"asset_id": asset_id, "start": 0, "end": 10}]},
    })

    form = _clip_form(0, asset_id, start="2", end="8", speed="1.5")
    response = agent_client(cfg, store, follow_redirects=False).post(
        f"/projects/{project_id}/clips", data=form
    )
    assert response.status_code == 303
    clip = invoke(ctx, "project", {"project_id": project_id})["edl"]["clips"][0]
    assert clip["start"] == 2.0
    assert clip["end"] == 8.0
    assert clip["speed"] == 1.5


def test_effect_and_transition_changes_apply(env, media_file):
    cfg, ctx, store = env
    asset_id = ingest_as_agent(ctx, media_file)
    project_id = _create_project(agent_client(cfg, store, follow_redirects=False))
    invoke(ctx, "set-edl", {
        "project_id": project_id,
        "edl": {"aspect": "landscape", "clips": [{"asset_id": asset_id, "start": 0, "end": 5}]},
    })

    form = _clip_form(0, asset_id, effect="grayscale", transition_in="fade")
    agent_client(cfg, store, follow_redirects=False).post(
        f"/projects/{project_id}/clips", data=form
    )
    clip = invoke(ctx, "project", {"project_id": project_id})["edl"]["clips"][0]
    assert clip["effect"] == "grayscale"
    assert clip["transition_in"] == "fade"


def test_removing_a_clip_via_the_checkbox(env, media_file):
    cfg, ctx, store = env
    asset_id = ingest_as_agent(ctx, media_file)
    project_id = _create_project(agent_client(cfg, store, follow_redirects=False))
    invoke(ctx, "set-edl", {
        "project_id": project_id,
        "edl": {"aspect": "landscape", "clips": [
            {"asset_id": asset_id, "start": 0, "end": 2},
            {"asset_id": asset_id, "start": 2, "end": 4},
        ]},
    })

    form = {}
    form.update(_clip_form(0, asset_id, start="0", end="2"))
    form[f"clip-0-remove"] = "true"
    form.update(_clip_form(1, asset_id, start="2", end="4"))
    response = agent_client(cfg, store, follow_redirects=False).post(
        f"/projects/{project_id}/clips", data=form
    )
    assert response.status_code == 303
    clips = invoke(ctx, "project", {"project_id": project_id})["edl"]["clips"]
    assert len(clips) == 1
    assert clips[0]["start"] == 2.0


def test_reordering_via_the_position_field(env, media_file):
    """AC-1's reorder requirement. Two clips, positions swapped."""
    cfg, ctx, store = env
    asset_id = ingest_as_agent(ctx, media_file)
    project_id = _create_project(agent_client(cfg, store, follow_redirects=False))
    invoke(ctx, "set-edl", {
        "project_id": project_id,
        "edl": {"aspect": "landscape", "clips": [
            {"asset_id": asset_id, "start": 0, "end": 1},   # will become 2nd
            {"asset_id": asset_id, "start": 5, "end": 6},   # will become 1st
        ]},
    })

    form = {}
    form.update(_clip_form(0, asset_id, start="0", end="1", position="2"))
    form.update(_clip_form(1, asset_id, start="5", end="6", position="1"))
    agent_client(cfg, store, follow_redirects=False).post(
        f"/projects/{project_id}/clips", data=form
    )
    clips = invoke(ctx, "project", {"project_id": project_id})["edl"]["clips"]
    assert [c["start"] for c in clips] == [5.0, 0.0]


def test_transition_duration_round_trips_even_though_it_has_no_column(env, media_file):
    """The bug this guards against: transition_duration is preserved via a
    hidden field rather than a visible column, and without it every save
    would silently reset it to the schema default (0.5), overwriting
    whatever an agent had set — a loss nothing would report."""
    cfg, ctx, store = env
    asset_id = ingest_as_agent(ctx, media_file)
    project_id = _create_project(agent_client(cfg, store, follow_redirects=False))
    invoke(ctx, "set-edl", {
        "project_id": project_id,
        "edl": {"aspect": "landscape", "clips": [
            {"asset_id": asset_id, "start": 0, "end": 5, "transition_duration": 2.25},
        ]},
    })
    page = agent_client(cfg, store).get(f"/projects/{project_id}")
    assert 'name="clip-0-transition_duration" value="2.25"' in page.text

    form = _clip_form(0, asset_id, start="0", end="5", transition_duration="2.25")
    agent_client(cfg, store, follow_redirects=False).post(
        f"/projects/{project_id}/clips", data=form
    )
    clip = invoke(ctx, "project", {"project_id": project_id})["edl"]["clips"][0]
    assert clip["transition_duration"] == 2.25


def test_clip_editing_creates_a_new_version_authored_by_the_operator(env, media_file):
    """AC-2. authored_kind must reflect who actually made the edit."""
    cfg, ctx, store = env
    asset_id = ingest_as_agent(ctx, media_file)
    project_id = _create_project(agent_client(cfg, store, follow_redirects=False))
    before_version = invoke(ctx, "project", {"project_id": project_id})["edl_version"]

    form = _clip_form("new", asset_id)
    operator_client(cfg, store, follow_redirects=False).post(
        f"/projects/{project_id}/clips", data=form
    )
    history = invoke(ctx, "project-versions", {"project_id": project_id})["versions"]
    latest = history[0]
    assert latest["version"] == before_version + 1
    assert latest["authored_kind"] == "operator"

    # And the previous version is still readable, unmutated.
    earlier = invoke(ctx, "project", {"project_id": project_id, "version": before_version})
    assert earlier["edl"]["clips"] == []


def test_saving_with_every_clip_removed_and_no_new_one_is_refused(env, media_file):
    cfg, ctx, store = env
    asset_id = ingest_as_agent(ctx, media_file)
    project_id = _create_project(agent_client(cfg, store, follow_redirects=False))
    invoke(ctx, "set-edl", {
        "project_id": project_id,
        "edl": {"aspect": "landscape", "clips": [{"asset_id": asset_id, "start": 0, "end": 1}]},
    })
    form = _clip_form(0, asset_id, start="0", end="1")
    form["clip-0-remove"] = "true"
    response = agent_client(cfg, store).post(f"/projects/{project_id}/clips", data=form)
    assert response.status_code == 400
    assert len(invoke(ctx, "project", {"project_id": project_id})["edl"]["clips"]) == 1


# --- AC-3: a transition that does not render as named is marked in the picker --


def test_the_transition_picker_marks_known_substitutions(env, media_file):
    """UPDATED FOR T-045 (retires F-003). This used to assert that the picker
    warned 'dissolve renders as fade' on every project page, because F-003
    made that true unconditionally. T-045 gave 'dissolve' (and the rest of
    the vocabulary) a real ffmpeg xfade implementation — independently
    verified by the coordinator per transition, not just read from the diff
    (see tests/test_projects.py's updated render tests and
    tests/test_transitions.py's pixel measurements) — so the picker now has
    nothing to warn about and must not show a warning that is no longer true.
    The sibling test below (test_the_marking_can_actually_go_missing) still
    proves the WARNING MACHINERY itself works, by forcing a substitution back
    in; this test proves it stays silent when there genuinely is none."""
    cfg, ctx, store = env
    project_id = _create_project(agent_client(cfg, store, follow_redirects=False))
    response = agent_client(cfg, store).get(f"/projects/{project_id}")
    assert response.status_code == 200
    # The picker still offers 'dissolve' as a plain, unmarked option — the
    # vocabulary itself is unchanged, only its honesty improved.
    assert "dissolve" in response.text
    assert "renders as fade" not in response.text
    assert "F-003" not in response.text


def test_the_marking_can_actually_go_missing(env, media_file, monkeypatch):
    """Proves the previous test can fail: with known_substitutions emptied,
    the picker would offer 'dissolve' with no warning at all."""
    import promedia.core.projects as projects_layer

    cfg, ctx, store = env
    project_id = _create_project(agent_client(cfg, store, follow_redirects=False))
    original = projects_layer.capabilities

    def _no_warnings(ctx_):
        result = original(ctx_)
        result["known_substitutions"] = []
        return result

    # ops/projects.py's media-capabilities handler calls
    # `projects_layer.capabilities(ctx)` — a dynamic attribute lookup on the
    # SAME module object this import gives us (Python caches modules), so
    # patching it here is visible to that call without touching app.py.
    monkeypatch.setattr(projects_layer, "capabilities", _no_warnings)
    response = agent_client(cfg, store).get(f"/projects/{project_id}")
    assert "renders as fade" not in response.text, (
        "sabotage did not take effect; this test's premise is untrustworthy"
    )


# --- T-052: dashboard, posts, publications, settings ----------------------------


def test_dashboard_leads_with_pending_posts_and_links_to_projects(env, media_file):
    cfg, ctx, store = env
    account = invoke(ctx, "connect-account", {"platform": "x", "handle": "me", "secret": "t"})
    asset_id = ingest_as_agent(ctx, media_file)
    attest(ctx, asset_id)
    invoke(ctx, "seal-provenance", {"asset_id": asset_id})
    post = invoke(ctx, "queue-post", {
        "account_id": account["account_id"], "asset_id": asset_id, "body": "hello",
    })

    response = agent_client(cfg, store).get("/")
    assert response.status_code == 200
    assert post["post_id"] in response.text
    assert 'href="/projects"' in response.text


def test_dashboard_shows_recent_renders(env, real_media):
    # A render actually decodes the source, unlike ingest/rights tests — the
    # conftest media_file fixture's placeholder bytes fail against real
    # ffmpeg (found running this suite after the pivot to the rich client:
    # RenderFailed, not the substitution this test means to exercise).
    # test_projects.py's real_media fixture generates genuine frames instead.
    #
    # SPLIT FROM test_dashboard_shows_recent_renders_and_flags_substitutions
    # (T-045, retires F-003). 'dissolve' used to substitute unconditionally,
    # so a real render with it was enough to prove both halves of the old
    # name at once. It no longer substitutes — independently verified per
    # transition by the coordinator, see test_projects.py and
    # test_transitions.py — so this half now only proves the dashboard lists
    # a real render. The flagging half moved to the test directly below,
    # which proves the WARNING MACHINERY itself still works by forcing a
    # substitution back in, the same technique
    # test_the_marking_can_actually_go_missing already uses for the picker.
    cfg, ctx, store = env
    from promedia.core.media import ffmpeg
    if not ffmpeg.available():
        pytest.skip("ffmpeg not installed on this machine")
    asset_id = ingest_as_agent(ctx, real_media)
    # T-044: render-project now refuses anything short of PERMITTED before it
    # does any work; attest is what a real editing session would have done
    # between ingest and render (also runs determine-rights, tests/conftest.py).
    attest(ctx, asset_id)
    project_id = _create_project(agent_client(cfg, store, follow_redirects=False))
    # No transition here on purpose: a transition_in on a project's very
    # first clip has no predecessor to blend from, and T-045's offset-based
    # composition model refuses it (edl.py, "has no previous clip to
    # transition from") — a rule this single-clip EDL cannot exercise
    # regardless of which transition name was used, before or after T-045.
    invoke(ctx, "set-edl", {
        "project_id": project_id,
        "edl": {"aspect": "landscape",
                "clips": [{"asset_id": asset_id, "start": 0, "end": 1}]},
    })
    result = invoke(ctx, "render-project", {"project_id": project_id})
    assert result["substitutions"] == []

    response = agent_client(cfg, store).get("/")
    assert response.status_code == 200
    assert "substituted" not in response.text


def test_dashboard_flags_a_substitution_if_one_is_ever_recorded(env, real_media):
    """The other half of the split above. No real transition substitutes
    today (F-003 is retired), so this proves the DISPLAY logic directly
    against a persisted render row rather than depending on a live
    substitution that no longer exists — the same reasoning
    test_the_marking_can_actually_go_missing already applies to the picker.
    A future transition added to the EDL vocabulary without a real
    implementation would hit this exact code path."""
    cfg, ctx, store = env
    from promedia.core.media import ffmpeg
    if not ffmpeg.available():
        pytest.skip("ffmpeg not installed on this machine")
    asset_id = ingest_as_agent(ctx, real_media)
    attest(ctx, asset_id)  # T-044: render-project now requires PERMITTED first
    project_id = _create_project(agent_client(cfg, store, follow_redirects=False))
    invoke(ctx, "set-edl", {
        "project_id": project_id,
        "edl": {"aspect": "landscape",
                "clips": [{"asset_id": asset_id, "start": 0, "end": 1}]},
    })
    invoke(ctx, "render-project", {"project_id": project_id})
    import json as _json
    ctx.conn.execute(
        "UPDATE renders SET substitutions = ? WHERE project_id = ?",
        (_json.dumps([{"requested": "dissolve", "rendered": "fade", "fabrication": "F-003"}]),
         project_id),
    )

    response = agent_client(cfg, store).get("/")
    assert response.status_code == 200
    assert "substituted" in response.text


def test_a_simulated_publication_is_marked_on_posts_and_publications_pages(env, media_file):
    """AC-2. The one property this whole task exists to protect: a simulated
    publication (fabrication F-001) must never read as a real one, anywhere."""
    cfg, ctx, store = env
    account = invoke(ctx, "connect-account", {"platform": "x", "handle": "me", "secret": "t"})
    asset_id = ingest_as_agent(ctx, media_file)
    attest(ctx, asset_id)
    invoke(ctx, "seal-provenance", {"asset_id": asset_id})
    post = invoke(ctx, "queue-post", {
        "account_id": account["account_id"], "asset_id": asset_id, "body": "hello",
    })
    invoke(ctx, "approve-post", {"post_id": post["post_id"]})
    invoke(ctx, "publish-post", {"post_id": post["post_id"]})

    posts_page = agent_client(cfg, store).get("/posts")
    assert "SIMULATED" in posts_page.text

    pubs_page = agent_client(cfg, store).get("/publications")
    assert "SIMULATED" in pubs_page.text


def test_the_simulated_marker_can_actually_go_missing(env, media_file, monkeypatch):
    """Proves the previous test can fail: hide the simulated flag inside the
    template context and confirm the marker disappears."""
    cfg, ctx, store = env
    account = invoke(ctx, "connect-account", {"platform": "x", "handle": "me", "secret": "t"})
    asset_id = ingest_as_agent(ctx, media_file)
    attest(ctx, asset_id)
    invoke(ctx, "seal-provenance", {"asset_id": asset_id})
    post = invoke(ctx, "queue-post", {
        "account_id": account["account_id"], "asset_id": asset_id, "body": "hello",
    })
    invoke(ctx, "approve-post", {"post_id": post["post_id"]})
    invoke(ctx, "publish-post", {"post_id": post["post_id"]})

    import promedia.core.posts as posts_layer
    original = posts_layer.listing

    def _hide_simulated(ctx_, status=None):
        result = original(ctx_, status)
        for p in result["posts"]:
            p["simulated"] = None
        return result

    monkeypatch.setattr(posts_layer, "listing", _hide_simulated)
    posts_page = agent_client(cfg, store).get("/posts")
    assert "SIMULATED" not in posts_page.text, (
        "sabotage did not take effect; this test's premise is untrustworthy"
    )


def test_posts_page_filters_by_status(env, media_file):
    cfg, ctx, store = env
    account = invoke(ctx, "connect-account", {"platform": "x", "handle": "me", "secret": "t"})
    asset_id = ingest_as_agent(ctx, media_file)
    post = invoke(ctx, "queue-post", {
        "account_id": account["account_id"], "asset_id": asset_id, "body": "still queued",
    })
    response = agent_client(cfg, store).get("/posts", params={"status": "rejected"})
    assert post["post_id"] not in response.text


def test_settings_page_shows_storage_ruleset_and_accounts(env, media_file):
    cfg, ctx, store = env
    invoke(ctx, "connect-account", {"platform": "linkedin", "handle": "biz", "secret": "t"})
    response = agent_client(cfg, store).get("/settings")
    assert response.status_code == 200
    assert "linkedin" in response.text
    assert "biz" in response.text
    assert "<script" not in response.text


def test_settings_connect_account_requires_operator(env, media_file):
    cfg, ctx, store = env
    response = agent_client(cfg, store, follow_redirects=False).post(
        "/settings/accounts", data={"platform": "x", "handle": "new", "secret": "t"}
    )
    assert response.status_code == 403
    assert invoke(ctx, "list-accounts", {})["count"] == 0


def test_settings_connect_account_succeeds_for_operator(env, media_file):
    cfg, ctx, store = env
    response = operator_client(cfg, store, follow_redirects=False).post(
        "/settings/accounts", data={"platform": "x", "handle": "new", "secret": "t"}
    )
    assert response.status_code == 303
    assert invoke(ctx, "list-accounts", {})["count"] == 1


def test_all_new_pages_carry_no_script_tags(env, media_file):
    cfg, ctx, store = env
    client = agent_client(cfg, store)
    for url in ("/", "/posts", "/publications", "/settings"):
        response = client.get(url)
        assert response.status_code == 200
        assert "<script" not in response.text, f"{url} is not JS-free"
