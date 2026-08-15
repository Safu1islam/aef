"""AI capability providers (T-048).

Mirrors DR-010 (the publisher pattern): one interface per AI capability
kind, each able to report whether it can run, exactly what would make it
able to, a cost estimate that is UNKNOWN unless independently verified, and
a ``run()`` that only ever executes for real when both are true — which is
never, on this machine, today, and that is the expected, correct outcome
(see T-048's task note), not a problem to paper over.

Five capability kinds: transcription, text, speech, image, video. ``text``
is included for interface completeness but is deliberately NOT how ProMedia
does agent-driven text generation — see ``providers/text.py`` for why an
agent must never call it.

``promedia/core/ops/providers.py`` is where these become operations,
reachable from both surfaces (F-1). ``promedia/core/providers/spend.py`` is
the C-31 ledger every real call would need to clear, and which nothing here
ever bypasses.
"""

from __future__ import annotations

from ...errors import ValidationError
from .base import (
    Capability,
    Estimate,
    ProviderUnavailable,
    Requirement,
    Requirements,
    UNKNOWN,
)
from .image import ImageCapability
from .speech import SpeechCapability
from .text import TextCapability
from .transcription import TranscriptionCapability
from .video import VideoCapability

CAPABILITIES: dict[str, Capability] = {
    "transcription": TranscriptionCapability(),
    "text": TextCapability(),
    "speech": SpeechCapability(),
    "image": ImageCapability(),
    "video": VideoCapability(),
}

__all__ = [
    "Capability",
    "Estimate",
    "ProviderUnavailable",
    "Requirement",
    "Requirements",
    "UNKNOWN",
    "CAPABILITIES",
    "for_capability",
]


def for_capability(kind: str) -> Capability:
    key = kind.strip().lower()
    if key not in CAPABILITIES:
        raise ValidationError(
            f"unknown AI capability '{kind}'", capability=kind, known=sorted(CAPABILITIES)
        )
    return CAPABILITIES[key]
