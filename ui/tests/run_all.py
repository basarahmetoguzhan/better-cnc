#!/usr/bin/env python3
"""Run every test in the repository: ui/tests plus scripts/test_grbl2ini.py.

    python3 ui/tests/run_all.py            # everything
    python3 ui/tests/run_all.py -v         # verbose
    python3 ui/tests/run_all.py -k estop   # substring filter on test ids

unittest rather than pytest because the target machine is a LinuxCNC live USB
with no third-party Python packages, and `pip install` is not a step we want
between a bug and a diagnosis. Every TestCase here is pytest-compatible if a
machine happens to have it.
"""

from __future__ import annotations

import argparse
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
UI_DIR = os.path.dirname(HERE)
REPO_ROOT = os.path.dirname(UI_DIR)
SCRIPTS_DIR = os.path.join(REPO_ROOT, "scripts")

# ui/ for `import bettercnc`, ui/tests for `import helpers`, scripts/ for grbl2ini
for path in (UI_DIR, HERE, SCRIPTS_DIR):
    if path not in sys.path:
        sys.path.insert(0, path)


def build_suite(pattern: str = "test_*.py") -> unittest.TestSuite:
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    suite.addTests(loader.discover(HERE, pattern=pattern, top_level_dir=HERE))
    suite.addTests(
        loader.discover(SCRIPTS_DIR, pattern="test_*.py", top_level_dir=SCRIPTS_DIR)
    )
    return suite


def filter_suite(suite: unittest.TestSuite, needle: str) -> unittest.TestSuite:
    out = unittest.TestSuite()
    for test in iter_tests(suite):
        if needle.lower() in test.id().lower():
            out.addTest(test)
    return out


def iter_tests(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            for sub in iter_tests(item):
                yield sub
        else:
            yield item


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="run the whole better-cnc test suite")
    ap.add_argument("-v", "--verbose", action="count", default=0)
    ap.add_argument("-k", dest="needle", default="", help="filter on test id substring")
    ap.add_argument("-q", "--quiet", action="store_true")
    args = ap.parse_args(argv)

    suite = build_suite()
    if args.needle:
        suite = filter_suite(suite, args.needle)

    total = sum(1 for _ in iter_tests(suite))
    if not args.quiet:
        sys.stderr.write(
            "better-cnc test suite: {} tests from ui/tests + scripts/\n".format(total)
        )

    verbosity = 0 if args.quiet else (2 if args.verbose else 1)
    result = unittest.TextTestRunner(verbosity=verbosity).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
