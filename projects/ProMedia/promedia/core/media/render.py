"""EDL to ffmpeg (T-041).

Compiles an edit into a single ffmpeg invocation. One invocation rather than a
chain of intermediate files is deliberate: intermediates cost a re-encode each,
and every re-encode loses quality and time. A filter graph does the whole edit
in one pass.

The compilation is a pure function of the EDL and the resolved source paths, so
it is testable without running ffmpeg at all — and it is, because a filter graph
is exactly the kind of string-built artefact where an error is invisible until
something segfaults at the far end.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ...errors import ValidationError
from . import ffmpeg
from .edl import EDL, GRADE_NEUTRAL, Clip, TextOverlay, TIMELINE_TRANSITIONS

# ffmpeg filter fragments per named effect. Kept here, next to the compiler, so
# that adding an effect to the EDL vocabulary and teaching the compiler to
# render it are the same edit rather than two that can drift apart.
EFFECT_FILTERS: dict[str, str] = {
    "none": "",
    "grayscale": "hue=s=0",
    "sepia": "colorchannelmixer=.393:.769:.189:0:.349:.686:.168:0:.272:.534:.131",
    "blur": "boxblur=4:1",
    "sharpen": "unsharp=5:5:1.0",
    "brighten": "eq=brightness=0.12",
    "darken": "eq=brightness=-0.12",
    "saturate": "eq=saturation=1.5",
}

# Which advertised transitions this compiler ACTUALLY renders, and what each
# one really produces. The single source of truth for fabrication F-003.
#
# It lives here, next to the code that emits the filters, so that implementing a
# transition and reporting it as implemented are the same edit. The first
# version of F-003 named `dissolve` alone and missed four more — the reporting
# was a hand-written list in another module, and it drifted from this one
# immediately. An independent audit found it by executing _clip_chain over all
# seven values rather than reading either list.
#
# RETIRED 2026-08-14 (T-045). All five were the same underlying defect: concat
# carries no absolute timeline offset, and every one of these needs ffmpeg's
# xfade (video) / acrossfade (audio) filters, which do. Once the graph could
# express an offset at all, it turned out ffmpeg's own xfade transition
# vocabulary already contains 'dissolve', 'wipeleft', 'wiperight', 'slideup'
# and 'slidedown' under exactly those names (see XFADE_TRANSITIONS) — so
# implementing the composition model once retired all five, not just the one
# AC-1 names. Every value below is None: nothing in the advertised vocabulary
# renders as anything other than what was asked for.
#
# projects.py's tests/test_projects.py::test_a_render_reports_what_it_did_not_do_as_asked
# and test_each_unimplemented_transition_is_reported_on_the_render pin the OLD
# values (dissolve/wipeleft/wiperight/slideup/slidedown reported as
# substitutions). Those are now stale by design — this dict is their source of
# truth and T-042 built it that way on purpose ("the reporting follows
# automatically") — but T-045 does not own tests/test_projects.py and does not
# edit it. Reported as a finding, not silently patched around.
TRANSITION_REALITY: dict[str, str | None] = {
    "cut": None,        # the absence of a transition; correct
    "fade": None,        # fade from black; correct
    "dissolve": None,    # real cross-dissolve via xfade (was: fade from black)
    "wipeleft": None,    # real xfade wipe (was: hard cut, no filter at all)
    "wiperight": None,   # real xfade wipe (was: hard cut, no filter at all)
    "slideup": None,     # real xfade slide (was: hard cut, no filter at all)
    "slidedown": None,   # real xfade slide (was: hard cut, no filter at all)
}

# Transitions realised with ffmpeg's xfade/acrossfade filters rather than a
# filter applied to one clip in isolation. Backed by edl.TIMELINE_TRANSITIONS
# (the vocabulary-level fact "this transition needs a previous clip") rather
# than redeclared here, so the two cannot drift.
#
# The mapping to ffmpeg's own `transition=` values is the identity: verified
# against this build (`ffmpeg -h filter=xfade`) that wipeleft/wiperight/
# slideup/slidedown/dissolve exist under exactly these names in xfade's own
# enum. That coincidence is what makes retiring four "no filter at all" cases
# and one "wrong filter" case the same eight lines of code instead of five
# bespoke ones.
XFADE_TRANSITIONS = frozenset(TIMELINE_TRANSITIONS)


def transition_substitution(transition: str) -> str | None:
    """What this transition really renders as, or None if it is honest."""
    return TRANSITION_REALITY.get(transition)


# Where named positions land, as ffmpeg x/y expressions. Expressions rather
# than numbers so they hold at any output resolution (see TextOverlay).
TEXT_POSITIONS: dict[str, str] = {
    "top": "x=(w-text_w)/2:y=h*0.08",
    "center": "x=(w-text_w)/2:y=(h-text_h)/2",
    "bottom": "x=(w-text_w)/2:y=h*0.85-text_h/2",
}

QUALITY_PRESETS: dict[str, dict[str, Any]] = {
    # Measured on this machine, 60s of 1490x1022 -> 1280x720 with fade + text.
    "fast":     {"encoder": "libx264", "args": ["-preset", "veryfast", "-crf", "23"]},
    "balanced": {"encoder": "libx264", "args": ["-preset", "medium", "-crf", "21"]},
    "quality":  {"encoder": "libx264", "args": ["-preset", "slow", "-crf", "18"]},
    # Roughly half the file size of x264 at comparable speed here. Default for
    # delivery; not a speed optimisation (the filter chain is CPU-bound anyway).
    "hardware": {"encoder": "h264_qsv", "args": ["-global_quality", "24"]},
}


@dataclass(frozen=True)
class RenderPlan:
    """A compiled render, inspectable before it is executed.

    Returned rather than run so the agent can show an operator what WILL happen,
    and so tests can assert the graph without a 30-second encode.
    """

    args: list[str]
    filter_graph: str
    output_path: Path
    width: int
    height: int
    quality: str
    source_count: int
    # AC-2. The timeline length this graph actually produces: the sum of every
    # clip's own duration MINUS every real transition's overlap (xfade/
    # acrossfade shorten the timeline by exactly their `duration`; a plain cut
    # or a fade-from-black do not). None when it cannot be computed without
    # probing a source — see _compose_timeline.
    expected_duration_seconds: float | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "output_path": str(self.output_path),
            "resolution": f"{self.width}x{self.height}",
            "quality": self.quality,
            "sources": self.source_count,
            "filter_graph": self.filter_graph,
            "expected_duration_seconds": self.expected_duration_seconds,
        }


def _grade_filters(clip: Clip) -> list[str]:
    """The colour-grade filter fragments for one clip (T-064, DR-019).

    Three known ffmpeg filters, one per field group, each omitted entirely
    when its field(s) sit at GRADE_NEUTRAL — this is what makes a clip with
    no grading applied compile to no grade filter at all (AC-2):

    * ``eq``               — brightness / contrast / saturation. Only the
      params that differ from neutral are emitted, so adjusting one field
      does not silently reset the other two to eq's own defaults.
    * ``colorbalance``     — white_balance. A warm (+) value shifts red up
      and blue down uniformly across shadows/midtones/highlights; cool (-)
      is the reverse. Simpler than grading each tonal range independently,
      which this vocabulary does not expose.
    * ``colortemperature`` — temperature, passed straight through in
      Kelvin, ffmpeg's own unit for this filter, so there is no second
      mapping to keep in sync with GRADE_RANGES.
    """
    filters: list[str] = []

    eq_params = []
    if clip.brightness != GRADE_NEUTRAL["brightness"]:
        eq_params.append(f"brightness={clip.brightness:g}")
    if clip.contrast != GRADE_NEUTRAL["contrast"]:
        eq_params.append(f"contrast={clip.contrast:g}")
    if clip.saturation != GRADE_NEUTRAL["saturation"]:
        eq_params.append(f"saturation={clip.saturation:g}")
    if eq_params:
        filters.append("eq=" + ":".join(eq_params))

    if clip.white_balance != GRADE_NEUTRAL["white_balance"]:
        wb = clip.white_balance
        filters.append(
            f"colorbalance=rs={wb:g}:bs={-wb:g}:rm={wb:g}:bm={-wb:g}:rh={wb:g}:bh={-wb:g}"
        )

    if clip.temperature != GRADE_NEUTRAL["temperature"]:
        filters.append(f"colortemperature=temperature={clip.temperature:g}")

    return filters


def _clip_chain(index: int, clip: Clip, width: int, height: int) -> str:
    """The video filter chain for one clip, ending at label [vN]."""
    steps = [
        # Scale into the target frame preserving aspect, then pad the remainder.
        # force_original_aspect_ratio=decrease + pad is what stops a vertical
        # render from stretching landscape footage into a distortion.
        f"scale={width}:{height}:force_original_aspect_ratio=decrease",
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black",
        "setsar=1",
    ]
    if clip.speed != 1.0:
        steps.append(f"setpts={1.0 / clip.speed:.6f}*PTS")
    # Colour correction before the creative EFFECT below, same order a
    # grading pass would run in by hand: correct, then stylise.
    steps.extend(_grade_filters(clip))
    effect = EFFECT_FILTERS.get(clip.effect, "")
    if effect:
        steps.append(effect)
    if clip.transition_in == "fade" and clip.transition_duration > 0:
        # A fade from black at the head of the clip. Deliberately NOT applied
        # for 'dissolve' (or any other XFADE_TRANSITIONS member) any more —
        # those are real cross-clip blends now, built at the graph level in
        # compile_render via xfade, not a filter on one clip in isolation.
        steps.append(f"fade=t=in:st=0:d={clip.transition_duration:g}")
    return f"[{index}:v]" + ",".join(steps) + f"[v{index}]"


def _audio_chain(index: int, clip: Clip) -> str:
    steps = []
    if clip.speed != 1.0:
        # atempo is limited to 0.5-2.0 per instance, so a larger change is
        # chained. Without this, speeds outside that range are silently ignored
        # by ffmpeg and the audio desynchronises from the video.
        remaining = clip.speed
        while remaining > 2.0:
            steps.append("atempo=2.0")
            remaining /= 2.0
        while remaining < 0.5:
            steps.append("atempo=0.5")
            remaining /= 0.5
        steps.append(f"atempo={remaining:.6f}")
    volume = 0.0 if clip.mute else clip.volume
    if volume != 1.0:
        steps.append(f"volume={volume:g}")
    steps.append("aresample=48000")
    steps.append("aformat=sample_fmts=fltp:channel_layouts=stereo")
    return f"[{index}:a]" + ",".join(steps) + f"[a{index}]"


# --- timeline composition (T-045) --------------------------------------------
#
# concat has no notion of "this clip starts before the previous one ends" — it
# is a strict end-to-end splice. xfade/acrossfade DO overlap two streams, but
# they need to be told exactly where on the timeline the overlap begins, and
# concat's output carries no such coordinate. So a real transition forces the
# clip list to be cut into SEGMENTS at every point one is requested: clips
# joined by 'cut' or 'fade' keep concatenating exactly as before (this is what
# keeps every pre-T-045 filter_graph assertion in tests/test_edl.py true
# byte-for-byte whenever an edit uses none of XFADE_TRANSITIONS), and adjacent
# segments are stitched with xfade/acrossfade instead.

Segment = list[tuple[int, Clip]]


def _segments(clips: list[Clip]) -> list[Segment]:
    """Group clips into runs joined by concat, split at every real transition.

    A boundary belongs to clip[i] (i >= 1): it describes how clip[i] enters,
    relative to clip[i-1]. edl.validate() already refuses a real transition on
    clip[0] (there is nothing before it to blend with), so index 0 never
    starts a segment on its own account here — it is simply always the first
    clip of the first segment.
    """
    segments: list[Segment] = [[(0, clips[0])]]
    for index in range(1, len(clips)):
        clip = clips[index]
        if clip.transition_in in XFADE_TRANSITIONS:
            segments.append([(index, clip)])
        else:
            segments[-1].append((index, clip))
    return segments


def _segment_duration(segment: Segment) -> float | None:
    """Total duration of a segment's clips, or None if any is open-ended.

    Deliberately NOT resolved by probing the source here: compile_render never
    spawns a subprocess (that is execute()'s job), and tests/test_edl.py pins
    exactly that property by calling compile_render with source paths that do
    not exist on disk at all. An open-ended clip (end=None) whose duration is
    needed for an offset is refused with a clear message instead — see its use
    in compile_render.
    """
    total = 0.0
    for _, clip in segment:
        duration = clip.duration(None)
        if duration is None:
            return None
        total += duration
    return total


def _concat_segment(segment: Segment, chains: list[str], tag: str) -> tuple[str, str]:
    """Concatenate one segment's clips into a single [v.]/[a.] pair.

    A single-clip segment needs no concat filter at all — its own [vN]/[aN]
    labels already ARE the segment's output. Skipping the no-op filter is what
    keeps a plain two-clip cut edit's filter_graph identical to the pre-T-045
    shape (single segment, no concat-of-one anywhere new).
    """
    if len(segment) == 1:
        index = segment[0][0]
        return f"v{index}", f"a{index}"
    concat_inputs = "".join(f"[v{i}][a{i}]" for i, _ in segment)
    chains.append(f"{concat_inputs}concat=n={len(segment)}:v=1:a=1[vseg{tag}][aseg{tag}]")
    return f"vseg{tag}", f"aseg{tag}"


def _compose_timeline(
    clips: list[Clip], chains: list[str]
) -> tuple[str, str, float | None]:
    """Build the video/audio graph for a clip list, honouring real transitions.

    Returns (video_label, audio_label, expected_duration_seconds). Appends
    whatever concat/xfade/acrossfade filters are needed to ``chains``.
    """
    segments = _segments(clips)

    if len(segments) == 1:
        # No real transition anywhere in this edit. This is the common case,
        # and it is spelled out exactly as compile_render always has, rather
        # than routed through _concat_segment, so the emitted string is
        # identical to every existing 'concat=n=...' assertion.
        concat_inputs = "".join(f"[v{i}][a{i}]" for i in range(len(clips)))
        chains.append(f"{concat_inputs}concat=n={len(clips)}:v=1:a=1[vcat][acat]")
        return "vcat", "acat", _segment_duration(segments[0])

    video_label, audio_label = _concat_segment(segments[0], chains, "0")
    cumulative_duration = _segment_duration(segments[0])

    for seg_index in range(1, len(segments)):
        segment = segments[seg_index]
        boundary_index, boundary_clip = segment[0]
        where = f"clips[{boundary_index}]"
        duration = boundary_clip.transition_duration

        if cumulative_duration is None:
            raise ValidationError(
                f"{where}.transition_in '{boundary_clip.transition_in}' needs the "
                "exact duration of every clip before it on the timeline, and an "
                "earlier clip has no explicit 'end' — give every clip before this "
                "one an explicit end, or use 'cut'",
                parameter=where,
            )
        if duration <= 0:
            raise ValidationError(
                f"{where}.transition_duration must be greater than 0 for "
                f"transition_in '{boundary_clip.transition_in}'",
                parameter=where,
            )
        if duration >= cumulative_duration:
            raise ValidationError(
                f"{where}.transition_duration ({duration:g}s) cannot reach past "
                f"the start of the clips before it ({cumulative_duration:g}s)",
                parameter=where,
            )
        seg_video, seg_audio = _concat_segment(segment, chains, str(seg_index))
        seg_duration = _segment_duration(segment)
        if seg_duration is not None and duration >= seg_duration:
            raise ValidationError(
                f"{where}.transition_duration ({duration:g}s) cannot reach past "
                f"the end of its own clip ({seg_duration:g}s)",
                parameter=where,
            )

        offset = cumulative_duration - duration
        new_video, new_audio = f"vx{seg_index}", f"ax{seg_index}"
        # xfade needs the ABSOLUTE offset concat cannot carry — the reason
        # this whole composition model exists (F-003's root cause).
        chains.append(
            f"[{video_label}][{seg_video}]xfade=transition={boundary_clip.transition_in}:"
            f"duration={duration:g}:offset={offset:g}[{new_video}]"
        )
        # acrossfade needs no offset: it crossfades the TAIL of stream 1 with
        # the HEAD of stream 2 for `d` seconds by construction, which is
        # exactly the audio counterpart of what the video offset expresses.
        chains.append(f"[{audio_label}][{seg_audio}]acrossfade=d={duration:g}[{new_audio}]")
        video_label, audio_label = new_video, new_audio
        cumulative_duration = None if seg_duration is None else offset + seg_duration

    return video_label, audio_label, cumulative_duration


def _text_filter(overlay: TextOverlay, font: Path | None, text_path: Path) -> str:
    """One drawtext, reading its text from a SIDECAR FILE rather than inline.

    This is the single most-tested decision in this module, because inline text
    could not be made to work reliably. Captions come from humans and from the
    agent, so they contain apostrophes, colons and percent signs as a matter of
    course, and ffmpeg's filtergraph quoting could not survive them:

      * a colon splits filter arguments, so it must be escaped;
      * an apostrophe TERMINATES the quoted section — a backslash does not
        escape it inside quotes, and close-escape-reopen fails too;
      * a percent triggers strftime expansion, which escaping cannot prevent
        because expansion happens after unescaping.

    Measured: inline text failed on every caption containing both a colon and
    an apostrophe ("Q1: revenue +12% (it's up)"), and the failure was not even
    reported against drawtext — the broken quote swallowed the chain separator,
    so ffmpeg complained about a `loudnorm` option instead. ``textfile=`` passes
    all of them because the content never enters the graph at all.
    """
    text_path.parent.mkdir(parents=True, exist_ok=True)
    text_path.write_text(overlay.text, encoding="utf-8")
    parts = [
        ffmpeg.font_argument(font),
        f"textfile='{ffmpeg.escape_path_for_filter(text_path)}'",
        # Belt and braces: the text is out of the graph, but drawtext would
        # still expand a '%' read from the file.
        "expansion=none",
        f"fontsize={overlay.size}",
        f"fontcolor={overlay.color}",
        TEXT_POSITIONS[overlay.position],
    ]
    if overlay.box:
        parts.append("box=1:boxcolor=black@0.5:boxborderw=12")
    if overlay.start or overlay.end is not None:
        end = overlay.end if overlay.end is not None else 99999
        parts.append(f"enable='between(t,{overlay.start:g},{end:g})'")
    return "drawtext=" + ":".join(parts)


def compile_render(
    edl: EDL,
    sources: dict[str, Path],
    output_path: Path,
    *,
    quality: str = "balanced",
    font: Path | None = None,
    workspace: Path | None = None,
) -> RenderPlan:
    """Turn an EDL plus resolved source paths into an ffmpeg command line.

    ``sources`` maps asset id to file path; the caller resolves them, because
    resolving an asset means checking rights and availability and that is not
    this module's business.

    ``workspace`` holds the caption sidecar files (see _text_filter). It
    defaults to a directory beside the output, so a caller that does not care
    need not think about it, and one that does — a temp dir in tests, a project
    scratch dir in production — can say so.
    """
    edl.validate()
    if quality not in QUALITY_PRESETS:
        raise ValidationError(
            f"unknown quality '{quality}'", parameter="quality",
            supported=sorted(QUALITY_PRESETS),
        )
    width, height = edl.resolution()

    # Input order is fixed here and referenced by index throughout the graph.
    ordered_ids: list[str] = []
    for clip in edl.clips:
        ordered_ids.append(clip.asset_id)
    audio_offset = len(ordered_ids)
    for track in edl.audio:
        ordered_ids.append(track.asset_id)

    args: list[str] = []
    for position, clip in enumerate(edl.clips):
        path = sources[clip.asset_id]
        # -ss before -i seeks by keyframe and is fast; -t bounds the read. Both
        # placed as INPUT options so ffmpeg decodes only the needed span rather
        # than the whole file and discarding most of it.
        args += ["-ss", f"{clip.start:g}"]
        if clip.end is not None:
            args += ["-t", f"{max(0.0, clip.end - clip.start):g}"]
        args += ["-i", str(path)]
    for track in edl.audio:
        args += ["-i", str(sources[track.asset_id])]

    chains: list[str] = []
    for index, clip in enumerate(edl.clips):
        chains.append(_clip_chain(index, clip, width, height))
        chains.append(_audio_chain(index, clip))

    video_label, base_audio_label, expected_duration = _compose_timeline(edl.clips, chains)

    if edl.text:
        scratch = workspace or (output_path.parent / f".{output_path.stem}-text")
        text_filters = ",".join(
            _text_filter(overlay, font, scratch / f"text_{index}.txt")
            for index, overlay in enumerate(edl.text)
        )
        chains.append(f"[{video_label}]{text_filters}[vtxt]")
        video_label = "vtxt"

    audio_label = base_audio_label
    for position, track in enumerate(edl.audio):
        input_index = audio_offset + position
        steps = [f"volume={track.volume:g}", "aresample=48000",
                 "aformat=sample_fmts=fltp:channel_layouts=stereo"]
        if track.fade_in > 0:
            steps.append(f"afade=t=in:st=0:d={track.fade_in:g}")
        chains.append(f"[{input_index}:a]" + ",".join(steps) + f"[bg{position}]")
        # duration=first keeps a long music bed from extending the video past
        # its last frame, which is the default and is almost never wanted.
        chains.append(f"[{audio_label}][bg{position}]amix=inputs=2:duration=first:"
                      f"dropout_transition=0[amix{position}]")
        audio_label = f"amix{position}"

    if edl.normalise_audio:
        # Single-pass loudness normalisation to broadcast target. The most
        # valuable audio step available and the one most often skipped.
        chains.append(f"[{audio_label}]loudnorm=I=-16:TP=-1.5:LRA=11[anorm]")
        audio_label = "anorm"

    filter_graph = ";".join(chains)
    preset = QUALITY_PRESETS[quality]

    args += [
        "-filter_complex", filter_graph,
        "-map", f"[{video_label}]",
        "-map", f"[{audio_label}]",
        "-c:v", preset["encoder"], *preset["args"],
        "-pix_fmt", "yuv420p",  # without this, some players show nothing at all
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        str(output_path),
    ]

    return RenderPlan(
        args=args, filter_graph=filter_graph, output_path=output_path,
        width=width, height=height, quality=quality, source_count=len(ordered_ids),
        expected_duration_seconds=expected_duration,
    )


def execute(plan: RenderPlan, *, timeout_seconds: float) -> dict[str, Any]:
    """Run a compiled plan and report what came out.

    The output is probed rather than assumed: ffmpeg can exit 0 having written
    a file that is not playable, and reporting a successful render of a broken
    file is the media equivalent of a fabricated result.
    """
    plan.output_path.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg.run(plan.args, timeout_seconds=timeout_seconds)
    if not plan.output_path.is_file() or plan.output_path.stat().st_size == 0:
        raise ffmpeg.RenderFailed(
            "ffmpeg reported success but produced no output file",
            output_path=str(plan.output_path),
        )
    info = ffmpeg.probe(plan.output_path)
    if not info.has_video:
        raise ffmpeg.RenderFailed(
            "the rendered file contains no video stream",
            output_path=str(plan.output_path),
        )
    return {
        "output_path": str(plan.output_path),
        "byte_size": info.byte_size,
        "duration_seconds": info.duration_seconds,
        "width": info.width,
        "height": info.height,
        "video_codec": info.video_codec,
        "audio_codec": info.audio_codec,
        "quality": plan.quality,
    }
