"""Scheduling capabilities (T-018, DR-009).

Thin registrations over ``core/scheduling.py``, matching how every other
capability is split. Being registered is what makes the tick reachable from both
surfaces (F-1) and what lets Windows Task Scheduler call it through the ordinary
CLI rather than through a private entry point of its own.
"""

from __future__ import annotations

from typing import Any

from .. import scheduling
from ..registry import Context, register


@register(
    "publish-tick",
    "Publish approved posts whose scheduled window is open; mark missed windows. Idempotent.",
    authority="operator",
    mutates=True,
    danger="Publishes to external platforms. Intended to be called by the scheduler, not by hand.",
)
def publish_tick(ctx: Context) -> dict[str, Any]:
    """Operator authority, because it publishes.

    The tick executes authorisations the operator already gave — every post it
    touches was approved through the F-2 gate — but it reaches external
    platforms, so it carries the same authority as ``publish-post`` rather than
    a weaker one. Windows Task Scheduler supplies the operator token through
    PROMEDIA_OPERATOR_TOKEN, which is the same mechanism the CLI already has;
    there is no scheduler-specific credential path.

    No ``entity``, so it takes no C-19 lock of its own: it acts on many posts,
    and each individual publish takes and releases the lock for its own post
    inside ``invoke``. A lock over the whole tick would block every other agent
    from every post for the duration.
    """
    return scheduling.tick(ctx)


@register(
    "schedule-status",
    "Show the scheduled queue: what is due, waiting, or was missed.",
)
def schedule_status(ctx: Context) -> dict[str, Any]:
    """Read-only, agent authority — the half of DR-009 that answers 'is it working'.

    A scheduler whose only output is a side effect cannot be checked. Ticks stop
    when the machine is off (the acknowledged limitation of DR-009), and this is
    how that becomes visible rather than silent.
    """
    from datetime import timedelta

    from ..db import now

    at = now()
    tolerance = timedelta(seconds=int(ctx.config.get("publishing", "tolerance_seconds")))
    split = scheduling.due_posts(ctx, at, tolerance)

    def brief(row: Any) -> dict[str, Any]:
        return {
            "post_id": row["id"],
            "account_id": row["account_id"],
            "scheduled_at": row["scheduled_at"],
        }

    missed_rows = ctx.conn.execute(
        "SELECT * FROM posts WHERE status = 'missed' ORDER BY scheduled_at"
    ).fetchall()

    return {
        "ok": True,
        "at": at.isoformat(),
        "tolerance_seconds": int(tolerance.total_seconds()),
        "due_now": [brief(r) for r in split["due"]],
        "waiting": [brief(r) for r in split["waiting"]],
        "unreadable": [brief(r) for r in split["unreadable"]],
        # Already marked by an earlier tick — the escalation C-27 requires, kept
        # visible rather than left only in the audit log.
        "missed": [brief(r) for r in missed_rows],
    }
