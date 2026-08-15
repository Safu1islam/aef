"""Media acquisition — import from a URL, through the rights gate (T-046).

``yt-dlp`` 2026.07.04 is installed on this machine and nothing called it.
Downloading someone else's video is the single most likely way unusable
material enters this system, so import is a RIGHTS EVENT before it is a file
operation, exactly like local ingest (T-008): it lands in the content-
addressed store WITH a rights declaration, and there is deliberately no code
path that acquires media without one.

This module does not grow a second ingest path. F-1 says two implementations
of one capability is a defect, not an optimisation, so the job here is
narrow: resolve the URL, reserve storage for the PROJECTED size, download to
a temp location, and hand the resulting local file to the EXISTING ingest
capability (``promedia.core.ingest.ingest_file``) with its declaration.
Hashing, storage accounting and declaration validation all still happen
there, exactly once, exactly as they do for a local file.

Reserve-before-you-know-the-size, solved honestly
---------------------------------------------------
You cannot know the real byte count of a remote video before you have it,
but F-7 requires the ceiling to be enforced BEFORE bytes land, not after.
``yt-dlp`` can report ``filesize`` / ``filesize_approx`` from a metadata-only
probe (``extract_info(..., download=False)``) without pulling any media
bytes. That number is treated as exactly what it is — an ESTIMATE the source
reports, not a measurement — and is used to take a REAL storage reservation
(``storage.reserve``) before a single byte is downloaded. If the estimate
alone would breach the ceiling, the import is refused (and queued, per F-7)
without ever opening a connection to fetch the media itself.

Once the download finishes, the estimate is reconciled against reality: the
estimate reservation is released, and ``ingest_file`` takes its OWN
reservation against the ACTUAL byte count and commits it. The authoritative
accounting step — the one the ceiling is actually measured against — happens
exactly once, in exactly the place local ingest already does it. This means
the ceiling is checked twice, honestly: once before download (estimate, so a
doomed download is never attempted), once at commit (actual, so a source
that under-reports its size cannot slip through). Never only after the bytes
already exist on disk.

When a source reports no size at all, ``acquire.default_estimate_bytes``
(configuration — protocol 05 forbids a literal here) is reserved instead of
refusing outright. Many ordinary sources omit ``filesize`` while still being
modest in size, and refusing every such import would make the feature
unusable for the common case; the real ceiling is still enforced honestly
against the real bytes at commit time, so a source that turns out huge is
still caught — in a temp file that is deleted, never inside the object
store — rather than silently absorbed.
"""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path
from typing import Any, Protocol

from ..errors import CeilingExceeded, PlatformError, ValidationError
from . import ingest as ingest_layer
from . import rights as rights_layer
from . import storage
from .db import iso
from .registry import Context


class Downloader(Protocol):
    """The seam production code and tests download through.

    Kept narrow and swappable deliberately (Constitution section 6 /
    NON-NEGOTIABLES browser-automation-style caution about external
    boundaries): production talks to yt-dlp; tests inject a fake that writes
    bytes locally and reports a size, so the test suite never depends on the
    live internet.
    """

    def probe(self, url: str) -> dict[str, Any]:
        """Metadata only. Must not download any media bytes."""
        ...

    def download(self, url: str, dest_dir: Path) -> Path:
        """Download the media into ``dest_dir``; return the path to the file."""
        ...


class YtDlpDownloader:
    """Real acquisition via yt-dlp (2026.07.04, installed on this machine).

    ``yt_dlp`` is imported lazily inside the methods below, not at module
    import time: ``promedia/core/ops/__init__.py`` imports every operation
    module unconditionally, so an eager import here would add yt-dlp's
    (non-trivial) import cost to every CLI cold start (C-4), including
    invocations that never call ``acquire`` at all.
    """

    def __init__(self, config: Any) -> None:
        self._config = config

    def _base_opts(self) -> dict[str, Any]:
        return {
            "quiet": True,
            "no_warnings": True,
            # A URL resolving to a playlist must not silently fan out into
            # dozens of downloads against one storage reservation.
            "noplaylist": True,
            "format": str(self._config.get("acquire", "format")),
            "socket_timeout": float(self._config.get("acquire", "socket_timeout_seconds")),
        }

    def probe(self, url: str) -> dict[str, Any]:
        import yt_dlp

        try:
            with yt_dlp.YoutubeDL(self._base_opts()) as ydl:
                info = ydl.extract_info(url, download=False)
        except yt_dlp.utils.DownloadError as exc:
            raise PlatformError(
                f"yt-dlp could not resolve '{url}': {exc}", url=url, stage="probe"
            ) from exc
        if info is None:
            raise PlatformError(
                f"yt-dlp returned no metadata for '{url}'", url=url, stage="probe"
            )
        return info

    def download(self, url: str, dest_dir: Path) -> Path:
        import yt_dlp

        opts = {**self._base_opts(), "outtmpl": str(dest_dir / "%(id)s.%(ext)s")}
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)
                filename = ydl.prepare_filename(info)
        except yt_dlp.utils.DownloadError as exc:
            raise PlatformError(
                f"yt-dlp could not download '{url}': {exc}", url=url, stage="download"
            ) from exc
        path = Path(filename)
        if path.is_file():
            return path
        # A postprocessor (merge, remux) can change the extension yt-dlp
        # itself predicted at extract_info time. Exactly one file should
        # exist in a directory this call created fresh; if not, something is
        # genuinely ambiguous and must be reported, not guessed at.
        candidates = [p for p in dest_dir.iterdir() if p.is_file()]
        if len(candidates) == 1:
            return candidates[0]
        raise PlatformError(
            f"yt-dlp reported '{filename}' but it is not on disk, and"
            f" {len(candidates)} candidate file(s) were found instead",
            url=url,
            stage="download",
            expected=filename,
            candidates=[str(c) for c in candidates],
        )


def acquire_url(
    ctx: Context,
    *,
    url: str,
    declaration: dict[str, Any] | None,
    derived_from: str | None = None,
    downloader: Downloader | None = None,
) -> dict[str, Any]:
    if not url or not url.strip():
        raise ValidationError("a source url is required", parameter="url")

    # AC-1: refused before any network call at all, and via the SAME
    # validation local ingest applies — this calls ingest's own function
    # rather than re-implementing the rule, so the two paths cannot drift
    # apart (F-1). ingest.py is deliberately not owned by this module.
    decl = dict(ingest_layer._validate_declaration(declaration))
    # The URL actually fetched is the authoritative source for this field;
    # it supersedes anything hand-typed in the declaration, because the
    # declaration is written before the fetch resolves redirects/canonical
    # URLs and this module is the one place that knows what was truly used.
    decl["source_url"] = url

    dl = downloader or YtDlpDownloader(ctx.config)

    info = dl.probe(url)
    reported = info.get("filesize")
    if reported is None:
        reported = info.get("filesize_approx")
    estimate_reported = reported is not None
    estimated_bytes = int(reported) if estimate_reported else int(
        ctx.config.get("acquire", "default_estimate_bytes")
    )

    projected_estimate = storage.projected_bytes(ctx.config, estimated_bytes)

    # AC-2, part 1: reserve BEFORE downloading, against the estimate.
    try:
        reservation_id = storage.reserve(
            ctx.conn, ctx.config, master_bytes=estimated_bytes, kind="master"
        )
    except CeilingExceeded as exc:
        # F-7: refused, not discarded — queued exactly as a local ingest
        # over the ceiling is, with the URL standing in for a source path so
        # a future retry knows what to re-fetch.
        queue_id = storage.enqueue_refused(
            ctx.conn,
            source_path=url,
            projected=projected_estimate,
            declaration=decl,
            shortfall_bytes=int(exc.detail.get("shortfall_bytes", 0)),
        )
        exc.detail["queued_as"] = queue_id
        exc.detail["queued"] = True
        exc.detail["estimated_bytes"] = estimated_bytes
        exc.detail["estimate_reported_by_source"] = estimate_reported
        raise

    tmp_dir = Path(tempfile.mkdtemp(prefix="promedia-acquire-"))
    acquired_at = iso()
    try:
        try:
            downloaded = dl.download(url, tmp_dir)
        except Exception:
            # A failed download must not leak the estimate's quota, same
            # reasoning as ingest_file's own reservation handling.
            storage.release(ctx.conn, reservation_id)
            raise

        # AC-2, part 2: reconcile the estimate against reality. The estimate
        # only ever existed to hold the ceiling closed during the download
        # window; ingest_file() below takes its OWN reservation against the
        # REAL byte count and commits it — that is the authoritative
        # accounting step (F-7), so the estimate is released, not committed.
        storage.release(ctx.conn, reservation_id)

        result = ingest_layer.ingest_file(
            ctx, source_path=str(downloaded), declaration=decl, derived_from=derived_from
        )
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    if result.get("ok"):
        # AC-3: the source URL and acquisition time are evidence, not a
        # verdict (F-5) — recorded via the same evidence table a model's
        # observations go into, attributed to 'system' because this process,
        # not a caller-supplied claim, observed it directly.
        recorded = rights_layer.add_evidence(
            ctx,
            asset_id=result["asset_id"],
            kind="acquisition_source",
            body=json.dumps(
                {"source_url": url, "acquired_at": acquired_at, "downloader": "yt-dlp"},
                sort_keys=True,
            ),
            produced_by="system",
        )
        result = {
            **result,
            "source_url": url,
            "acquired_at": acquired_at,
            "evidence_id": recorded["evidence_id"],
            "estimated_bytes": estimated_bytes,
            "estimate_reported_by_source": estimate_reported,
        }
    return result
