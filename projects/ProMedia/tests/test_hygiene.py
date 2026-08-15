"""T-030 — the minor correctness and hygiene items (O2, O3, O4, N6).

Four findings the reviewer raised as non-blocking. They are grouped because
they share a shape: each is a value or a rule that exists in two places, where
one place is authoritative and the other is the one actually used.

* **O2/O3** — the ffprobe timeout and SQLite's busy_timeout were literals in
  the modules that used them, so configuring them did nothing.
* **O4** — ``config.load()`` handed out the module-level ``DEFAULTS`` dict
  itself, so every Config built without a promedia.toml shared one mutable
  object.
* **N6** — ``provenance.seal`` and ``rights.latest_verdict`` selected "the
  current verdict" with different ORDER BY clauses, so at equal timestamps the
  sealed record could disagree with the publish gate.

N6 is the one with teeth: F-8 makes the sealed record the durable account of a
rights position, and a record that disagrees with the gate is worse than no
record, because it is believed.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from promedia import config as config_module
from promedia.core import db, ingest, provenance, rights
from promedia.core.registry import invoke
from tests.conftest import attest, declaration_original

PACKAGE = Path(config_module.__file__).parent


# --- O4: load() must not hand out the module's own dict ------------------------


def test_load_returns_a_fresh_copy_of_the_defaults(monkeypatch, tmp_path):
    """Mutating one Config must not move the defaults for the whole process."""
    monkeypatch.delenv(config_module.ENV_CONFIG_PATH, raising=False)
    monkeypatch.chdir(tmp_path)  # no promedia.toml anywhere above: the no-file path

    first = config_module.load()
    assert first.source is None, "this test must exercise the no-file path"

    original = config_module.DEFAULTS["storage"]["ceiling_bytes"]
    first.values["storage"]["ceiling_bytes"] = 1

    assert config_module.DEFAULTS["storage"]["ceiling_bytes"] == original, (
        "load() handed out the module-level DEFAULTS; one Config moved the ceiling for all"
    )
    assert config_module.load().ceiling_bytes == original


def test_two_loads_do_not_share_one_mutable_dict(monkeypatch, tmp_path):
    monkeypatch.delenv(config_module.ENV_CONFIG_PATH, raising=False)
    monkeypatch.chdir(tmp_path)
    a, b = config_module.load(), config_module.load()
    a.values["storage"]["ceiling_bytes"] = 1
    assert b.values["storage"]["ceiling_bytes"] != 1
    assert a.values is not b.values


def test_defaults_helper_copies_every_section():
    fresh = config_module.defaults()
    assert fresh == config_module.DEFAULTS
    for section in fresh:
        assert fresh[section] is not config_module.DEFAULTS[section], (
            f"section '{section}' is shared by reference, so the copy is shallow"
        )


# --- R-007: a nested dict VALUE must not be shared across Configs --------------


def test_defaults_helper_also_copies_a_nested_dict_value():
    """media.estimated_bitrate_bytes_per_second (T-043) is the first config
    entry whose value is itself a dict. A one-level copy leaves it pointing at
    the exact same object as DEFAULTS; mutating it through one Config must not
    move it for every other Config built the same way."""
    fresh = config_module.defaults()
    table = fresh["media"]["estimated_bitrate_bytes_per_second"]
    assert table is not config_module.DEFAULTS["media"]["estimated_bitrate_bytes_per_second"], (
        "the nested dict is shared by reference, not copied"
    )

    original = dict(config_module.DEFAULTS["media"]["estimated_bitrate_bytes_per_second"])
    table["fast"] = -1
    assert config_module.DEFAULTS["media"]["estimated_bitrate_bytes_per_second"] == original, (
        "mutating one Config's nested dict moved the module-level DEFAULTS"
    )
    assert config_module.defaults()["media"]["estimated_bitrate_bytes_per_second"] == original


def test_two_loads_do_not_share_a_nested_dict_either(monkeypatch, tmp_path):
    """The two-Config version of the test above, on the no-file path — the
    same shape as test_two_loads_do_not_share_one_mutable_dict, one level
    deeper."""
    monkeypatch.delenv(config_module.ENV_CONFIG_PATH, raising=False)
    monkeypatch.chdir(tmp_path)
    a, b = config_module.load(), config_module.load()
    a.values["media"]["estimated_bitrate_bytes_per_second"]["fast"] = -1
    assert b.values["media"]["estimated_bitrate_bytes_per_second"]["fast"] != -1
    assert (
        a.values["media"]["estimated_bitrate_bytes_per_second"]
        is not b.values["media"]["estimated_bitrate_bytes_per_second"]
    )


def test_a_config_loaded_from_a_toml_file_does_not_share_the_nested_dict_either(tmp_path, monkeypatch):
    """_deep_merge's path, not defaults()'s: load() calls
    ``_deep_merge(DEFAULTS, ...)`` directly, so this is the branch the old
    ``{k: dict(v) ...}`` one-level copy in _deep_merge itself protected —
    incompletely, which is exactly what R-007 reported."""
    monkeypatch.delenv(config_module.ENV_CONFIG_PATH, raising=False)
    toml_path = tmp_path / "promedia.toml"
    toml_path.write_text('[storage]\nceiling_bytes = 12345\n', encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    cfg = config_module.load()
    assert cfg.source == toml_path  # exercising the file path, not the default path

    original = dict(config_module.DEFAULTS["media"]["estimated_bitrate_bytes_per_second"])
    cfg.values["media"]["estimated_bitrate_bytes_per_second"]["fast"] = -1
    assert config_module.DEFAULTS["media"]["estimated_bitrate_bytes_per_second"] == original, (
        "a Config built from a promedia.toml still shared DEFAULTS' nested dict"
    )
    assert config_module.load().values["media"]["estimated_bitrate_bytes_per_second"]["fast"] != -1


# --- O2/O3: the limits are configuration, not literals -------------------------


def test_the_probe_timeout_and_busy_timeout_are_configurable():
    assert config_module.DEFAULTS["ingest"]["probe_timeout_seconds"]
    assert config_module.DEFAULTS["database"]["busy_timeout_ms"]


@pytest.mark.parametrize(
    "literal, module",
    [
        (config_module.DEFAULTS["ingest"]["probe_timeout_seconds"], "core/ingest.py"),
        (config_module.DEFAULTS["database"]["busy_timeout_ms"], "core/db.py"),
    ],
)
def test_the_value_is_not_also_a_literal_in_the_module_that_uses_it(literal, module):
    """Extends T-001 AC-3's rule from the ceiling to these two.

    A second copy of a limit is how a configuration change silently fails to
    take effect — the operator edits the file, the code keeps its literal.

    Scans the AST rather than the file text, so a number appearing in a comment
    or docstring (this project explains its fixes in prose, and those
    explanations name the old value) is not mistaken for a live literal. The
    thing that can actually go wrong is a numeric constant in executable code.
    """
    import ast

    tree = ast.parse((PACKAGE / module).read_text(encoding="utf-8"))
    offenders = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, (int, float))
        and not isinstance(node.value, bool)
        and node.value == literal
    ]
    assert offenders == [], f"{module} still has {literal} as a code literal at line(s) {offenders}"


def test_configured_busy_timeout_reaches_the_connection(tmp_path):
    """Proven against the live PRAGMA, not by reading the call site."""
    conn = db.connect(tmp_path / "t.db", busy_timeout_ms=1234)
    try:
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 1234
    finally:
        conn.close()


def test_the_default_busy_timeout_comes_from_config(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    try:
        assert (
            conn.execute("PRAGMA busy_timeout").fetchone()[0]
            == config_module.DEFAULTS["database"]["busy_timeout_ms"]
        )
    finally:
        conn.close()


def test_ingest_passes_the_configured_probe_timeout(agent_ctx, media_file, monkeypatch):
    """The value must arrive at subprocess.run, which is the only place it acts."""
    seen: dict[str, object] = {}
    real_run = subprocess.run

    def capturing_run(*args, **kwargs):
        seen["timeout"] = kwargs.get("timeout")
        return real_run(*args, **kwargs)

    monkeypatch.setattr(ingest.subprocess, "run", capturing_run)
    agent_ctx.config.values["ingest"]["probe_timeout_seconds"] = 7
    invoke(
        agent_ctx,
        "ingest",
        {"source_path": str(media_file), "declaration": declaration_original()},
    )
    # ffprobe is absent here (A-15), so run() raises FileNotFoundError — but it
    # is called, and the timeout it was called WITH is what this pins.
    assert seen.get("timeout") == 7, f"ingest used {seen.get('timeout')}, not the configured 7"


# --- N6: one authority on which verdict is current -----------------------------


def _verdicts_at_the_same_instant(ctx, asset_id: str) -> None:
    """Force the tie the reviewer described: two verdicts, one decided_at.

    Windows' clock granularity makes this ordinary rather than exotic —
    determine-rights twice in one tick lands both rows on the same timestamp.

    The two rows are also given distinct ``matched_rule`` values, because the
    sealed payload copies verdict FIELDS rather than the verdict id: without a
    field that differs, both paths produce identical output and the test cannot
    tell which row either one chose.

    Ids are rewritten so that lexicographic id order MATCHES insertion order.
    That is what makes the falsification deterministic rather than a coin flip:
    the tiebroken query (``id DESC``) then picks the LAST-inserted row, while an
    untiebroken one takes SQLite's natural rowid order and picks the FIRST. The
    two disagree every run instead of agreeing roughly half the time — and a
    sabotage test that passes half the time proves nothing either way.
    """
    rows = ctx.conn.execute(
        "SELECT rowid, id, decided_at FROM rights_verdicts WHERE asset_id = ? ORDER BY rowid",
        (asset_id,),
    ).fetchall()
    assert len(rows) >= 2, "the fixture did not produce two verdicts to tie"
    shared = rows[0]["decided_at"]
    for position, row in enumerate(rows):
        ctx.conn.execute(
            "UPDATE rights_verdicts SET id = ?, decided_at = ?, matched_rule = ?"
            " WHERE rowid = ?",
            (f"rv_tie{position}", shared, f"tie-marker-{position}", row["rowid"]),
        )


def test_seal_and_the_gate_pick_the_same_verdict_at_equal_timestamps(
    agent_ctx, operator_ctx, media_file
):
    """N6, at the point where it would have mattered.

    The sealed record is what F-8 preserves after the media is gone. If it
    freezes a different verdict than the gate enforces, the durable account of
    the decision is wrong — and nothing downstream can tell.
    """
    result = invoke(
        agent_ctx,
        "ingest",
        {"source_path": str(media_file), "declaration": declaration_original()},
    )
    asset_id = result["asset_id"]
    attest(agent_ctx, asset_id)
    invoke(operator_ctx, "determine-rights", {"asset_id": asset_id})  # a second verdict
    _verdicts_at_the_same_instant(agent_ctx, asset_id)

    gate = rights.latest_verdict(agent_ctx, asset_id)
    sealed = invoke(operator_ctx, "seal-provenance", {"asset_id": asset_id})
    record = provenance.read(agent_ctx.conn, sealed["provenance_id"])

    assert gate is not None
    embedded = record["payload"]["verdict"]["matched_rule"]
    assert embedded == gate["matched_rule"], (
        f"the sealed record froze '{embedded}' while the gate enforces "
        f"'{gate['matched_rule']}' — the record and the gate disagree"
    )


def test_seal_does_not_run_its_own_verdict_query():
    """The structural half: one authority, not two kept in step.

    A tiebreaker repeated in every caller is one that will eventually not be,
    which is exactly how this defect arose.
    """
    text = (PACKAGE / "core" / "provenance.py").read_text(encoding="utf-8")
    assert "FROM rights_verdicts" not in text, (
        "provenance.py selects verdicts itself again; it must go through rights.latest_verdict"
    )
    assert "rights.latest_verdict" in text
