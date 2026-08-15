"""Runtime configuration.

Single source of every threshold, limit and switch in the system. Protocol 05
forbids hardcoding these anywhere else: modules read them from here, and the
values come from ``promedia.toml`` at runtime rather than from literals baked
in at import time.

Import cost matters — this module is on the CLI cold-start path (C-4), so it
uses only ``tomllib`` and ``pathlib`` from the standard library.
"""

from __future__ import annotations

import copy
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .errors import ConfigurationError

CONFIG_FILENAME = "promedia.toml"
ENV_CONFIG_PATH = "PROMEDIA_CONFIG"
ENV_DATA_DIR = "PROMEDIA_DATA_DIR"

# The only place these numbers appear in the package. Overridden by promedia.toml.
DEFAULTS: dict[str, dict[str, Any]] = {
    "storage": {
        "ceiling_bytes": 107374182400,  # 100 GB — C-13/F-7
        "warn_fraction": 0.70,
        "refuse_fraction": 0.85,
        "derivative_multiplier": 0.5,  # A-3
        "reservation_ttl_seconds": 3600,
    },
    "rights": {
        "ruleset": "conservative",
        "ruleset_version": "1.0.0",
        "jurisdiction": "neutral",
    },
    "publishing": {
        "tolerance_seconds": 300,  # C-26
        "allow_simulation": False,  # DR-010 / F-001
        # T-019. Neither platform's own docs state a call timeout; this is an
        # operational budget of ours, not a platform limit, so it belongs here
        # rather than as a literal in the adapters.
        "request_timeout_seconds": 15,
        # LinkedIn requires a "Linkedin-Version: YYYYMM" header on every
        # request and revises it on its own schedule (Microsoft Learn lists
        # monthly monikers). 202607 is the defaultMoniker verified live against
        # https://learn.microsoft.com/en-us/linkedin/marketing/community-management/shares/posts-api
        # on 2026-08-13 (project.md O-3: not from model memory). An operator
        # bumps this as LinkedIn revises it, without touching code.
        "linkedin_api_version": "202607",
    },
    "locks": {"ttl_minutes": 90},
    # Media production (T-042). Protocol 05: a render budget or a default
    # quality baked into a literal is one an operator cannot change without a
    # code edit, and render time is exactly the thing they will want to trade
    # against quality on this hardware.
    "media": {
        "render_timeout_seconds": 1800,   # 30 min; a wedged ffmpeg holds a lock
        "default_quality": "balanced",
        "font_path": "",                  # empty = discover a platform default
        # T-043. A render has no source file to read a size from before it
        # exists, so admission control projects duration x bitrate, the
        # standard proxy for an unencoded video's size. 'balanced' and
        # 'hardware' are anchored to a real measurement (T-041 AC-2: 60s at
        # 1280x720 rendered 10.4 MB on 'balanced', 5.4 MB on 'hardware');
        # 'fast' and 'quality' were never separately measured and are
        # extrapolated from the CRF each preset uses. storage.commit()
        # always reconciles the reservation to the ACTUAL output size once
        # ffmpeg is done, so an estimate here is a gate, never a permanent
        # figure.
        "estimated_bitrate_bytes_per_second": {
            "fast": 150000,
            "balanced": 175000,
            "quality": 260000,
            "hardware": 95000,
        },
        # Headroom on top of the bitrate estimate above. Resolution and
        # source content complexity both move the real encoded size and
        # neither is modelled by the table above, so this is what keeps the
        # estimate a safe upper bound for admission rather than a precise
        # prediction of a number that cannot be known before encoding.
        "render_size_safety_margin": 1.25,
        # Used only when a clip runs to the end of its source (no explicit
        # `end` in the EDL) and that source's duration was never probed
        # (A-15 residue: ffprobe absent/failed at ingest). One generous
        # per-clip guess so a bookkeeping gap refuses nothing by itself; the
        # render result reports when this fired rather than presenting the
        # estimate as measured.
        "unknown_clip_duration_seconds": 60.0,
    },
    # C-31 spend ledger (T-048). $100/month ceiling, hard stop at 150%
    # ($150), $5 per-operation cap without explicit approval. Enforced by
    # promedia/core/providers/spend.py, which records and refuses only —
    # nothing in this codebase spends money.
    "spend": {
        "monthly_ceiling_usd": 100.0,
        "hard_stop_fraction": 1.5,
        "per_operation_cap_usd": 5.0,
    },
    "web": {"host": "127.0.0.1", "port": 8765},
    # T-030 (O2, O3). Both were literals in the modules that used them, which
    # protocol 05 forbids for the same reason as any other limit: the value a
    # reader finds in configuration was not the value the code used.
    "database": {"busy_timeout_ms": 5000},
    "ingest": {"probe_timeout_seconds": 30},
    # T-046. Acquisition (import from a URL via yt-dlp) cannot know a remote
    # file's real size before downloading it, so the pre-download storage
    # reservation (F-7) falls back to this when the source reports no
    # filesize/filesize_approx at all. 1.5 GB, matching project.md C-12's
    # "typical master file size" assumption — an operator can raise or lower
    # it without a code change as that assumption is revisited.
    "acquire": {
        "default_estimate_bytes": 1610612736,
        "format": "best",
        "socket_timeout_seconds": 30,
    },
    # T-047. Silence/scene detection thresholds and the rough-cut boundary
    # parameters — protocol 05 forbids hardcoding a threshold an operator will
    # obviously want to tune per source (a noisy microphone needs a different
    # noise floor than a clean one). transcription_model_size is the default
    # faster-whisper picks when an operation does not name one explicitly.
    "analysis": {
        "silence_noise_threshold_db": -30.0,
        "silence_min_duration_seconds": 0.5,
        "rough_cut_min_clip_seconds": 1.0,
        # Shrinks each excluded silent span by this much on each side, so a
        # rough cut leaves a small buffer rather than trimming flush against
        # detected speech (the more common way an automatic cut clips a word).
        "rough_cut_padding_seconds": 0.15,
        "scene_change_threshold": 0.4,
        "transcription_model_size": "base",
        "analysis_timeout_seconds": 120,
    },
}


def defaults() -> dict[str, dict[str, Any]]:
    """A fresh, fully independent copy of DEFAULTS.

    T-030 (O4). ``load()`` used to hand out the module-level dict itself on the
    no-file path, so every Config built without a promedia.toml SHARED one
    mutable object with the module and with each other. Config is frozen, but
    ``values`` is a plain nested dict and freezing does not reach into it: one
    ``cfg.values["storage"]["ceiling_bytes"] = 1`` would have moved the ceiling
    for every subsequent load in the process, including the test suite's.

    R-007: a one-level ``{section: dict(keys) ...}`` copy only protects VALUES
    that are themselves scalars. ``media.estimated_bitrate_bytes_per_second``
    (T-043) is a dict-valued config entry — the first one — and a shallow copy
    still shares that inner dict object with ``DEFAULTS`` and with every other
    Config built the same way. ``copy.deepcopy`` closes that at every nesting
    depth, present and future, rather than patching one known level.
    """
    return copy.deepcopy(DEFAULTS)


@dataclass(frozen=True)
class Config:
    """Resolved configuration. Immutable once built."""

    values: dict[str, dict[str, Any]]
    data_dir: Path
    source: Path | None = None
    _cache: dict[str, Any] = field(default_factory=dict, compare=False, repr=False)

    def get(self, section: str, key: str) -> Any:
        try:
            return self.values[section][key]
        except KeyError as exc:
            raise ConfigurationError(
                f"unknown configuration key {section}.{key}", section=section, key=key
            ) from exc

    # Derived values, so callers never recompute thresholds from the ceiling.
    @property
    def ceiling_bytes(self) -> int:
        return int(self.get("storage", "ceiling_bytes"))

    @property
    def warn_bytes(self) -> int:
        return int(self.ceiling_bytes * float(self.get("storage", "warn_fraction")))

    @property
    def refuse_bytes(self) -> int:
        return int(self.ceiling_bytes * float(self.get("storage", "refuse_fraction")))

    @property
    def derivative_multiplier(self) -> float:
        return float(self.get("storage", "derivative_multiplier"))

    @property
    def db_path(self) -> Path:
        return self.data_dir / "promedia.db"

    @property
    def object_root(self) -> Path:
        return self.data_dir / "media" / "objects"


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Project keys replace framework keys at the same path (same rule as AEF overrides).

    R-007: ``base`` is ``load()``'s module-level ``DEFAULTS`` itself, not a
    copy — ``defaults()`` is only called on the no-file path. The old
    one-level ``dict(v)`` copy of each section left any dict-VALUED key inside
    a section (``media.estimated_bitrate_bytes_per_second``) pointing at the
    exact same inner dict as ``DEFAULTS``, so a mutation of that nested dict
    on a Config built from a promedia.toml would have leaked into every other
    Config in the process, file or no file. ``copy.deepcopy`` copies every
    level, matching what ``defaults()`` now does.
    """
    out = copy.deepcopy(base)
    for section, values in override.items():
        if isinstance(values, dict) and isinstance(out.get(section), dict):
            out[section].update(values)
        else:
            out[section] = values
    return out


def find_config_file(start: Path | None = None) -> Path | None:
    explicit = os.environ.get(ENV_CONFIG_PATH)
    if explicit:
        p = Path(explicit)
        if not p.is_file():
            raise ConfigurationError(f"{ENV_CONFIG_PATH} points at a missing file", path=str(p))
        return p
    here = (start or Path.cwd()).resolve()
    for candidate in [here, *here.parents]:
        f = candidate / CONFIG_FILENAME
        if f.is_file():
            return f
    return None


def default_data_dir() -> Path:
    """Application data lives outside the repository tree by default.

    Credentials never live here at all (DR-008) — see promedia.core.credentials.
    """
    override = os.environ.get(ENV_DATA_DIR)
    if override:
        return Path(override)
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~/.local/share")
    return Path(base) / "ProMedia"


def load(start: Path | None = None) -> Config:
    """Load configuration. Absent file is not an error — defaults apply."""
    path = find_config_file(start)
    values = defaults()
    if path is not None:
        # Decoded here rather than handed to tomllib.load() because Notepad —
        # the default editor on the operator's platform — writes UTF-8 with a
        # BOM, and tomllib rejects it with an opaque "Invalid statement at line
        # 1". Refusing to start because of an invisible byte is not acceptable
        # behaviour for a config file a human is expected to edit.
        try:
            text = path.read_text(encoding="utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ConfigurationError(
                f"{path} is not valid UTF-8: {exc}", path=str(path)
            ) from exc
        try:
            values = _deep_merge(DEFAULTS, tomllib.loads(text))
        except tomllib.TOMLDecodeError as exc:
            raise ConfigurationError(f"{path} is not valid TOML: {exc}", path=str(path)) from exc
    return Config(values=values, data_dir=default_data_dir(), source=path)
