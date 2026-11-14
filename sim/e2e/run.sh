#!/bin/bash
# SIMULATOR ONLY: the end-to-end tests, run from the Mac (`make sim-e2e`).
#
#   sim/e2e/run.sh                    every sim/e2e/test_*.py
#   sim/e2e/run.sh test_baseline.py   only the modules named (make sim-e2e E2E=...)
#
# Each module gets a fresh LinuxCNC: `sim/sim.sh up`, the module's tests in
# the container (python3 -m unittest; sim/e2e/harness.py drives the UI over
# HTTP and checks linuxcnc.stat and halcmd), then `sim/sim.sh down`.
# Positions, homed flags, motor offsets and moved switches outlive a test in
# a running LinuxCNC, so modules never share one. It runs under its own
# container name and port (better-cnc-e2e, 8771; E2E_SIM_NAME and
# E2E_SIM_PORT change them, an exported SIM_NAME does not): a developer's
# `make sim` on 8770 is left alone. The container goes down however this
# ends (trap).
# Expected failures are the known gaps of later work: the module named after
# a work package's area holds them (sim/README.md, "Uçtan uca testler").
set -euo pipefail

E2E_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$E2E_DIR/../.." && pwd)"
SIM="$REPO/sim/sim.sh"
# sim/sim.sh reads SIM_NAME and SIM_PORT; set here, never inherited.
export SIM_NAME="${E2E_SIM_NAME:-better-cnc-e2e}"
export SIM_PORT="${E2E_SIM_PORT:-8771}"

modules=()
if [ "$#" -gt 0 ]; then
  for m in "$@"; do
    m="$(basename "$m")"
    case "$m" in *.py) ;; *) m="$m.py" ;; esac
    if [ ! -f "$E2E_DIR/$m" ]; then
      echo "sim-e2e: sim/e2e/$m yok; olanlar: $(cd "$E2E_DIR" && echo test_*.py)" >&2
      exit 2
    fi
    modules+=("$m")
  done
else
  for f in "$E2E_DIR"/test_*.py; do
    modules+=("$(basename "$f")")
  done
fi

trap '"$SIM" down >/dev/null 2>&1 || true' EXIT

failed=0
summary=""
for m in "${modules[@]}"; do
  start=$SECONDS
  echo "=== $m"
  "$SIM" down >/dev/null
  if ! "$SIM" up; then
    summary="$summary$(printf '%-24s %s' "$m" "SİM AÇILMADI")"$'\n'
    failed=1
    continue
  fi
  if docker exec -e PYTHONDONTWRITEBYTECODE=1 "$SIM_NAME" \
       python3 -m unittest discover -s /repo/sim/e2e -p "$m" -v --durations 0; then
    result="ok"
  else
    result="BAŞARISIZ"
    failed=1
    echo "--- arayüzün son çıktısı (sim/sim.sh logs):"
    "$SIM" logs 2>&1 | tail -n 20 || true
  fi
  "$SIM" down >/dev/null
  summary="$summary$(printf '%-24s %-10s %4d s' "$m" "$result" $((SECONDS - start)))"$'\n'
done

echo "=== özet (modül başına, sim up/down dahil)"
printf '%s' "$summary"
exit "$failed"
