"""Transcription and deterministic rough-cut analysis (T-047).

Registers the capabilities from ``core/media/analyse.py`` so both surfaces can
reach them (F-1). All three operations are agent-callable: they analyse and
propose, they never publish, spend, or clear a rights flag (F-2), and none of
them mutates a project — ``propose-rough-cut`` in particular returns a document
for review rather than writing one (AC-2). Applying a proposal is a deliberate,
separate call to the already-registered ``set-edl`` operation (T-042).

None of these declare an ``entity``, so none takes a C-19 lock — they only
read an asset's bytes and run ffmpeg/faster-whisper against them, and a lock
exists to arbitrate WRITERS.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ...errors import MediaUnavailable, NotFound
from .. import rights as rights_layer
from ..media import analyse as analyse_engine
from ..media import ffmpeg
from ..registry import Context, Param, register


def _asset_path(ctx: Context, asset_id: str) -> Path:
    """Resolve an asset id to a file on disk, refusing anything not usable.

    The same check ``core/projects.py`` makes before a render
    (``_resolve_sources``), kept as a small duplicate here rather than an
    import: ``projects.py`` is owned by another agent in this parallel run
    (T-043), and this task's lock covers only ``media/analyse.py``,
    ``ops/analyse.py`` and ``tests/test_analyse.py``.
    """
    row = ctx.conn.execute(
        "SELECT id, object_path FROM assets WHERE id = ?", (asset_id,)
    ).fetchone()
    if row is None:
        raise NotFound(f"no asset {asset_id}", asset_id=asset_id)
    state = rights_layer.media_state(ctx, asset_id)
    if state != "stored" or not row["object_path"]:
        raise MediaUnavailable(
            f"asset {asset_id} has no media on this machine (state '{state}'),"
            " so it cannot be analysed",
            asset_id=asset_id, asset_state=state,
        )
    path = Path(row["object_path"])
    if not path.is_file():
        raise MediaUnavailable(
            f"asset {asset_id} is recorded as stored but its file is missing",
            asset_id=asset_id, object_path=str(path),
        )
    return path


@register(
    "analysis-capabilities",
    "What deterministic analysis and transcription this installation can actually do.",
)
def analysis_capabilities(ctx: Context) -> dict[str, Any]:
    """Answers 'why did that fail' without requiring a failure first.

    Mirrors ``media-capabilities`` (T-042): an absent capability is named
    plainly, with exactly what would satisfy it, rather than discovered only
    by trying and hitting a refusal.
    """
    model_size = str(ctx.config.get("analysis", "transcription_model_size"))
    ffmpeg_ok = ffmpeg.available()
    transcription_ok = analyse_engine.transcription_available()
    return {
        "ok": True,
        "ffmpeg_available": ffmpeg_ok,
        "deterministic_analysis": {
            "silence_detection": ffmpeg_ok,
            "scene_detection": ffmpeg_ok,
            "note": (
                "no model involved; ffmpeg's silencedetect and scene filters only"
                if ffmpeg_ok
                else "ffmpeg is not installed; no analysis of any kind can run"
            ),
        },
        "transcription": {
            "available": transcription_ok,
            "engine": "faster-whisper",
            "configured_model_size": model_size,
            **(
                {}
                if transcription_ok
                else {"requirements": analyse_engine.transcription_requirements(model_size)}
            ),
        },
    }


@register(
    "transcribe",
    "Transcribe an asset's speech into timed segments and burned-in captions built from them.",
    params=(
        Param("asset_id", "str"),
        Param("model_size", "str", required=False,
              help="tiny | base | small | medium. Defaults to configuration."),
        Param("language", "str", required=False,
              help="ISO 639-1 code, e.g. 'en'. Omit to auto-detect."),
    ),
)
def transcribe(
    ctx: Context,
    asset_id: str,
    model_size: str | None = None,
    language: str | None = None,
) -> dict[str, Any]:
    """AC-1 / AC-3. Agent-callable: produces derived data, nothing published (F-2).

    Raises ``TranscriptionUnavailable`` — structured, naming the package, the
    model size and a time estimate — when faster-whisper is not installed.
    Never returns empty segments and reports success: that silent-skip shape
    is the exact defect class of fabrication F-003 (Constitution section 6).
    """
    path = _asset_path(ctx, asset_id)
    chosen_size = model_size or str(ctx.config.get("analysis", "transcription_model_size"))
    segments, detected_language = analyse_engine.transcribe(
        path, model_size=chosen_size, language=language,
    )
    captions = analyse_engine.segments_to_captions(segments)
    return {
        "ok": True,
        "asset_id": asset_id,
        "model_size": chosen_size,
        "language": detected_language,
        "segment_count": len(segments),
        "segments": [s.to_dict() for s in segments],
        # AC-1's second option. Ready to append to an EDL's 'text' list via
        # set-edl; nothing is written automatically by this operation.
        "captions": [c.to_dict() for c in captions],
        "note": (
            "captions are shaped for an EDL's 'text' list; call set-edl"
            " explicitly to store them, this operation writes nothing"
        ),
    }


@register(
    "propose-rough-cut",
    "Detect silent spans and propose an EDL that excludes them, for review only.",
    params=(
        Param("asset_id", "str"),
        Param("noise_threshold_db", "float", required=False,
              help="dB below which audio counts as silence. Defaults to configuration."),
        Param("min_silence_seconds", "float", required=False,
              help="Minimum length of a span to count as silence. Defaults to configuration."),
        Param("min_clip_seconds", "float", required=False,
              help="Kept spans shorter than this are dropped. Defaults to configuration."),
        Param("padding_seconds", "float", required=False,
              help="Buffer kept on each side of a cut, into the silence. Defaults to configuration."),
        Param("aspect", "str", required=False, help="Output aspect for the proposed EDL."),
        Param("include_scene_changes", "bool", required=False,
              help="Also report ffmpeg scene-change timestamps for the operator's context."),
    ),
)
def propose_rough_cut(
    ctx: Context,
    asset_id: str,
    noise_threshold_db: float | None = None,
    min_silence_seconds: float | None = None,
    min_clip_seconds: float | None = None,
    padding_seconds: float | None = None,
    aspect: str | None = None,
    include_scene_changes: bool = False,
) -> dict[str, Any]:
    """AC-2. Non-mutating by construction: returns a document, writes nothing.

    ``mutates`` is left at its default False and no ``entity`` is declared, so
    ``registry.invoke`` takes no C-19 lock and nothing here can collide with a
    concurrent edit. Applying the proposal is a deliberate, separate call to
    the existing ``set-edl`` operation with the returned ``edl`` — which
    appends a new, independently reviewable version attributed to whichever
    principal makes that call. This operation itself never touches project
    storage, which is the literal reading of "for review, not automatic
    application."
    """
    path = _asset_path(ctx, asset_id)
    threshold = (
        noise_threshold_db if noise_threshold_db is not None
        else float(ctx.config.get("analysis", "silence_noise_threshold_db"))
    )
    min_silence = (
        min_silence_seconds if min_silence_seconds is not None
        else float(ctx.config.get("analysis", "silence_min_duration_seconds"))
    )
    min_clip = (
        min_clip_seconds if min_clip_seconds is not None
        else float(ctx.config.get("analysis", "rough_cut_min_clip_seconds"))
    )
    padding = (
        padding_seconds if padding_seconds is not None
        else float(ctx.config.get("analysis", "rough_cut_padding_seconds"))
    )
    timeout = float(ctx.config.get("analysis", "analysis_timeout_seconds"))

    info = ffmpeg.probe(path)
    if info.duration_seconds is None:
        raise MediaUnavailable(
            f"asset {asset_id} has no known duration, so a rough cut cannot be bounded",
            asset_id=asset_id,
        )

    silences = analyse_engine.detect_silence(
        path, noise_threshold_db=threshold, min_silence_seconds=min_silence,
        timeout_seconds=timeout,
    )
    document = analyse_engine.propose_rough_cut(
        asset_id, info.duration_seconds, silences,
        min_clip_seconds=min_clip, padding_seconds=padding,
        aspect=aspect or "landscape",
    )

    kept_seconds = sum((clip.end or 0.0) - clip.start for clip in document.clips)
    result: dict[str, Any] = {
        "ok": True,
        "asset_id": asset_id,
        "edl": document.to_dict(),
        "silence_spans": [span.to_dict() for span in silences],
        "clips_proposed": len(document.clips),
        "source_duration_seconds": info.duration_seconds,
        "kept_duration_seconds": kept_seconds,
        "cut_duration_seconds": max(0.0, info.duration_seconds - kept_seconds),
        "parameters": {
            "noise_threshold_db": threshold,
            "min_silence_seconds": min_silence,
            "min_clip_seconds": min_clip,
            "padding_seconds": padding,
        },
        "note": (
            "PROPOSAL ONLY. Nothing is written. Review 'edl', then call set-edl"
            " with it to store it as a new, reviewable version — this operation"
            " never overwrites a project's current edit."
        ),
    }
    if include_scene_changes:
        result["scene_changes_seconds"] = analyse_engine.detect_scene_changes(
            path,
            threshold=float(ctx.config.get("analysis", "scene_change_threshold")),
            timeout_seconds=timeout,
        )
    return result
