"""AI capability providers and the C-31 spend ledger (T-048).

Mirrors DR-010: an interface for capabilities that do not exist on this
machine, honest about exactly that (AC-2), plus a ledger that refuses
anything that would breach the C-31 ceiling (AC-3). NO operation here can
ever spend money — see ``core/providers/base.py`` and
``core/providers/spend.py`` for why that is structural, not a promise.

Authority: reads (listing, requirements, estimates, spend status/history/
check) are agent authority — inspecting is analysing, which F-2 permits.
``run-capability`` and ``record-spend`` are operator authority: the first
is the one call that would ever reach a paid API if a live adapter existed
behind it, and the second writes the financial record itself. Both are
mutating and reach money-adjacent state, which is exactly the class F-2
reserves for the operator ("agents may never spend money without operator
approval") — matching how ``publish-post`` and ``publish-tick`` were made
operator authority for the same reason once they could reach something
external.
"""

from __future__ import annotations

from typing import Any

from .. import providers as providers_layer
from ..providers import spend as spend_layer
from ..registry import Context, Param, register


@register(
    "list-capabilities",
    "List every AI capability, whether it is available, and what would make it so.",
)
def list_capabilities(ctx: Context) -> dict[str, Any]:
    out = []
    for kind, cap in providers_layer.CAPABILITIES.items():
        out.append(
            {
                "capability": kind,
                "provider": cap.provider_name,
                "available": cap.available(),
                "requirements": cap.requirements().to_dict(),
            }
        )
    return {"ok": True, "count": len(out), "capabilities": out}


@register(
    "capability-requirements",
    "What exactly would make an AI capability available: package, credential, verified price.",
    params=(Param("capability", "str", help="transcription | text | speech | image | video"),),
)
def capability_requirements(ctx: Context, capability: str) -> dict[str, Any]:
    cap = providers_layer.for_capability(capability)
    return {"ok": True, **cap.requirements().to_dict()}


@register(
    "estimate-capability-cost",
    "Estimated per-unit cost of an AI capability. UNKNOWN unless independently verified — never guessed.",
    params=(Param("capability", "str", help="transcription | text | speech | image | video"),),
)
def estimate_capability_cost(ctx: Context, capability: str) -> dict[str, Any]:
    cap = providers_layer.for_capability(capability)
    return {"ok": True, **cap.estimate().to_dict()}


@register(
    "run-capability",
    "Invoke an AI capability. Refuses structurally today; see capability-requirements for why.",
    params=(
        Param("capability", "str", help="transcription | text | speech | image | video"),
        Param(
            "input_ref",
            "str",
            required=False,
            help="Reference to the input (e.g. an asset id), for a future live adapter.",
        ),
    ),
    authority="operator",
    mutates=True,
    danger=(
        "Would incur real API cost once a live adapter exists behind this capability."
        " Today it always refuses — see capability-requirements."
    ),
)
def run_capability(ctx: Context, capability: str, input_ref: str | None = None) -> dict[str, Any]:
    cap = providers_layer.for_capability(capability)
    return cap.run(input_ref=input_ref)


@register("spend-status", "Spend recorded so far this month against the C-31 ceiling.")
def spend_status(ctx: Context) -> dict[str, Any]:
    return {"ok": True, **spend_layer.month_to_date(ctx.conn, ctx.config)}


@register(
    "spend-check",
    "Would this amount be permitted against the C-31 ceiling right now? Read-only, records nothing.",
    params=(
        Param("amount_usd", "float", help="Amount in US dollars to check."),
        Param(
            "approved",
            "bool",
            required=False,
            default=False,
            help="Explicit approval for amounts over the C-31 per-operation cap.",
        ),
    ),
)
def spend_check(ctx: Context, amount_usd: float, approved: bool = False) -> dict[str, Any]:
    return {"ok": True, **spend_layer.check(ctx.conn, ctx.config, amount_usd=amount_usd, approved=approved)}


@register(
    "record-spend",
    "Record that a spend occurred. Refuses rather than records if C-31 would be breached. Never performs a purchase.",
    params=(
        Param("capability", "str", help="transcription | text | speech | image | video | other"),
        Param("provider", "str", help="Which API or service this was spent with."),
        Param("amount_usd", "float", help="Amount in US dollars."),
        Param("note", "str", required=False, help="What this spend was for."),
        Param(
            "approved",
            "bool",
            required=False,
            default=False,
            help="Explicit approval, required for amounts over the C-31 per-operation cap.",
        ),
    ),
    authority="operator",
    mutates=True,
    danger="Writes a permanent financial record. Refuses rather than records if C-31 would be breached.",
)
def record_spend(
    ctx: Context,
    capability: str,
    provider: str,
    amount_usd: float,
    note: str | None = None,
    approved: bool = False,
) -> dict[str, Any]:
    return spend_layer.record(
        ctx.conn,
        ctx.config,
        capability=capability,
        provider=provider,
        amount_usd=amount_usd,
        note=note or "",
        approved=approved,
        recorded_by=ctx.principal.id,
    )


@register(
    "spend-history",
    "Ledger entries recorded so far, most recent first.",
    params=(
        Param("month", "str", required=False, help="YYYY-MM. Omit for every month."),
        Param("limit", "int", required=False, default=100),
    ),
)
def spend_history(ctx: Context, month: str | None = None, limit: int = 100) -> dict[str, Any]:
    rows = spend_layer.history(ctx.conn, month=month, limit=limit)
    return {"ok": True, "count": len(rows), "entries": rows}
