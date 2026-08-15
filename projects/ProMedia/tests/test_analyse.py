"""T-047 — transcription and deterministic rough-cut analysis.

Two halves with opposite evidence shapes, kept in separate sections below:

* Silence and scene detection are DETERMINISTIC. ``tests/test_edl.py`` proves
  compilation is a pure function by asserting the graph structurally; this
  file proves detection the same way — generate audio/video with a KNOWN
  property using ffmpeg's own synthetic sources, then assert the spans come
  back where they were put. That is real evidence regardless of what
  transcription hardware is available.

* Transcription needs faster-whisper, which this machine does not have
  (``pip show faster-whisper`` reports nothing installed, confirmed before
  writing these tests). AC-3 is the criterion this task is judged on: an
  unavailable capability must return a STRUCTURED refusal naming exactly what
  would satisfy it, never an empty result reported as success. Every
  transcription test below asserts the refusal path; none can exercise a real
  model on this machine, and that gap is disclosed rather than worked around.
"""

from __future__ import annotations

import pytest

from promedia.core import db
from promedia.core.media import analyse, ffmpeg
from promedia.core.media.edl import EDL
from promedia.core.principal import agent
from promedia.core.registry import Context, invoke, load_operations
from promedia.errors import MediaUnavailable, NotFound, ValidationError
from tests.conftest import declaration_original, make_config

OPERATIONS = load_operations()
needs_ffmpeg = pytest.mark.skipif(not ffmpeg.available(), reason="ffmpeg not installed")

ANALYSE_OPS = {"analysis-capabilities", "transcribe", "propose-rough-cut"}


# --- fixtures: audio and video with KNOWN, deliberately placed properties ---


@pytest.fixture(scope="module")
def silence_audio(tmp_path_factory):
    """2s tone, 3s TRUE silence, 2s tone. The ground truth: silence at [2, 5).

    Built by concatenating three lavfi sources through a filter graph rather
    than post-processing real speech, so the "correct answer" is known exactly
    rather than estimated by ear.
    """
    if not ffmpeg.available():
        pytest.skip("ffmpeg not installed")
    out = tmp_path_factory.mktemp("silence") / "speech_like.wav"
    ffmpeg.run([
        "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
        "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono:d=3",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
        "-filter_complex", "[0:a][1:a][2:a]concat=n=3:v=0:a=1[aout]",
        "-map", "[aout]", "-c:a", "pcm_s16le", str(out),
    ], timeout_seconds=120)
    return out


@pytest.fixture(scope="module")
def silence_video(tmp_path_factory):
    """The same known silence, this time on a file WITH a video stream too —
    what propose-rough-cut actually receives, as opposed to detect_silence's
    unit-level audio-only fixture above."""
    if not ffmpeg.available():
        pytest.skip("ffmpeg not installed")
    out = tmp_path_factory.mktemp("silence_video") / "clip.mp4"
    ffmpeg.run([
        "-f", "lavfi", "-i", "testsrc=size=320x240:rate=10:duration=7",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
        "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono:d=3",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
        "-filter_complex", "[1:a][2:a][3:a]concat=n=3:v=0:a=1[aout]",
        "-map", "0:v", "-map", "[aout]",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "pcm_s16le", "-shortest", str(out),
    ], timeout_seconds=120)
    return out


@pytest.fixture(scope="module")
def two_scene_video(tmp_path_factory):
    """A hard cut at t=2s: 2s flat red, then 2s flat blue. Ground truth: one
    scene change near t=2. Flat colour (not testsrc's animation) keeps the
    scene score near zero WITHIN each half, so the only spike is the cut."""
    if not ffmpeg.available():
        pytest.skip("ffmpeg not installed")
    out = tmp_path_factory.mktemp("scenes") / "cut.mp4"
    ffmpeg.run([
        "-f", "lavfi", "-i", "color=c=red:size=320x240:rate=10:duration=2",
        "-f", "lavfi", "-i", "color=c=blue:size=320x240:rate=10:duration=2",
        "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[vout]",
        "-map", "[vout]", "-pix_fmt", "yuv420p", str(out),
    ], timeout_seconds=120)
    return out


@pytest.fixture(scope="module")
def flat_video(tmp_path_factory):
    """No cut anywhere: ground truth is zero scene changes."""
    if not ffmpeg.available():
        pytest.skip("ffmpeg not installed")
    out = tmp_path_factory.mktemp("flat") / "flat.mp4"
    ffmpeg.run([
        "-f", "lavfi", "-i", "color=c=green:size=320x240:rate=10:duration=3",
        "-pix_fmt", "yuv420p", str(out),
    ], timeout_seconds=120)
    return out


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("PROMEDIA_CREDENTIAL_STORE", str(tmp_path / "creds.json"))
    cfg = make_config(tmp_path)
    conn = db.connect(cfg.db_path)
    db.apply_schema(conn)
    yield cfg, Context(config=cfg, conn=conn, principal=agent("ag"), agent_id="agent-a")
    conn.close()


def ingest(env, path):
    cfg, ctx = env
    return invoke(ctx, "ingest", {
        "source_path": str(path), "declaration": declaration_original(),
    })["asset_id"]


# =============================================================================
# Silence detection — ffmpeg silencedetect, deterministic, no model
# =============================================================================


@needs_ffmpeg
def test_silence_detection_finds_the_span_that_was_actually_there(silence_audio):
    """The core claim of AC-2's evidence: detection reports what was put there.

    2s tone / 3s TRUE silence / 2s tone. silencedetect reports the moment
    silence genuinely began, not a delayed confirmation, so a tight tolerance
    is the right assertion, not a loose one.
    """
    spans = analyse.detect_silence(
        silence_audio, noise_threshold_db=-30.0, min_silence_seconds=0.4,
        timeout_seconds=60,
    )
    assert len(spans) == 1
    assert spans[0].start == pytest.approx(2.0, abs=0.15)
    assert spans[0].end == pytest.approx(5.0, abs=0.15)
    assert spans[0].duration == pytest.approx(3.0, abs=0.2)


@needs_ffmpeg
def test_silence_shorter_than_the_minimum_is_not_reported(silence_audio):
    """d= is the minimum SUSTAINED duration to count. A 3s span is not silence
    at all when the minimum required is 10s — the negative case matters as
    much as the positive one."""
    spans = analyse.detect_silence(
        silence_audio, noise_threshold_db=-30.0, min_silence_seconds=10.0,
        timeout_seconds=60,
    )
    assert spans == []


@needs_ffmpeg
def test_silence_detection_refuses_a_file_with_no_audio_track(flat_video):
    with pytest.raises(ValidationError, match="no audio track"):
        analyse.detect_silence(
            flat_video, noise_threshold_db=-30.0, min_silence_seconds=0.5,
            timeout_seconds=60,
        )


# --- kept_spans: the pure boundary arithmetic (the sabotage target) --------


def test_kept_spans_is_the_exact_complement_of_the_silent_span():
    """No padding, no minimum: kept spans are exactly what is left over."""
    kept = analyse.kept_spans(
        10.0, [analyse.SilenceSpan(3.0, 5.0)],
        min_clip_seconds=0.0, padding_seconds=0.0,
    )
    assert kept == [(0.0, 3.0), (5.0, 10.0)]


def test_kept_spans_handles_leading_and_trailing_silence():
    """Silence touching either edge must not produce a phantom zero-length
    kept span at that edge."""
    kept = analyse.kept_spans(
        10.0,
        [analyse.SilenceSpan(0.0, 2.0), analyse.SilenceSpan(8.0, 10.0)],
        min_clip_seconds=0.0, padding_seconds=0.0,
    )
    assert kept == [(2.0, 8.0)]


def test_kept_spans_padding_shrinks_the_cut_not_the_keep():
    """Padding leaves a buffer of near-silence attached to the kept clips,
    rather than trimming flush against the detected boundary."""
    kept = analyse.kept_spans(
        10.0, [analyse.SilenceSpan(3.0, 5.0)],
        min_clip_seconds=0.0, padding_seconds=0.2,
    )
    assert kept == [(0.0, 3.2), (4.8, 10.0)]


def test_kept_spans_drops_fragments_below_the_minimum_clip_length():
    """A 1.5s gap between two long silences is not worth keeping as its own
    clip once the minimum is 2s."""
    kept = analyse.kept_spans(
        10.0,
        [analyse.SilenceSpan(0.0, 3.0), analyse.SilenceSpan(4.5, 10.0)],
        min_clip_seconds=2.0, padding_seconds=0.0,
    )
    assert kept == []


def test_kept_spans_sorts_unordered_input():
    """Silence spans are not guaranteed to arrive in order; the complement
    must still be correct if they do not."""
    kept = analyse.kept_spans(
        10.0,
        [analyse.SilenceSpan(7.0, 9.0), analyse.SilenceSpan(1.0, 2.0)],
        min_clip_seconds=0.0, padding_seconds=0.0,
    )
    assert kept == [(0.0, 1.0), (2.0, 7.0), (9.0, 10.0)]


def test_kept_spans_merges_overlapping_silence():
    """Two overlapping silent spans must not double-subtract or leave a
    phantom sliver of 'kept' time between them."""
    kept = analyse.kept_spans(
        10.0,
        [analyse.SilenceSpan(2.0, 5.0), analyse.SilenceSpan(4.0, 7.0)],
        min_clip_seconds=0.0, padding_seconds=0.0,
    )
    assert kept == [(0.0, 2.0), (7.0, 10.0)]


# --- propose_rough_cut: the document, never applied -------------------------


def test_propose_rough_cut_builds_a_valid_edl_excluding_the_silent_span():
    document = analyse.propose_rough_cut(
        "asset-x", 10.0, [analyse.SilenceSpan(3.0, 5.0)],
        min_clip_seconds=0.0, padding_seconds=0.0,
    )
    assert isinstance(document, EDL)
    document.validate()  # must not raise — a proposal that cannot render is useless
    assert [(c.start, c.end) for c in document.clips] == [(0.0, 3.0), (5.0, 10.0)]
    assert all(c.asset_id == "asset-x" for c in document.clips)


def test_propose_rough_cut_refuses_rather_than_returning_an_unrenderable_edl():
    """If nothing survives the cut, EDL.validate() would refuse zero clips
    anyway — this raises with the reason stated plainly instead of deferring
    to that refusal two calls later."""
    with pytest.raises(ValidationError, match="no span survived"):
        analyse.propose_rough_cut(
            "asset-x", 10.0, [analyse.SilenceSpan(0.0, 10.0)],
            min_clip_seconds=0.0, padding_seconds=0.0,
        )


# =============================================================================
# Scene-change detection — deterministic, informational
# =============================================================================


@needs_ffmpeg
def test_scene_detection_finds_the_cut_that_was_actually_there(two_scene_video):
    changes = analyse.detect_scene_changes(two_scene_video, threshold=0.4, timeout_seconds=60)
    assert len(changes) >= 1
    assert any(1.5 <= t <= 2.5 for t in changes)


@needs_ffmpeg
def test_scene_detection_reports_nothing_for_flat_footage(flat_video):
    assert analyse.detect_scene_changes(flat_video, threshold=0.4, timeout_seconds=60) == []


@needs_ffmpeg
def test_scene_detection_refuses_a_file_with_no_video_track(silence_audio):
    with pytest.raises(ValidationError, match="no video track"):
        analyse.detect_scene_changes(silence_audio, threshold=0.4, timeout_seconds=60)


# =============================================================================
# Transcription — AC-1 / AC-3. faster-whisper is NOT installed on this machine.
# =============================================================================


def test_transcription_is_reported_unavailable_on_this_machine():
    """Ground truth, checked directly rather than assumed: `pip show
    faster-whisper` reports nothing installed. If this ever starts failing it
    means the environment changed, which is exactly the signal AC-1's
    NOT_RUN status is conditioned on."""
    assert analyse.transcription_available() is False


def test_an_unavailable_transcription_raises_a_structured_refusal_not_an_empty_result():
    """The exact defect class this task is judged on: an unavailable
    capability must name what would satisfy it, never silently return
    nothing and report success."""
    with pytest.raises(analyse.TranscriptionUnavailable) as excinfo:
        analyse.transcribe(__file__ and __import__("pathlib").Path(__file__), model_size="base", language=None)
    detail = excinfo.value.detail
    assert detail["package"] == "faster-whisper"
    assert detail["install"] == "pip install faster-whisper"
    assert detail["model_size"] == "base"
    assert "remedy" in detail
    assert "cpu_realtime_factor_estimate" in detail


def test_require_transcription_names_the_requested_model_size():
    with pytest.raises(analyse.TranscriptionUnavailable) as excinfo:
        analyse.require_transcription("small")
    assert excinfo.value.detail["model_size"] == "small"


def test_transcription_requirements_covers_every_configured_model_size():
    for size in ("tiny", "base", "small", "medium"):
        req = analyse.transcription_requirements(size)
        assert req["model_size"] == size
        assert req["model_download_mb_estimate"] > 0
        assert req["package"] == "faster-whisper"


def test_segments_to_captions_is_a_pure_transform_independent_of_whisper():
    """AC-1's evidence that does not depend on the model being installed:
    the segment -> caption transform is tested directly on plain values."""
    segments = [
        analyse.TranscriptSegment(start=0.0, end=1.5, text="Hello there"),
        analyse.TranscriptSegment(start=1.5, end=3.0, text="  "),  # blank, dropped
        analyse.TranscriptSegment(start=3.0, end=4.2, text="Second line"),
    ]
    captions = analyse.segments_to_captions(segments)
    assert len(captions) == 2
    assert captions[0].text == "Hello there"
    assert captions[0].start == 0.0 and captions[0].end == 1.5
    assert captions[1].text == "Second line"
    # Captions must themselves be valid EDL text overlays — the whole point of
    # reusing TextOverlay rather than inventing new vocabulary.
    document = EDL(clips=[], text=captions)
    for overlay in document.text:
        assert overlay.text.strip()


# =============================================================================
# Through the operation layer — both surfaces reach these via the registry.
# =============================================================================


def test_the_three_operations_are_registered():
    assert ANALYSE_OPS <= set(OPERATIONS)


@needs_ffmpeg
def test_analysis_capabilities_reports_ffmpeg_available_and_transcription_not(env):
    cfg, ctx = env
    result = invoke(ctx, "analysis-capabilities", {})
    assert result["ffmpeg_available"] is True
    assert result["deterministic_analysis"]["silence_detection"] is True
    assert result["transcription"]["available"] is False
    assert result["transcription"]["requirements"]["package"] == "faster-whisper"


@needs_ffmpeg
def test_transcribe_operation_refuses_structurally_through_the_real_operation_layer(env, silence_video):
    """AC-3, end to end: not the bare function, the actual registered
    operation an agent or the web surface would call."""
    asset_id = ingest(env, silence_video)
    cfg, ctx = env
    with pytest.raises(analyse.TranscriptionUnavailable) as excinfo:
        invoke(ctx, "transcribe", {"asset_id": asset_id})
    assert excinfo.value.detail["package"] == "faster-whisper"


@needs_ffmpeg
def test_transcribe_operation_refuses_for_an_unknown_asset(env):
    cfg, ctx = env
    with pytest.raises(NotFound):
        invoke(ctx, "transcribe", {"asset_id": "no-such-asset"})


@needs_ffmpeg
def test_propose_rough_cut_operation_excludes_the_real_detected_silence(env, silence_video):
    """The full path: ingest real media with a KNOWN silent span, call the
    registered operation, and check the numbers that come back against the
    span that was actually placed in the fixture (2s tone / 3s silence / 2s
    tone, ground truth silence at [2, 5))."""
    asset_id = ingest(env, silence_video)
    cfg, ctx = env
    result = invoke(ctx, "propose-rough-cut", {
        "asset_id": asset_id, "min_silence_seconds": 0.4, "padding_seconds": 0.0,
    })
    assert result["ok"] is True
    assert len(result["silence_spans"]) == 1
    assert result["silence_spans"][0]["start"] == pytest.approx(2.0, abs=0.15)
    assert result["silence_spans"][0]["end"] == pytest.approx(5.0, abs=0.15)
    assert result["source_duration_seconds"] == pytest.approx(7.0, abs=0.2)
    assert result["kept_duration_seconds"] == pytest.approx(4.0, abs=0.3)
    assert result["cut_duration_seconds"] == pytest.approx(3.0, abs=0.3)

    # The returned document is what set-edl (T-042, already registered)
    # would accept — proving the proposal is genuinely reviewable/appliable,
    # not just descriptive JSON.
    document = EDL.from_dict(result["edl"])
    document.validate()
    assert len(document.clips) == 2


@needs_ffmpeg
def test_propose_rough_cut_never_writes_anything(env, silence_video):
    """AC-2's rule made concrete: calling this operation must leave no trace
    in the database at all — no project, no version, no lock — because it is
    a read/compute-only operation by construction."""
    asset_id = ingest(env, silence_video)
    cfg, ctx = env
    invoke(ctx, "propose-rough-cut", {"asset_id": asset_id, "min_silence_seconds": 0.4})
    projects = ctx.conn.execute("SELECT COUNT(*) AS n FROM projects").fetchone()["n"]
    locks = ctx.conn.execute("SELECT COUNT(*) AS n FROM entity_locks").fetchone()["n"]
    assert projects == 0
    assert locks == 0


@needs_ffmpeg
def test_propose_rough_cut_reports_scene_changes_when_asked(env, silence_video):
    cfg, ctx = env
    asset_id = ingest(env, silence_video)
    result = invoke(ctx, "propose-rough-cut", {
        "asset_id": asset_id, "min_silence_seconds": 0.4, "include_scene_changes": True,
    })
    assert "scene_changes_seconds" in result


@needs_ffmpeg
def test_propose_rough_cut_refuses_media_whose_bytes_are_gone(env, silence_video):
    """Availability, not rights (T-029's distinction, reused here): a
    retention-deleted asset must fail with MediaUnavailable, not a generic
    error and not a silent empty proposal."""
    asset_id = ingest(env, silence_video)
    cfg, ctx = env
    ctx.conn.execute("UPDATE assets SET state = 'deleted' WHERE id = ?", (asset_id,))
    ctx.conn.commit()
    with pytest.raises(MediaUnavailable):
        invoke(ctx, "propose-rough-cut", {"asset_id": asset_id})


@needs_ffmpeg
def test_thresholds_come_from_configuration_not_literals(tmp_path, monkeypatch, silence_video):
    """Protocol 05: an operator on this hardware will want to tune the noise
    floor per source. Moving the CONFIGURED default and asserting the
    operation follows it, then that an explicit argument still wins."""
    monkeypatch.setenv("PROMEDIA_CREDENTIAL_STORE", str(tmp_path / "creds.json"))
    cfg = make_config(tmp_path, **{"analysis.silence_min_duration_seconds": 10.0})
    conn = db.connect(cfg.db_path)
    db.apply_schema(conn)
    ctx = Context(config=cfg, conn=conn, principal=agent("ag"), agent_id="agent-a")
    try:
        asset_id = ingest((cfg, ctx), silence_video)
        # Configured minimum (10s) is longer than the 3s silence in the
        # fixture, so with no override the proposal must find NO silence.
        via_config = invoke(ctx, "propose-rough-cut", {"asset_id": asset_id})
        assert via_config["silence_spans"] == []
        # An explicit argument overrides the configured default.
        via_argument = invoke(ctx, "propose-rough-cut", {
            "asset_id": asset_id, "min_silence_seconds": 0.4,
        })
        assert len(via_argument["silence_spans"]) == 1
    finally:
        conn.close()
