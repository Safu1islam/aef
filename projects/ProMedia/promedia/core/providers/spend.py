"""Spend ledger — C-31 enforcement (T-048).

project.md C-31: "$100/month total ceiling. Hard stop at 150% ($150). Per-
operation cap $5 unless approved." This module is how that sentence becomes
code, and it is written under one absolute instruction: it RECORDS and
REFUSES; it never spends.

There is no function in this module, or anywhere reachable from it, that
calls a payment API, stores a card, or moves money. ``record()`` writes a row
saying a spend happened — through some channel entirely outside this
process, today always the operator paying a bill by hand, and in the future
perhaps a live provider call this repository does not yet implement (see
``providers/base.py``). Recording is refused, never partially applied, when
it would breach the ceiling — the same shape ``storage.py`` uses for the F-7
byte ceiling, applied to dollars instead.

Two independent refusal gates, checked every time:

  * **Per-operation cap.** An amount over C-31's $5 cap is refused unless the
    caller passes ``approved=True`` — and ``record-spend`` is registered
    operator-authority (F-2), so that flag can only ever be set by a human
    acting through the operator principal, never by an agent.
  * **Monthly hard stop.** A recording that would carry the month's committed
    total past 150% of the $100 ceiling is refused outright, with no
    approval flag able to override it — C-31 calls this a stop, not a
    warning.

Spending between $100 and $150 is permitted but reported as ``over_ceiling``
rather than ``ok``, matching the letter of C-31 (a ceiling with a separate,
harder stop above it, not two synonyms for the same limit).
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Any

from ...errors import ProMediaError, ValidationError
from ..db import iso, new_id, now


class SpendCeilingExceeded(ProMediaError):
    """C-31: refuses BEFORE any row is written. Carries the shortfall detail."""

    code = "SPEND_CEILING_EXCEEDED"


class SpendApprovalRequired(ProMediaError):
    """C-31 per-operation cap: amounts over the cap need an explicit approval flag.

    Exit code 3, matching Forbidden/ApprovalRequired in promedia/errors.py —
    the same "hand this to a human" signal, not "this call was malformed".
    """

    code = "SPEND_APPROVAL_REQUIRED"
    exit_code = 3


def _month_key(moment: datetime | None = None) -> str:
    m = moment or now()
    return f"{m.year:04d}-{m.month:02d}"


def month_to_date(conn: sqlite3.Connection, config: Any, *, month: str | None = None) -> dict[str, Any]:
    """Committed spend for one accounting month against the C-31 thresholds."""
    key = month or _month_key()
    row = conn.execute(
        "SELECT COALESCE(SUM(amount_usd), 0) AS total FROM spend_ledger WHERE month = ?",
        (key,),
    ).fetchone()
    committed = float(row["total"])
    ceiling = float(config.get("spend", "monthly_ceiling_usd"))
    hard_stop = ceiling * float(config.get("spend", "hard_stop_fraction"))
    return {
        "month": key,
        "committed_usd": round(committed, 2),
        "monthly_ceiling_usd": ceiling,
        "hard_stop_usd": round(hard_stop, 2),
        "remaining_to_ceiling_usd": round(max(0.0, ceiling - committed), 2),
        "remaining_to_hard_stop_usd": round(max(0.0, hard_stop - committed), 2),
        "state": (
            "hard_stopped" if committed >= hard_stop
            else "over_ceiling" if committed >= ceiling
            else "ok"
        ),
    }


def check(conn: sqlite3.Connection, config: Any, *, amount_usd: float, approved: bool = False) -> dict[str, Any]:
    """Would recording this amount be permitted right now? Read-only; reserves nothing.

    Safe for agent authority (F-2: analysing is not spending) — an agent may
    ask "would this be allowed" so it can hand the operator an informed
    request, but this function never writes a row.
    """
    if amount_usd < 0:
        raise ValidationError("amount_usd must not be negative", amount_usd=amount_usd)
    status = month_to_date(conn, config)
    per_op_cap = float(config.get("spend", "per_operation_cap_usd"))
    projected = status["committed_usd"] + float(amount_usd)
    reasons: list[str] = []
    if amount_usd > per_op_cap and not approved:
        reasons.append(
            f"${amount_usd:.2f} exceeds the ${per_op_cap:.2f} per-operation cap (C-31)"
            " without explicit approval"
        )
    if projected > status["hard_stop_usd"]:
        reasons.append(
            f"recording ${amount_usd:.2f} would bring the month to ${projected:.2f},"
            f" past the ${status['hard_stop_usd']:.2f} hard stop (C-31)"
        )
    return {
        **status,
        "amount_usd": round(float(amount_usd), 2),
        "projected_usd": round(projected, 2),
        "permitted": not reasons,
        "reasons": reasons,
    }


def record(
    conn: sqlite3.Connection,
    config: Any,
    *,
    capability: str,
    provider: str,
    amount_usd: float,
    note: str = "",
    approved: bool = False,
    recorded_by: str = "operator",
) -> dict[str, Any]:
    """Append a ledger entry, or refuse — never both, never partially.

    Refuses before any row is written (AC-3), naming which C-31 gate fired.
    This is the only function in this module that mutates the database, and
    it never contacts anything outside it: the amount is asserted by the
    caller, not measured from a real transaction, because no transaction
    this codebase can perform exists (see the module docstring).
    """
    outcome = check(conn, config, amount_usd=amount_usd, approved=approved)
    if not outcome["permitted"]:
        per_op_cap = float(config.get("spend", "per_operation_cap_usd"))
        if amount_usd > per_op_cap and not approved:
            raise SpendApprovalRequired(
                f"${amount_usd:.2f} exceeds the ${per_op_cap:.2f} per-operation cap;"
                " explicit approval is required (C-31)",
                amount_usd=round(float(amount_usd), 2),
                per_operation_cap_usd=per_op_cap,
                remedy="record again with approved=true once a human has actually approved this amount",
            )
        raise SpendCeilingExceeded(
            "recording this spend would breach the C-31 monthly hard stop; refused",
            **outcome,
        )
    entry_id = new_id("spend")
    conn.execute(
        "INSERT INTO spend_ledger (id, month, capability, provider, amount_usd,"
        " approved, note, recorded_by, recorded_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            entry_id,
            outcome["month"],
            capability,
            provider,
            round(float(amount_usd), 2),
            int(bool(approved)),
            note or "",
            recorded_by,
            iso(),
        ),
    )
    return {"ok": True, "entry_id": entry_id, **month_to_date(conn, config)}


def history(conn: sqlite3.Connection, *, month: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    if month:
        rows = conn.execute(
            "SELECT * FROM spend_ledger WHERE month = ? ORDER BY recorded_at DESC LIMIT ?",
            (month, int(limit)),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM spend_ledger ORDER BY recorded_at DESC LIMIT ?",
            (int(limit),),
        ).fetchall()
    return [dict(r) for r in rows]
