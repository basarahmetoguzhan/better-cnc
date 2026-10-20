#!/bin/bash
# SIMULATOR ONLY: the container's entry point (sim/Dockerfile CMD).
#
# 1. derive the sim INI from the rig's (sim/make_sim_ini.py)
# 2. start LinuxCNC on it in the background, log to $RUN/linuxcnc.log
# 3. wait until LinuxCNC's status channel answers
# 4. run the operator UI in the foreground on LinuxCncAdapter
#
# When LinuxCNC shuts down, `halcmd unload all` sends the UI SIGTERM (the UI
# holds a HAL component, docs/11 "Reading the HAL pins needs a HAL
# component"), the UI exits, and so does the container.
set -euo pipefail

SIM_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO="$(dirname "$SIM_DIR")"
RUN="${RUN:-/tmp/better-cnc-sim}"
PORT="${PORT:-8080}"

mkdir -p "$RUN"
python3 "$SIM_DIR/make_sim_ini.py" "$RUN/test-rig-sim.ini"

echo "sim: LinuxCNC $(dpkg-query -W -f='${Version}' linuxcnc-uspace) başlatılıyor ($RUN/linuxcnc.log)"
linuxcnc "$RUN/test-rig-sim.ini" > "$RUN/linuxcnc.log" 2>&1 &
LCNC_PID=$!

# Each attempt is a fresh process: the first poll() in a process that finds
# no LinuxCNC tool mmap spoils every later poll() in that process
# (SystemError, emcmodule.cc:286-305; adapter.POLL_SPOILED).
for i in $(seq 1 60); do
  if ! kill -0 "$LCNC_PID" 2>/dev/null; then
    echo "sim: LinuxCNC açılırken durdu - sim/sim.sh log, ve konteynerde ~/linuxcnc_debug.txt" >&2
    exit 1
  fi
  if timeout 5 python3 -c 'import linuxcnc; linuxcnc.stat().poll()' >/dev/null 2>&1; then
    echo "sim: LinuxCNC hazır (${i}. deneme)"
    break
  fi
  [ "$i" = 60 ] && { echo "sim: LinuxCNC 60 denemede cevap vermedi" >&2; exit 1; }
  sleep 1
done

echo "sim: arayüz ${SIM_URL:-konteynerde :$PORT}, LinuxCncAdapter ile"
cd "$REPO"
export PYTHONPATH="$REPO/ui"
exec python3 -m bettercnc.app --profile test-rig --adapter linuxcnc --host 0.0.0.0 --port "$PORT"
