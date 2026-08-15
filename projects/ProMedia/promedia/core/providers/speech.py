"""Speech-synthesis (text-to-speech) capability (T-048)."""

from __future__ import annotations

from .base import BaseCapability


class SpeechCapability(BaseCapability):
    kind = "speech"
    provider_name = "a text-to-speech API — for example ElevenLabs"
    package = "elevenlabs"
    credential_env = "ELEVENLABS_API_KEY"
    pricing_reference = "the provider's own current API pricing page, checked at time of use"
    what_it_would_satisfy = (
        "Synthesised voiceover for a video's audio track. Any comparable, "
        "verified TTS API satisfies this seam equally — ElevenLabs is named "
        "here as one concrete example, not a commitment to that vendor. The "
        "exact package name, credential convention and pricing above are "
        "NOT verified against live documentation by this task (project.md "
        "O-3) and must be confirmed before use."
    )
