"""Storage operations (T-007)."""

from __future__ import annotations

from typing import Any

from .. import storage as storage_layer
from ..registry import Context, register


@register("storage-status", "Report usage against the ceiling, and the admission state.")
def storage_status(ctx: Context) -> dict[str, Any]:
    return {"ok": True, **storage_layer.status(ctx.conn, ctx.config)}


@register("ingest-queue", "List ingest refused by admission control and awaiting space (F-7).")
def ingest_queue(ctx: Context) -> dict[str, Any]:
    rows = storage_layer.queued(ctx.conn)
    return {"ok": True, "count": len(rows), "queued": rows}


@register("reclaim-reservations", "Release expired reservations left by crashed ingests.", mutates=True)
def reclaim_reservations(ctx: Context) -> dict[str, Any]:
    released = storage_layer.reclaim_expired(ctx.conn)
    return {"ok": True, "released": released}
