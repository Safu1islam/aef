"""Deterministic analysis and transcription (T-047).

Two capabilities that together get most of the way to "remove the unnecessary
parts" from a screen recording, and they are deliberately kept apart because
they have opposite honesty profiles:

* **Silence and scene-change detection need no model at all.** ffmpeg's
  ``silencedetect`` filter is deterministic — the same audio always reports the
  same spans — so it is testable exactly the way ``tests/test_edl.py`` tests
  compilation: generate media with a KNOWN property and assert detection finds
  it. ``propose_rough_cut`` turns those spans into an EDL, but never applies
  it — see the module-level note on that function.

* **Transcription needs faster-whisper**, which this machine may not have. The
  one failure mode this module refuses categorically is a transcription
  operation that returns empty segments, or invented text, and reports
  success — that is the exact shape of fabrication F-003 (a silent
  substitution that succeeds) and Constitution section 6 forbids it.
  ``require_transcription`` raises a structured refusal naming exactly what
  would satisfy the capability; nothing here ever fakes a transcript.

Both halves stay inside the EDL vocabulary T-041 already shipped
(``media/edl.py``, owned by T-045 in this run and read-only from here):
transcript segments become ``TextOverlay`` burned-in captions, and a rough cut
becomes an ordinary list of ``Clip``. Nothing here needs the vocabulary
extended.
"""

from __future__ import annotations

import importlib.util
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ...errors import ConfigurationError, ValidationError
from . import ffmpeg
from .edl import EDL, Clip, TextOverlay

# --- errors -------------------------------------------------------------
# Local subclasses rather than additions to promedia/errors.py, which this
# task does not own — the same pattern ffmpeg.py uses for MediaToolMissing.


class TranscriptionUnavailable(ConfigurationError):
    """faster-whisper (or a model for it) is not installed.

    Its own class, distinct from MediaToolMissing, because the fix is a Python
    package plus a model download rather than a system binary, and because it
    must never be reported as "no speech found" — the capability is absent,
    the media is not empty.
    """

    code = "TRANSCRIPTION_UNAVAILABLE"


# --- silence detection ----------------------------------------------------


@dataclass(frozen=True)
class SilenceSpan:
    """One span ffmpeg's ``silencedetect`` reported as below the noise floor."""

    start: float
    end: float

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)

    def to_dict(self) -> dict[str, Any]:
        return {"start": self.start, "end": self.end, "duration": self.duration}


_SILENCE_START_RE = re.compile(r"silence_start:\s*(-?\d+(?:\.\d+)?)")
_SILENCE_END_RE = re.compile(r"silence_end:\s*(-?\d+(?:\.\d+)?)")


def detect_silence(
    path: Path,
    *,
    noise_threshold_db: float,
    min_silence_seconds: float,
    timeout_seconds: float,
) -> list[SilenceSpan]:
    """Run ffmpeg's ``silencedetect`` and parse the spans it reports.

    Deterministic and needs no model: the same audio at the same threshold
    always reports the same spans, which is what makes this testable with
    fixtures of a known shape (``tests/test_analyse.py``) rather than only by
    inspection.

    A trailing silence that runs to the end of the file emits a
    ``silence_start`` with no matching ``silence_end`` from ffmpeg itself in
    some builds, so a dangling start is closed at the probed duration rather
    than dropped — dropping it would silently un-report real trailing silence.
    """
    info = ffmpeg.probe(path)
    if not info.has_audio:
        raise ValidationError(
            f"{path.name} has no audio track, so silence detection has nothing to measure",
            path=str(path),
        )

    stderr = ffmpeg.run(
        [
            "-i", str(path),
            "-af", f"silencedetect=noise={noise_threshold_db:g}dB:d={min_silence_seconds:g}",
            "-f", "null", "-",
        ],
        timeout_seconds=timeout_seconds,
    )

    spans: list[SilenceSpan] = []
    pending_start: float | None = None
    for line in stderr.splitlines():
        start_match = _SILENCE_START_RE.search(line)
        if start_match:
            pending_start = float(start_match.group(1))
            continue
        end_match = _SILENCE_END_RE.search(line)
        if end_match and pending_start is not None:
            spans.append(SilenceSpan(start=pending_start, end=float(end_match.group(1))))
            pending_start = None

    if pending_start is not None:
        # Trailing silence: close it at the file's own duration rather than
        # discarding a span ffmpeg genuinely reported starting.
        closing = info.duration_seconds if info.duration_seconds is not None else pending_start
        if closing > pending_start:
            spans.append(SilenceSpan(start=pending_start, end=closing))

    return spans


# --- scene-change detection -------------------------------------------------

_SCENE_PTS_RE = re.compile(r"pts_time:\s*(-?\d+(?:\.\d+)?)")


def detect_scene_changes(
    path: Path,
    *,
    threshold: float,
    timeout_seconds: float,
) -> list[float]:
    """Timestamps ffmpeg's ``select='gt(scene,threshold)'`` flags as hard cuts.

    Deterministic, like silence detection, and informational rather than
    decision-making here: ``propose_rough_cut`` cuts on SILENCE, because that
    is what AC-2 asks for, and this is reported alongside it so an operator
    reviewing the proposal can see where the footage itself changes, which
    silence alone does not tell them.
    """
    info = ffmpeg.probe(path)
    if not info.has_video:
        raise ValidationError(
            f"{path.name} has no video track, so scene-change detection has nothing to measure",
            path=str(path),
        )

    stderr = ffmpeg.run(
        [
            "-i", str(path),
            "-vf", f"select='gt(scene,{threshold:g})',showinfo",
            "-f", "null", "-",
        ],
        timeout_seconds=timeout_seconds,
    )
    return [float(m.group(1)) for m in _SCENE_PTS_RE.finditer(stderr)]


# --- rough cut: silence spans -> a proposed EDL -----------------------------


def kept_spans(
    total_duration: float,
    silences: list[SilenceSpan],
    *,
    min_clip_seconds: float,
    padding_seconds: float,
) -> list[tuple[float, float]]:
    """The complement of the silent spans: what a rough cut would KEEP.

    Pure function, no ffmpeg involved, so the boundary arithmetic is testable
    on its own (the sabotage class this task is judged on is an off-by-one
    here). ``padding_seconds`` shrinks each excluded span symmetrically,
    leaving a small buffer of near-silence on each side of a cut rather than
    trimming flush against detected speech — trimming flush is the more
    common way an automatic cut clips the first consonant of a word.
    """
    if total_duration <= 0:
        raise ValidationError("total_duration must be positive", total_duration=total_duration)

    ordered = sorted(silences, key=lambda s: s.start)
    cursor = 0.0
    kept: list[tuple[float, float]] = []
    for span in ordered:
        cut_start = min(max(span.start + padding_seconds, cursor), total_duration)
        cut_end = min(max(span.end - padding_seconds, cut_start), total_duration)
        if cut_start > cursor:
            kept.append((cursor, cut_start))
        cursor = max(cursor, cut_end)
    if cursor < total_duration:
        kept.append((cursor, total_duration))

    return [(s, e) for s, e in kept if (e - s) >= min_clip_seconds]


def propose_rough_cut(
    asset_id: str,
    total_duration: float,
    silences: list[SilenceSpan],
    *,
    min_clip_seconds: float,
    padding_seconds: float,
    aspect: str = "landscape",
) -> EDL:
    """A PROPOSED EDL whose clips exclude the detected silent spans.

    Named "propose" rather than "cut" on purpose: this function returns a
    document, it does not write anything anywhere. Nothing in this module
    calls ``projects.set_edl`` or touches a database connection — the caller
    (``ops/analyse.py``) returns the document for review. Applying it is a
    deliberate, separate call to the ALREADY-REGISTERED ``set-edl`` operation
    (T-042), which appends a new EDL version attributed to whichever principal
    calls it — never a silent in-place mutation of the current version, and
    never automatic. That satisfies AC-2's rule using the EDL's existing
    append-only versioning rather than inventing a second write path.

    Raises rather than returning an empty EDL when nothing survives the cut
    (e.g. the source is silence throughout, or every kept span is below the
    minimum clip duration) — ``EDL.validate()`` refuses zero clips, and a
    caller deserves that reason stated plainly rather than a validation error
    surfacing two calls later.
    """
    spans = kept_spans(
        total_duration, silences,
        min_clip_seconds=min_clip_seconds, padding_seconds=padding_seconds,
    )
    if not spans:
        raise ValidationError(
            "no span survived the rough cut — the source may be silent throughout, "
            "or every non-silent span is shorter than the configured minimum clip length",
            asset_id=asset_id, total_duration=total_duration,
            silence_spans_detected=len(silences), min_clip_seconds=min_clip_seconds,
        )
    return EDL(
        aspect=aspect,
        clips=[Clip(asset_id=asset_id, start=start, end=end) for start, end in spans],
    )


# --- transcription -----------------------------------------------------------


@dataclass(frozen=True)
class TranscriptSegment:
    start: float
    end: float
    text: str

    def to_dict(self) -> dict[str, Any]:
        return {"start": self.start, "end": self.end, "text": self.text}


def transcription_available() -> bool:
    """Whether faster-whisper is importable, without paying its import cost.

    ``find_spec`` answers the question without loading ctranslate2 and its own
    native dependencies, which matters here because this is called by a
    read-only capabilities check that must stay cheap (C-1/C-4 class latency),
    not only by the transcribe path that is about to do real work anyway.
    """
    return importlib.util.find_spec("faster_whisper") is not None


# Rough, disclosed estimate — not a benchmark run on THIS machine, because
# nothing here can run faster-whisper to measure it without the package
# installed. Framed as a range for exactly that reason. See findings in the
# task's completion record for the reasoning behind the range.
_MODEL_ESTIMATES: dict[str, dict[str, Any]] = {
    "tiny":   {"download_mb": 75,  "realtime_factor": "~4-8x"},
    "base":   {"download_mb": 145, "realtime_factor": "~2-4x"},
    "small":  {"download_mb": 480, "realtime_factor": "~1-2x"},
    "medium": {"download_mb": 1500, "realtime_factor": "~0.5-1x"},
}


def transcription_requirements(model_size: str) -> dict[str, Any]:
    """What would satisfy the transcription capability, stated plainly.

    Returned by the capabilities check AND embedded in the refusal itself
    (AC-3), so an operator or an agent sees the same answer whether they ask
    proactively or hit the refusal by trying to transcribe.
    """
    estimate = _MODEL_ESTIMATES.get(model_size, _MODEL_ESTIMATES["base"])
    return {
        "package": "faster-whisper",
        "install": "pip install faster-whisper",
        "model_size": model_size,
        "model_download_mb_estimate": estimate["download_mb"],
        "cpu_realtime_factor_estimate": estimate["realtime_factor"],
        "note": (
            "faster-whisper downloads its model the first time it runs a given"
            " size; no network access happens until transcription is actually"
            " invoked with the package installed."
        ),
    }


def require_transcription(model_size: str) -> None:
    if not transcription_available():
        raise TranscriptionUnavailable(
            "faster-whisper is not installed, so no transcription can run",
            remedy="pip install faster-whisper",
            **transcription_requirements(model_size),
        )


def transcribe(
    path: Path,
    *,
    model_size: str,
    language: str | None,
) -> tuple[list[TranscriptSegment], str | None]:
    """Real transcription via faster-whisper. Raises if it is not installed.

    Never returns an empty list to mean "unavailable" — unavailability is
    always an exception (``TranscriptionUnavailable``), never a silent empty
    success. An empty list here means faster-whisper genuinely ran and found
    no speech, which is a different fact and must not be reported the same
    way.
    """
    require_transcription(model_size)
    from faster_whisper import WhisperModel  # imported only once available

    model = WhisperModel(model_size, device="cpu", compute_type="int8")
    raw_segments, info = model.transcribe(str(path), language=language)
    segments = [
        TranscriptSegment(start=float(s.start), end=float(s.end), text=s.text.strip())
        for s in raw_segments
    ]
    detected_language = getattr(info, "language", None) or language
    return segments, detected_language


def segments_to_captions(
    segments: list[TranscriptSegment],
    *,
    position: str = "bottom",
    size: int = 36,
    color: str = "white",
    box: bool = True,
) -> list[TextOverlay]:
    """Transcript segments as burned-in captions — AC-1's second option.

    A pure transform, so it is tested on plain segment values regardless of
    whether faster-whisper is installed on this machine (T-047's evidence for
    AC-1 despite AC-1 itself being NOT_RUN end-to-end). Empty-text segments
    are dropped rather than emitted as blank captions, since ``TextOverlay``
    refuses an empty ``text`` on validation.
    """
    return [
        TextOverlay(
            text=segment.text, start=segment.start, end=segment.end,
            position=position, size=size, color=color, box=box,
        )
        for segment in segments
        if segment.text.strip()
    ]
