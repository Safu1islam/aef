"""Rights determination against stored state (T-009).

Wraps the pure engine with database lookup and, critically, lineage:
derivatives inherit their source's verdict rather than being re-evaluated.

That inheritance rule is F-4 made executable. Without it the system would offer
an obvious laundering path — ingest blocked material, transform it, re-run the
check against a declaration that no longer mentions the source, and receive a
clean verdict. Editing is a production function, not a copyright-clearing one.
"""

from __future__ import annotations

import json
from typing import Any, Iterable

from ..errors import NotFound, ValidationError
from . import rights_engine as engine
from .db import canonical_json, iso, new_id
from .registry import Context

__all__ = [
    "add_evidence",
    "ancestry",
    "attest",
    "determine",
    "effective_verdict",
    "latest_verdict",
    "media_available",
    "media_state",
    "worst_verdict_of",
]

_WORST_FIRST = {engine.BLOCKED: 0, engine.ESCALATE: 1, engine.PERMITTED: 2}


def media_state(ctx: Context, asset_id: str) -> str:
    """Whether the BYTES still exist: 'stored' | 'deleted' | 'absent'.

    Finding I9b (T-029). ``determine-rights`` returns PERMITTED for an asset
    retention has deleted, and that is correct — this function exists precisely
    so it can stay correct.

    The line drawn here, and why it is drawn there:

      * A verdict is a statement about RIGHTS. F-8 says a rights record must
        remain valid and readable after the media it describes is deleted, so a
        verdict must survive deletion; and C-20 says the same asset, evidence
        and ruleset version must always produce the identical verdict. Media
        existence is not evidence. If deleting a file could turn PERMITTED into
        BLOCKED, C-20 would be broken and every sealed provenance record would
        become unreadable in the only sense that matters — you could no longer
        re-derive the basis on which publication was permitted.

      * Availability is a statement about BYTES. It is not a rights fact, it is
        not durable, and it is not the engine's business.

    So the engine is untouched, and availability is reported alongside every
    verdict and enforced at the two gates that actually need the media to exist:
    approving a post for publication, and publishing it. Refusing to PUBLISH a
    phantom asset is right; refusing to READ its provenance would break F-8.
    """
    row = ctx.conn.execute("SELECT state FROM assets WHERE id = ?", (asset_id,)).fetchone()
    if row is None:
        # The asset row itself is gone. Provenance still reads (F-8: no foreign
        # key), so this is 'absent', not an error to raise from here.
        return "absent"
    return str(row["state"])


def media_available(ctx: Context, asset_id: str) -> bool:
    return media_state(ctx, asset_id) == "stored"


def _declaration_for(ctx: Context, asset_id: str) -> engine.Declaration:
    row = ctx.conn.execute(
        "SELECT * FROM rights_declarations WHERE asset_id = ? ORDER BY declared_at DESC LIMIT 1",
        (asset_id,),
    ).fetchone()
    if row is None:
        raise NotFound(f"asset {asset_id} has no rights declaration", asset_id=asset_id)
    return engine.Declaration(
        authorship=row["authorship"],
        third_party_material=tuple(json.loads(row["third_party_material"])),
        source_url=row["source_url"],
        licence_grantor=row["licence_grantor"],
        licence_scope=row["licence_scope"],
        licence_evidence_ref=row["licence_evidence_ref"],
        public_domain_source=row["public_domain_source"],
        attested_by=row["declared_by_kind"],
    )


def attest(ctx: Context, asset_id: str) -> dict[str, Any]:
    """Operator confirms the declaration an agent proposed.

    Appends a new declaration row rather than editing the agent's, so the
    record shows what was proposed, by whom, and that the operator then
    attested to it. Rewriting the original would erase exactly the provenance
    a dispute would turn on.
    """
    row = ctx.conn.execute(
        "SELECT * FROM rights_declarations WHERE asset_id = ? ORDER BY declared_at DESC LIMIT 1",
        (asset_id,),
    ).fetchone()
    if row is None:
        raise NotFound(f"asset {asset_id} has no rights declaration", asset_id=asset_id)
    if row["declared_by_kind"] == "operator":
        return {
            "ok": True,
            "asset_id": asset_id,
            "already_attested": True,
            "declaration_id": row["id"],
        }
    declaration_id = new_id("dec")
    ctx.conn.execute(
        "INSERT INTO rights_declarations (id, asset_id, authorship, third_party_material,"
        " source_url, licence_grantor, licence_scope, licence_evidence_ref,"
        " public_domain_source, declared_by, declared_by_kind, declared_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'operator', ?)",
        (
            declaration_id,
            asset_id,
            row["authorship"],
            row["third_party_material"],
            row["source_url"],
            row["licence_grantor"],
            row["licence_scope"],
            row["licence_evidence_ref"],
            row["public_domain_source"],
            ctx.principal.id,
            iso(),
        ),
    )
    return {
        "ok": True,
        "asset_id": asset_id,
        "declaration_id": declaration_id,
        "attested_by": ctx.principal.id,
        "proposed_by": row["declared_by"],
        "note": "re-run determine-rights to evaluate against the attested declaration",
    }


def _evidence_for(ctx: Context, asset_id: str) -> tuple[engine.EvidenceItem, ...]:
    rows = ctx.conn.execute(
        "SELECT kind, body, confidence, produced_by, model_id FROM evidence WHERE asset_id = ?",
        (asset_id,),
    ).fetchall()
    return tuple(
        engine.EvidenceItem(
            kind=r["kind"],
            body=r["body"],
            produced_by=r["produced_by"],
            confidence=r["confidence"],
            model_id=r["model_id"],
        )
        for r in rows
    )


def latest_verdict(ctx: Context, asset_id: str) -> dict[str, Any] | None:
    row = ctx.conn.execute(
        "SELECT * FROM rights_verdicts WHERE asset_id = ? ORDER BY decided_at DESC, id DESC LIMIT 1",
        (asset_id,),
    ).fetchone()
    return dict(row) if row else None


def ancestry(ctx: Context, asset_id: str) -> list[str]:
    """Every ancestor of an asset, nearest first. Cycle-safe."""
    chain: list[str] = []
    seen = {asset_id}
    current = asset_id
    while True:
        row = ctx.conn.execute(
            "SELECT derived_from FROM assets WHERE id = ?", (current,)
        ).fetchone()
        if row is None or not row["derived_from"]:
            return chain
        parent = row["derived_from"]
        if parent in seen:  # defensive: a cycle must not hang the gate
            return chain
        chain.append(parent)
        seen.add(parent)
        current = parent


def effective_verdict(ctx: Context, asset_id: str) -> dict[str, Any]:
    """The verdict that actually governs this asset RIGHT NOW, chain included.

    BLOCKING finding B3 (independent review, 2026-08-08). Inheritance used to
    consult only the immediate parent, once, at determination time. Three
    laundering paths followed from that, all reproduced by the reviewer:

      * an ungraded intermediate broke the chain, so the grandchild of a
        BLOCKED asset came out PERMITTED;
      * grading order changed the answer, violating C-20 determinism;
      * a source that later degraded left its derivative stale and publishable.

    So the chain is walked in full, an ancestor with NO verdict counts as
    ESCALATE rather than as absent, and this is evaluated at the moment the gate
    runs — not baked in when the derivative was first graded. F-4 says
    transformation never confers usability; that has to remain true after the
    transformation, not merely at the instant of it.
    """
    own = latest_verdict(ctx, asset_id)
    if own is None:
        return {
            "verdict": engine.ESCALATE,
            "matched_rule": "NO_VERDICT_YET",
            "source_asset": asset_id,
            "reason": "asset has no rights verdict; run determine-rights",
        }

    worst = dict(own)
    worst["source_asset"] = asset_id
    for ancestor_id in ancestry(ctx, asset_id):
        parent = latest_verdict(ctx, ancestor_id)
        if parent is None:
            return {
                "verdict": engine.ESCALATE,
                "matched_rule": "ANCESTOR_UNGRADED",
                "source_asset": ancestor_id,
                "reason": (
                    f"ancestor {ancestor_id} has no rights verdict; an ungraded"
                    " source cannot confer usability on a derivative (F-4)"
                ),
            }
        if _WORST_FIRST[parent["verdict"]] < _WORST_FIRST[worst["verdict"]]:
            worst = dict(parent)
            worst["matched_rule"] = "DERIVATIVE_INHERITS_SOURCE"
            worst["source_asset"] = ancestor_id
            worst["reason"] = (
                "Transformation is a production function, not a copyright-clearing"
                f" function (F-4). Inherited from ancestor {ancestor_id}."
            )
    return worst


def worst_verdict_of(ctx: Context, asset_ids: Iterable[str]) -> dict[str, Any]:
    """The verdict that governs content built from ALL of these sources at
    once — the single most restrictive result among them (T-044).

    ``effective_verdict`` already walks ONE asset's ``derived_from`` chain to
    find its worst ancestor (F-4, finding B3). A render can name many direct
    sources in the same EDL, and ``assets.derived_from`` is a single column,
    so the identical rule — a derivative is never cleaner than its worst
    input — is applied here across the whole set rather than one chain.

    Used both to gate a render before it starts (refuse and name the
    offending asset) and to compute the verdict the rendered output itself
    should carry, so the two never drift apart into separately-maintained
    copies of the same rule.
    """
    ids = list(dict.fromkeys(asset_ids))  # de-dup, first-seen order (C-20: reproducible)
    if not ids:
        raise ValidationError("no source assets to evaluate", parameter="asset_ids")
    worst: dict[str, Any] | None = None
    for asset_id in ids:
        verdict = effective_verdict(ctx, asset_id)
        if worst is None or _WORST_FIRST[verdict["verdict"]] < _WORST_FIRST[worst["verdict"]]:
            worst = dict(verdict)
            worst["evaluated_source"] = asset_id
    assert worst is not None
    return worst


def add_evidence(
    ctx: Context,
    *,
    asset_id: str,
    kind: str,
    body: str,
    produced_by: str,
    confidence: float | None = None,
    model_id: str | None = None,
) -> dict[str, Any]:
    """Record evidence. There is no verdict field here, by design (F-5)."""
    if ctx.conn.execute("SELECT 1 FROM assets WHERE id = ?", (asset_id,)).fetchone() is None:
        raise NotFound(f"no asset {asset_id}", asset_id=asset_id)
    evidence_id = new_id("ev")
    ctx.conn.execute(
        "INSERT INTO evidence (id, asset_id, kind, body, confidence, produced_by, model_id, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (evidence_id, asset_id, kind, body, confidence, produced_by, model_id, iso()),
    )
    return {"ok": True, "evidence_id": evidence_id, "asset_id": asset_id}


def determine(ctx: Context, asset_id: str) -> dict[str, Any]:
    """Evaluate and record a verdict. Verdicts are append-only."""
    asset = ctx.conn.execute("SELECT * FROM assets WHERE id = ?", (asset_id,)).fetchone()
    if asset is None:
        raise NotFound(f"no asset {asset_id}", asset_id=asset_id)

    ruleset = engine.load_ruleset(
        str(ctx.config.get("rights", "ruleset")),
        str(ctx.config.get("rights", "ruleset_version")),
    )

    declaration = _declaration_for(ctx, asset_id)
    evidence = _evidence_for(ctx, asset_id)
    verdict = engine.evaluate(declaration, evidence, ruleset)

    # F-4: a derivative can never be cleaner than any ancestor. The full chain
    # is walked, and an ungraded ancestor counts as ESCALATE (finding B3).
    inherited_from = None
    for ancestor_id in ancestry(ctx, asset_id):
        parent = latest_verdict(ctx, ancestor_id)
        parent_verdict = parent["verdict"] if parent else engine.ESCALATE
        if _WORST_FIRST[parent_verdict] < _WORST_FIRST[verdict.verdict]:
            inherited_from = ancestor_id
            verdict = engine.Verdict(
                verdict=parent_verdict,
                matched_rule=(
                    "DERIVATIVE_INHERITS_SOURCE" if parent else "ANCESTOR_UNGRADED"
                ),
                reasons=(
                    "Transformation is a production function, not a copyright-clearing"
                    f" function (F-4). Inherited from ancestor {ancestor_id}"
                    + ("" if parent else ", which has no verdict of its own."),
                ),
                ruleset=verdict.ruleset,
                ruleset_version=verdict.ruleset_version,
                jurisdiction=verdict.jurisdiction,
                evidence_digest=verdict.evidence_digest,
            )

    verdict_id = new_id("vd")
    ctx.conn.execute(
        "INSERT INTO rights_verdicts (id, asset_id, verdict, matched_rule, reasons, ruleset,"
        " ruleset_version, jurisdiction, evidence_digest, decided_at, decided_by)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            verdict_id,
            asset_id,
            verdict.verdict,
            verdict.matched_rule,
            canonical_json(list(verdict.reasons)),
            verdict.ruleset,
            verdict.ruleset_version,
            verdict.jurisdiction,
            verdict.evidence_digest,
            iso(),
            ctx.principal.id,
        ),
    )
    result = {"ok": True, "verdict_id": verdict_id, "asset_id": asset_id, **verdict.to_dict()}
    if inherited_from:
        result["inherited_from"] = inherited_from

    # T-029. The verdict above is unchanged by this and is NOT stamped with it:
    # availability is not evidence, and writing it into rights_verdicts would
    # make the same inputs yield two different rows (C-20). It is reported, so a
    # caller cannot read PERMITTED as "ready to publish" for media that is gone.
    state = media_state(ctx, asset_id)
    result["media_state"] = state
    result["media_available"] = state == "stored"
    if state != "stored":
        result["publication_blocked"] = True
        result["note"] = (
            f"this verdict is valid and remains valid (F-8), but the media is"
            f" '{state}': publication is refused until the bytes exist"
        )
    return result
