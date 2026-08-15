"""Rights operations (T-009).

Running a determination is agent authority — agents are explicitly permitted to
run rights checks. Clearing or overriding a flag is not an operation that
exists at all, for anyone: F-3 admits no override path, so none is offered.
There is deliberately no ``clear-rights-flag`` capability in this registry.
"""

from __future__ import annotations

from typing import Any

from ...errors import Forbidden, ValidationError
from .. import rights as rights_layer
from ..registry import Context, Param, register


@register(
    "add-evidence",
    "Record evidence about an asset. Evidence is never a verdict (F-5).",
    params=(
        Param("asset_id", "str"),
        Param("kind", "str", help="e.g. third_party_material_suspected, public_domain_verification."),
        Param("body", "str", help="What the evidence says."),
        Param("produced_by", "str", help="operator | agent | model | system."),
        Param("confidence", "float", required=False, help="0.0-1.0, for model evidence."),
        Param("model_id", "str", required=False, help="Model identifier, if produced_by is model."),
    ),
    mutates=True,
    entity="asset",
)
def add_evidence(
    ctx: Context,
    asset_id: str,
    kind: str,
    body: str,
    produced_by: str,
    confidence: float | None = None,
    model_id: str | None = None,
) -> dict[str, Any]:
    valid = {"operator", "agent", "model", "system"}
    if produced_by not in valid:
        raise ValidationError(
            f"produced_by must be one of {sorted(valid)}", parameter="produced_by", got=produced_by
        )

    # BLOCKING finding B1 (independent review, 2026-08-08).
    #
    # produced_by guards the F-5 boundary: PUBLIC_DOMAIN_VERIFIED permits only
    # on evidence attributed to 'operator' or 'system'. Taking that attribution
    # from a caller-supplied parameter made it a self-declaration, so an agent
    # could write produced_by='operator' and manufacture a PERMITTED verdict —
    # which is "clearing a rights flag", exactly what F-2 forbids agents.
    #
    # Authorship is now derived from the authenticated principal. An agent may
    # speak for itself or relay a model; it may not speak as the operator.
    if not ctx.principal.is_operator and produced_by in {"operator", "system"}:
        raise Forbidden(
            f"an agent may not record evidence attributed to '{produced_by}'",
            parameter="produced_by",
            attempted=produced_by,
            allowed=["agent", "model"],
            why="evidence authorship guards the rights engine's permitting rules (F-5)",
        )
    if produced_by == "model" and not model_id:
        raise ValidationError(
            "model-authored evidence must name the model", parameter="model_id"
        )
    if confidence is not None and not (0.0 <= confidence <= 1.0):
        raise ValidationError("confidence must be between 0.0 and 1.0", parameter="confidence")
    return rights_layer.add_evidence(
        ctx,
        asset_id=asset_id,
        kind=kind,
        body=body,
        produced_by=produced_by,
        confidence=confidence,
        model_id=model_id,
    )


@register(
    "determine-rights",
    "Evaluate an asset against the ruleset and record an immutable verdict.",
    params=(Param("asset_id", "str"),),
    mutates=True,
    entity="asset",
)
def determine_rights(ctx: Context, asset_id: str) -> dict[str, Any]:
    return rights_layer.determine(ctx, asset_id)


@register(
    "attest-declaration",
    "Operator confirms the rights declaration an agent proposed for an asset.",
    params=(Param("asset_id", "str"),),
    authority="operator",
    mutates=True,
    entity="asset",
    danger="Attests to authorship or licence. Permitting rules only fire on an operator attestation.",
)
def attest_declaration(ctx: Context, asset_id: str) -> dict[str, Any]:
    return rights_layer.attest(ctx, asset_id)


@register(
    "rights",
    "Show the current rights position of an asset.",
    params=(Param("asset_id", "str"),),
)
def rights(ctx: Context, asset_id: str) -> dict[str, Any]:
    """Report the verdict that GOVERNS this asset, not merely the stored one.

    Finding N4: this returned the asset's own last verdict, so an agent could
    be told PERMITTED for content the publish gate would refuse because an
    ancestor had degraded. A reporting operation that disagrees with the gate is
    worse than no reporting operation.
    """
    stored = rights_layer.latest_verdict(ctx, asset_id)
    effective = rights_layer.effective_verdict(ctx, asset_id)
    # T-029, same reasoning as finding N4 above: a reporting operation that
    # disagrees with the gate is worse than none. The gate now also requires the
    # media to exist, so this must say whether it does — without touching the
    # verdict, which is a rights fact and survives deletion by design (F-8).
    state = rights_layer.media_state(ctx, asset_id)
    return {
        "ok": True,
        "asset_id": asset_id,
        "media_state": state,
        "media_available": state == "stored",
        "publishable": bool(effective["verdict"] == "PERMITTED" and state == "stored"),
        "verdict": effective["verdict"],
        "matched_rule": effective.get("matched_rule"),
        "ruleset_version": effective.get("ruleset_version"),
        "jurisdiction": effective.get("jurisdiction"),
        "decided_at": effective.get("decided_at"),
        "governing_asset": effective.get("source_asset"),
        "reason": effective.get("reason"),
        "stored_verdict": stored["verdict"] if stored else None,
        "differs_from_stored": bool(stored and stored["verdict"] != effective["verdict"]),
        "note": (
            "no verdict yet; run determine-rights"
            if not stored
            else f"media is '{state}'; the verdict stands (F-8) but publication is refused"
            if state != "stored"
            else None
        ),
    }
