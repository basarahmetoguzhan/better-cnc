#!/bin/bash
# SIMULATOR ONLY: deploy/'s kiosk in the container, as the machine's
# bettercnc-kiosk.service runs it, but headless (wlroots' headless backend,
# software rendering) and on the simulator's INI. `sim/sim.sh kiosk` starts
# it; `sim/sim.sh shot FILE` takes a screenshot of what the operator would see.
set -euo pipefail

SIM_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO="$(dirname "$SIM_DIR")"
RUN=/tmp/better-cnc-sim
mkdir -p "$RUN"
python3 "$SIM_DIR/make_sim_ini.py" "$RUN/test-rig-sim.ini"

# What pam_systemd gives the service on the machine.
export XDG_RUNTIME_DIR=/tmp/xdg
mkdir -p "$XDG_RUNTIME_DIR"
chmod 0700 "$XDG_RUNTIME_DIR"
export WLR_BACKENDS=headless WLR_LIBINPUT_NO_DEVICES=1 WLR_RENDERER=pixman
export BETTERCNC_INI="${BETTERCNC_INI:-$RUN/test-rig-sim.ini}" BETTERCNC_PROFILE=test-rig
# The machine's kiosk keyboard, as the service sets it (no keyboard is
# attached here; deploy/'s keymap is checked by test_deploy.TestKioskKeymap).
export XKB_CONFIG_EXTRA_PATH="$REPO/deploy/files/xkb" XKB_DEFAULT_LAYOUT=tr XKB_DEFAULT_OPTIONS=bettercnc:kiosk
# Docker gives Chromium no user namespaces for its sandbox. Never on the machine.
export BETTERCNC_BROWSER_FLAGS="--no-sandbox"
exec cage -d -- "$REPO/deploy/kiosk-session"
