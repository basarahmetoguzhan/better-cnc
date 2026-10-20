#!/usr/bin/env python3
"""Derive the simulator's INI from the rig's, so the two cannot drift apart.

    python3 sim/make_sim_ini.py OUT.ini

Copies configs/test-rig/test-rig.ini line for line and changes exactly two
keys, then appends a [SIM] section (scripts/ini_derive.py does the copying):

    [HAL]HALFILE      test-rig.hal  ->  sim/test-rig-sim.hal (absolute)
    [DISPLAY]DISPLAY  axis          ->  sim/headless_display.sh (absolute)
    [SIM]LINK_STUB    sim/hm2_link_stub.py (absolute), for the sim HAL file

Every joint, axis, homing and timing value is therefore the rig's own. If the
rig's INI changes shape so that either key is not found exactly once, this
refuses rather than write a half-converted file.

Relative paths left in the INI (PARAMETER_FILE, TOOL_TABLE, PROGRAM_PREFIX)
resolve against OUT's directory, as LinuxCNC resolves them against the INI's.
"""

import os
import sys

SIM_DIR = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(SIM_DIR)
RIG_INI = os.path.join(REPO, "configs", "test-rig", "test-rig.ini")

sys.path.insert(0, os.path.join(REPO, "scripts"))
from ini_derive import derive  # noqa: E402

REPLACE = {
    ("HAL", "HALFILE"): os.path.join(SIM_DIR, "test-rig-sim.hal"),
    ("DISPLAY", "DISPLAY"): os.path.join(SIM_DIR, "headless_display.sh"),
}


def convert(text: str) -> str:
    return derive(
        text,
        REPLACE,
        RIG_INI,
        append=(
            "\n# Added by sim/make_sim_ini.py - read by sim/test-rig-sim.hal.\n"
            "[SIM]\n"
            "LINK_STUB = {}\n".format(os.path.join(SIM_DIR, "hm2_link_stub.py"))
        ),
    )


def main(argv) -> int:
    if len(argv) != 2:
        sys.stderr.write("usage: make_sim_ini.py OUT.ini\n")
        return 2
    with open(RIG_INI, "r", encoding="utf-8") as fh:
        text = fh.read()
    with open(argv[1], "w", encoding="utf-8") as fh:
        fh.write(convert(text))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
