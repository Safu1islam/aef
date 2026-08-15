"""T-045 — timeline composition and real transitions (retires F-003).

Split the way test_edl.py / test_media.py are split: the structural tests
below (segment boundaries, xfade offsets, expected-duration arithmetic,
validation refusals) run with FAKE, non-existent source paths and no ffmpeg
on PATH, because compile_render is still a pure function that never spawns a
subprocess — it only builds a command line. The @needs_ffmpeg tests generate
real solid-colour media and render it for real, because "it compiled" is not
evidence that a dissolve blends anything; only a decoded frame is.

Background — why this task exists at all (F-003, replacement task of T-045):
the old renderer built its video graph with ffmpeg's `concat` filter, which
splices streams end-to-end and carries no absolute timeline offset. A real
cross-dissolve needs ffmpeg's `xfade` filter, and xfade needs an offset:
"start blending at second N". `dissolve` was therefore rendered as a fade
from black on the incoming clip (visually wrong), and wipeleft/wiperight/
slideup/slidedown were not rendered as anything at all — no filter emitted,
a silent hard cut while the render reported success.

The fix is a composition-model change, not a filter swap: clips are now
grouped into SEGMENTS at every point a real transition is requested, each
segment still concatenates internally exactly as before, and adjacent
segments are stitched with `xfade` (video) / `acrossfade` (audio) at a
computed absolute offset. See render._compose_timeline.

It turns out ffmpeg's own xfade transition vocabulary already contains
'dissolve', 'wipeleft', 'wiperight', 'slideup' and 'slidedown' under exactly
those names (verified against this build: `ffmpeg -h filter=xfade`), so
building that composition model once retired all five substitutions in
render.TRANSITION_REALITY, not just the one AC-1 names.

FINDING, not silently worked around: tests/test_projects.py (T-042, not
owned by this task) contains two tests that pin the OLD, fabricated values —
test_a_render_reports_what_it_did_not_do_as_asked and
test_each_unimplemented_transition_is_reported_on_the_render assert that
'dissolve'/'wipeleft'/'wiperight'/'slideup'/'slidedown' are reported as
substitutions. Now that render.TRANSITION_REALITY reports them as honest
(None), projects.py's _substitutions() — which the task brief for this work
explicitly says "follows automatically" from that map — reports none, and
those two tests fail. This is not a regression introduced carelessly; it is
the intended, unavoidable consequence of retiring F-003 for real, on a file
this task does not own and must not edit. Reported in the completion record.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from promedia.core.media import ffmpeg, render
from promedia.core.media.edl import EDL, TRANSITIONS, Clip
from promedia.errors import ValidationError

needs_ffmpeg = pytest.mark.skipif(
    not ffmpeg.available(), reason="ffmpeg/ffprobe not installed"
)

# Fake, non-existent paths — compile_render must never touch these on disk.
# Mirrors tests/test_edl.py's SRC/OUT convention exactly, for the same reason:
# pinning that compilation never spawns a subprocess.
SRC = {"a": Path("/media/a.mp4"), "b": Path("/media/b.mp4"), "c": Path("/media/c.mp4")}
OUT = Path("/out/final.mp4")

# The real-render tests use a resolution that matches an ASPECT_PRESETS entry
# exactly (landscape_720), so compile_render's scale+pad is a no-op and does
# not contaminate the colour measurement with padding bars.
WIDTH, HEIGHT = 1280, 720


# --- vocabulary integrity: F-003's own regression guard -----------------------


def test_every_advertised_transition_has_a_reality_entry():
    """Same rule test_projects.py pins from its side: a transition added to
    the EDL tomorrow must be covered by TRANSITION_REALITY the day it lands,
    or it can render as a silent substitution again."""
    assert set(TRANSITIONS) == set(render.TRANSITION_REALITY)


def test_f_003_is_fully_retired():
    """The headline claim of this task, asserted as code rather than prose.
    If this next regresses, it fails here first, not in the field."""
    still_fake = {name: what for name, what in render.TRANSITION_REALITY.items() if what is not None}
    assert still_fake == {}, f"still substituted, F-003 not fully retired: {still_fake}"


# --- structural: the graph actually changed shape, not just a renamed filter --


def test_a_plain_cut_edit_is_untouched():
    """The common case (no real transition anywhere) must be byte-for-byte
    what it always was — this is what keeps every 'concat=n=...' assertion in
    tests/test_edl.py (not owned by this task, must not be edited) passing."""
    plan = render.compile_render(
        EDL(clips=[Clip(asset_id="a", end=2), Clip(asset_id="b", end=2)]), SRC, OUT
    )
    assert "concat=n=2:v=1:a=1[vcat][acat]" in plan.filter_graph
    assert "xfade" not in plan.filter_graph
    assert "acrossfade" not in plan.filter_graph


def test_dissolve_uses_xfade_with_an_absolute_offset_not_concat():
    """AC-1's structural half: the graph must carry an offset at all, which
    concat cannot express. offset = duration of everything before the
    transition, minus the transition's own duration: 4s clip, 1s dissolve,
    offset=3."""
    edl = EDL(clips=[
        Clip(asset_id="a", end=4),
        Clip(asset_id="b", end=4, transition_in="dissolve", transition_duration=1.0),
    ])
    plan = render.compile_render(edl, SRC, OUT)
    assert "xfade=transition=dissolve:duration=1:offset=3" in plan.filter_graph
    assert "acrossfade=d=1" in plan.filter_graph


@pytest.mark.parametrize("name", ["wipeleft", "wiperight", "slideup", "slidedown"])
def test_every_previously_silent_hard_cut_now_emits_its_own_xfade(name):
    """These four used to emit NO FILTER AT ALL. Each must now appear in the
    graph under ffmpeg's own transition name, at the correct offset."""
    edl = EDL(clips=[
        Clip(asset_id="a", end=4),
        Clip(asset_id="b", end=4, transition_in=name, transition_duration=0.5),
    ])
    plan = render.compile_render(edl, SRC, OUT)
    assert f"xfade=transition={name}:duration=0.5:offset=3.5" in plan.filter_graph


def test_cut_joined_runs_still_concatenate_between_real_transitions():
    """A mixed edit: A cuts to B, B dissolves into C. A+B must still be a
    single concatenated segment (not two singleton xfades) — the point of
    segmenting rather than xfading every boundary unconditionally."""
    edl = EDL(clips=[
        Clip(asset_id="a", end=2),
        Clip(asset_id="b", end=2),
        Clip(asset_id="c", end=2, transition_in="dissolve", transition_duration=0.5),
    ])
    plan = render.compile_render(edl, SRC, OUT)
    assert "concat=n=2:v=1:a=1[vseg0][aseg0]" in plan.filter_graph
    assert "xfade=transition=dissolve" in plan.filter_graph


def test_total_duration_subtracts_the_overlap_not_the_naive_sum():
    """AC-2, as arithmetic pinned independently of any real render. Three 4s
    clips, naive sum 12; a 1.0s dissolve and a 0.5s wipeleft each shorten the
    timeline by their own duration."""
    edl = EDL(clips=[
        Clip(asset_id="a", end=4),
        Clip(asset_id="b", end=4, transition_in="dissolve", transition_duration=1.0),
        Clip(asset_id="c", end=4, transition_in="wipeleft", transition_duration=0.5),
    ])
    plan = render.compile_render(edl, SRC, OUT)
    assert plan.expected_duration_seconds == pytest.approx(10.5)


def test_a_naive_cut_edit_reports_the_naive_sum_as_expected_duration():
    plan = render.compile_render(
        EDL(clips=[Clip(asset_id="a", end=3), Clip(asset_id="b", end=5)]), SRC, OUT
    )
    assert plan.expected_duration_seconds == pytest.approx(8.0)


# --- refusals: honest failure, never a silent wrong answer --------------------


def test_a_real_transition_on_the_first_clip_is_refused():
    """There is no previous clip for the first clip to blend with. Allowing
    it would mean either silently ignoring the request (F-003's exact shape)
    or crashing deep inside filter-graph construction; edl.validate() refuses
    it up front instead, by name."""
    with pytest.raises(ValidationError) as excinfo:
        EDL(clips=[Clip(asset_id="a", end=4, transition_in="dissolve")]).validate()
    assert "clips[0]" in str(excinfo.value)


def test_an_open_ended_predecessor_cannot_be_offset():
    """xfade needs the EXACT duration of everything before it. An open-ended
    clip (end=None) has no known duration without probing its source, and
    compile_render must never do that (see module docstring + test_edl.py's
    no-ffmpeg-on-PATH guarantee) — so this is refused, loudly, rather than
    guessed or silently rendered as a cut."""
    edl = EDL(clips=[
        Clip(asset_id="a"),  # end=None: open-ended
        Clip(asset_id="b", end=2, transition_in="dissolve"),
    ])
    with pytest.raises(ValidationError) as excinfo:
        render.compile_render(edl, SRC, OUT)
    assert "clips[1]" in str(excinfo.value)


def test_a_transition_duration_that_exceeds_the_earlier_clip_is_refused():
    edl = EDL(clips=[
        Clip(asset_id="a", end=1),
        Clip(asset_id="b", end=4, transition_in="dissolve", transition_duration=2.0),
    ])
    with pytest.raises(ValidationError):
        render.compile_render(edl, SRC, OUT)


def test_a_transition_duration_that_exceeds_its_own_clip_is_refused():
    edl = EDL(clips=[
        Clip(asset_id="a", end=4),
        Clip(asset_id="b", end=1, transition_in="dissolve", transition_duration=2.0),
    ])
    with pytest.raises(ValidationError):
        render.compile_render(edl, SRC, OUT)


# --- AC-1 and AC-2, proved against a real decoded render -----------------------


def _solid_clip(tmp_path_factory, color: str, duration: float, name: str) -> Path:
    out = tmp_path_factory.mktemp("media") / f"{name}.mp4"
    ffmpeg.run([
        "-f", "lavfi", "-i", f"color=c={color}:size={WIDTH}x{HEIGHT}:rate=25:duration={duration}",
        "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
        "-t", f"{duration}",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", str(out),
    ], timeout_seconds=60)
    return out


@pytest.fixture(scope="module")
def red_clip(tmp_path_factory) -> Path:
    """Solid, maximally-saturated colours rather than real footage on
    purpose: a true dissolve's midpoint is then a PREDICTABLE blend, and a
    fade-to-black's midpoint is predictably dark. Neither is eyeballed."""
    return _solid_clip(tmp_path_factory, "red", 3.0, "red")


@pytest.fixture(scope="module")
def blue_clip(tmp_path_factory) -> Path:
    return _solid_clip(tmp_path_factory, "blue", 3.0, "blue")


@pytest.fixture(scope="module")
def grey_clip(tmp_path_factory) -> Path:
    """Neutral mid-grey (128/128/128) — the right base for T-064's grading
    measurements below: every channel starts equal, so a colour shift
    (white_balance, temperature) or a luminance shift (brightness) shows up
    as a clean deviation from one known baseline, not confounded by a
    channel that is already saturated the way red/blue above would be."""
    return _solid_clip(tmp_path_factory, "gray", 2.0, "grey")


def _mean_rgb(path: Path, at_seconds: float) -> tuple[float, float, float]:
    """The average colour of the decoded frame at ``at_seconds``.

    Raw RGB24 bytes straight from ffmpeg rather than a decoded-image library,
    so the measurement stays inside the same ffmpeg-only boundary as the rest
    of this module, and so it is exactly what a viewer's eye would average
    over that frame — no interpretation layer to second-guess.
    """
    binary = ffmpeg.require("ffmpeg")
    result = subprocess.run(
        [binary, "-hide_banner", "-nostdin", "-y",
         "-ss", f"{at_seconds:.3f}", "-i", str(path),
         "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        capture_output=True, timeout=30, check=False,
    )
    data = result.stdout
    expected_bytes = WIDTH * HEIGHT * 3
    assert len(data) == expected_bytes, (
        f"expected one {WIDTH}x{HEIGHT} RGB24 frame ({expected_bytes} bytes), "
        f"got {len(data)} bytes; stderr: {(result.stderr or b'').decode(errors='replace')[-400:]}"
    )
    pixels = len(data) // 3
    r = sum(data[0::3]) / pixels
    g = sum(data[1::3]) / pixels
    b = sum(data[2::3]) / pixels
    return r, g, b


@needs_ffmpeg
def test_dissolve_blends_both_clips_at_the_midpoint_ac1(tmp_path, red_clip, blue_clip):
    """AC-1, proved rather than asserted: 'a dissolve blends the outgoing
    clip into the incoming one, and is visually distinguishable from a fade
    to black in the rendered output'.

    Red (255,0,0) cuts to blue (0,0,255) with a 1.0s dissolve over 3+3s clips.
    AC-2's arithmetic (3+3-1=5.0s total) puts the transition's exact midpoint
    at t=2.5s. A TRUE dissolve's frame there contains material from BOTH
    clips: red and blue channels both well above zero, and — the decisive
    check — mean luminance nowhere near black, which is what the OLD
    fade-from-black substitution would show at the equivalent point (see the
    contrast test directly below, same clips, same duration, only the
    transition name differs).
    """
    edl = EDL(
        aspect="landscape_720",
        clips=[
            Clip(asset_id="red", start=0, end=3),
            Clip(asset_id="blue", start=0, end=3, transition_in="dissolve",
                 transition_duration=1.0),
        ],
    )
    out = tmp_path / "dissolve.mp4"
    plan = render.compile_render(edl, {"red": red_clip, "blue": blue_clip}, out, quality="fast")
    assert plan.expected_duration_seconds == pytest.approx(5.0, abs=0.01)

    result = render.execute(plan, timeout_seconds=120)
    # AC-2, confirmed against the ACTUAL file, not just the plan's arithmetic.
    assert result["duration_seconds"] == pytest.approx(5.0, abs=0.3)

    midpoint = 2.5  # (3 - 1) + 1.0/2: exactly half-way through the dissolve
    r, g, b = _mean_rgb(out, midpoint)
    assert r > 60, f"red channel {r:.1f} too low for a blend containing red"
    assert b > 60, f"blue channel {b:.1f} too low for a blend containing blue"
    assert g < 40, f"unexpected green {g:.1f}; neither source clip has any green"
    mean_luminance = (r + g + b) / 3
    assert mean_luminance > 60, (
        f"mean luminance {mean_luminance:.1f} collapsed toward black at the "
        "transition midpoint — that is what a fade-to-black looks like, not "
        "a dissolve. If this fails, dissolve has regressed to the old "
        "fade-from-black substitution (F-003)."
    )


@needs_ffmpeg
def test_fade_to_black_is_the_dark_baseline_dissolve_is_not(tmp_path, red_clip, blue_clip):
    """The contrast case AC-1 asks for explicitly. Same two clips, same 1.0s
    duration; only the transition name changes from 'dissolve' to 'fade'.
    'fade' genuinely IS a fade from black (TRANSITION_REALITY still reports
    it honestly, unchanged by this task) so its midpoint SHOULD be dark —
    proving the previous test's high-luminance result is not a measurement
    artefact, but the actual, visible difference between the two edits.
    """
    edl = EDL(
        aspect="landscape_720",
        clips=[
            Clip(asset_id="red", start=0, end=3),
            Clip(asset_id="blue", start=0, end=3, transition_in="fade",
                 transition_duration=1.0),
        ],
    )
    out = tmp_path / "fade.mp4"
    plan = render.compile_render(edl, {"red": red_clip, "blue": blue_clip}, out, quality="fast")
    # 'fade' does not overlap the timeline (it is not in XFADE_TRANSITIONS) —
    # naive sum, unlike dissolve above. This is itself an AC-2 contrast: only
    # a REAL transition subtracts overlap from the total.
    assert plan.expected_duration_seconds == pytest.approx(6.0, abs=0.01)

    render.execute(plan, timeout_seconds=120)
    midpoint = 3.5  # blue starts at t=3.0; its own 1s fade-in is half over here
    r, g, b = _mean_rgb(out, midpoint)
    mean_luminance = (r + g + b) / 3
    assert mean_luminance < 60, (
        f"mean luminance {mean_luminance:.1f} at a fade-to-black midpoint "
        "should be low; if this fails 'fade' has stopped fading from black"
    )


@needs_ffmpeg
@pytest.mark.parametrize("name", ["wipeleft", "wiperight", "slideup", "slidedown"])
def test_every_previously_hard_cut_transition_renders_without_error(tmp_path, red_clip, blue_clip, name):
    """These four used to emit no filter at all — a hard cut while the render
    reported success. Proof of life: each now compiles AND renders a real,
    playable file at the expected (overlap-subtracted) duration."""
    edl = EDL(
        aspect="landscape_720",
        clips=[
            Clip(asset_id="red", start=0, end=3),
            Clip(asset_id="blue", start=0, end=3, transition_in=name, transition_duration=1.0),
        ],
    )
    out = tmp_path / f"{name}.mp4"
    plan = render.compile_render(edl, {"red": red_clip, "blue": blue_clip}, out, quality="fast")
    result = render.execute(plan, timeout_seconds=120)
    assert result["duration_seconds"] == pytest.approx(5.0, abs=0.3)
    assert result["byte_size"] > 0


# --- colour grading (T-064, DR-019) — AC-1 and AC-2, proved against real -----
# decoded renders, same discipline as the dissolve tests above: "it compiled"
# is not evidence a grade changed anything, only a decoded frame is. AC-1
# needs at least one field measured per filter (eq, colorbalance,
# colortemperature); each gets its own test below. The structural filter-
# string assertions (which params appear, which don't) live in
# tests/test_edl.py, which this task also owns — this file supplies the
# pixel-level proof those string assertions cannot.
#
# Every EDL below sets normalise_audio=False, deliberately, not for speed:
# these clips' audio is anullsrc (pure digital silence), and loudnorm on a
# fully-silent input was found, while writing these tests, to emit NaN
# samples that crash the AAC encoder ("Input contains (near) NaN/+-Inf") —
# reproduced independently of any grade field (a single plain clip with
# normalise_audio left at its True default fails the same way). That is a
# pre-existing defect in the untouched normalise_audio/loudnorm path, not
# something this task's grade filters caused or own; filed as R-019 rather
# than fixed here, and worked around in these tests the same way a test
# would avoid any other unrelated known-broken path.


@needs_ffmpeg
def test_neutral_grade_renders_the_source_colour_unchanged_ac2(tmp_path, grey_clip):
    """AC-2, proved on a real decoded frame rather than the filter string
    alone: a clip with every grade field at GRADE_NEUTRAL renders the same
    128/128/128 a pre-T-064 build would have, because no eq/colorbalance/
    colortemperature filter touches the pixels at all when nothing is
    graded."""
    edl = EDL(aspect="landscape_720", clips=[Clip(asset_id="grey", start=0, end=2)],
              normalise_audio=False)
    out = tmp_path / "neutral.mp4"
    plan = render.compile_render(edl, {"grey": grey_clip}, out, quality="fast")
    assert "eq=" not in plan.filter_graph
    assert "colorbalance=" not in plan.filter_graph
    assert "colortemperature=" not in plan.filter_graph
    render.execute(plan, timeout_seconds=60)
    r, g, b = _mean_rgb(out, 1.0)
    assert abs(r - 128) < 8 and abs(g - 128) < 8 and abs(b - 128) < 8, (
        f"neutral grade should leave a 128/128/128 source untouched, got "
        f"({r:.1f}, {g:.1f}, {b:.1f})"
    )


@needs_ffmpeg
def test_brightness_measurably_darkens_the_rendered_frame_ac1_eq(tmp_path, grey_clip):
    """AC-1 for the eq-backed field group: brightness alone must produce a
    real, measured pixel difference from the neutral render above, not just
    a different string in the filter graph."""
    edl = EDL(aspect="landscape_720",
              clips=[Clip(asset_id="grey", start=0, end=2, brightness=-0.4)],
              normalise_audio=False)
    out = tmp_path / "brightness.mp4"
    plan = render.compile_render(edl, {"grey": grey_clip}, out, quality="fast")
    assert "eq=brightness=-0.4" in plan.filter_graph
    render.execute(plan, timeout_seconds=60)
    r, g, b = _mean_rgb(out, 1.0)
    mean_luminance = (r + g + b) / 3
    assert mean_luminance < 100, (
        f"brightness=-0.4 should darken a 128/128/128 grey clip measurably; "
        f"got luminance {mean_luminance:.1f}"
    )


@needs_ffmpeg
def test_white_balance_measurably_shifts_red_versus_blue_ac1_colorbalance(tmp_path, grey_clip):
    """AC-1 for the colorbalance-backed field: a warm white_balance must
    shift red measurably above blue on a source where they started equal —
    the specific thing colorbalance, not eq or colortemperature, produces."""
    edl = EDL(aspect="landscape_720",
              clips=[Clip(asset_id="grey", start=0, end=2, white_balance=0.8)],
              normalise_audio=False)
    out = tmp_path / "warm_wb.mp4"
    plan = render.compile_render(edl, {"grey": grey_clip}, out, quality="fast")
    assert "colorbalance=" in plan.filter_graph
    render.execute(plan, timeout_seconds=60)
    r, g, b = _mean_rgb(out, 1.0)
    assert r - b > 20, (
        f"white_balance=0.8 (warm) should push red measurably above blue on "
        f"a neutral-grey source; got r={r:.1f} b={b:.1f}"
    )


@needs_ffmpeg
def test_temperature_measurably_shifts_colour_balance_ac1_colortemperature(tmp_path, grey_clip):
    """AC-1 for the colortemperature-backed field: a low-Kelvin (warm,
    candlelight-like) render must differ measurably from the 6500K neutral
    render — the decisive check ffmpeg's own colortemperature filter is
    built to produce, and distinct from white_balance's colorbalance path
    above (a different filter, tested here on its own)."""
    neutral_edl = EDL(aspect="landscape_720", clips=[Clip(asset_id="grey", start=0, end=2)],
                      normalise_audio=False)
    warm_edl = EDL(aspect="landscape_720",
                   clips=[Clip(asset_id="grey", start=0, end=2, temperature=3000.0)],
                   normalise_audio=False)

    neutral_out = tmp_path / "temp_neutral.mp4"
    render.execute(
        render.compile_render(neutral_edl, {"grey": grey_clip}, neutral_out, quality="fast"),
        timeout_seconds=60,
    )
    warm_out = tmp_path / "temp_warm.mp4"
    plan = render.compile_render(warm_edl, {"grey": grey_clip}, warm_out, quality="fast")
    assert "colortemperature=temperature=3000" in plan.filter_graph
    render.execute(plan, timeout_seconds=60)

    nr, ng, nb = _mean_rgb(neutral_out, 1.0)
    wr, wg, wb = _mean_rgb(warm_out, 1.0)
    assert (wr - wb) - (nr - nb) > 15, (
        f"temperature=3000K should warm the render measurably relative to "
        f"the 6500K neutral baseline; neutral r-b={nr - nb:.1f}, "
        f"3000K r-b={wr - wb:.1f}"
    )
