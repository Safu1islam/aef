"""Post queue, approval gate, publication (T-011, F-2).

The authority gate is the product. Three independent conditions must hold
before anything leaves this machine, and each is checked server-side rather
than being enforced by hiding a control:

  1. an operator approval exists for this post;
  2. the asset's latest verdict is PERMITTED;
  3. a provenance record has been sealed;
  4. the asset's media still exists (T-029).

The fourth is deliberately not expressed as a rights verdict. A verdict about
deleted media stays valid and stays PERMITTED — F-8 requires the record to
outlive the media, and C-20 requires the same inputs to yield the same verdict
for ever. Availability is a separate fact, checked separately, so that "you may
publish this" and "there is something to publish" never get confused for one
another.

Agents may queue. Only the operator may approve or publish.

Publishing is made idempotent by ``_claim_for_publish``: a transactional
status transition from 'approved' to 'publishing' that exactly one caller can
win, taken BEFORE the external call. UNIQUE(post_id) on publications is a
backstop, not the mechanism — it deduplicates the RECORD, not the POST, and
believing otherwise is what allowed a double publish (finding B2). A double
post is not recoverable once seen.
"""

from __future__ import annotations

from typing import Any

from ..errors import (  # noqa: F401
    ApprovalRequired,
    MediaUnavailable,
    NotFound,
    RightsBlocked,
    ValidationError,
)
from . import provenance, publishers
from . import rights as rights_layer
from .db import iso, new_id, transaction
from .registry import Context


def create(ctx: Context, *, account_id: str, asset_id: str, body: str, scheduled_at: str | None = None) -> dict[str, Any]:
    """Queue a post. Agents may do this — it has no external effect."""
    if ctx.conn.execute("SELECT 1 FROM accounts WHERE id = ?", (account_id,)).fetchone() is None:
        raise NotFound(f"no account {account_id}", account_id=account_id)
    if ctx.conn.execute("SELECT 1 FROM assets WHERE id = ?", (asset_id,)).fetchone() is None:
        raise NotFound(f"no asset {asset_id}", asset_id=asset_id)
    if not body.strip():
        raise ValidationError("post body cannot be empty", parameter="body")

    post_id = new_id("post")
    ctx.conn.execute(
        "INSERT INTO posts (id, account_id, asset_id, body, status, scheduled_at, created_by, created_at)"
        " VALUES (?, ?, ?, ?, 'queued', ?, ?, ?)",
        (post_id, account_id, asset_id, body, scheduled_at, ctx.principal.id, iso()),
    )
    verdict = rights_layer.latest_verdict(ctx, asset_id)
    return {
        "ok": True,
        "post_id": post_id,
        "status": "queued",
        "rights_verdict": verdict["verdict"] if verdict else None,
        "note": "queued only; publication requires operator approval (F-2)",
    }


def _post_or_404(ctx: Context, post_id: str) -> Any:
    row = ctx.conn.execute("SELECT * FROM posts WHERE id = ?", (post_id,)).fetchone()
    if row is None:
        raise NotFound(f"no post {post_id}", post_id=post_id)
    return row


def decision_context(ctx: Context, post_id: str) -> dict[str, Any]:
    """Everything the operator must see before an approve control is reachable.

    Assembled here rather than in the template so both surfaces present the
    same facts, and so the web UI cannot accidentally omit one.
    """
    post = _post_or_404(ctx, post_id)
    account = ctx.conn.execute("SELECT * FROM accounts WHERE id = ?", (post["account_id"],)).fetchone()
    asset = ctx.conn.execute("SELECT * FROM assets WHERE id = ?", (post["asset_id"],)).fetchone()
    # The operator is authorising on the strength of a declaration. Who made
    # that declaration is part of what they are authorising, so it is shown
    # rather than implied (reviewer's declaration-channel finding).
    declaration = ctx.conn.execute(
        "SELECT authorship, declared_by, declared_by_kind, licence_grantor, public_domain_source"
        " FROM rights_declarations WHERE asset_id = ? ORDER BY declared_at DESC LIMIT 1",
        (post["asset_id"],),
    ).fetchone()
    verdict = rights_layer.effective_verdict(ctx, post["asset_id"])
    prov = provenance.latest_for_asset(ctx.conn, post["asset_id"])
    simulation = bool(ctx.config.get("publishing", "allow_simulation"))
    # Finding I6: whether a publication WAS simulated is a property of the
    # record, not of current config. Deriving it from config meant that turning
    # simulation off later made past simulated publications render as real —
    # the exact indistinguishability F-001's registry entry warns about.
    publication = ctx.conn.execute(
        "SELECT id, platform_post_id, permalink, published_at, simulated"
        " FROM publications WHERE post_id = ?",
        (post_id,),
    ).fetchone()
    return {
        "ok": True,
        "post_id": post["id"],
        "status": post["status"],
        "body": post["body"],
        "created_by": post["created_by"],
        "scheduled_at": post["scheduled_at"],
        "account": {
            "id": account["id"],
            "platform": account["platform"],
            "handle": account["handle"],
            # N11: surfaced so the operator sees a broken account before
            # approving, not after publish refuses.
            "status": account["status"],
        } if account else None,
        "asset": {
            "id": asset["id"],
            "content_hash": asset["content_hash"],
            "original_filename": asset["original_filename"],
            "byte_size": asset["byte_size"],
            "state": asset["state"],
        } if asset else None,
        "rights": {
            "verdict": verdict["verdict"],
            "matched_rule": verdict.get("matched_rule"),
            "ruleset": verdict.get("ruleset"),
            "ruleset_version": verdict.get("ruleset_version"),
            "jurisdiction": verdict.get("jurisdiction"),
            "decided_at": verdict.get("decided_at"),
            "governing_asset": verdict.get("source_asset"),
            "reason": verdict.get("reason"),
        } if verdict.get("matched_rule") != "NO_VERDICT_YET" else None,
        "declaration": {
            "authorship": declaration["authorship"],
            "declared_by": declaration["declared_by"],
            "attested_by_operator": declaration["declared_by_kind"] == "operator",
            "licence_grantor": declaration["licence_grantor"],
            "public_domain_source": declaration["public_domain_source"],
        } if declaration else None,
        "provenance_sealed": prov is not None,
        "provenance_id": prov["id"] if prov else None,
        # T-029: the media check is one of the four publish gates, so it belongs
        # in the decision context too. Otherwise the surface renders an enabled
        # approve control for an asset the server will refuse — and the operator
        # is being asked to authorise the publication of nothing.
        "media_state": asset["state"] if asset else "absent",
        "media_available": bool(asset and asset["state"] == "stored"),
        "approvable": bool(
            verdict["verdict"] == "PERMITTED"
            and post["status"] == "queued"
            and asset is not None
            and asset["state"] == "stored"
        ),
        "publication": {
            "id": publication["id"],
            "platform_post_id": publication["platform_post_id"],
            "permalink": publication["permalink"],
            "published_at": publication["published_at"],
            "simulated": bool(publication["simulated"]),
        } if publication else None,
        "was_simulated": bool(publication["simulated"]) if publication else None,
        "simulation_enabled": simulation,
        "warning": (
            "This post was NEVER PUBLISHED. It was recorded by the simulated"
            " publisher (fabrication F-001)."
            if publication and publication["simulated"]
            else "Publishing is SIMULATED (fabrication F-001). Nothing will reach any platform."
            if simulation
            else None
        ),
    }


def approve(ctx: Context, *, post_id: str, decision: str = "approved") -> dict[str, Any]:
    """Operator authority only — enforced by the registry before this runs."""
    if decision not in {"approved", "rejected"}:
        raise ValidationError("decision must be 'approved' or 'rejected'", parameter="decision")
    post = _post_or_404(ctx, post_id)

    # Finding N2: transitions were unguarded, so a post already live on a
    # platform could be walked back to 'rejected' — showing the operator the
    # opposite of reality on the surface they use to decide. Since B2 made
    # posts.status a concurrency primitive, unguarded transitions also race.
    published = ctx.conn.execute(
        "SELECT id FROM publications WHERE post_id = ?", (post_id,)
    ).fetchone()
    if published is not None:
        raise ValidationError(
            "this post has already been published; its status cannot be changed",
            post_id=post_id,
            publication_id=published["id"],
        )
    if post["status"] != "queued":
        raise ValidationError(
            f"only a queued post can be approved or rejected; this one is '{post['status']}'",
            post_id=post_id,
            status=post["status"],
        )

    if decision == "rejected":
        with transaction(ctx.conn):
            ctx.conn.execute(
                "UPDATE posts SET status = 'rejected' WHERE id = ? AND status = 'queued'",
                (post_id,),
            )
            ctx.conn.execute(
                "INSERT OR REPLACE INTO approvals (id, post_id, decision, approved_by, approved_at, verdict_id)"
                " VALUES (?, ?, 'rejected', ?, ?, ?)",
                (new_id("ap"), post_id, ctx.principal.id, iso(), ""),
            )
        return {"ok": True, "post_id": post_id, "status": "rejected"}

    # effective_verdict, not latest_verdict: the governing verdict includes the
    # whole derivation chain evaluated now (finding B3).
    verdict = rights_layer.effective_verdict(ctx, post["asset_id"])
    # Server-side refusal. Hiding the control in the UI is not a control.
    if verdict["verdict"] != "PERMITTED":
        raise RightsBlocked(
            f"cannot approve: rights verdict is {verdict['verdict']}, not PERMITTED",
            post_id=post_id,
            asset_id=post["asset_id"],
            verdict=verdict["verdict"],
            matched_rule=verdict.get("matched_rule"),
            governing_asset=verdict.get("source_asset"),
            reason=verdict.get("reason"),
        )

    # T-029. PERMITTED is not the same as available. Retention deletes the media
    # while the verdict, the declaration and the sealed provenance all remain —
    # so without this check a phantom asset walks straight through the rights
    # gate and an operator approves a publication of nothing.
    state = rights_layer.media_state(ctx, post["asset_id"])
    if state != "stored":
        raise MediaUnavailable(
            f"cannot approve: this asset's media is '{state}', so there is nothing to publish",
            post_id=post_id,
            asset_id=post["asset_id"],
            asset_state=state,
            verdict=verdict["verdict"],
            why=(
                "the rights verdict is unaffected and remains valid (F-8); it is"
                " the media that is gone, and retention deletion is final"
            ),
        )

    with transaction(ctx.conn):
        ctx.conn.execute(
            "UPDATE posts SET status = 'approved' WHERE id = ? AND status = 'queued'", (post_id,)
        )
        ctx.conn.execute(
            "INSERT OR REPLACE INTO approvals (id, post_id, decision, approved_by, approved_at, verdict_id)"
            " VALUES (?, ?, 'approved', ?, ?, ?)",
            (new_id("ap"), post_id, ctx.principal.id, iso(), verdict["id"]),
        )
    return {
        "ok": True,
        "post_id": post_id,
        "status": "approved",
        "approved_by": ctx.principal.id,
        "verdict_id": verdict["id"],
    }


def _claim_for_publish(ctx: Context, post_id: str) -> None:
    """Take exclusive ownership of the publish, transactionally.

    BLOCKING finding B2 (independent review, 2026-08-08). Publishing used to
    check for an existing publication, call the platform, and then INSERT —
    three steps with nothing holding them together. UNIQUE(post_id) deduplicates
    the RECORD, not the POST: two concurrent calls both reached the platform and
    only one left a row, so the system did not know the second post existed.
    A double-click on the publish button was enough.

    The status transition is the claim. BEGIN IMMEDIATE plus a status-guarded
    UPDATE means exactly one caller can move approved -> publishing, and the
    loser never reaches the external call.
    """
    with transaction(ctx.conn):
        existing = ctx.conn.execute(
            "SELECT id FROM publications WHERE post_id = ?", (post_id,)
        ).fetchone()
        if existing is not None:
            raise AlreadyPublished(post_id, existing["id"])
        claimed = ctx.conn.execute(
            "UPDATE posts SET status = 'publishing' WHERE id = ? AND status = 'approved'",
            (post_id,),
        )
        if claimed.rowcount == 0:
            row = ctx.conn.execute(
                "SELECT status FROM posts WHERE id = ?", (post_id,)
            ).fetchone()
            status = row["status"] if row else "missing"
            if status == "publishing":
                raise ValidationError(
                    "this post is already being published by another caller",
                    post_id=post_id,
                    status=status,
                )
            raise ApprovalRequired(
                "cannot publish: no operator approval for this post (F-2)",
                post_id=post_id,
                status=status,
                remedy="approve in the ProMedia UI",
            )


class AlreadyPublished(Exception):
    """Internal control-flow signal; never surfaces to a caller."""

    def __init__(self, post_id: str, publication_id: str) -> None:
        super().__init__(post_id)
        self.post_id = post_id
        self.publication_id = publication_id


def publish(ctx: Context, *, post_id: str) -> dict[str, Any]:
    """Operator authority only. Refuses unless all three gates hold."""
    post = _post_or_404(ctx, post_id)

    existing = ctx.conn.execute(
        "SELECT * FROM publications WHERE post_id = ?", (post_id,)
    ).fetchone()
    if existing is not None:
        # Idempotent: a retry reports the original publication.
        return {
            "ok": True,
            "post_id": post_id,
            "already_published": True,
            "publication_id": existing["id"],
            "platform_post_id": existing["platform_post_id"],
            "simulated": bool(existing["simulated"]),
        }

    approval = ctx.conn.execute(
        "SELECT * FROM approvals WHERE post_id = ? AND decision = 'approved'", (post_id,)
    ).fetchone()
    if approval is None:
        raise ApprovalRequired(
            "cannot publish: no operator approval for this post (F-2)",
            post_id=post_id,
            remedy="approve in the ProMedia UI",
        )

    # Re-checked against the live chain, not the verdict recorded at approval
    # time: an ancestor may have degraded since (finding B3).
    verdict = rights_layer.effective_verdict(ctx, post["asset_id"])
    if verdict["verdict"] != "PERMITTED":
        raise RightsBlocked(
            "cannot publish: rights verdict is not PERMITTED",
            post_id=post_id,
            verdict=verdict["verdict"],
            governing_asset=verdict.get("source_asset"),
            reason=verdict.get("reason"),
        )

    prov = provenance.latest_for_asset(ctx.conn, post["asset_id"])
    if prov is None:
        raise RightsBlocked(
            "cannot publish: no sealed provenance record for this asset (F-8)",
            post_id=post_id,
            asset_id=post["asset_id"],
            remedy="seal provenance first",
        )

    account = ctx.conn.execute(
        "SELECT * FROM accounts WHERE id = ?", (post["account_id"],)
    ).fetchone()
    asset = ctx.conn.execute("SELECT * FROM assets WHERE id = ?", (post["asset_id"],)).fetchone()

    # T-029. Re-checked here and not merely at approval, for the same reason the
    # verdict is: retention can fire in the window between the two, and the
    # approval records that publication was authorised, not that the bytes are
    # still there. This sits BEFORE the claim, so a refusal leaves the post
    # approved and retryable rather than stranded in 'publishing'.
    if asset is None or asset["state"] != "stored":
        raise MediaUnavailable(
            "cannot publish: this asset's media no longer exists",
            post_id=post_id,
            asset_id=post["asset_id"],
            asset_state=asset["state"] if asset is not None else "absent",
            why=(
                "the rights verdict and the sealed provenance record remain valid"
                " and readable (F-8); the media itself was deleted by retention"
            ),
        )

    # N11: account.status existed but nothing consulted it, so the system could
    # report an account as broken and then publish to it anyway.
    if account["status"] != "connected":
        raise ValidationError(
            f"cannot publish: account is '{account['status']}', not connected",
            post_id=post_id,
            account_id=account["id"],
            status=account["status"],
            remedy="reconnect the account with a credential",
        )

    # Claim BEFORE the irreversible act. Everything above is a read-only gate;
    # from here on exactly one caller may proceed (finding B2).
    try:
        _claim_for_publish(ctx, post_id)
    except AlreadyPublished as claimed:
        existing = ctx.conn.execute(
            "SELECT * FROM publications WHERE id = ?", (claimed.publication_id,)
        ).fetchone()
        return {
            "ok": True,
            "post_id": post_id,
            "already_published": True,
            "publication_id": existing["id"],
            "platform_post_id": existing["platform_post_id"],
            "simulated": bool(existing["simulated"]),
        }

    publisher = publishers.for_platform(account["platform"], ctx.config)
    try:
        result = publisher.publish(
            body=post["body"],
            content_hash=asset["content_hash"],
            credential_ref=account["credential_ref"],
        )
    except Exception:
        # Release the claim so a failed attempt is retryable. The post did not
        # leave the machine, so returning it to 'approved' is accurate.
        ctx.conn.execute(
            "UPDATE posts SET status = 'approved' WHERE id = ? AND status = 'publishing'",
            (post_id,),
        )
        raise

    publication_id = new_id("pub")
    with transaction(ctx.conn):
        ctx.conn.execute(
            "INSERT INTO publications (id, post_id, account_id, platform, content_hash,"
            " platform_post_id, permalink, published_at, simulated, provenance_id)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                publication_id,
                post_id,
                account["id"],
                account["platform"],
                asset["content_hash"],
                result.platform_post_id,
                result.permalink,
                result.published_at,
                1 if result.simulated else 0,
                prov["id"],
            ),
        )
        ctx.conn.execute("UPDATE posts SET status = 'published' WHERE id = ?", (post_id,))

    return {
        "ok": True,
        "post_id": post_id,
        "publication_id": publication_id,
        "platform": account["platform"],
        "platform_post_id": result.platform_post_id,
        "permalink": result.permalink,
        "published_at": result.published_at,
        "simulated": result.simulated,
        "provenance_id": prov["id"],
        "warning": result.detail.get("warning"),
    }


def release_claim(ctx: Context, *, post_id: str) -> dict[str, Any]:
    """Return a stranded 'publishing' post to 'approved'.

    Finding N3. If the process dies between the claim and the publication
    INSERT, the post sits in 'publishing' for ever: publish refuses because
    another caller holds the claim, and the web surface rendered no control at
    all for that status. Recovery existed only as a side effect of the N2 bug,
    which is now fixed — so the capability has to exist properly, on both
    surfaces, like everything else (F-1).

    Refuses if a publication row exists, because then the claim was not
    stranded: the post really did go out.
    """
    post = _post_or_404(ctx, post_id)
    published = ctx.conn.execute(
        "SELECT id FROM publications WHERE post_id = ?", (post_id,)
    ).fetchone()
    if published is not None:
        raise ValidationError(
            "this post was published; the claim is not stranded",
            post_id=post_id,
            publication_id=published["id"],
        )
    if post["status"] != "publishing":
        raise ValidationError(
            f"post is '{post['status']}', not 'publishing'; nothing to release",
            post_id=post_id,
            status=post["status"],
        )
    with transaction(ctx.conn):
        ctx.conn.execute(
            "UPDATE posts SET status = 'approved' WHERE id = ? AND status = 'publishing'",
            (post_id,),
        )
    return {
        "ok": True,
        "post_id": post_id,
        "status": "approved",
        "note": (
            "claim released; verify on the platform that nothing was posted before"
            " publishing again"
        ),
    }


def listing(ctx: Context, status: str | None = None) -> dict[str, Any]:
    # `simulated` is joined from the publication record (I6) so a listing can
    # never present a simulated publication as a real one.
    sql = (
        "SELECT p.*, ("
        "  SELECT pub.simulated FROM publications pub WHERE pub.post_id = p.id"
        ") AS simulated FROM posts p"
    )
    if status:
        rows = ctx.conn.execute(
            f"{sql} WHERE p.status = ? ORDER BY p.created_at DESC", (status,)
        ).fetchall()
    else:
        rows = ctx.conn.execute(f"{sql} ORDER BY p.created_at DESC").fetchall()
    posts = []
    for row in rows:
        record = dict(row)
        record["simulated"] = None if record["simulated"] is None else bool(record["simulated"])
        posts.append(record)
    return {"ok": True, "count": len(posts), "posts": posts}
