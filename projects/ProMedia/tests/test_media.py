"""T-041 — the ffmpeg boundary, exercised against real media.

Separate from tests/test_edl.py because everything here needs ffmpeg installed
and actually encodes something. Kept small and short for that reason: the graph
itself is asserted structurally in test_edl.py, so these exist to prove the
boundary holds against the real tool rather than to re-test compilation.

Fixtures generate their own media with ffmpeg's synthetic sources, so the suite
depends on no checked-in binary and no particular file being present.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from promedia.core.media import ffmpeg, render
from promedia.core.media.edl import EDL, AudioTrack, Clip, TextOverlay

needs_ffmpeg = pytest.mark.skipif(
    not ffmpeg.available(), reason="ffmpeg/ffprobe not installed"
)


@pytest.fixture(scope="module")
def clip_a(tmp_path_factory) -> Path:
    """4 seconds of colour bars with a tone. Synthetic, so no fixture binary."""
    out = tmp_path_factory.mktemp("media") / "a.mp4"
    ffmpeg.run([
        "-f", "lavfi", "-i", "testsrc=size=640x480:rate=25:duration=4",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=4",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-shortest", str(out),
    ], timeout_seconds=120)
    return out


@pytest.fixture(scope="module")
def clip_b(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("media") / "b.mp4"
    ffmpeg.run([
        "-f", "lavfi", "-i", "smptebars=size=320x240:rate=25:duration=3",
        "-f", "lavfi", "-i", "sine=frequency=880:duration=3",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-shortest", str(out),
    ], timeout_seconds=120)
    return out


# --- the boundary ------------------------------------------------------------


@needs_ffmpeg
def test_probe_reads_what_is_actually_there(clip_a):
    info = ffmpeg.probe(clip_a)
    assert info.has_video and info.has_audio
    assert (info.width, info.height) == (640, 480)
    assert info.duration_seconds == pytest.approx(4.0, abs=0.3)
    assert info.frame_rate == pytest.approx(25.0, abs=0.1)
    assert info.video_codec == "h264"
    assert info.byte_size > 0


@needs_ffmpeg
def test_probing_a_non_media_file_fails_loudly(tmp_path):
    junk = tmp_path / "not-media.mp4"
    junk.write_text("this is not a video", encoding="utf-8")
    with pytest.raises(ffmpeg.RenderFailed):
        ffmpeg.probe(junk)


def test_probing_a_missing_file_is_refused(tmp_path):
    from promedia.errors import ProMediaError

    with pytest.raises(ProMediaError):
        ffmpeg.probe(tmp_path / "nope.mp4")


@needs_ffmpeg
def test_a_bad_filter_graph_reports_ffmpeg_s_own_words(tmp_path):
    """ffmpeg's diagnostics are the only useful ones; a generic message would
    throw away the single thing that says what is wrong."""
    with pytest.raises(ffmpeg.RenderFailed) as excinfo:
        ffmpeg.run(["-f", "lavfi", "-i", "testsrc=duration=1",
                    "-vf", "thisfilterdoesnotexist", str(tmp_path / "x.mp4")],
                   timeout_seconds=60)
    assert excinfo.value.detail.get("detail")


def test_a_missing_tool_names_the_fix(monkeypatch):
    """The one failure with a single actionable remedy, and it must never read
    as a media problem — the media is fine, the machine is not equipped."""
    monkeypatch.setattr(ffmpeg, "tool_path", lambda name: None)
    with pytest.raises(ffmpeg.MediaToolMissing) as excinfo:
        ffmpeg.require("ffmpeg")
    assert "winget" in str(excinfo.value.detail)


@needs_ffmpeg
def test_a_font_file_exists_for_text_rendering():
    """drawtext SEGFAULTS without one on this platform, so its absence must be
    a refusal rather than something discovered at render time."""
    assert ffmpeg.default_font() is not None
    assert "fontfile=" in ffmpeg.font_argument()


def test_filter_escaping_handles_the_characters_that_break_graphs():
    escaped = ffmpeg.escape_for_filter("time 10:30 'x' 100%")
    assert "\\:" in escaped and "\\'" in escaped and "\\%" in escaped


def test_paths_and_text_escape_by_DIFFERENT_rules():
    """The bug this pins cost a debugging round.

    A Windows path escaped as TEXT becomes C\\:\\\\Windows\\\\Fonts, whose doubled
    backslashes the filter parser rejects. Paths need forward slashes plus an
    escaped colon; text needs its backslashes doubled. Verified empirically —
    the two rules genuinely differ.
    """
    windows_path = Path(r"C:\Windows\Fonts\arial.ttf")
    as_path = ffmpeg.escape_path_for_filter(windows_path)
    assert as_path == "C\\:/Windows/Fonts/arial.ttf"
    assert "\\\\" not in as_path, "a doubled backslash is what broke the graph"
    assert "\\\\" in ffmpeg.escape_for_filter(r"a\b"), "text still doubles backslashes"


# --- end to end --------------------------------------------------------------


@needs_ffmpeg
def test_a_two_clip_edit_renders_and_is_playable(clip_a, clip_b, tmp_path):
    """The whole point, in one test: two different sources, different
    resolutions, concatenated into one playable file."""
    edl = EDL(
        aspect="landscape_720",
        clips=[Clip(asset_id="a", start=0, end=2),
               Clip(asset_id="b", start=0, end=2, effect="grayscale")],
    )
    plan = render.compile_render(
        edl, {"a": clip_a, "b": clip_b}, tmp_path / "out.mp4", quality="fast"
    )
    result = render.execute(plan, timeout_seconds=300)

    assert (result["width"], result["height"]) == (1280, 720)
    assert result["duration_seconds"] == pytest.approx(4.0, abs=0.5)
    assert result["video_codec"] == "h264" and result["audio_codec"] == "aac"


@needs_ffmpeg
def test_text_and_music_render_together(clip_a, tmp_path):
    edl = EDL(
        aspect="landscape_720",
        clips=[Clip(asset_id="a", start=0, end=3)],
        text=[TextOverlay(text="Caption: it's fine", position="bottom", size=32)],
        audio=[AudioTrack(asset_id="a", volume=0.2, fade_in=0.5)],
    )
    plan = render.compile_render(edl, {"a": clip_a}, tmp_path / "out.mp4", quality="fast")
    result = render.execute(plan, timeout_seconds=300)
    assert result["byte_size"] > 0


@needs_ffmpeg
@pytest.mark.parametrize("caption", [
    "Caption: it's fine",
    "Save 50% today",                 # percent: needs expansion=none
    "Q1: revenue +12% (it's up)",     # colon AND percent AND apostrophe
    r"path C:\x and 'quoted'",        # a Windows path typed into a caption
    "plain",
])
def test_captions_survive_the_characters_people_actually_type(caption, clip_a, tmp_path):
    """Every one of these broke the render at some point while building this.

    Captions come from humans and from the agent, so they contain apostrophes,
    colons and percent signs as a matter of course. A percent sign is the nasty
    one: drawtext runs text through strftime expansion, escaping does NOT help
    because expansion happens after unescaping, and the render simply fails.
    """
    edl = EDL(aspect="landscape_720",
              clips=[Clip(asset_id="a", start=0, end=1)],
              text=[TextOverlay(text=caption, size=24)])
    plan = render.compile_render(edl, {"a": clip_a}, tmp_path / "c.mp4", quality="fast")
    assert render.execute(plan, timeout_seconds=300)["byte_size"] > 0


@needs_ffmpeg
def test_speed_change_shortens_the_output(clip_a, tmp_path):
    """Proves setpts and atempo actually took effect, rather than being
    accepted and ignored."""
    edl = EDL(aspect="landscape_720",
              clips=[Clip(asset_id="a", start=0, end=4, speed=2.0)])
    plan = render.compile_render(edl, {"a": clip_a}, tmp_path / "fast.mp4", quality="fast")
    result = render.execute(plan, timeout_seconds=300)
    assert result["duration_seconds"] == pytest.approx(2.0, abs=0.4)


@needs_ffmpeg
def test_a_landscape_source_renders_vertical_without_distortion(clip_a, tmp_path):
    """The source is 640x480; the output must be 1080x1920 with the picture
    letterboxed rather than stretched."""
    edl = EDL(aspect="vertical", clips=[Clip(asset_id="a", start=0, end=2)])
    plan = render.compile_render(edl, {"a": clip_a}, tmp_path / "v.mp4", quality="fast")
    result = render.execute(plan, timeout_seconds=300)
    assert (result["width"], result["height"]) == (1080, 1920)


@needs_ffmpeg
def test_a_render_that_produces_nothing_is_reported_as_a_failure(clip_a, tmp_path, monkeypatch):
    """ffmpeg can exit 0 having written nothing usable. Reporting that as a
    successful render is the media equivalent of a fabricated result."""
    edl = EDL(aspect="landscape_720", clips=[Clip(asset_id="a", start=0, end=1)])
    plan = render.compile_render(edl, {"a": clip_a}, tmp_path / "gone.mp4", quality="fast")
    monkeypatch.setattr(ffmpeg, "run", lambda *a, **k: "")  # pretend success, write nothing
    with pytest.raises(ffmpeg.RenderFailed) as excinfo:
        render.execute(plan, timeout_seconds=60)
    assert "no output file" in str(excinfo.value)


@needs_ffmpeg
def test_a_runaway_render_is_stopped(tmp_path):
    """A malformed graph can make ffmpeg run effectively forever, and an
    operation that never returns holds its lock for the whole of it."""
    with pytest.raises(ffmpeg.RenderFailed) as excinfo:
        ffmpeg.run(["-f", "lavfi", "-i", "testsrc=duration=3600",
                    "-c:v", "libx264", "-preset", "veryslow", str(tmp_path / "slow.mp4")],
                   timeout_seconds=2)
    assert "budget" in str(excinfo.value)
