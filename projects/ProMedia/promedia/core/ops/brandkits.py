"""Brand kits — data model and CRUD operations (T-068, DR-021).

A brand kit is data ABOUT how to build an EDL — a name, a logo reference,
two colours and a font — never a second thing a render reads. Applying one
(``apply-brand-kit``) compiles it into a NEW EDL version as a burned-in
``ImageOverlay``; once that version exists, this table is a convenience
generator and is safely deletable without touching the render it produced
(DR-021's core constraint).

``apply-brand-kit`` itself is NOT implemented here yet: it needs a new
``ImageOverlay`` type in ``promedia.core.media.edl`` and a compiler branch in
``promedia.core.media.render``, and at the time this module was written
those two files (plus ``tests/test_edl.py``) were under an active, live file
lock held by a concurrent session working T-064 (colour grading, same
files). Editing a path another live agent owns is exactly what Constitution
rule 3 forbids, so that half of DR-021 is deferred rather than worked
around — see T-068's task record (``.ai/state/tasks.yaml``, AC-2/AC-3) and
R-019 for the full account. What IS here — create/list/update/delete — has
no dependency on either locked file.

Authority (F-2): every operation below is agent-callable. A brand kit is
drafting material, like an EDL itself — it never publishes, spends, or
clears a rights flag.
"""

from __future__ import annotations

from typing import Any

from ...errors import NotFound, ValidationError
from ..db import iso, new_id, transaction
from ..registry import Context, Param, register


def _require_asset(ctx: Context, asset_id: str) -> None:
    if ctx.conn.execute("SELECT 1 FROM assets WHERE id = ?", (asset_id,)).fetchone() is None:
        raise NotFound(f"no asset {asset_id}", asset_id=asset_id)


def _require_rights_declared(ctx: Context, asset_id: str) -> None:
    """AC-1: a brand kit cannot be created pointing at an asset with no
    rights declaration.

    Deliberately not a verdict check — that gate belongs to apply-brand-kit's
    eventual render (F-3/F-4 apply there, the same way they apply to every
    other asset an EDL references). This is the narrower, earlier check
    DR-021/AC-1 asks for: the logo must at least be a real, DECLARED asset,
    not one that reached the assets table some other way. A rendered
    derivative is the concrete case that would otherwise slip through:
    ``projects.render()`` registers its output as an asset with a rights
    VERDICT (inherited from its sources) but writes no row into
    ``rights_declarations`` at all — ingest is the only path that does.
    """
    declared = ctx.conn.execute(
        "SELECT 1 FROM rights_declarations WHERE asset_id = ?", (asset_id,)
    ).fetchone()
    if declared is None:
        raise ValidationError(
            f"asset {asset_id} has no rights declaration; a brand kit's logo"
            " must be a real ingested asset (use 'ingest' first), not one that"
            " reached the asset table some other way — branding never launders"
            " rights (F-3/F-4)",
            parameter="logo_asset_id",
            asset_id=asset_id,
        )


def _brand_kit_row(ctx: Context, brand_kit_id: str) -> Any:
    row = ctx.conn.execute(
        "SELECT * FROM brand_kits WHERE id = ?", (brand_kit_id,)
    ).fetchone()
    if row is None:
        raise NotFound(f"no brand kit {brand_kit_id}", brand_kit_id=brand_kit_id)
    return row


@register(
    "create-brand-kit",
    "Create a brand kit: a named logo, colours and font, applied to a project's edit on demand.",
    params=(
        Param("name", "str", help="What this kit is, e.g. 'Main channel'."),
        Param(
            "logo_asset_id", "str",
            help="A real, rights-declared asset id — ingest the logo file first.",
        ),
        Param("primary_color", "str", required=False, help="e.g. '#1a73e8'."),
        Param("secondary_color", "str", required=False, help="e.g. '#f4b400'."),
        Param("font_family", "str", required=False, help="e.g. 'Inter'."),
    ),
    mutates=True,
    entity="brand_kit",
)
def create_brand_kit(
    ctx: Context,
    name: str,
    logo_asset_id: str,
    primary_color: str | None = None,
    secondary_color: str | None = None,
    font_family: str | None = None,
) -> dict[str, Any]:
    clean = name.strip()
    if not clean:
        raise ValidationError("a brand kit needs a name", parameter="name")
    _require_asset(ctx, logo_asset_id)
    _require_rights_declared(ctx, logo_asset_id)

    kit_id = new_id("bk")
    moment = iso()
    with transaction(ctx.conn):
        ctx.conn.execute(
            "INSERT INTO brand_kits (id, name, logo_asset_id, primary_color,"
            " secondary_color, font_family, created_by, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                kit_id, clean, logo_asset_id, primary_color, secondary_color,
                font_family, ctx.principal.id, moment, moment,
            ),
        )
    return {
        "ok": True,
        "brand_kit_id": kit_id,
        "name": clean,
        "logo_asset_id": logo_asset_id,
        "primary_color": primary_color,
        "secondary_color": secondary_color,
        "font_family": font_family,
        "created_at": moment,
    }


@register("list-brand-kits", "List brand kits, most recently updated first.")
def list_brand_kits(ctx: Context) -> dict[str, Any]:
    rows = ctx.conn.execute(
        "SELECT * FROM brand_kits ORDER BY updated_at DESC"
    ).fetchall()
    return {"ok": True, "count": len(rows), "brand_kits": [dict(r) for r in rows]}


@register(
    "brand-kit",
    "Inspect one brand kit.",
    params=(Param("brand_kit_id", "str"),),
)
def brand_kit(ctx: Context, brand_kit_id: str) -> dict[str, Any]:
    return {"ok": True, "brand_kit": dict(_brand_kit_row(ctx, brand_kit_id))}


@register(
    "update-brand-kit",
    "Change a brand kit's name, logo or colours. Fields left blank keep their current value.",
    params=(
        Param("brand_kit_id", "str"),
        Param("name", "str", required=False),
        Param("logo_asset_id", "str", required=False),
        Param("primary_color", "str", required=False),
        Param("secondary_color", "str", required=False),
        Param("font_family", "str", required=False),
    ),
    mutates=True,
    entity="brand_kit",
)
def update_brand_kit(
    ctx: Context,
    brand_kit_id: str,
    name: str | None = None,
    logo_asset_id: str | None = None,
    primary_color: str | None = None,
    secondary_color: str | None = None,
    font_family: str | None = None,
) -> dict[str, Any]:
    row = _brand_kit_row(ctx, brand_kit_id)

    new_name = row["name"]
    if name is not None:
        clean = name.strip()
        if not clean:
            raise ValidationError("a brand kit needs a name", parameter="name")
        new_name = clean

    new_logo = row["logo_asset_id"]
    if logo_asset_id is not None:
        _require_asset(ctx, logo_asset_id)
        _require_rights_declared(ctx, logo_asset_id)
        new_logo = logo_asset_id

    new_primary = primary_color if primary_color is not None else row["primary_color"]
    new_secondary = secondary_color if secondary_color is not None else row["secondary_color"]
    new_font = font_family if font_family is not None else row["font_family"]

    moment = iso()
    with transaction(ctx.conn):
        ctx.conn.execute(
            "UPDATE brand_kits SET name = ?, logo_asset_id = ?, primary_color = ?,"
            " secondary_color = ?, font_family = ?, updated_at = ? WHERE id = ?",
            (new_name, new_logo, new_primary, new_secondary, new_font, moment, brand_kit_id),
        )
    return {
        "ok": True,
        "brand_kit_id": brand_kit_id,
        "name": new_name,
        "logo_asset_id": new_logo,
        "primary_color": new_primary,
        "secondary_color": new_secondary,
        "font_family": new_font,
        "updated_at": moment,
    }


@register(
    "delete-brand-kit",
    "Delete a brand kit. Any EDL version it was already applied to is untouched (DR-021).",
    params=(Param("brand_kit_id", "str"),),
    mutates=True,
    entity="brand_kit",
    danger="Deletes the brand kit record. Does not affect any project's rendered output.",
)
def delete_brand_kit(ctx: Context, brand_kit_id: str) -> dict[str, Any]:
    _brand_kit_row(ctx, brand_kit_id)
    with transaction(ctx.conn):
        ctx.conn.execute("DELETE FROM brand_kits WHERE id = ?", (brand_kit_id,))
    return {
        "ok": True,
        "brand_kit_id": brand_kit_id,
        "deleted": True,
        "note": (
            "a render never depends on this row (DR-021): any EDL version this"
            " kit was already applied to keeps its own image overlay, byte for"
            " byte, unchanged by this deletion"
        ),
    }
