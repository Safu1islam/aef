"""T-046 — acquisition (import from a URL), through the rights gate.

Import is a rights event before it is a file operation, exactly like local
ingest (T-008): there is deliberately no code path in ``promedia.core.acquire``
that reaches ``ingest_file`` without a validated declaration, and no path
that writes bytes to the content-addressed store before a real storage
reservation exists.

Registered fabrication (Constitution section 6 / .ai/state/fabrications.yaml):
``FakeDownloader`` below stands in for ``promedia.core.acquire.YtDlpDownloader``
so this suite never depends on the live internet, per this task's brief. It
implements the same ``Downloader`` seam (``probe`` / ``download``) production
code uses, and records every call it receives so tests can assert exactly
what did and did not happen — in particular, that a refusal (missing
declaration, or the ceiling breached by the estimate alone) never reaches
``download`` at all. Replacement condition: a live-network smoke test against
a real, small, unambiguously-freely-licensed source, run outside the
regular suite (this task's report says which one and why).
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from promedia.core import acquire as acquire_layer
from promedia.core import storage
from promedia.core.principal import agent
from promedia.core.registry import Context, invoke
from promedia.errors import CeilingExceeded, PlatformError, ValidationError
from tests.conftest import declaration_original, make_config


class FakeDownloader:
    """Fakes the yt-dlp boundary. Never opens a socket."""

    def __init__(
        self,
        *,
        filesize: int | None = None,
        filesize_approx: int | None = None,
        actual_bytes: bytes = b"acquired media bytes" * 10,
        raise_on_download: Exception | None = None,
    ) -> None:
        self.filesize = filesize
        self.filesize_approx = filesize_approx
        self.actual_bytes = actual_bytes
        self.raise_on_download = raise_on_download
        self.probe_calls: list[str] = []
        self.download_calls: list[str] = []

    def probe(self, url: str) -> dict:
        self.probe_calls.append(url)
        info: dict = {}
        if self.filesize is not None:
            info["filesize"] = self.filesize
        if self.filesize_approx is not None:
            info["filesize_approx"] = self.filesize_approx
        return info

    def download(self, url: str, dest_dir: Path) -> Path:
        self.download_calls.append(url)
        if self.raise_on_download is not None:
            raise self.raise_on_download
        path = dest_dir / "acquired.mp4"
        path.write_bytes(self.actual_bytes)
        return path


# ---------------------------------------------------------------------------
# AC-1: refused without a rights declaration, exactly as local ingest is.
# ---------------------------------------------------------------------------


def test_acquire_refused_without_declaration_through_the_registry(agent_ctx):
    """The registry-level refusal: the same shape ingest's own AC-3 test checks."""
    with pytest.raises(ValidationError) as excinfo:
        invoke(agent_ctx, "acquire", {"url": "https://example.com/video"})
    assert excinfo.value.detail["parameter"] == "declaration"


def test_acquire_url_refuses_a_none_declaration_before_any_network_call(agent_ctx):
    fake = FakeDownloader(filesize=1000)
    with pytest.raises(ValidationError) as excinfo:
        acquire_layer.acquire_url(
            agent_ctx, url="https://example.com/video", declaration=None, downloader=fake
        )
    assert excinfo.value.detail["parameter"] == "declaration"
    assert fake.probe_calls == [], "an unevaluable import must be refused before any network call"
    assert fake.download_calls == []


def test_acquire_rejects_bad_authorship_before_any_network_call(agent_ctx):
    fake = FakeDownloader(filesize=1000)
    with pytest.raises(ValidationError) as excinfo:
        acquire_layer.acquire_url(
            agent_ctx,
            url="https://example.com/video",
            declaration={"authorship": "probably mine"},
            downloader=fake,
        )
    assert excinfo.value.detail["parameter"] == "declaration.authorship"
    assert fake.probe_calls == []
    assert fake.download_calls == []


# ---------------------------------------------------------------------------
# AC-2: storage reserved before downloading; refused if it would breach the
# ceiling; the estimate is reconciled against the real byte count at commit.
# ---------------------------------------------------------------------------


def test_acquire_refuses_over_ceiling_on_the_estimate_alone_and_never_downloads(tmp_path, conn):
    cfg = make_config(tmp_path, **{"storage.ceiling_bytes": 1000})  # refuse at 850
    ctx = Context(config=cfg, conn=conn, principal=agent("t"))
    fake = FakeDownloader(filesize=2000)  # projects to 3000, well over 850

    with pytest.raises(CeilingExceeded) as excinfo:
        acquire_layer.acquire_url(
            ctx, url="https://example.com/too-big", declaration=declaration_original(), downloader=fake
        )

    assert excinfo.value.detail["queued"] is True
    assert fake.probe_calls == ["https://example.com/too-big"]
    assert fake.download_calls == [], "the ceiling must be enforced BEFORE downloading, not after"
    queued = storage.queued(conn)
    assert len(queued) == 1
    assert queued[0]["source_path"] == "https://example.com/too-big"
    assert storage.usage(conn)["total_bytes"] == 0, "a refused reservation must not leak quota"


def test_acquire_reconciles_estimate_against_the_real_byte_count(agent_ctx):
    """The estimate can be wrong in either direction; the ledger must reflect reality."""
    actual = b"y" * 5000
    fake = FakeDownloader(filesize=50_000_000, actual_bytes=actual)  # wildly overestimated

    result = acquire_layer.acquire_url(
        agent_ctx,
        url="https://example.com/video",
        declaration=declaration_original(),
        downloader=fake,
    )

    assert result["ok"] is True
    assert result["estimated_bytes"] == 50_000_000
    assert result["estimate_reported_by_source"] is True
    expected_projected = storage.projected_bytes(agent_ctx.config, len(actual))
    usage = storage.usage(agent_ctx.conn)
    assert usage["committed_bytes"] == expected_projected, (
        "committed usage must be reconciled against the ACTUAL bytes, not the estimate"
    )


def test_acquire_falls_back_to_configured_default_when_source_reports_no_size(agent_ctx):
    fake = FakeDownloader(filesize=None, filesize_approx=None, actual_bytes=b"z" * 1000)

    result = acquire_layer.acquire_url(
        agent_ctx,
        url="https://example.com/unknown-size",
        declaration=declaration_original(),
        downloader=fake,
    )

    assert result["estimate_reported_by_source"] is False
    assert result["estimated_bytes"] == agent_ctx.config.get("acquire", "default_estimate_bytes")


def test_acquire_download_failure_releases_the_estimate_reservation(agent_ctx):
    fake = FakeDownloader(
        filesize=1000, raise_on_download=PlatformError("simulated network failure", url="x", stage="download")
    )
    with pytest.raises(PlatformError):
        acquire_layer.acquire_url(
            agent_ctx, url="https://example.com/flaky", declaration=declaration_original(), downloader=fake
        )
    assert storage.usage(agent_ctx.conn)["total_bytes"] == 0, "a failed download must not leak quota"


# ---------------------------------------------------------------------------
# AC-3: the source URL and acquisition time are recorded as evidence.
# ---------------------------------------------------------------------------


def test_acquire_records_source_url_and_acquisition_time_as_evidence(agent_ctx):
    fake = FakeDownloader(filesize=1000, actual_bytes=b"clip bytes" * 50)

    result = acquire_layer.acquire_url(
        agent_ctx,
        url="https://example.com/watch?v=abc123",
        declaration=declaration_original(),
        downloader=fake,
    )
    assert result["ok"] is True

    detail = invoke(agent_ctx, "asset", {"asset_id": result["asset_id"]})
    matches = [e for e in detail["evidence"] if e["kind"] == "acquisition_source"]
    assert len(matches) == 1, "exactly one acquisition evidence row must exist"
    body = json.loads(matches[0]["body"])
    assert body["source_url"] == "https://example.com/watch?v=abc123"
    datetime.fromisoformat(body["acquired_at"])  # parses; recorded, not invented
    assert matches[0]["produced_by"] == "system"

    # F-5: evidence is not a verdict. Recording the URL must not by itself
    # produce PERMITTED — this asset was declared by an agent, not attested
    # by the operator, so the permitting rules do not fire on it at all.
    verdict = invoke(agent_ctx, "determine-rights", {"asset_id": result["asset_id"]})
    assert verdict["verdict"] != "PERMITTED"


def test_acquire_records_evidence_even_when_the_bytes_were_already_ingested(agent_ctx):
    """Deduplication must not silently drop the acquisition evidence."""
    payload = b"duplicate bytes for acquire" * 20
    first = acquire_layer.acquire_url(
        agent_ctx,
        url="https://example.com/original",
        declaration=declaration_original(),
        downloader=FakeDownloader(filesize=1000, actual_bytes=payload),
    )
    second = acquire_layer.acquire_url(
        agent_ctx,
        url="https://example.com/mirror",
        declaration=declaration_original(),
        downloader=FakeDownloader(filesize=1000, actual_bytes=payload),
    )
    assert second["asset_id"] == first["asset_id"]
    assert second["duplicate"] is True

    detail = invoke(agent_ctx, "asset", {"asset_id": first["asset_id"]})
    urls = {
        json.loads(e["body"])["source_url"]
        for e in detail["evidence"]
        if e["kind"] == "acquisition_source"
    }
    assert urls == {"https://example.com/original", "https://example.com/mirror"}


# ---------------------------------------------------------------------------
# Reachable end to end through the operation registry (what the CLI and the
# web surface actually call) — the real YtDlpDownloader is swapped out so
# this still never touches the network.
# ---------------------------------------------------------------------------


def test_acquire_reachable_through_the_registry(agent_ctx, monkeypatch):
    fake = FakeDownloader(filesize=500, actual_bytes=b"registry path bytes" * 20)
    monkeypatch.setattr(acquire_layer, "YtDlpDownloader", lambda config: fake)

    result = invoke(
        agent_ctx,
        "acquire",
        {"url": "https://example.com/video", "declaration": declaration_original()},
    )

    assert result["ok"] is True
    assert result["source_url"] == "https://example.com/video"
    assert fake.probe_calls == ["https://example.com/video"]
    assert fake.download_calls == ["https://example.com/video"]
