"""Publisher selection.

T-019 added live adapters for both platforms (see x.py, linkedin.py), each
implemented against LIVE documentation per project.md O-3. ``allow_simulation``
still routes to the stub unconditionally — that switch exists so the whole
slice can be exercised safely with no network calls and no risk of a real
post, independent of whether real credentials happen to be configured — and
is the ONLY way to reach fabrication F-001. With it off (the default), a
platform call now reaches the real adapter rather than raising
ConfigurationError: T-019's whole purpose was to make that reachable, once it
could be done without guessing at API terms.
"""

from __future__ import annotations

from ...config import Config
from ...errors import ConfigurationError
from .base import Capabilities, Publisher, PublishResult
from .stub import StubPublisher

SUPPORTED_PLATFORMS = ("x", "linkedin")

__all__ = ["Capabilities", "Publisher", "PublishResult", "StubPublisher", "for_platform", "SUPPORTED_PLATFORMS"]


def for_platform(platform: str, config: Config) -> Publisher:
    key = platform.strip().lower()
    if key not in SUPPORTED_PLATFORMS:
        raise ConfigurationError(
            f"unsupported platform '{platform}'", platform=platform, supported=list(SUPPORTED_PLATFORMS)
        )
    allow_simulation = bool(config.get("publishing", "allow_simulation"))
    if allow_simulation:
        return StubPublisher(key, allow_simulation=True)

    timeout = int(config.get("publishing", "request_timeout_seconds"))
    if key == "x":
        from .x import XPublisher

        return XPublisher(request_timeout_seconds=timeout)

    from .linkedin import LinkedInPublisher

    api_version = str(config.get("publishing", "linkedin_api_version"))
    return LinkedInPublisher(request_timeout_seconds=timeout, api_version=api_version)
