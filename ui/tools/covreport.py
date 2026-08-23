#!/usr/bin/env python3
"""Line coverage for ui/bettercnc, using only the standard library.

`coverage` is not installed and cannot be assumed on a LinuxCNC live USB, so
this measures coverage with sys.settrace and derives the set of executable lines
from each module's code objects via dis.findlinestarts().

    python3 ui/tools/covreport.py            # summary
    python3 ui/tools/covreport.py --missing  # list uncovered lines

Caveat worth knowing: "executable lines" here means lines that appear in a code
object's line table. That excludes lines never compiled into a code object at
all, and counts a multi-line statement once at its first line. It is close to
what coverage.py reports for a codebase like this, but it is not identical, and
this tool is not a substitute for one where precision matters.
"""

from __future__ import annotations

import argparse
import dis
import os
import sys
import types
from typing import Dict, Set, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
UI_DIR = os.path.dirname(HERE)
TESTS_DIR = os.path.join(UI_DIR, "tests")
REPO_ROOT = os.path.dirname(UI_DIR)
PKG_DIR = os.path.join(UI_DIR, "bettercnc")

for path in (UI_DIR, TESTS_DIR, os.path.join(REPO_ROOT, "scripts")):
    if path not in sys.path:
        sys.path.insert(0, path)

MODULES = [
    "bettercnc.capabilities",
    "bettercnc.iniload",
    "bettercnc.profiles",
    "bettercnc.state",
    "bettercnc.configmodel",
    "bettercnc.adapter",
    "bettercnc.viewmodel",
    "bettercnc.render",
    "bettercnc.app",
]


def executable_lines(path: str) -> Set[int]:
    """Lines that can be traced, from the compiled code objects."""
    with open(path, "r", encoding="utf-8") as fh:
        src = fh.read()
    code = compile(src, path, "exec")
    found: Set[int] = set()
    stack = [code]
    while stack:
        c = stack.pop()
        for _, line in dis.findlinestarts(c):
            if line:
                found.add(line)
        for const in c.co_consts:
            if isinstance(const, types.CodeType):
                stack.append(const)
    return found


def module_paths() -> Dict[str, str]:
    """Resolve module -> file WITHOUT importing.

    Importing here would run every module-level line before the tracer exists,
    which would then report all of it as uncovered. Deriving the path from the
    filesystem keeps the import inside the traced region.
    """
    out: Dict[str, str] = {}
    for name in MODULES:
        rel = name.split(".", 1)[1] + ".py"
        out[name] = os.path.abspath(os.path.join(PKG_DIR, rel))
    return out


def run_suite_traced(targets: Dict[str, str]) -> Dict[str, Set[int]]:
    """Run the suite under a tracer, returning executed lines per file.

    The tracer is installed BEFORE the test modules (and therefore bettercnc)
    are imported, so module-level definitions are counted.
    """
    hit: Dict[str, Set[int]] = {p: set() for p in targets.values()}
    watched = set(targets.values())

    def tracer(frame, event, arg):
        filename = frame.f_code.co_filename
        if filename in watched:
            if event == "line" or event == "call":
                hit[filename].add(frame.f_lineno)
            return tracer
        # Still need to descend into calls from untraced frames.
        return tracer

    import threading
    import unittest

    # sys.settrace only covers the calling thread. The HTTP route tests run the
    # server in its own thread, so without threading.settrace app.py would look
    # far less covered than it is.
    threading.settrace(tracer)
    sys.settrace(tracer)
    try:
        # Imported inside the traced region on purpose - see the docstring.
        import run_all  # noqa: WPS433  (tests dir is on sys.path)

        suite = run_all.build_suite()
        result = unittest.TextTestRunner(verbosity=0).run(suite)
    finally:
        sys.settrace(None)
        threading.settrace(None)

    if not result.wasSuccessful():
        sys.stderr.write(
            "\n!! tests FAILED - coverage below is from a failing run\n"
        )
    return hit


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="stdlib line coverage for bettercnc")
    ap.add_argument("--missing", action="store_true", help="list uncovered lines")
    ap.add_argument("--fail-under", type=float, default=0.0)
    args = ap.parse_args(argv)

    targets = module_paths()
    missing = [n for n, p in targets.items() if not os.path.exists(p)]
    if missing:
        sys.stderr.write("cannot find: {}\n".format(", ".join(missing)))
        return 2

    hit = run_suite_traced(targets)

    rows = []
    total_exec = 0
    total_hit = 0
    for name, path in targets.items():
        execu = executable_lines(path)
        covered = execu & hit[path]
        total_exec += len(execu)
        total_hit += len(covered)
        pct = 100.0 * len(covered) / len(execu) if execu else 100.0
        rows.append((name, len(execu), len(covered), pct, sorted(execu - covered)))

    width = max(len(r[0]) for r in rows)
    print()
    print("{:<{w}}  {:>6}  {:>7}  {:>7}".format("module", "lines", "covered", "pct", w=width))
    print("-" * (width + 26))
    for name, n, c, pct, missing in sorted(rows, key=lambda r: r[3]):
        print("{:<{w}}  {:>6}  {:>7}  {:>6.1f}%".format(name, n, c, pct, w=width))
        if args.missing and missing:
            print("      uncovered: {}".format(_ranges(missing)))
    overall = 100.0 * total_hit / total_exec if total_exec else 100.0
    print("-" * (width + 26))
    print("{:<{w}}  {:>6}  {:>7}  {:>6.1f}%".format("TOTAL", total_exec, total_hit, overall, w=width))
    print()

    if args.fail_under and overall < args.fail_under:
        sys.stderr.write(
            "coverage {:.1f}% is below --fail-under {:.1f}%\n".format(overall, args.fail_under)
        )
        return 1
    return 0


def _ranges(nums) -> str:
    """Compact 1,2,3,7 into 1-3,7."""
    if not nums:
        return ""
    out = []
    start = prev = nums[0]
    for n in nums[1:]:
        if n == prev + 1:
            prev = n
            continue
        out.append(str(start) if start == prev else "{}-{}".format(start, prev))
        start = prev = n
    out.append(str(start) if start == prev else "{}-{}".format(start, prev))
    return ",".join(out)


if __name__ == "__main__":
    sys.exit(main())
