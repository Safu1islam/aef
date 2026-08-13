#!/usr/bin/env python3
"""Run the AEF tooling tests. Stdlib only, no pytest, no install.

    python aef/tools/run_tests.py [-v]

Exit code is 0 only if every test passed, so this is usable as a gate.
"""

from __future__ import annotations

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


def main() -> int:
    verbosity = 2 if "-v" in sys.argv or "--verbose" in sys.argv else 1
    suite = unittest.defaultTestLoader.discover(
        start_dir=os.path.join(HERE, "tests"), top_level_dir=HERE
    )
    result = unittest.TextTestRunner(verbosity=verbosity).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
