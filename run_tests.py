#!/usr/bin/env python3
"""Run every test file, not just test_webapp.

    python3 run_tests.py

The suite lived in a single `test_webapp.py` for most of this project's life,
and CLAUDE.md/README still tell you to run `python3 -m unittest test_webapp`.
On 2026-09-16 a review added `test_battery_safety.py` and
`test_review_regressions.py`, so that command silently stopped covering 21
tests -- including the ones guarding the battery hand-back. A test that is not
run is not a test, and the failure is silent, which is the worst kind.

Warnings are errors here: the suite has been kept free of ResourceWarnings
(leaked file handles) deliberately, and a regression in that is a real bug on
a machine that loses power without warning.
"""

from __future__ import annotations

import sys
import unittest


def main() -> int:
    loader = unittest.TestLoader()
    suite = loader.discover(".", pattern="test_*.py")
    if loader.errors:
        for err in loader.errors:
            print(err, file=sys.stderr)
        return 2
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.argv[1:] = ["-W", "error::ResourceWarning"] and sys.argv[1:]
    raise SystemExit(main())
