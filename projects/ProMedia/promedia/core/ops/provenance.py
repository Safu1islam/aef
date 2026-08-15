"""Provenance operations (T-010)."""

from __future__ import annotations

from typing import Any

from .. import provenance as provenance_layer
from ..registry import Context, Param, register


@register(
    "seal-provenance",
    "Seal a self-contained provenance record that outlives the media (F-8).",
    params=(Param("asset_id", "str"),),
    mutates=True,
    entity="asset",
)
def seal_provenance(ctx: Context, asset_id: str) -> dict[str, Any]:
    return provenance_layer.seal(ctx, asset_id)


@register(
    "provenance",
    "Read a sealed provenance record. Works after the media is deleted.",
    params=(Param("provenance_id", "str"),),
)
def provenance(ctx: Context, provenance_id: str) -> dict[str, Any]:
    return provenance_layer.read(ctx.conn, provenance_id)


@register(
    "verify-provenance",
    "Verify the integrity hash of a sealed record.",
    params=(Param("provenance_id", "str"),),
)
def verify_provenance(ctx: Context, provenance_id: str) -> dict[str, Any]:
    return provenance_layer.verify(ctx.conn, provenance_id)


@register("list-provenance", "List sealed provenance records.")
def list_provenance(ctx: Context) -> dict[str, Any]:
    rows = ctx.conn.execute(
        "SELECT id, asset_id, content_hash, schema_version, sealed_at"
        " FROM provenance_records ORDER BY sealed_at DESC"
    ).fetchall()
    return {"ok": True, "count": len(rows), "records": [dict(r) for r in rows]}
