"""Append-only audit log (T-013).

Records every authority-gated and mutating operation, including denials.
A log that only records successes cannot answer the question that matters
after an incident: what was attempted.

Credential values never reach here — only references (DR-008).
"""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING, Any

from .db import iso

if TYPE_CHECKING:  # pragma: no cover
    from .registry import Context


def record(
    ctx: "Context",
    operation: str,
    *,
    outcome: str,
    detail: str | None = None,
    entity_type: str | None = None,
    entity_id: str | None = None,
) -> None:
    ctx.conn.execute(
        "INSERT INTO audit_log (at, principal, principal_id, operation, entity_type,"
        " entity_id, outcome, detail) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            iso(),
            ctx.principal.kind,
            ctx.principal.id,
            operation,
            entity_type,
            entity_id,
            outcome,
            detail,
        ),
    )


def entries(conn: sqlite3.Connection, limit: int = 100) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (int(limit),)
    ).fetchall()
    return [dict(r) for r in rows]
