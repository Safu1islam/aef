"""Video-generation capability (T-048)."""

from __future__ import annotations

from .base import BaseCapability


class VideoCapability(BaseCapability):
    kind = "video"
    provider_name = "a video-generation API — for example a hosted text-to-video or image-to-video service"
    package = "runwayml"
    credential_env = "RUNWAY_API_KEY"
    pricing_reference = "the provider's own current API pricing page, checked at time of use"
    what_it_would_satisfy = (
        "Generated video clips or B-roll from a text or image prompt. This "
        "category is new and providers change quickly, so the name, package "
        "and credential convention above are explicitly unverified — one "
        "concrete illustration, not a commitment. The exact package name, "
        "credential convention and pricing must be confirmed against "
        "current live documentation before use (project.md O-3), more so "
        "here than for any other capability in this module."
    )
