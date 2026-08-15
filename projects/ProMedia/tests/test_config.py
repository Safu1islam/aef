"""T-001 — configuration is external and not duplicated in code."""

from __future__ import annotations

import re
from pathlib import Path

from promedia import config as config_module

PACKAGE = Path(__file__).resolve().parents[1] / "promedia"


def test_defaults_without_file(tmp_path, monkeypatch):
    """AC-1: absent promedia.toml is not an error."""
    monkeypatch.delenv(config_module.ENV_CONFIG_PATH, raising=False)
    monkeypatch.chdir(tmp_path)  # no promedia.toml anywhere above
    cfg = config_module.load(start=tmp_path)
    assert cfg.source is None
    assert cfg.ceiling_bytes == config_module.DEFAULTS["storage"]["ceiling_bytes"]
    assert cfg.derivative_multiplier == 0.5


def test_file_overrides_default(tmp_path, monkeypatch):
    """AC-2: overrides apply at runtime, from the file, not from a rebuild."""
    cfg_file = tmp_path / "promedia.toml"
    cfg_file.write_text(
        "[storage]\nceiling_bytes = 1000\nderivative_multiplier = 2.0\n", encoding="utf-8"
    )
    monkeypatch.setenv(config_module.ENV_CONFIG_PATH, str(cfg_file))
    cfg = config_module.load()
    assert cfg.ceiling_bytes == 1000
    assert cfg.derivative_multiplier == 2.0
    # Unspecified keys still fall back to defaults (deep merge, not replace).
    assert cfg.get("publishing", "tolerance_seconds") == 300


def test_thresholds_derive_from_ceiling(tmp_path, monkeypatch):
    cfg_file = tmp_path / "promedia.toml"
    cfg_file.write_text("[storage]\nceiling_bytes = 1000\n", encoding="utf-8")
    monkeypatch.setenv(config_module.ENV_CONFIG_PATH, str(cfg_file))
    cfg = config_module.load()
    assert cfg.warn_bytes == 700
    assert cfg.refuse_bytes == 850


def test_no_hardcoded_thresholds():
    """AC-3: the ceiling literal appears only in config.py.

    Protocol 05 forbids hardcoded limits. A second copy of this number is how a
    configuration change silently fails to take effect somewhere.
    """
    ceiling = str(config_module.DEFAULTS["storage"]["ceiling_bytes"])
    offenders = []
    for path in PACKAGE.rglob("*.py"):
        if path.name == "config.py":
            continue
        text = path.read_text(encoding="utf-8")
        if re.search(rf"\b{ceiling}\b", text):
            offenders.append(str(path.relative_to(PACKAGE)))
    assert offenders == [], f"ceiling literal duplicated in: {offenders}"


def test_config_with_utf8_bom_loads(tmp_path, monkeypatch):
    """Regression: Notepad on Windows writes UTF-8 with a BOM.

    Found by hand-verification, not by the suite — the suite wrote its fixtures
    from Python, which never emits a BOM. Before the fix the app refused to
    start with "Invalid statement (at line 1, column 1)".
    """
    cfg_file = tmp_path / "promedia.toml"
    cfg_file.write_bytes(b"\xef\xbb\xbf[storage]\nceiling_bytes = 4242\n")
    monkeypatch.setenv(config_module.ENV_CONFIG_PATH, str(cfg_file))
    cfg = config_module.load()
    assert cfg.ceiling_bytes == 4242


def test_invalid_toml_names_the_file(tmp_path, monkeypatch):
    from promedia.errors import ConfigurationError

    cfg_file = tmp_path / "promedia.toml"
    cfg_file.write_text("this is not toml = = =", encoding="utf-8")
    monkeypatch.setenv(config_module.ENV_CONFIG_PATH, str(cfg_file))
    with __import__("pytest").raises(ConfigurationError) as excinfo:
        config_module.load()
    assert excinfo.value.detail["path"] == str(cfg_file)


def test_unknown_key_raises():
    from promedia.errors import ConfigurationError

    cfg = config_module.load()
    try:
        cfg.get("storage", "no_such_key")
    except ConfigurationError as exc:
        assert exc.detail["key"] == "no_such_key"
    else:  # pragma: no cover
        raise AssertionError("expected ConfigurationError")
