"""The bundled reader must be a drop-in for PyYAML on the subset AEF uses.

The important test here is test_both_readers_agree_on_every_state_file. It is a
differential test against a real parser over the project's real files, which is
the only kind of evidence that means anything for a hand-written parser: unit
tests over constructs I thought of cannot find the constructs I did not.
"""

from __future__ import annotations

import glob
import os
import unittest

from aefkit.paths import framework_root

from aefkit import yamlio

try:
    import yaml as pyyaml
except ImportError:  # pragma: no cover
    pyyaml = None

# Resolve BOTH layouts. AEF is normally vendored at <project>/aef, but it is
# also a repository in its own right, and its suite must pass in both — it did
# not, which was found by running it from a fresh clone before publishing.
_HERE = os.path.dirname(os.path.abspath(__file__))
FRAMEWORK = os.path.abspath(os.path.join(_HERE, "..", ".."))
_PARENT = os.path.abspath(os.path.join(FRAMEWORK, ".."))
# Vendored iff the parent actually contains aef/config; otherwise the framework
# root IS the project root, which is what a standalone checkout looks like.
ROOT = _PARENT if os.path.isdir(os.path.join(_PARENT, "aef", "config")) else FRAMEWORK


def state_files() -> list[str]:
    patterns = [
        ".ai/state/*.yaml", ".ai/state/decisions/*.yaml", ".ai/config/*.yaml",
    ]
    found: list[str] = []
    for pattern in patterns:
        found.extend(sorted(glob.glob(os.path.join(ROOT, pattern))))
    # Framework config and schemas, wherever the framework actually is.
    for pattern in ("config/*.yaml", "schemas/*.yaml"):
        found.extend(sorted(glob.glob(os.path.join(framework_root(ROOT), pattern))))
    return found


class BothReadersAgree(unittest.TestCase):
    @unittest.skipIf(pyyaml is None, "PyYAML not installed; nothing to compare against")
    def test_both_readers_agree_on_every_state_file(self):
        files = state_files()
        if not files:
            # A standalone framework checkout has no project state at all. That
            # is a legitimate layout, not a failure — skipping is honest where
            # asserting would make the framework's own repository red.
            self.skipTest("no state or config files here; standalone framework checkout")
        self.assertGreater(len(files), 4, "expected to find config and schema files")
        for path in files:
            with self.subTest(path=os.path.relpath(path, ROOT)):
                with open(path, encoding="utf-8") as handle:
                    text = handle.read()
                self.assertEqual(
                    yamlio.loads(text, name=path, force_bundled=True),
                    pyyaml.safe_load(text),
                    "bundled reader disagrees with PyYAML",
                )

    @unittest.skipIf(pyyaml is None, "PyYAML not installed")
    def test_the_forced_flag_actually_bypasses_pyyaml(self):
        """Guards against the mistake that hid a broken parser once already:
        comparing PyYAML against itself and calling the fallback verified."""
        self.assertTrue(yamlio.USING_PYYAML)
        # An anchor is valid YAML that PyYAML accepts and the bundled reader
        # refuses. If force_bundled were ignored, this would not raise.
        text = "base: &a 1\nother: *a\n"
        self.assertEqual(pyyaml.safe_load(text)["other"], 1)
        with self.assertRaises(yamlio.YamlSubsetError):
            yamlio.loads(text, force_bundled=True)


class Subset(unittest.TestCase):
    def parse(self, text: str):
        return yamlio.loads(text, force_bundled=True)

    def test_block_scalar_after_a_dash(self):
        self.assertEqual(self.parse("items:\n  - >\n      one two\n      three\n"),
                         {"items": ["one two three\n"]})

    def test_folded_keeps_breaks_around_more_indented_lines(self):
        text = "note: >\n  intro:\n\n      code line\n\n  outro\n"
        self.assertEqual(self.parse(text)["note"], "intro:\n\n    code line\n\noutro\n")

    def test_literal_block_preserves_newlines_and_hashes(self):
        text = "note: |\n  a # not a comment\n  b\n"
        self.assertEqual(self.parse(text)["note"], "a # not a comment\nb\n")

    def test_chomping_indicators(self):
        self.assertEqual(self.parse("a: >-\n  x\n")["a"], "x")
        self.assertEqual(self.parse("a: >\n  x\n")["a"], "x\n")

    def test_multiline_quoted_scalar(self):
        text = 'title: "one two\n  three four"\n'
        self.assertEqual(self.parse(text), {"title": "one two three four"})

    def test_multiline_plain_scalar(self):
        text = "meta:\n  slice: v1 slice covering\n    ingest and publish\n  mode: critical\n"
        self.assertEqual(self.parse(text),
                         {"meta": {"slice": "v1 slice covering ingest and publish", "mode": "critical"}})

    def test_quoted_sequence_item_containing_a_colon_is_a_string(self):
        text = 'checks:\n  - "Scope: only one file changed — PASSED"\n'
        self.assertEqual(self.parse(text), {"checks": ["Scope: only one file changed — PASSED"]})

    def test_url_in_a_sequence_is_a_string_not_a_mapping(self):
        self.assertEqual(self.parse("refs:\n  - https://example.com/x\n"),
                         {"refs": ["https://example.com/x"]})

    def test_trailing_comments_are_stripped_but_quoted_hashes_survive(self):
        text = 'a: 25          # per task\nb: "keep # this"\nc: [1, 2]  # note\n'
        self.assertEqual(self.parse(text), {"a": 25, "b": "keep # this", "c": [1, 2]})

    def test_dates_resolve_to_date_objects(self):
        import datetime
        self.assertEqual(self.parse("planned_at: 2026-08-12\n"),
                         {"planned_at": datetime.date(2026, 8, 12)})
        self.assertEqual(self.parse('planned_at: "2026-08-12"\n'), {"planned_at": "2026-08-12"})

    def test_yaml_11_boolean_keys_match_pyyaml_however_surprising(self):
        """`on:` is the boolean True in YAML 1.1, not the string "on" — and
        PyYAML agrees. Pinned because a divergence here would surface as a
        dashboard bug on machines without PyYAML and nowhere else."""
        text = "on: 1\noff: 2\n"
        self.assertEqual(self.parse(text), {True: 1, False: 2})
        if pyyaml is not None:
            self.assertEqual(self.parse(text), pyyaml.safe_load(text))
        self.assertEqual(self.parse('"on": 1\n'), {"on": 1}, "quoting keeps it a string")

    def test_integer_keys_resolve(self):
        self.assertEqual(self.parse("codes:\n  0: ok\n  3: forbidden\n"),
                         {"codes": {0: "ok", 3: "forbidden"}})

    def test_nested_sequence_of_mappings(self):
        text = "tasks:\n  - id: T-1\n    deps: [T-0]\n  - id: T-2\n    deps: []\n"
        self.assertEqual(self.parse(text),
                         {"tasks": [{"id": "T-1", "deps": ["T-0"]}, {"id": "T-2", "deps": []}]})

    def test_null_forms(self):
        self.assertEqual(self.parse("a:\nb: null\nc: ~\n"), {"a": None, "b": None, "c": None})


class RefusesRatherThanGuesses(unittest.TestCase):
    def test_anchors_and_aliases_are_refused(self):
        with self.assertRaises(yamlio.YamlSubsetError):
            yamlio.loads("a: &x 1\nb: *x\n", force_bundled=True)

    def test_tags_are_refused(self):
        with self.assertRaises(yamlio.YamlSubsetError):
            yamlio.loads("a: !!binary abc\n", force_bundled=True)

    def test_the_error_names_the_file_and_line(self):
        with self.assertRaises(yamlio.YamlSubsetError) as caught:
            yamlio.loads("ok: 1\nbad: &anchor 2\n", name="plan.yaml", force_bundled=True)
        message = str(caught.exception)
        self.assertIn("plan.yaml:2", message)
        self.assertIn("anchors", message)


if __name__ == "__main__":
    unittest.main()
