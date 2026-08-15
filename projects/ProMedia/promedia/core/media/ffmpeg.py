"""The ffmpeg boundary (T-041).

Every call to ffmpeg or ffprobe in this system goes through here. One boundary
means one place that knows the platform's quirks, one place that parses errors
into structured refusals, and one place to change when a codec or flag moves.

Two findings from bringing ffmpeg up on this machine are encoded below rather
than left as folklore, because both cost real time to discover:

* **drawtext requires an explicit fontfile on Windows.** The gyan.dev build
  ships without a fontconfig configuration, so ``drawtext`` without a
  ``fontfile=`` argument does not fall back to a default — it SEGFAULTS. A
  segfault gives no error message to parse, so a caption feature built without
  knowing this would fail in a way that looks like a bug in the caller.
* **Quick Sync is worth using for size, not speed.** Measured on this hardware:
  x264 veryfast 4.2x realtime at 10.4 MB, h264_qsv 3.7x realtime at 5.4 MB for
  the same 60-second source. The filter chain runs on the CPU either way, so
  hardware encoding accelerates only the encode step — the win is compression
  efficiency, which is why it is the default for delivery rather than for speed.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from ...errors import ConfigurationError, ProMediaError

# Windows fonts that exist on every install. drawtext needs a real file (see
# module docstring); this is the fallback when a caller names no font.
DEFAULT_FONT_CANDIDATES = (
    Path("C:/Windows/Fonts/arial.ttf"),
    Path("C:/Windows/Fonts/segoeui.ttf"),
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
)


class MediaToolMissing(ConfigurationError):
    """ffmpeg or ffprobe is not installed.

    Its own class because it is the one failure with a single, actionable fix,
    and because it must never be reported as a media problem — the media is
    fine, the machine is not equipped.
    """

    code = "MEDIA_TOOL_MISSING"


class RenderFailed(ProMediaError):
    """ffmpeg ran and refused. Carries its own last words, which are the only
    useful diagnostic ffmpeg produces."""

    code = "RENDER_FAILED"


@dataclass(frozen=True)
class MediaInfo:
    """What a file actually is, as opposed to what its name suggests."""

    duration_seconds: float | None
    width: int | None
    height: int | None
    frame_rate: float | None
    video_codec: str | None
    audio_codec: str | None
    sample_rate: int | None
    channels: int | None
    byte_size: int
    has_video: bool
    has_audio: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "duration_seconds": self.duration_seconds,
            "width": self.width,
            "height": self.height,
            "frame_rate": self.frame_rate,
            "video_codec": self.video_codec,
            "audio_codec": self.audio_codec,
            "sample_rate": self.sample_rate,
            "channels": self.channels,
            "byte_size": self.byte_size,
            "has_video": self.has_video,
            "has_audio": self.has_audio,
        }


def tool_path(name: str) -> str | None:
    return shutil.which(name)


def available() -> bool:
    return tool_path("ffmpeg") is not None and tool_path("ffprobe") is not None


def require(name: str = "ffmpeg") -> str:
    """The binary's path, or a refusal that says exactly what to install."""
    found = tool_path(name)
    if found is None:
        raise MediaToolMissing(
            f"{name} is not installed, so no media operation can run",
            tool=name,
            remedy="winget install Gyan.FFmpeg  (then restart the shell)",
        )
    return found


def default_font() -> Path | None:
    for candidate in DEFAULT_FONT_CANDIDATES:
        if candidate.is_file():
            return candidate
    return None


def escape_for_filter(value: str) -> str:
    """Escape TEXT for use inside a filtergraph argument.

    ffmpeg's filter syntax uses ``:`` to separate arguments, ``'`` to quote and
    ``\\`` to escape, so a caption containing any of them corrupts the graph.
    This is the difference between a caption feature and a filter-injection bug,
    and captions come from humans and models — they will contain apostrophes.

    NOT for paths. See escape_path_for_filter, which follows a different rule;
    using this one on a Windows path is what produced ``C\\:\\\\Windows`` and a
    filter the parser rejects.
    """
    return (
        value.replace("\\", "\\\\")
        .replace(":", "\\:")
        .replace("'", "\\'")
        .replace("%", "\\%")
    )


def escape_path_for_filter(path: Path | str) -> str:
    """Escape a PATH for a filtergraph argument.

    Verified empirically against this ffmpeg build, because the rule is not the
    same as for text and the difference is invisible until the graph fails:

      * separators must be FORWARD SLASHES. A backslash is an escape character
        to the filter parser, so ``C:\\Windows`` either escapes the next
        character or, doubled, produces a path that does not exist.
      * the drive colon must still be escaped, even inside quotes.

    So the only form that parses is ``C\\:/Windows/Fonts/arial.ttf``.
    """
    return str(path).replace("\\", "/").replace(":", "\\:")


def font_argument(font: Path | None = None) -> str:
    """``fontfile=...`` for drawtext, escaped, never empty.

    Raises rather than emitting a drawtext without a font, because the failure
    that would cause is a segfault (see module docstring) — the least
    debuggable outcome available.
    """
    chosen = font or default_font()
    if chosen is None:
        raise MediaToolMissing(
            "no usable font file found for text rendering",
            remedy="supply an explicit font path; drawtext cannot run without one",
        )
    return f"fontfile='{escape_path_for_filter(chosen)}'"


def probe(path: Path) -> MediaInfo:
    """Read what a media file contains. Never guesses.

    A field ffprobe does not report stays None rather than becoming a plausible
    default — the same rule ingest already follows, because a guessed duration
    is a number later arithmetic silently trusts.
    """
    binary = require("ffprobe")
    if not path.is_file():
        raise ProMediaError(f"no media file at {path}", path=str(path))
    result = subprocess.run(
        [binary, "-v", "quiet", "-print_format", "json", "-show_format", "-show_streams", str(path)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RenderFailed(
            f"ffprobe could not read {path.name}",
            path=str(path),
            detail=(result.stderr or "").strip()[-400:],
        )
    try:
        data = json.loads(result.stdout)
    except ValueError as exc:
        raise RenderFailed(f"ffprobe returned unreadable output for {path.name}") from exc

    fmt = data.get("format", {})
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)

    return MediaInfo(
        duration_seconds=_float(fmt.get("duration")),
        width=_int(video.get("width")) if video else None,
        height=_int(video.get("height")) if video else None,
        frame_rate=_ratio(video.get("r_frame_rate")) if video else None,
        video_codec=video.get("codec_name") if video else None,
        audio_codec=audio.get("codec_name") if audio else None,
        sample_rate=_int(audio.get("sample_rate")) if audio else None,
        channels=_int(audio.get("channels")) if audio else None,
        byte_size=path.stat().st_size,
        has_video=video is not None,
        has_audio=audio is not None,
    )


def run(args: Sequence[str], *, timeout_seconds: float) -> str:
    """Execute ffmpeg. Returns stderr, which is where ffmpeg reports everything.

    A timeout is mandatory rather than optional: a malformed filter graph can
    make ffmpeg run effectively forever, and an operation that never returns is
    worse than one that fails, because a lock is held for the whole of it.
    """
    binary = require("ffmpeg")
    command = [binary, "-hide_banner", "-nostdin", "-y", *args]
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, timeout=timeout_seconds, check=False
        )
    except subprocess.TimeoutExpired as exc:
        raise RenderFailed(
            f"render exceeded its {timeout_seconds:.0f}s budget and was stopped",
            timeout_seconds=timeout_seconds,
        ) from exc
    if result.returncode != 0:
        # ffmpeg's diagnostics are the last few lines; earlier output is banner
        # and progress noise that buries the actual cause.
        tail = "\n".join((result.stderr or "").strip().splitlines()[-6:])
        raise RenderFailed(
            "ffmpeg refused this render",
            exit_code=result.returncode,
            detail=tail[-800:],
        )
    return result.stderr or ""


def _float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _ratio(value: Any) -> float | None:
    """'30000/1001' -> 29.97. Frame rates are rationals, not decimals."""
    if not isinstance(value, str) or "/" not in value:
        return _float(value)
    numerator, _, denominator = value.partition("/")
    try:
        den = float(denominator)
        return float(numerator) / den if den else None
    except (TypeError, ValueError, ZeroDivisionError):
        return None
