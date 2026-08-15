"""Stub publisher — REGISTERED FABRICATION F-001.

This does not publish anything. It exists so the v1 slice can be exercised end
to end without credentials, and it is the highest-risk fabrication in the
system: a simulated publish that looked real would be indistinguishable from a
real one in the UI, which is precisely how a fabrication reaches a user.

Three guards, all deliberate:
  * unreachable unless publishing.allow_simulation is explicitly true;
  * every result carries simulated=True, which reaches the publication record
    and the UI;
  * the platform post id is visibly fake, not a plausible-looking identifier.

Replaced by T-019 when the operator supplies credentials.
"""

from __future__ import annotations

from typing import Any

from ...errors import ConfigurationError
from ..db import iso, new_id
from .base import UNKNOWN, Capabilities, PublishResult

SIMULATED_MARKER = "SIMULATED-NOT-PUBLISHED"


class StubPublisher:
    """Simulates publication. Publishes nothing, anywhere, ever."""

    simulated = True

    def __init__(self, platform: str, *, allow_simulation: bool) -> None:
        if not allow_simulation:
            raise ConfigurationError(
                "the stub publisher is a fabrication (F-001) and is disabled;"
                " set publishing.allow_simulation = true to exercise the slice"
                " without credentials, or supply real credentials (T-019)",
                platform=platform,
                fabrication="F-001",
            )
        self.platform = platform

    def capabilities(self) -> Capabilities:
        return Capabilities(
            platform=self.platform,
            max_body_chars=UNKNOWN,
            max_media_bytes=UNKNOWN,
            posts_per_day=UNKNOWN,
            rate_limit_note=UNKNOWN,
            verified_against_documentation=False,
        )

    def publish(self, *, body: str, content_hash: str, credential_ref: str) -> PublishResult:
        # The id announces itself. A realistic-looking id is how a simulated
        # publish gets mistaken for a real one.
        fake_id = f"{SIMULATED_MARKER}-{new_id('sim')}"
        return PublishResult(
            platform_post_id=fake_id,
            permalink=None,
            published_at=iso(),
            simulated=True,
            detail={
                "warning": "NOTHING WAS PUBLISHED. This is fabrication F-001.",
                "platform": self.platform,
                "body_chars": len(body),
                "content_hash": content_hash,
                "credential_ref": credential_ref,
            },
        )

    def verify_published(self, platform_post_id: str, *, credential_ref: str) -> bool:
        """Always False.

        A stub must never assert that something is live on a platform: that
        assertion is the precondition for deleting the master, and a false one
        would authorise irreversible deletion of media that was never published.
        ``credential_ref`` is accepted (T-019 widened the protocol) and ignored
        — a stub reaches no network and needs no credential either way.
        """
        return False


def capabilities_for_real_platform(platform: str) -> dict[str, Any]:
    """Limits for a real platform.

    T-019 populated verified numbers for x/linkedin behind their own adapter
    modules (each cites the live documentation it came from — O-3). Anything
    not verified there, and every other platform string, still reads as
    UNKNOWN rather than a guess — T-012 AC-3's discipline, unchanged by this
    task; what changed is that "unverified" no longer means "everything",
    because some fields now genuinely are verified.
    """
    key = platform.strip().lower()
    if key == "x":
        from .x import CAPABILITIES as X_CAPABILITIES

        return X_CAPABILITIES.to_dict()
    if key == "linkedin":
        from .linkedin import CAPABILITIES as LINKEDIN_CAPABILITIES

        return LINKEDIN_CAPABILITIES.to_dict()
    return Capabilities(platform=platform).to_dict()
