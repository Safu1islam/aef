"""Acquisition operation (T-046).

Import from a URL is agent authority, same as local ``ingest`` — pulling
media in has no external effect and spends nothing beyond local disk. What an
agent still cannot do is make the result publishable: the rights gate
(T-009) governs both paths identically, and this operation cannot see or
touch a ``PERMITTED`` verdict.
"""

from __future__ import annotations

from typing import Any

from .. import acquire as acquire_layer
from ..registry import Context, Param, register


@register(
    "acquire",
    "Import media from a URL via yt-dlp, through the same rights gate as local ingest.",
    params=(
        Param("url", "str", help="Source URL to resolve and download."),
        Param(
            "declaration",
            "json",
            help=(
                'Rights declaration, e.g. {"authorship":"third_party",'
                '"third_party_material":["entire video"]}. Required — an import that'
                " cannot be evaluated must never become publishable, exactly as local"
                " ingest."
            ),
        ),
        Param("derived_from", "str", required=False, help="Source asset id, if this is a derivative."),
    ),
    mutates=True,
    entity="asset",
)
def acquire(
    ctx: Context,
    url: str,
    declaration: dict[str, Any],
    derived_from: str | None = None,
) -> dict[str, Any]:
    return acquire_layer.acquire_url(
        ctx, url=url, declaration=declaration, derived_from=derived_from
    )
