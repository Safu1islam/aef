"""T-041 — the edit document and its compilation to ffmpeg.

Split deliberately from tests/test_media.py: everything here runs WITHOUT
ffmpeg installed, because the EDL is a document and its compilation is a pure
function. An edit can be created, validated, versioned and reviewed on a
machine with no media tooling, and that property is worth pinning — it is what
lets the agent draft an edit anywhere and the render happen elsewhere.

The filter graph is string-built, which is exactly the kind of artefact where a
mistake is invisible until ffmpeg segfaults at the far end. So the graph is
asserted structurally here, not just "it produced something".
"""

from __future__ import annotations

from pathlib import Path

import pytest

from promedia.core.media import render
from promedia.core.media.edl import (
    ASPECT_PRESETS,
    EDL,
    AudioTrack,
    Clip,
    TextOverlay,
)
from promedia.errors import ValidationError

SRC = {"a": Path("/media/a.mp4"), "b": Path("/media/b.mp4"), "music": Path("/media/m.mp3")}
OUT = Path("/out/final.mp4")


def simple(**kw) -> EDL:
    return EDL(clips=[Clip(asset_id="a", start=0, end=5)], **kw)


# --- the document ------------------------------------------------------------


def test_an_edl_round_trips_through_json():
    """It is stored as JSON and edited by two different actors; a lossy round
    trip would silently discard whichever field the other one set."""
    original = EDL(
        aspect="vertical",
        clips=[Clip(asset_id="a", start=1, end=4, effect="sepia", speed=2.0,
                    transition_in="fade", volume=0.8,
                    brightness=0.2, contrast=1.3, saturation=0.7,
                    white_balance=-0.4, temperature=4200.0)],
        text=[TextOverlay(text="Title", position="top", size=50)],
        audio=[AudioTrack(asset_id="music", volume=0.2, fade_in=1.5)],
        subtitle_asset_id="subs",
    )
    restored = EDL.from_dict(original.to_dict())
    assert restored.to_dict() == original.to_dict()


def test_a_newer_edl_version_is_refused():
    """Same reasoning as the backup artefact: an older build cannot know what a
    later field means, and guessing corrupts an edit."""
    raw = simple().to_dict()
    raw["version"] = 99
    with pytest.raises(ValidationError) as excinfo:
        EDL.from_dict(raw)
    assert "99" in str(excinfo.value)


def test_clip_duration_accounts_for_speed():
    assert Clip(asset_id="a", start=10, end=20).duration(None) == 10
    assert Clip(asset_id="a", start=10, end=20, speed=2.0).duration(None) == 5
    # An open-ended clip needs the source duration to be knowable at all.
    assert Clip(asset_id="a", start=0).duration(None) is None
    assert Clip(asset_id="a", start=0).duration(30) == 30


def test_an_edl_written_before_grading_existed_loads_with_neutral_grade(tmp_path):
    """AC-2's document-level half: a v1 EDL (T-064 bumped EDL_VERSION to 2)
    with no grade keys at all — exactly what every EDL on disk before this
    task looked like — must load with every grade field at GRADE_NEUTRAL."""
    raw = {
        "version": 1,
        "aspect": "landscape",
        "clips": [{"asset_id": "a", "start": 0, "end": 5}],
        "text": [], "audio": [], "subtitle_asset_id": None, "normalise_audio": True,
    }
    restored = EDL.from_dict(raw)
    clip = restored.clips[0]
    assert clip.brightness == 0.0
    assert clip.contrast == 1.0
    assert clip.saturation == 1.0
    assert clip.white_balance == 0.0
    assert clip.temperature == 6500.0
    assert clip.is_graded() is False


def test_a_default_clip_reports_not_graded_a_graded_one_does():
    assert Clip(asset_id="a").is_graded() is False
    assert Clip(asset_id="a", brightness=0.1).is_graded() is True
    assert Clip(asset_id="a", temperature=5000.0).is_graded() is True


def test_asset_ids_covers_video_audio_and_subtitles():
    """Used to check rights and availability BEFORE rendering. Missing one
    means an edit that fails part-way through instead of up front."""
    edl = EDL(
        clips=[Clip(asset_id="a"), Clip(asset_id="b"), Clip(asset_id="a")],
        audio=[AudioTrack(asset_id="music")],
        subtitle_asset_id="subs",
    )
    assert edl.asset_ids() == ["a", "b", "music", "subs"]


# --- validation refuses what cannot render -----------------------------------


def test_an_empty_edl_is_refused():
    with pytest.raises(ValidationError):
        EDL().validate()


@pytest.mark.parametrize("bad,message", [
    (Clip(asset_id="a", start=-1), "negative"),
    (Clip(asset_id="a", start=10, end=5), "after start"),
    (Clip(asset_id="a", speed=50), "range"),
    (Clip(asset_id="a", effect="cartoonify"), "not available"),
    (Clip(asset_id="a", transition_in="starwipe"), "not available"),
    (Clip(asset_id="a", brightness=5.0), "range"),
    (Clip(asset_id="a", contrast=-1.0), "range"),
    (Clip(asset_id="a", saturation=10.0), "range"),
    (Clip(asset_id="a", white_balance=2.0), "range"),
    (Clip(asset_id="a", temperature=100.0), "range"),
])
def test_unrenderable_clips_are_refused(bad, message):
    """Each refusal names the offending clip, because an agent acting on it
    needs to know which of forty clips is wrong."""
    with pytest.raises(ValidationError) as excinfo:
        EDL(clips=[bad]).validate()
    text = str(excinfo.value)
    assert message in text and "clips[0]" in text


def test_an_out_of_range_grade_value_is_refused_before_any_render_is_attempted():
    """AC-3, spelled out explicitly rather than folded only into the
    parametrized table above: compile_render must never reach ffmpeg for an
    unrenderable grade value — edl.validate() is called first and refuses."""
    edl = EDL(clips=[Clip(asset_id="a", end=5, saturation=99.0)])
    with pytest.raises(ValidationError) as excinfo:
        render.compile_render(edl, SRC, OUT)
    assert "saturation" in str(excinfo.value) and "clips[0]" in str(excinfo.value)


def test_an_unknown_aspect_is_refused_and_lists_the_real_ones():
    with pytest.raises(ValidationError) as excinfo:
        simple(aspect="imax").validate()
    assert "landscape" in str(excinfo.value.detail)


def test_empty_text_is_refused():
    with pytest.raises(ValidationError):
        EDL(clips=[Clip(asset_id="a")], text=[TextOverlay(text="   ")]).validate()


def test_every_aspect_preset_validates_and_has_a_resolution():
    for name in ASPECT_PRESETS:
        edl = simple(aspect=name)
        edl.validate()
        w, h = edl.resolution()
        assert w > 0 and h > 0


# --- compilation to a filter graph -------------------------------------------


def test_the_graph_concatenates_every_clip():
    plan = render.compile_render(
        EDL(clips=[Clip(asset_id="a", end=2), Clip(asset_id="b", end=2)]), SRC, OUT
    )
    assert "concat=n=2:v=1:a=1" in plan.filter_graph
    assert plan.source_count == 2


def test_scaling_preserves_aspect_and_pads():
    """The difference between a vertical render and a distorted one. Without
    force_original_aspect_ratio + pad, landscape footage is stretched."""
    plan = render.compile_render(simple(aspect="vertical"), SRC, OUT)
    assert "force_original_aspect_ratio=decrease" in plan.filter_graph
    assert "pad=1080:1920" in plan.filter_graph
    assert (plan.width, plan.height) == (1080, 1920)


def test_speed_change_adjusts_video_and_audio_together():
    """setpts without atempo desynchronises audio from video — the classic
    speed-change bug, and silent."""
    plan = render.compile_render(
        EDL(clips=[Clip(asset_id="a", end=4, speed=2.0)]), SRC, OUT
    )
    assert "setpts=0.500000*PTS" in plan.filter_graph
    assert "atempo=" in plan.filter_graph


def test_extreme_speeds_chain_atempo():
    """atempo only accepts 0.5-2.0 per instance. A single atempo=4 is IGNORED
    by ffmpeg rather than rejected, so the audio would drift silently."""
    plan = render.compile_render(
        EDL(clips=[Clip(asset_id="a", end=4, speed=4.0)]), SRC, OUT
    )
    assert plan.filter_graph.count("atempo=") >= 2


def test_caption_text_never_enters_the_filter_graph(tmp_path):
    """The rule that made captions work at all.

    Inline text could not be made reliable: an apostrophe terminates ffmpeg's
    quoted section, a colon splits arguments, and a percent triggers strftime
    expansion that escaping cannot prevent. Text goes to a sidecar file so the
    content is never parsed as graph syntax.
    """
    nasty = "Q1: revenue +12% (it's up) \\ 100%"
    plan = render.compile_render(
        EDL(clips=[Clip(asset_id="a", end=2)],
            text=[TextOverlay(text=nasty, start=1, end=3)]),
        SRC, OUT, font=Path("C:/Windows/Fonts/arial.ttf"), workspace=tmp_path,
    )
    assert "textfile=" in plan.filter_graph
    assert "revenue" not in plan.filter_graph, "caption content leaked into the graph"
    assert "expansion=none" in plan.filter_graph
    assert "between(t,1,3)" in plan.filter_graph
    # and the text really is on disk, byte for byte
    assert (tmp_path / "text_0.txt").read_text(encoding="utf-8") == nasty


def test_each_overlay_gets_its_own_sidecar(tmp_path):
    render.compile_render(
        EDL(clips=[Clip(asset_id="a", end=2)],
            text=[TextOverlay(text="one"), TextOverlay(text="two")]),
        SRC, OUT, font=Path("C:/Windows/Fonts/arial.ttf"), workspace=tmp_path,
    )
    assert (tmp_path / "text_0.txt").read_text(encoding="utf-8") == "one"
    assert (tmp_path / "text_1.txt").read_text(encoding="utf-8") == "two"


def test_text_always_carries_an_explicit_fontfile(tmp_path):
    """Without it ffmpeg SEGFAULTS on this platform — no error to parse, which
    is the least debuggable failure available."""
    plan = render.compile_render(
        EDL(clips=[Clip(asset_id="a", end=2)], text=[TextOverlay(text="hi")]),
        SRC, OUT, font=Path("C:/Windows/Fonts/arial.ttf"), workspace=tmp_path,
    )
    assert plan.filter_graph.count("fontfile=") == 1


def test_background_audio_is_mixed_without_extending_the_video():
    """duration=first. A long music bed otherwise runs past the last frame,
    which is ffmpeg's default and almost never what anyone wants."""
    plan = render.compile_render(
        EDL(clips=[Clip(asset_id="a", end=2)],
            audio=[AudioTrack(asset_id="music", volume=0.3, fade_in=2)]),
        SRC, OUT,
    )
    assert "amix=inputs=2:duration=first" in plan.filter_graph
    assert "afade=t=in" in plan.filter_graph


def test_loudness_normalisation_is_on_by_default_and_can_be_turned_off():
    assert "loudnorm" in render.compile_render(simple(), SRC, OUT).filter_graph
    off = render.compile_render(simple(normalise_audio=False), SRC, OUT)
    assert "loudnorm" not in off.filter_graph


def test_pixel_format_is_forced():
    """Without yuv420p some players show a black frame and no error at all."""
    assert "yuv420p" in render.compile_render(simple(), SRC, OUT).args


def test_only_the_needed_span_is_decoded():
    """-ss and -t as INPUT options. As output options ffmpeg decodes the whole
    file and throws most of it away, which on long sources dominates render time."""
    plan = render.compile_render(
        EDL(clips=[Clip(asset_id="a", start=60, end=70)]), SRC, OUT
    )
    args = plan.args
    assert args.index("-ss") < args.index("-i")
    assert args[args.index("-t") + 1] == "10"


@pytest.mark.parametrize("quality", sorted(render.QUALITY_PRESETS))
def test_every_quality_preset_compiles(quality):
    plan = render.compile_render(simple(), SRC, OUT, quality=quality)
    assert render.QUALITY_PRESETS[quality]["encoder"] in plan.args


def test_an_unknown_quality_is_refused():
    with pytest.raises(ValidationError):
        render.compile_render(simple(), SRC, OUT, quality="cinema")


def test_compiling_validates_first():
    """Rendering is the expensive step, so a knowable problem must be caught
    before ffmpeg is ever invoked."""
    with pytest.raises(ValidationError):
        render.compile_render(EDL(clips=[Clip(asset_id="a", start=10, end=5)]), SRC, OUT)


# --- colour grading (T-064, DR-019) -------------------------------------------
#
# Structural only, run without ffmpeg on PATH — same split as everything else
# in this file. The real-render, decoded-pixel proof for AC-1 and AC-2 lives
# in tests/test_transitions.py, which already carries the solid-colour clip
# fixtures and the _mean_rgb helper this needs; see its "colour grading"
# section for measured evidence rather than filter-string reasoning alone.


def test_a_neutral_grade_clip_emits_no_grade_filter_at_all_ac2():
    """AC-2's structural half: an EDL whose clips carry only GRADE_NEUTRAL
    values (exactly what every pre-T-064 EDL defaults to, per from_dict)
    must produce a filter_graph with none of the three grade filters in it —
    this is what makes it byte-identical to what compile_render produced
    before this task."""
    plan = render.compile_render(simple(), SRC, OUT)
    assert "eq=" not in plan.filter_graph
    assert "colorbalance=" not in plan.filter_graph
    assert "colortemperature=" not in plan.filter_graph


def test_only_the_changed_grade_field_is_emitted_in_the_eq_filter():
    """Adjusting one eq-backed field must not silently reset the other two to
    eq's own built-in defaults — only the field that changed appears."""
    plan = render.compile_render(
        EDL(clips=[Clip(asset_id="a", end=2, brightness=0.3)]), SRC, OUT
    )
    assert "eq=brightness=0.3" in plan.filter_graph
    assert "contrast=" not in plan.filter_graph
    assert "saturation=" not in plan.filter_graph


def test_all_three_eq_backed_fields_combine_into_one_eq_call():
    plan = render.compile_render(
        EDL(clips=[Clip(asset_id="a", end=2, brightness=0.1, contrast=1.2, saturation=0.5)]),
        SRC, OUT,
    )
    assert "eq=brightness=0.1:contrast=1.2:saturation=0.5" in plan.filter_graph


def test_white_balance_compiles_to_colorbalance():
    plan = render.compile_render(
        EDL(clips=[Clip(asset_id="a", end=2, white_balance=0.5)]), SRC, OUT
    )
    assert "colorbalance=rs=0.5:bs=-0.5:rm=0.5:bm=-0.5:rh=0.5:bh=-0.5" in plan.filter_graph
    assert "eq=" not in plan.filter_graph
    assert "colortemperature=" not in plan.filter_graph


def test_temperature_compiles_to_colortemperature():
    plan = render.compile_render(
        EDL(clips=[Clip(asset_id="a", end=2, temperature=3200.0)]), SRC, OUT
    )
    assert "colortemperature=temperature=3200" in plan.filter_graph
    assert "eq=" not in plan.filter_graph
    assert "colorbalance=" not in plan.filter_graph
