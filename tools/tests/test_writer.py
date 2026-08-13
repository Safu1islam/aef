"""The emitter must round-trip through BOTH readers, and be deterministic.

AEF ships without PyYAML and falls back to a bundled reader, so "the file we
wrote is the file both readers see" is a correctness requirement rather than a
nicety. If the two disagree, a project's coordination state means one thing to
an agent with PyYAML installed and another to an agent without it.
"""

from __future__ import annotations

import os
import tempfile
import unittest

from aefkit import writer, yamlio

try:
    import yaml as pyyaml
except ImportError:  # pragma: no cover - exercised only where PyYAML is absent
    pyyaml = None


SAMPLE = {
    "sessions": [
        {
            "id": "s-1",
            "agent": "backend-agent",
            "main_engineer": True,
            "task": "T-1",
            "activity": "line with: a colon, a #hash and \"quotes\"",
            "status": "working",
            "handoff": {
                "outcome": "paused",
                "evidence": ["tasks.yaml T-1", "DR-006"],
                "next": "continue",
            },
        },
        {"id": "s-2", "agent": "frontend-agent", "main_engineer": False,
         "affected": [], "count": 3, "ratio": 0.5},
    ]
}


class RoundTrip(unittest.TestCase):
    def write(self, data) -> str:
        path = os.path.join(tempfile.mkdtemp(), "out.yaml")
        writer.dump(path, data, "a header\nsecond line")
        return path

    def test_the_bundled_reader_reads_back_exactly_what_was_written(self):
        path = self.write(SAMPLE)
        self.assertEqual(yamlio.load(path, force_bundled=True), SAMPLE)

    @unittest.skipIf(pyyaml is None, "PyYAML not installed")
    def test_pyyaml_reads_back_exactly_what_was_written(self):
        path = self.write(SAMPLE)
        with open(path, encoding="utf-8") as handle:
            self.assertEqual(pyyaml.safe_load(handle), SAMPLE)

    @unittest.skipIf(pyyaml is None, "PyYAML not installed")
    def test_both_readers_agree(self):
        path = self.write(SAMPLE)
        with open(path, encoding="utf-8") as handle:
            theirs = pyyaml.safe_load(handle)
        self.assertEqual(yamlio.load(path, force_bundled=True), theirs)

    def test_output_is_deterministic(self):
        """A heartbeat rewrites this file constantly. Reordered keys would make
        every diff unreadable and every commit noisy."""
        self.assertEqual(writer.dumps(SAMPLE), writer.dumps(SAMPLE))

    def test_strings_that_look_like_other_types_survive_as_strings(self):
        """`status: no` would come back as False. Everything is quoted so no
        value can change type by looking like one."""
        data = {"v": ["no", "yes", "true", "null", "12", "3.4", "~", "on"]}
        path = self.write(data)
        self.assertEqual(yamlio.load(path, force_bundled=True), data)

    def test_none_values_are_omitted_rather_than_written_as_null(self):
        path = self.write({"a": "x", "b": None})
        self.assertEqual(yamlio.load(path, force_bundled=True), {"a": "x"})

    def test_a_header_becomes_comments(self):
        path = self.write({"a": "x"})
        with open(path, encoding="utf-8") as handle:
            first = handle.readline()
        self.assertTrue(first.startswith("# "), first)


if __name__ == "__main__":
    unittest.main()
