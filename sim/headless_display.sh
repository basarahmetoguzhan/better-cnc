#!/bin/sh
# SIMULATOR ONLY: the [DISPLAY]DISPLAY program for sim/make_sim_ini.py's INI.
#
# LinuxCNC runs its DISPLAY program as `$EMCDISPLAY -ini <file> ...` and shuts
# down when it exits (reference/linuxcnc/scripts/linuxcnc.in:1003). There is no
# screen in the container, and the operator UI connects on its own, so this
# shows nothing and waits. Arguments are ignored.
exec sleep infinity
