"""The edit decision list (T-041).

The centre of the production design. An EDL is a declarative description of a
finished video — which clips, from where, in what order, with what applied — and
it is the ONLY thing an edit ever changes. Rendering is a pure function of
(source media + EDL + render settings), which buys four things at once:

* **AI and human edit the same object.** The agent writes an EDL from an
  instruction; the operator opens the same project and adjusts it. There is no
  synchronisation problem to solve because there is only one document. This is
  what makes the collaboration requirement structural rather than a feature.
* **Nothing is destructive.** Sources are never modified, so an edit is always
  reversible and a mistake costs a re-render rather than the footage.
* **Versioning is nearly free.** An EDL is small structured data; keeping every
  version is cheap, and diffing two of them says exactly what changed.
* **Renders are reproducible.** The same EDL always produces the same output,
  which is what lets a render be cached, audited, or re-run years later.

The vocabulary is deliberately small. Every operation here maps onto an ffmpeg
filter that is known to work — this is a description of achievable edits, not an
aspirational schema. Anything that cannot be rendered has no business being
expressible.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ...errors import ValidationError

EDL_VERSION = 2

# Aspect presets, named by what they are FOR rather than by their numbers,
# because that is how the operator and the agent both think about them.
ASPECT_PRESETS: dict[str, tuple[int, int]] = {
    "landscape": (1920, 1080),
    "vertical": (1080, 1920),
    "square": (1080, 1080),
    "landscape_720": (1280, 720),
    "vertical_720": (720, 1280),
}

TRANSITIONS = ("cut", "fade", "dissolve", "wipeleft", "wiperight", "slideup", "slidedown")

# Transitions that blend the tail of the clip BEFORE this one into it, and
# therefore need a previous clip to blend with. 'fade' is deliberately
# excluded from this set: it fades this clip in from black, which is a
# meaningful thing to ask for even on the very first clip of an edit.
#
# render.py (T-045) uses this same tuple to decide which boundaries need an
# absolute timeline offset (ffmpeg's xfade/acrossfade) instead of a plain
# concat. It lives here rather than being duplicated there because it is a
# property of the VOCABULARY, not of the compiler.
TIMELINE_TRANSITIONS = ("dissolve", "wipeleft", "wiperight", "slideup", "slidedown")

# Effects expressible today. Each is one ffmpeg filter with a known invocation;
# adding one means adding its compilation, not just its name.
CLIP_EFFECTS = ("none", "grayscale", "sepia", "blur", "sharpen", "brighten", "darken", "saturate")

# Colour-grade vocabulary (T-064, DR-019). Same discipline as CLIP_EFFECTS and
# TRANSITIONS: each field maps to one known ffmpeg filter in render.py's
# compiler (see render._grade_filters) —
#   brightness / contrast / saturation -> eq (one filter, only the changed
#     params are emitted, so adjusting one does not silently reset the others)
#   white_balance                      -> colorbalance (warm/cool shift,
#     applied uniformly across shadows/midtones/highlights)
#   temperature                        -> colortemperature (ffmpeg's own
#     Kelvin parameter, used directly rather than remapped)
# Every value below is each field's NEUTRAL default: a clip whose grade
# fields are all at these values must compile to no grade filter at all,
# which is what makes an EDL written before this change render identically
# (AC-2) — Clip.from_dict defaults every missing grade field to exactly one
# of these.
GRADE_NEUTRAL: dict[str, float] = {
    "brightness": 0.0,
    "contrast": 1.0,
    "saturation": 1.0,
    "white_balance": 0.0,
    "temperature": 6500.0,
}

# Range each field is refused outside of by EDL.validate(). Not the raw
# tolerance of the underlying ffmpeg filter (colortemperature alone accepts
# 1000-40000) but the range this vocabulary considers a meaningful grade for
# short screen-recording social clips (project.md C-11), matching the
# existing speed field's own "supported range, not filter tolerance" style.
GRADE_RANGES: dict[str, tuple[float, float]] = {
    "brightness": (-1.0, 1.0),
    "contrast": (0.0, 3.0),
    "saturation": (0.0, 3.0),
    "white_balance": (-1.0, 1.0),
    "temperature": (2000.0, 12000.0),
}


@dataclass
class Clip:
    """One segment of one source, placed on a track.

    ``start``/``end`` are offsets INTO THE SOURCE, not positions on the
    timeline: the timeline position is implied by order, which keeps a trim from
    silently moving everything after it.
    """

    asset_id: str
    start: float = 0.0
    end: float | None = None          # None = to the end of the source
    speed: float = 1.0
    effect: str = "none"
    transition_in: str = "cut"
    transition_duration: float = 0.5
    volume: float = 1.0
    mute: bool = False
    # Colour grade (T-064, DR-019). Defaults are GRADE_NEUTRAL's own values,
    # not duplicated as literals here so the two cannot drift apart.
    brightness: float = GRADE_NEUTRAL["brightness"]
    contrast: float = GRADE_NEUTRAL["contrast"]
    saturation: float = GRADE_NEUTRAL["saturation"]
    white_balance: float = GRADE_NEUTRAL["white_balance"]
    temperature: float = GRADE_NEUTRAL["temperature"]

    def duration(self, source_duration: float | None) -> float | None:
        end = self.end if self.end is not None else source_duration
        if end is None:
            return None
        return max(0.0, (end - self.start) / (self.speed or 1.0))

    def is_graded(self) -> bool:
        """True if any colour-grade field differs from its neutral default.

        Used for reporting (EDL.summary()) — the render compiler decides
        filter-by-filter, on its own, whether each individual field needs to
        be emitted (see render._grade_filters), rather than calling this.
        """
        return any(getattr(self, name) != neutral for name, neutral in GRADE_NEUTRAL.items())

    def to_dict(self) -> dict[str, Any]:
        return {
            "asset_id": self.asset_id, "start": self.start, "end": self.end,
            "speed": self.speed, "effect": self.effect,
            "transition_in": self.transition_in,
            "transition_duration": self.transition_duration,
            "volume": self.volume, "mute": self.mute,
            "brightness": self.brightness, "contrast": self.contrast,
            "saturation": self.saturation, "white_balance": self.white_balance,
            "temperature": self.temperature,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Clip":
        return cls(
            asset_id=str(raw["asset_id"]),
            start=float(raw.get("start", 0.0)),
            end=None if raw.get("end") is None else float(raw["end"]),
            speed=float(raw.get("speed", 1.0)),
            effect=str(raw.get("effect", "none")),
            transition_in=str(raw.get("transition_in", "cut")),
            transition_duration=float(raw.get("transition_duration", 0.5)),
            volume=float(raw.get("volume", 1.0)),
            mute=bool(raw.get("mute", False)),
            # An EDL written before T-064 has none of these keys at all — each
            # defaults to GRADE_NEUTRAL, which is what makes a pre-existing
            # EDL render identically to before (AC-2).
            brightness=float(raw.get("brightness", GRADE_NEUTRAL["brightness"])),
            contrast=float(raw.get("contrast", GRADE_NEUTRAL["contrast"])),
            saturation=float(raw.get("saturation", GRADE_NEUTRAL["saturation"])),
            white_balance=float(raw.get("white_balance", GRADE_NEUTRAL["white_balance"])),
            temperature=float(raw.get("temperature", GRADE_NEUTRAL["temperature"])),
        )


@dataclass
class TextOverlay:
    """Burned-in text: titles, lower thirds, captions authored by hand.

    Position is expressed by name rather than coordinates so the same EDL
    survives a change of aspect ratio — a caption pinned to 'bottom' stays
    readable when a landscape edit is re-rendered vertical, whereas y=980 does
    not.
    """

    text: str
    start: float = 0.0
    end: float | None = None
    position: str = "bottom"          # top | center | bottom
    size: int = 42
    color: str = "white"
    box: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text, "start": self.start, "end": self.end,
            "position": self.position, "size": self.size,
            "color": self.color, "box": self.box,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "TextOverlay":
        return cls(
            text=str(raw["text"]),
            start=float(raw.get("start", 0.0)),
            end=None if raw.get("end") is None else float(raw["end"]),
            position=str(raw.get("position", "bottom")),
            size=int(raw.get("size", 42)),
            color=str(raw.get("color", "white")),
            box=bool(raw.get("box", True)),
        )


@dataclass
class AudioTrack:
    """Music, voiceover or an effect laid under the video.

    ``duck`` is why this is a first-class object rather than another clip: music
    that does not drop under speech is the single most common amateur mistake,
    and it is cheap to get right automatically.
    """

    asset_id: str
    start: float = 0.0
    volume: float = 0.35
    loop: bool = False
    fade_in: float = 0.0
    fade_out: float = 0.0
    duck: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "asset_id": self.asset_id, "start": self.start, "volume": self.volume,
            "loop": self.loop, "fade_in": self.fade_in, "fade_out": self.fade_out,
            "duck": self.duck,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "AudioTrack":
        return cls(
            asset_id=str(raw["asset_id"]),
            start=float(raw.get("start", 0.0)),
            volume=float(raw.get("volume", 0.35)),
            loop=bool(raw.get("loop", False)),
            fade_in=float(raw.get("fade_in", 0.0)),
            fade_out=float(raw.get("fade_out", 0.0)),
            duck=bool(raw.get("duck", True)),
        )


@dataclass
class EDL:
    """A complete edit. Serialises to JSON; that JSON is the project's content."""

    aspect: str = "landscape"
    clips: list[Clip] = field(default_factory=list)
    text: list[TextOverlay] = field(default_factory=list)
    audio: list[AudioTrack] = field(default_factory=list)
    subtitle_asset_id: str | None = None
    normalise_audio: bool = True
    version: int = EDL_VERSION

    # --- serialisation ---
    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "aspect": self.aspect,
            "clips": [c.to_dict() for c in self.clips],
            "text": [t.to_dict() for t in self.text],
            "audio": [a.to_dict() for a in self.audio],
            "subtitle_asset_id": self.subtitle_asset_id,
            "normalise_audio": self.normalise_audio,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "EDL":
        if not isinstance(raw, dict):
            raise ValidationError("an EDL must be an object", got=type(raw).__name__)
        version = int(raw.get("version", EDL_VERSION))
        if version > EDL_VERSION:
            # Same reasoning as the backup artefact: an older build cannot know
            # what a later field means, and guessing corrupts an edit.
            raise ValidationError(
                f"EDL version {version} is newer than this build understands ({EDL_VERSION})",
                edl_version=version, supported=EDL_VERSION,
            )
        try:
            return cls(
                aspect=str(raw.get("aspect", "landscape")),
                clips=[Clip.from_dict(c) for c in raw.get("clips", [])],
                text=[TextOverlay.from_dict(t) for t in raw.get("text", [])],
                audio=[AudioTrack.from_dict(a) for a in raw.get("audio", [])],
                subtitle_asset_id=raw.get("subtitle_asset_id"),
                normalise_audio=bool(raw.get("normalise_audio", True)),
                version=version,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValidationError(f"malformed EDL: {exc}") from exc

    # --- validation ---
    def validate(self) -> None:
        """Refuse an edit that cannot be rendered, before anything is attempted.

        Rendering is the expensive step, so every knowable problem is caught
        here. Each message names the offending value, because an agent acting on
        a refusal needs to know which of forty clips is wrong.
        """
        if self.aspect not in ASPECT_PRESETS:
            raise ValidationError(
                f"unknown aspect '{self.aspect}'",
                parameter="aspect", supported=sorted(ASPECT_PRESETS),
            )
        if not self.clips:
            raise ValidationError("an EDL needs at least one clip to render", parameter="clips")

        for index, clip in enumerate(self.clips):
            where = f"clips[{index}]"
            if clip.start < 0:
                raise ValidationError(f"{where}.start cannot be negative", parameter=where)
            if clip.end is not None and clip.end <= clip.start:
                raise ValidationError(
                    f"{where}.end ({clip.end}) must be after start ({clip.start})",
                    parameter=where,
                )
            if not 0.1 <= clip.speed <= 10.0:
                raise ValidationError(
                    f"{where}.speed {clip.speed} is outside the supported 0.1-10x range",
                    parameter=where,
                )
            if clip.effect not in CLIP_EFFECTS:
                raise ValidationError(
                    f"{where}.effect '{clip.effect}' is not available",
                    parameter=where, supported=list(CLIP_EFFECTS),
                )
            if clip.transition_in not in TRANSITIONS:
                raise ValidationError(
                    f"{where}.transition_in '{clip.transition_in}' is not available",
                    parameter=where, supported=list(TRANSITIONS),
                )
            if index == 0 and clip.transition_in in TIMELINE_TRANSITIONS:
                raise ValidationError(
                    f"{where}.transition_in '{clip.transition_in}' has no previous "
                    "clip to transition from; only 'cut' or 'fade' are meaningful "
                    "on the first clip",
                    parameter=where,
                )
            if clip.transition_duration < 0:
                raise ValidationError(f"{where}.transition_duration cannot be negative",
                                      parameter=where)
            for name, (low, high) in GRADE_RANGES.items():
                value = getattr(clip, name)
                if not low <= value <= high:
                    raise ValidationError(
                        f"{where}.{name} {value:g} is outside the supported "
                        f"{low:g}-{high:g} range",
                        parameter=where,
                    )

        for index, overlay in enumerate(self.text):
            where = f"text[{index}]"
            if not overlay.text.strip():
                raise ValidationError(f"{where}.text is empty", parameter=where)
            if overlay.position not in ("top", "center", "bottom"):
                raise ValidationError(
                    f"{where}.position '{overlay.position}' is not one of top/center/bottom",
                    parameter=where,
                )
            if overlay.end is not None and overlay.end <= overlay.start:
                raise ValidationError(f"{where}.end must be after start", parameter=where)
            if overlay.size < 8 or overlay.size > 400:
                raise ValidationError(f"{where}.size {overlay.size} is outside 8-400",
                                      parameter=where)

        for index, track in enumerate(self.audio):
            where = f"audio[{index}]"
            if track.volume < 0:
                raise ValidationError(f"{where}.volume cannot be negative", parameter=where)
            if track.start < 0:
                raise ValidationError(f"{where}.start cannot be negative", parameter=where)

    def asset_ids(self) -> list[str]:
        """Every asset this edit depends on, video and audio alike.

        Used to check availability and rights BEFORE rendering — an edit that
        references media the retention policy deleted must fail early and say
        so, not part-way through a render.
        """
        seen: list[str] = []
        for item in [*self.clips, *self.audio]:
            if item.asset_id not in seen:
                seen.append(item.asset_id)
        if self.subtitle_asset_id and self.subtitle_asset_id not in seen:
            seen.append(self.subtitle_asset_id)
        return seen

    def resolution(self) -> tuple[int, int]:
        return ASPECT_PRESETS[self.aspect]

    def summary(self) -> dict[str, Any]:
        """What this edit is, in the terms a human reviews it in."""
        return {
            "aspect": self.aspect,
            "resolution": "x".join(str(n) for n in self.resolution()),
            "clips": len(self.clips),
            "text_overlays": len(self.text),
            "audio_tracks": len(self.audio),
            "has_subtitles": self.subtitle_asset_id is not None,
            "effects_used": sorted({c.effect for c in self.clips if c.effect != "none"}),
            "transitions_used": sorted({c.transition_in for c in self.clips
                                        if c.transition_in != "cut"}),
            "graded_clips": sum(1 for c in self.clips if c.is_graded()),
        }
