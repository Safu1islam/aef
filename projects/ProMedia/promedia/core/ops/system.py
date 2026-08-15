"""System operations: discovery, initialisation, audit."""

from __future__ import annotations

from typing import Any

from .. import audit as audit_layer
from .. import db
from ..registry import Context, Param, OPERATIONS, register


@register("ops", "List every capability, with parameters and required authority.")
def list_ops(ctx: Context) -> dict[str, Any]:
    """Discovery for agents: the whole contract, machine-readable, in one call."""
    return {
        "ok": True,
        "count": len(OPERATIONS),
        "operations": [op.to_dict() for op in sorted(OPERATIONS.values(), key=lambda o: o.name)],
    }


@register("init", "Create the database and apply the schema.", mutates=True)
def init(ctx: Context) -> dict[str, Any]:
    db.apply_schema(ctx.conn)
    return {
        "ok": True,
        "schema_version": db.schema_version(ctx.conn),
        "database": str(ctx.config.db_path),
        "object_root": str(ctx.config.object_root),
    }


@register("status", "Report configuration, storage usage and rights ruleset in force.")
def status(ctx: Context) -> dict[str, Any]:
    from .. import storage as storage_layer

    return {
        "ok": True,
        "version": __import__("promedia").__version__,
        "principal": ctx.principal.to_dict(),
        "config_source": str(ctx.config.source) if ctx.config.source else None,
        "database": str(ctx.config.db_path),
        "schema_version": db.schema_version(ctx.conn),
        "storage": storage_layer.status(ctx.conn, ctx.config),
        "rights_ruleset": {
            "ruleset": ctx.config.get("rights", "ruleset"),
            "version": ctx.config.get("rights", "ruleset_version"),
            "jurisdiction": ctx.config.get("rights", "jurisdiction"),
        },
        "simulation_enabled": bool(ctx.config.get("publishing", "allow_simulation")),
    }


@register(
    "audit",
    "Read the audit log: who attempted what, including denials.",
    params=(Param("limit", "int", required=False, default=50, help="Maximum entries."),),
)
def audit(ctx: Context, limit: int | None = None) -> dict[str, Any]:
    entries = audit_layer.entries(ctx.conn, limit or 50)
    return {"ok": True, "count": len(entries), "entries": entries}


@register("locks", "List entity locks currently held, with their owners (C-19).")
def locks(ctx: Context) -> dict[str, Any]:
    held = db.list_locks(ctx.conn)
    return {"ok": True, "count": len(held), "locks": held}
