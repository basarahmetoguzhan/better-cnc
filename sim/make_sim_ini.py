#!/usr/bin/env python3
"""Derive the simulator's INI from the rig's, so the two cannot drift apart.

    python3 sim/make_sim_ini.py OUT.ini

Copies configs/test-rig/test-rig.ini line for line and changes exactly three
keys, then appends a [SIM] section (scripts/ini_derive.py does the copying):

    [HAL]HALFILE             test-rig.hal     ->  sim/test-rig-sim.hal (absolute)
    [DISPLAY]DISPLAY         axis             ->  sim/headless_display.sh (absolute)
    [DISPLAY]PROGRAM_PREFIX  ../../nc_files/  ->  <repo>/nc_files (absolute)
    [SIM]LINK_STUB           sim/hm2_link_stub.py (absolute), for the sim HAL file

Every joint, axis, homing and timing value is therefore the rig's own. If the
rig's INI changes shape so that any of the three keys is not found exactly
once, this refuses rather than write a half-converted file.

Relative paths left in the INI (PARAMETER_FILE, TOOL_TABLE) resolve against
OUT's directory, as LinuxCNC resolves them against the INI's: it starts in
the INI's directory (reference/linuxcnc/scripts/linuxcnc.in:777, `cd
"$INI_DIR"`). That is why PROGRAM_PREFIX is made absolute: OUT is
/tmp/better-cnc-sim (sim/start.sh, sim/kiosk.sh), where the rig's
../../nc_files/ would name /nc_files, which does not exist, while on the rig
it names the repo's nc_files/ - the same directory this points at.
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
    ("DISPLAY", "PROGRAM_PREFIX"): os.path.join(REPO, "nc_files"),
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
