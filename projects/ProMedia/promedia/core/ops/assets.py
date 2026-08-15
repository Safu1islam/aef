"""Asset ingest and inspection (T-008).

Ingest is agent authority: pulling a local file in has no external effect and
spends nothing. What an agent cannot do is make the result publishable.
"""

from __future__ import annotations

import json
from typing import Any

from .. import ingest as ingest_layer
from ..registry import Context, Param, register


@register(
    "ingest",
    "Ingest a local media file with its rights declaration.",
    params=(
        Param("source_path", "str", help="Path to the file to ingest."),
        Param(
            "declaration",
            "json",
            help=(
                'Rights declaration, e.g. {"authorship":"operator_original",'
                '"third_party_material":[]}. Required — an asset that cannot be'
                " evaluated must never become publishable."
            ),
        ),
        Param("derived_from", "str", required=False, help="Source asset id, if this is a derivative."),
    ),
    mutates=True,
    entity="asset",
)
def ingest(
    ctx: Context,
    source_path: str,
    declaration: dict[str, Any],
    derived_from: str | None = None,
) -> dict[str, Any]:
    return ingest_layer.ingest_file(
        ctx, source_path=source_path, declaration=declaration, derived_from=derived_from
    )


@register("list-assets", "List ingested assets with the rights verdict that governs each.")
def list_assets(ctx: Context) -> dict[str, Any]:
    from .. import rights as rights_layer

    rows = ctx.conn.execute(
        "SELECT a.*, ("
        "  SELECT v.verdict FROM rights_verdicts v WHERE v.asset_id = a.id"
        "  ORDER BY v.decided_at DESC, v.id DESC LIMIT 1"
        ") AS stored_verdict FROM assets a ORDER BY a.ingested_at DESC"
    ).fetchall()
    assets = []
    for row in rows:
        record = dict(row)
        # Finding N4: show what the gate will do, not what was last stored.
        if record["stored_verdict"] is None and not record["derived_from"]:
            record["latest_verdict"] = None
        else:
            record["latest_verdict"] = rights_layer.effective_verdict(ctx, record["id"])["verdict"]
        assets.append(record)
    return {"ok": True, "count": len(assets), "assets": assets}


@register(
    "asset",
    "Inspect one asset: declaration, evidence, verdict history, provenance state.",
    params=(Param("asset_id", "str"),),
)
def asset(ctx: Context, asset_id: str) -> dict[str, Any]:
    from ...errors import NotFound
    from .. import provenance as provenance_layer

    row = ctx.conn.execute("SELECT * FROM assets WHERE id = ?", (asset_id,)).fetchone()
    if row is None:
        raise NotFound(f"no asset {asset_id}", asset_id=asset_id)
    declaration = ctx.conn.execute(
        "SELECT * FROM rights_declarations WHERE asset_id = ? ORDER BY declared_at DESC LIMIT 1",
        (asset_id,),
    ).fetchone()
    evidence = ctx.conn.execute(
        "SELECT * FROM evidence WHERE asset_id = ? ORDER BY created_at", (asset_id,)
    ).fetchall()
    verdicts = ctx.conn.execute(
        "SELECT * FROM rights_verdicts WHERE asset_id = ? ORDER BY decided_at DESC", (asset_id,)
    ).fetchall()
    # Added for T-050's asset detail screen, which the brief (section 2, rule 2)
    # requires to show provenance state — sealed or not — alongside the
    # declaration and evidence this operation already returned. Summary only
    # (id, sealed_at): the full payload duplicates what evidence/verdicts above
    # already carry, and 'provenance' (by id) already exists for a caller that
    # wants the sealed record itself, including its integrity verification.
    sealed = provenance_layer.latest_for_asset(ctx.conn, asset_id)
    decl_out = dict(declaration) if declaration else None
    if decl_out is not None:
        # Stored as a JSON string (ingest.py); parsed here rather than in a
        # template filter, matching how renders() already parses substitutions
        # for its caller instead of handing back an opaque string.
        decl_out["third_party_material"] = json.loads(decl_out["third_party_material"])
    return {
        "ok": True,
        "asset": dict(row),
        "declaration": decl_out,
        "evidence": [dict(e) for e in evidence],
        "verdicts": [dict(v) for v in verdicts],
        "provenance": (
            {"provenance_id": sealed["id"], "sealed_at": sealed["sealed_at"]}
            if sealed
            else None
        ),
    }
