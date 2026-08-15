"""T-012 — the stub is a fabrication and must behave like one."""

from __future__ import annotations

import pytest

from promedia.core import publishers
from promedia.core.publishers.base import UNKNOWN
from promedia.core.publishers.stub import SIMULATED_MARKER, StubPublisher
from promedia.core.publishers.x import XPublisher
from promedia.errors import ConfigurationError
from tests.conftest import make_config


def test_stub_requires_explicit_simulation_flag(config, tmp_path):
    """AC-2: a fabrication must not be reachable by default.

    UPDATED FOR T-019 (real adapters landed behind the frozen interface).
    This used to assert that for_platform() RAISED ConfigurationError without
    the flag, because the stub was the only implementation that existed.
    publishers/__init__.py's own module docstring documents the deliberate
    change: without the flag, a platform call now reaches the REAL adapter
    rather than raising — "T-019's whole purpose was to make that reachable,
    once it could be done without guessing at API terms." The stub
    (fabrication F-001) is reachable ONLY through the explicit flag; without
    it, construction returns a real adapter, not an error and not the stub.
    """
    publisher = publishers.for_platform("x", config)
    assert isinstance(publisher, XPublisher)
    assert not isinstance(publisher, StubPublisher)

    simulated_cfg = make_config(tmp_path, **{"publishing.allow_simulation": True})
    assert isinstance(publishers.for_platform("x", simulated_cfg), StubPublisher)


def test_stub_marks_simulated(tmp_path):
    """AC-1: and the marker is visibly fake, not a plausible id."""
    cfg = make_config(tmp_path, **{"publishing.allow_simulation": True})
    publisher = publishers.for_platform("x", cfg)
    result = publisher.publish(body="hello", content_hash="abc", credential_ref="x:me")
    assert result.simulated is True
    assert SIMULATED_MARKER in result.platform_post_id
    assert "NOTHING WAS PUBLISHED" in result.detail["warning"]


def test_stub_never_confirms_published(tmp_path):
    """verify_published gates irreversible deletion. A stub must never assert live.

    UPDATED FOR T-019: verify_published() gained a required keyword-only
    credential_ref parameter (base.py) once a real adapter needed to make an
    authenticated read to confirm a post is live — the frozen interface only
    exposed the gap once a live implementation tried to satisfy it. The stub
    accepts and ignores it, per its own contract.
    """
    cfg = make_config(tmp_path, **{"publishing.allow_simulation": True})
    publisher = publishers.for_platform("linkedin", cfg)
    assert publisher.verify_published("anything", credential_ref="linkedin:test") is False


def test_real_platform_limits_are_unknown_not_guessed(agent_ctx):
    """AC-3: operator instruction — no rate limits from model memory.

    UPDATED FOR T-019: this used to assert every field was blanket UNKNOWN,
    because no field had ever been verified. T-019 verified X's own posting
    limit against live documentation (docs.x.com/x-api/getting-started/pricing,
    fetched 2026-08-13, cited in publishers/x.py's CAPABILITIES) and now
    reports it — T-012 AC-3's actual discipline was always "unknown reads as
    unknown", not "every field is unknown forever"; a known field pretending
    to be unknown would itself be dishonest. Fields still unverified (media
    upload byte limit, a fixed per-endpoint write-rate — X is pay-per-use
    instead) still correctly read UNKNOWN.
    """
    from promedia.core.registry import invoke

    caps = invoke(agent_ctx, "platform-capabilities", {"platform": "x"})
    assert caps["max_body_chars"] == 280
    # x.py states its own UNKNOWN reason per field rather than the shared
    # base.UNKNOWN sentinel literally — both still read as honestly unknown,
    # never a plausible guess.
    assert str(caps["max_media_bytes"]).startswith("UNKNOWN")
    assert str(caps["posts_per_day"]).startswith("UNKNOWN")
    assert caps["verified_against_documentation"] is True


def test_unsupported_platform_rejected(tmp_path):
    cfg = make_config(tmp_path, **{"publishing.allow_simulation": True})
    with pytest.raises(ConfigurationError):
        publishers.for_platform("myspace", cfg)
