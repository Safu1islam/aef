"""Scheduled publishing — the tick (T-018, DR-009).

DR-009 chose a one-shot command invoked by Windows Task Scheduler over a daemon
or a cloud scheduler: the OS supervises it, it survives reboot, and there is
nothing to keep alive. This module is that command's logic; ``ops/schedule.py``
registers it as a capability like any other, so the scheduler is a *caller* of
the same repo-callable surface the operator and agents use, not a component with
its own copy of the publish path.

Three constraints shape everything here, and two of them pull in opposite
directions:

* **C-26** — a +/- 5 minute tolerance (configured, never hardcoded).
* **C-27** — a window missed by more than the tolerance is marked ``missed`` and
  escalated. It is NEVER posted late. This is the constraint that makes the
  design honest: a desktop that sleeps cannot guarantee a deadline, so the
  system reports the miss rather than pretending the window was met.
* **C-22** — scheduled order is posted order, per account.

What the tick deliberately does NOT do is decide anything. It publishes posts an
operator already approved, through ``posts.publish``, which re-checks the rights
verdict, media availability, account status and approval on every call. The tick
is an executor of prior authorisation, never a source of it (F-2).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from ..errors import ProMediaError
from . import posts as posts_layer
from .audit import record
from .db import now
from .registry import Context


def _parse(moment: str) -> datetime | None:
    """A scheduled_at we cannot read is not a reason to publish anything."""
    try:
        return datetime.fromisoformat(moment)
    except (TypeError, ValueError):
        return None


def due_posts(ctx: Context, at: datetime, tolerance: timedelta) -> dict[str, list[Any]]:
    """Split the approved-and-scheduled queue into due, missed and waiting.

    Ordered by ``scheduled_at`` then account, so a drain publishes in the order
    the operator scheduled (C-22). Posts with no ``scheduled_at`` are excluded
    entirely: they are manual-publish posts, and a tick that swept them up would
    publish things nobody asked to be published on a timer.
    """
    rows = ctx.conn.execute(
        "SELECT * FROM posts WHERE status = 'approved' AND scheduled_at IS NOT NULL"
        " ORDER BY scheduled_at, account_id"
    ).fetchall()

    due, missed, waiting, unreadable = [], [], [], []
    for row in rows:
        when = _parse(row["scheduled_at"])
        if when is None:
            unreadable.append(row)
            continue
        if when.tzinfo is None:
            when = when.replace(tzinfo=at.tzinfo)
        if when > at:
            waiting.append(row)
        elif at - when <= tolerance:
            due.append(row)
        else:
            missed.append(row)
    return {"due": due, "missed": missed, "waiting": waiting, "unreadable": unreadable}


def _mark_missed(ctx: Context, row: Any, at: datetime, why: str) -> dict[str, Any]:
    """C-27. Status change plus an audit entry — the escalation IS the record.

    Guarded on status so a post that moved on between the read above and this
    write is not dragged backwards into 'missed'.
    """
    ctx.conn.execute(
        "UPDATE posts SET status = 'missed' WHERE id = ? AND status = 'approved'",
        (row["id"],),
    )
    # outcome='failed', not a new 'missed' value: audit_log's CHECK constraint
    # admits only allowed/denied/failed, and widening it means migrating a table
    # SQLite cannot ALTER a CHECK on — a data_model_change, for a distinction the
    # detail already carries and that schedule-status reports structurally. A
    # missed window IS a failure of the schedule, so the value is not a
    # compromise. Recorded in the task's open items rather than decided silently.
    record(
        ctx,
        "publish-tick",
        outcome="failed",
        detail=f"MISSED WINDOW — {why}",
        entity_type="post",
        entity_id=row["id"],
    )
    return {
        "post_id": row["id"],
        "account_id": row["account_id"],
        "scheduled_at": row["scheduled_at"],
        "why": why,
    }


def tick(ctx: Context) -> dict[str, Any]:
    """One pass over the scheduled queue. Idempotent, and safe to run twice.

    Idempotence is not this function's achievement — it is ``posts.publish``'s.
    That call claims the post transactionally before touching the platform
    (finding B2), so two ticks overlapping cannot double-publish even though
    both see the same due list. The tick adds nothing on top, which is the
    point: one implementation of "publish exactly once", used by every caller.
    """
    at = now()
    tolerance = timedelta(seconds=int(ctx.config.get("publishing", "tolerance_seconds")))
    split = due_posts(ctx, at, tolerance)

    missed = [
        _mark_missed(
            ctx,
            row,
            at,
            f"window {row['scheduled_at']} missed by more than the "
            f"{int(tolerance.total_seconds())}s tolerance (C-27); never posted late",
        )
        for row in split["missed"]
    ]
    # An unparseable timestamp is escalated rather than skipped. Skipping would
    # leave the post silently approved-and-never-published, which is the failure
    # mode C-27 exists to make visible.
    missed += [
        _mark_missed(ctx, row, at, f"scheduled_at {row['scheduled_at']!r} is not a readable time")
        for row in split["unreadable"]
    ]

    published, failed = [], []
    for row in split["due"]:
        try:
            result = posts_layer.publish(ctx, post_id=row["id"])
        except ProMediaError as exc:
            # A refusal here is the gates doing their job — the verdict degraded,
            # the media was deleted (T-029), the account broke (N11). Recorded
            # and reported; the post stays approved and the next tick retries
            # until its window closes, at which point it is marked missed.
            failed.append({"post_id": row["id"], "error": exc.code, "message": exc.message})
            continue
        published.append(
            {
                "post_id": row["id"],
                "publication_id": result.get("publication_id"),
                "simulated": result.get("simulated"),
                "already_published": result.get("already_published", False),
            }
        )

    return {
        "ok": True,
        "at": at.isoformat(),
        "tolerance_seconds": int(tolerance.total_seconds()),
        "published": published,
        "missed": missed,
        "failed": failed,
        "waiting": len(split["waiting"]),
        # Named so an operator reading a tick's output is told what to look at
        # rather than having to infer it from three empty lists.
        "needs_attention": len(missed) + len(failed),
    }
