"""Post operations (T-011).

The authority split lives here in declaration form:

  queue-post    agent      — no external effect
  approve-post  operator   — F-2
  publish-post  operator   — F-2, and NON-NEGOTIABLES list B (publishing publicly)
"""

from __future__ import annotations

from typing import Any

from .. import posts as posts_layer
from ..registry import Context, Param, register


@register(
    "queue-post",
    "Queue a post for the operator to review. Does not publish.",
    params=(
        Param("account_id", "str"),
        Param("asset_id", "str"),
        Param("body", "str", help="Post text."),
        Param("scheduled_at", "str", required=False, help="ISO 8601 target time."),
    ),
    mutates=True,
    entity="post",
)
def queue_post(
    ctx: Context, account_id: str, asset_id: str, body: str, scheduled_at: str | None = None
) -> dict[str, Any]:
    return posts_layer.create(
        ctx, account_id=account_id, asset_id=asset_id, body=body, scheduled_at=scheduled_at
    )


@register(
    "post",
    "Show a post with everything needed to decide on it: account, rights verdict, ruleset, asset hash.",
    params=(Param("post_id", "str"),),
)
def post(ctx: Context, post_id: str) -> dict[str, Any]:
    return posts_layer.decision_context(ctx, post_id)


@register(
    "list-posts",
    "List posts, optionally filtered by status.",
    params=(Param("status", "str", required=False, help="queued | approved | published | rejected | missed"),),
)
def list_posts(ctx: Context, status: str | None = None) -> dict[str, Any]:
    return posts_layer.listing(ctx, status)


@register(
    "approve-post",
    "Operator approval of a queued post. Refused unless the rights verdict is PERMITTED.",
    params=(
        Param("post_id", "str"),
        Param("decision", "str", required=False, default="approved", help="approved | rejected"),
    ),
    authority="operator",
    mutates=True,
    entity="post",
    danger="Authorises this content for publication.",
)
def approve_post(ctx: Context, post_id: str, decision: str | None = None) -> dict[str, Any]:
    return posts_layer.approve(ctx, post_id=post_id, decision=decision or "approved")


@register(
    "publish-post",
    "Publish an approved post. Requires operator approval, a PERMITTED verdict, and sealed provenance.",
    params=(Param("post_id", "str"),),
    authority="operator",
    mutates=True,
    entity="post",
    danger="Sends content to an external platform. Not reversible once seen.",
)
def publish_post(ctx: Context, post_id: str) -> dict[str, Any]:
    return posts_layer.publish(ctx, post_id=post_id)


@register(
    "release-publish-claim",
    "Return a post stranded in 'publishing' to 'approved' after a crash.",
    params=(Param("post_id", "str"),),
    authority="operator",
    mutates=True,
    entity="post",
    danger="Only use after confirming on the platform that nothing was posted.",
)
def release_publish_claim(ctx: Context, post_id: str) -> dict[str, Any]:
    return posts_layer.release_claim(ctx, post_id=post_id)


@register("publications", "List what was actually published, and whether it was simulated.")
def publications(ctx: Context) -> dict[str, Any]:
    rows = ctx.conn.execute(
        "SELECT * FROM publications ORDER BY published_at DESC"
    ).fetchall()
    return {
        "ok": True,
        "count": len(rows),
        "publications": [{**dict(r), "simulated": bool(r["simulated"])} for r in rows],
    }
