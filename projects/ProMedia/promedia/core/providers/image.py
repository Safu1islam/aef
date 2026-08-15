"""Image-generation capability (T-048)."""

from __future__ import annotations

from .base import BaseCapability


class ImageCapability(BaseCapability):
    kind = "image"
    provider_name = "an image-generation API — for example a hosted diffusion or DALL-E-style endpoint"
    package = "stability_sdk"
    credential_env = "STABILITY_API_KEY"
    pricing_reference = "the provider's own current API pricing page, checked at time of use"
    what_it_would_satisfy = (
        "Generated thumbnails or cover art for a post or a project. Any "
        "comparable, verified image-generation API satisfies this seam "
        "equally — the package named here is one concrete example and is "
        "explicitly unverified, not a commitment to that vendor. The exact "
        "package name, credential convention and pricing above are NOT "
        "verified against live documentation by this task (project.md O-3) "
        "and must be confirmed before use."
    )
