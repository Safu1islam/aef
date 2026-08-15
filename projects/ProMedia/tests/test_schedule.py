"""T-018 — the publish tick (DR-009).

DR-009's design exists because a hard +/- 5 minute deadline on a desktop that
sleeps is not satisfiable in general. The honest answer is not to try harder; it
is C-27 — a window missed by more than the tolerance is marked ``missed`` and
escalated, and NEVER posted late. A late post is worse than no post, because the
operator scheduled it for a reason they no longer control.

So the assertion that carries this task is the negative one: after the window
closes, the tick must NOT publish. Everything else here supports it.

The tick deliberately owns no publishing logic of its own — it calls
``posts.publish``, which claims transactionally before the external call (B2).
These tests prove the tick INHERITS those guarantees rather than restating them,
because a scheduler with its own copy of the publish path is how the two drift.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from promedia.core import db
from promedia.core.principal import agent, operator
from promedia.core.registry import Context, invoke, load_operations
from promedia.errors import Forbidden
from tests.conftest import attest, declaration_original, declaration_unknown, make_config

OPERATIONS = load_operations()


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("PROMEDIA_CREDENTIAL_STORE", str(tmp_path / "creds.json"))
    cfg = make_config(tmp_path, **{"publishing.allow_simulation": True})
    conn = db.connect(cfg.db_path)
    db.apply_schema(conn)
    yield cfg, Context(config=cfg, conn=conn, principal=operator("op"))
    conn.close()


def _at(ctx, offset_seconds: int) -> str:
    """A timestamp relative to now, as the operator would have scheduled it."""
    return (db.now() + timedelta(seconds=offset_seconds)).isoformat()


def _approved_post(ctx, media_file, *, scheduled_at, body="scheduled", permitted=True):
    account = invoke(
        ctx, "connect-account", {"platform": "x", "handle": "me", "secret": "t"}
    )["account_id"]
    as_agent = Context(config=ctx.config, conn=ctx.conn, principal=agent("ag"))
    asset = invoke(
        as_agent,
        "ingest",
        {
            "source_path": str(media_file),
            "declaration": declaration_original() if permitted else declaration_unknown(),
        },
    )["asset_id"]
    if permitted:
        attest(ctx, asset)
        invoke(ctx, "seal-provenance", {"asset_id": asset})
    else:
        invoke(ctx, "determine-rights", {"asset_id": asset})
    post_id = invoke(
        ctx, "queue-post",
        {"account_id": account, "asset_id": asset, "body": body, "scheduled_at": scheduled_at},
    )["post_id"]
    if permitted:
        invoke(ctx, "approve-post", {"post_id": post_id})
    return post_id


def _status(ctx, post_id):
    return ctx.conn.execute("SELECT status FROM posts WHERE id = ?", (post_id,)).fetchone()[0]


# --- C-27: never post late ----------------------------------------------------


def test_a_missed_window_is_marked_missed_and_not_published(env, media_file):
    """AC-1. The constraint this whole design exists to honour.

    Scheduled an hour ago, tolerance is 300s: the window is long closed. The
    machine was asleep, which is the ordinary case DR-009 accepts it cannot
    prevent. What it must not do is post it now.
    """
    cfg, ctx = env
    post_id = _approved_post(ctx, media_file, scheduled_at=_at(ctx, -3600))

    result = invoke(ctx, "publish-tick", {})

    assert _status(ctx, post_id) == "missed"
    assert invoke(ctx, "publications", {})["count"] == 0, "C-27 violated: posted late"
    assert [m["post_id"] for m in result["missed"]] == [post_id]
    assert result["published"] == []


def test_the_miss_is_escalated_not_just_recorded(env, media_file):
    """C-27 says escalate. An audit entry is the escalation channel that exists.

    Recorded as outcome 'failed' rather than a new 'missed' value: audit_log's
    CHECK admits only allowed/denied/failed, and a missed window is a failure of
    the schedule. The detail is what makes it identifiable.
    """
    cfg, ctx = env
    post_id = _approved_post(ctx, media_file, scheduled_at=_at(ctx, -3600))
    invoke(ctx, "publish-tick", {})

    entries = invoke(ctx, "audit", {"limit": 50})["entries"]
    missed = [e for e in entries if e["detail"] and "MISSED WINDOW" in e["detail"]]
    assert missed, "a missed window left no audit trace"
    assert missed[0]["entity_id"] == post_id
    assert missed[0]["outcome"] == "failed"
    assert "C-27" in missed[0]["detail"]


def test_a_window_just_inside_the_tolerance_still_publishes(env, media_file):
    """The opposite direction: over-strictness would miss windows that were met."""
    cfg, ctx = env
    post_id = _approved_post(ctx, media_file, scheduled_at=_at(ctx, -60))

    result = invoke(ctx, "publish-tick", {})

    assert _status(ctx, post_id) == "published"
    assert [p["post_id"] for p in result["published"]] == [post_id]
    assert result["missed"] == []


def test_the_tolerance_boundary_comes_from_configuration(env, media_file):
    """C-26 / protocol 05: the same post is due or missed depending on config.

    Proven by moving the config rather than the clock, which is what makes this
    a test of the tolerance and not of the arithmetic.
    """
    cfg, ctx = env
    post_id = _approved_post(ctx, media_file, scheduled_at=_at(ctx, -600))

    ctx.config.values["publishing"]["tolerance_seconds"] = 60
    assert invoke(ctx, "publish-tick", {})["missed"], "600s late must miss a 60s tolerance"
    assert _status(ctx, post_id) == "missed"

    # And with a tolerance wide enough to contain it, the same post is due.
    ctx.conn.execute("UPDATE posts SET status = 'approved' WHERE id = ?", (post_id,))
    ctx.config.values["publishing"]["tolerance_seconds"] = 3600
    assert [p["post_id"] for p in invoke(ctx, "publish-tick", {})["published"]] == [post_id]


def test_a_future_window_is_left_alone(env, media_file):
    cfg, ctx = env
    post_id = _approved_post(ctx, media_file, scheduled_at=_at(ctx, 3600))

    result = invoke(ctx, "publish-tick", {})

    assert _status(ctx, post_id) == "approved"
    assert result["waiting"] == 1
    assert result["published"] == [] and result["missed"] == []


def test_an_unreadable_schedule_escalates_rather_than_being_skipped(env, media_file):
    """Skipping would leave it approved and never published — silently."""
    cfg, ctx = env
    post_id = _approved_post(ctx, media_file, scheduled_at=_at(ctx, 3600))
    ctx.conn.execute("UPDATE posts SET scheduled_at = 'next tuesday' WHERE id = ?", (post_id,))

    result = invoke(ctx, "publish-tick", {})

    assert _status(ctx, post_id) == "missed"
    assert result["needs_attention"] == 1


# --- what the tick must not sweep up ------------------------------------------


def test_an_unscheduled_post_is_never_touched(env, media_file):
    """A post with no scheduled_at is a manual publish, not a timer."""
    cfg, ctx = env
    post_id = _approved_post(ctx, media_file, scheduled_at=None)

    result = invoke(ctx, "publish-tick", {})

    assert _status(ctx, post_id) == "approved"
    assert result["published"] == [] and result["missed"] == []
    assert invoke(ctx, "publications", {})["count"] == 0


def test_an_unapproved_post_is_never_published_by_the_tick(env, media_file):
    """F-2. The tick executes prior authorisation; it cannot create any."""
    cfg, ctx = env
    post_id = _approved_post(ctx, media_file, scheduled_at=_at(ctx, -60))
    ctx.conn.execute("UPDATE posts SET status = 'queued' WHERE id = ?", (post_id,))

    invoke(ctx, "publish-tick", {})

    assert _status(ctx, post_id) == "queued"
    assert invoke(ctx, "publications", {})["count"] == 0


def test_the_rights_gate_still_refuses_through_the_tick(env, media_file):
    """F-3 has no override path, and a scheduler must not become one.

    The post is genuinely approved — approved while PERMITTED, through the real
    F-2 gate — and its window is open. Then the asset's rights degrade before
    the tick runs, which is B3 variant C on a timer: exactly the case where a
    scheduler could publish something the operator would no longer approve.
    publish() re-checks at gate time, so the tick records a refusal and the post
    stays approved rather than being published or silently dropped.
    """
    cfg, ctx = env
    post_id = _approved_post(ctx, media_file, scheduled_at=_at(ctx, -60))
    asset_id = ctx.conn.execute(
        "SELECT asset_id FROM posts WHERE id = ?", (post_id,)
    ).fetchone()[0]

    invoke(
        ctx, "add-evidence",
        {"asset_id": asset_id, "kind": "third_party_material_suspected",
         "body": "music bed detected", "produced_by": "model",
         "confidence": 0.9, "model_id": "some-llm"},
    )
    invoke(ctx, "determine-rights", {"asset_id": asset_id})

    result = invoke(ctx, "publish-tick", {})

    assert invoke(ctx, "publications", {})["count"] == 0
    assert [f["post_id"] for f in result["failed"]] == [post_id]
    assert result["failed"][0]["error"] == "RIGHTS_BLOCKED"
    assert _status(ctx, post_id) == "approved", "a refused post must stay retryable"


def test_an_agent_cannot_run_the_tick(env, media_file):
    """It publishes, so it carries publish-post's authority (F-2)."""
    cfg, ctx = env
    _approved_post(ctx, media_file, scheduled_at=_at(ctx, -60))
    as_agent = Context(config=ctx.config, conn=ctx.conn, principal=agent("ag"))

    with pytest.raises(Forbidden):
        invoke(as_agent, "publish-tick", {})
    assert invoke(ctx, "publications", {})["count"] == 0


# --- idempotence, inherited rather than restated ------------------------------


def test_running_the_tick_twice_publishes_once(env, media_file):
    """The tick adds no idempotence of its own — posts.publish claims (B2).

    Asserted at the publication table, which is the thing that must not double.
    """
    cfg, ctx = env
    post_id = _approved_post(ctx, media_file, scheduled_at=_at(ctx, -60))

    invoke(ctx, "publish-tick", {})
    second = invoke(ctx, "publish-tick", {})

    assert invoke(ctx, "publications", {})["count"] == 1
    assert _status(ctx, post_id) == "published"
    # The second pass finds nothing due: the post is no longer 'approved'.
    assert second["published"] == [] and second["missed"] == []


def test_ordering_is_scheduled_order(env, media_file):
    """C-22: scheduled order is posted order."""
    cfg, ctx = env
    account = invoke(
        ctx, "connect-account", {"platform": "x", "handle": "me", "secret": "t"}
    )["account_id"]
    as_agent = Context(config=ctx.config, conn=ctx.conn, principal=agent("ag"))
    asset = invoke(
        as_agent, "ingest",
        {"source_path": str(media_file), "declaration": declaration_original()},
    )["asset_id"]
    attest(ctx, asset)
    invoke(ctx, "seal-provenance", {"asset_id": asset})

    ids = []
    for offset in (-30, -90, -60):  # deliberately not insertion order
        pid = invoke(
            ctx, "queue-post",
            {"account_id": account, "asset_id": asset, "body": f"p{offset}",
             "scheduled_at": _at(ctx, offset)},
        )["post_id"]
        invoke(ctx, "approve-post", {"post_id": pid})
        ids.append((offset, pid))

    published = [p["post_id"] for p in invoke(ctx, "publish-tick", {})["published"]]
    expected = [pid for offset, pid in sorted(ids, key=lambda pair: pair[0])]
    assert published == expected, "posts were published out of scheduled order (C-22)"


# --- the status view ----------------------------------------------------------


def test_schedule_status_reports_without_publishing(env, media_file):
    """A scheduler whose only output is a side effect cannot be checked."""
    cfg, ctx = env
    due = _approved_post(ctx, media_file, scheduled_at=_at(ctx, -60))

    status = invoke(ctx, "schedule-status", {})

    assert [p["post_id"] for p in status["due_now"]] == [due]
    assert invoke(ctx, "publications", {})["count"] == 0, "a read published something"
    assert _status(ctx, due) == "approved"


def test_schedule_status_is_readable_by_an_agent(env, media_file):
    """Reporting is not authority. An agent must be able to see the queue."""
    cfg, ctx = env
    _approved_post(ctx, media_file, scheduled_at=_at(ctx, 3600))
    as_agent = Context(config=ctx.config, conn=ctx.conn, principal=agent("ag"))
    assert invoke(as_agent, "schedule-status", {})["ok"] is True


def test_missed_posts_stay_visible_after_the_tick(env, media_file):
    """The escalation must not be a one-off line in a log nobody reads."""
    cfg, ctx = env
    post_id = _approved_post(ctx, media_file, scheduled_at=_at(ctx, -3600))
    invoke(ctx, "publish-tick", {})

    assert [p["post_id"] for p in invoke(ctx, "schedule-status", {})["missed"]] == [post_id]


# --- registry shape -----------------------------------------------------------


def test_the_tick_takes_no_entity_lock():
    """It acts on many posts; each publish locks its own post inside invoke().

    A lock over the whole tick would hold every post against every other agent
    for the duration, which is worse than the contention it would prevent.
    """
    from promedia.core.registry import lock_target

    assert lock_target(OPERATIONS["publish-tick"], {}) is None
    assert OPERATIONS["publish-tick"].entity is None


def test_the_tick_is_reachable_from_both_surfaces():
    """F-1/S4 — asserted in full by tests/test_parity.py; named here too."""
    assert "publish-tick" in OPERATIONS and "schedule-status" in OPERATIONS
