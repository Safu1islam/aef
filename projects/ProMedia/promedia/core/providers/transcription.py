"""Transcription capability (T-048).

Distinct from T-047's local, ffmpeg-based silence detection and rough-cut
tooling (``promedia/core/media/analyse.py``) — that path needs no paid API
and no seam, and is a different agent's owned work in this parallel run.
This class is for an actual speech-to-text MODEL API, which nothing on this
machine can reach today.
"""

from __future__ import annotations

from .base import BaseCapability


class TranscriptionCapability(BaseCapability):
    kind = "transcription"
    provider_name = "an ASR (automatic speech recognition) API — for example OpenAI's audio transcription endpoint"
    package = "openai"
    credential_env = "OPENAI_API_KEY"
    pricing_reference = "the provider's own current API pricing page, checked at time of use"
    what_it_would_satisfy = (
        "Automated speech-to-text on ingested media, beyond what local, "
        "non-AI silence detection can do (T-047). Any comparable, verified "
        "ASR API satisfies this seam equally — OpenAI's is named here as one "
        "concrete example, not a commitment to that vendor. The exact "
        "package name, credential convention and pricing above are NOT "
        "verified against live documentation by this task (project.md O-3) "
        "and must be confirmed before use."
    )
