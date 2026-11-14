#!/bin/bash
# The LinuxCNC simulator, from the Mac. See sim/README.md.
#
#   sim/sim.sh build            build the image (Debian 13 arm64 + LinuxCNC 2.9.9)
#   sim/sim.sh up               start LinuxCNC + the UI; UI at http://localhost:8770/
#   sim/sim.sh down             stop it
#   sim/sim.sh logs             the UI's output (LinuxCNC's own log: `log`)
#   sim/sim.sh log              LinuxCNC's log inside the container
#   sim/sim.sh halcmd ARGS...   e.g. `halcmd show pin joint.0`, `halcmd sets watchdog TRUE`
#   sim/sim.sh press X+|X-|Y+|Y-|Z+   a limit switch reads pressed where the axis stands
#                               (sim/e2e/harness.py press; refuses where it cannot)
#   sim/sim.sh release [END...] the switches (all, or those named) back where the HAL file has them
#   sim/sim.sh switches         each simulated switch: position, motor position, state
#   sim/sim.sh shell            a shell in the running container
#   sim/sim.sh test             the test suite (`make test`) inside the image (Python 3.13, arm64, real linuxcnc module)
#   sim/sim.sh kiosk            deploy/'s kiosk (cage + chromium + LinuxCNC + UI), headless
#   sim/sim.sh shot FILE.png    a screenshot of the kiosk's screen
#   sim/sim.sh keys WTYPE-ARGS  type into the kiosk (wtype: its own keymap, so
#                               this tests the page and policy, not the kiosk keymap)
#   sim/sim.sh kiosk-down       stop it
#   sim/sim.sh lint             shellcheck the sim/, deploy/ and scripts/ shell scripts, verify the unit file
#
# The repo is mounted read-only at /repo, so edits on the Mac are live; restart
# with `down` + `up` to pick up a changed INI, HAL or UI file.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
IMAGE="${SIM_IMAGE:-better-cnc-sim:2.9.9}"
NAME="${SIM_NAME:-better-cnc-sim}"
PORT="${SIM_PORT:-8770}"

cmd="${1:-}"; shift || true
case "$cmd" in
  build)
    docker build --platform linux/arm64 -t "$IMAGE" "$REPO/sim" ;;
  up)
    if docker inspect "$NAME" >/dev/null 2>&1; then
      echo "sim zaten çalışıyor: http://localhost:$PORT/ (yeniden başlatmak için: down, sonra up)"; exit 0
    fi
    # 127.0.0.1 only: the UI drives a (simulated) machine; it is not for the LAN.
    docker run -d --rm --init --cap-add IPC_OWNER --name "$NAME" --platform linux/arm64 \
      -e SIM_URL="http://localhost:$PORT/" -v "$REPO:/repo:ro" -p "127.0.0.1:$PORT:8080" "$IMAGE" >/dev/null
    for _ in $(seq 1 60); do
      if curl -fsS "http://127.0.0.1:$PORT/healthz" >/dev/null 2>&1; then
        echo "sim hazır: http://localhost:$PORT/"; exit 0
      fi
      if ! docker inspect "$NAME" >/dev/null 2>&1; then
        echo "sim durdu - çıktısı yukarıda değilse: sim/sim.sh up yerine 'docker run' ile ön planda çalıştır" >&2
        exit 1
      fi
      sleep 1
    done
    echo "sim 60 s içinde açılmadı; sim/sim.sh logs" >&2; exit 1 ;;
  down)
    docker stop "$NAME" >/dev/null 2>&1 || true
    # --rm removes the container after stop returns; wait, so `up` can reuse the name.
    while docker inspect "$NAME" >/dev/null 2>&1; do sleep 0.2; done
    echo "sim durduruldu" ;;
  logs)
    docker logs "$NAME" "$@" ;;
  log)
    docker exec "$NAME" cat /tmp/better-cnc-sim/linuxcnc.log ;;
  halcmd)
    docker exec "$NAME" halcmd "$@" ;;
  press|release|switches)
    # The switch helpers of the e2e harness, so both move switches one way
    # (sim/README.md, "Limit switch canlandırma").
    docker exec -e PYTHONDONTWRITEBYTECODE=1 "$NAME" python3 /repo/sim/e2e/harness.py "$cmd" "$@" ;;
  shell)
    docker exec -it "$NAME" bash ;;
  test)
    docker run --rm --platform linux/arm64 -v "$REPO:/repo:ro" -w /repo \
      -e PYTHONDONTWRITEBYTECODE=1 -e PYTHONPATH=ui "$IMAGE" python3 ui/tests/run_all.py ;;
  kiosk)
    # The machine's Chromium policy too (deploy/kiosk.sh install puts it there).
    docker run -d --rm --init --cap-add IPC_OWNER --name "$NAME-kiosk" --platform linux/arm64 \
      -v "$REPO:/repo:ro" \
      -v "$REPO/deploy/files/chromium-policy.json:/etc/chromium/policies/managed/bettercnc-kiosk.json:ro" \
      -v "$REPO/deploy/files/wait.html:/usr/share/bettercnc/wait.html:ro" \
      "$IMAGE" /repo/sim/kiosk.sh >/dev/null
    echo "kiosk başladı (ekran: sim/sim.sh shot ekran.png; durdur: sim/sim.sh kiosk-down)" ;;
  shot)
    out="${1:?sim/sim.sh shot FILE.png}"
    docker exec -e XDG_RUNTIME_DIR=/tmp/xdg -e WAYLAND_DISPLAY=wayland-0 "$NAME-kiosk" grim /tmp/shot.png
    docker cp "$NAME-kiosk:/tmp/shot.png" "$out" >/dev/null
    echo "$out" ;;
  keys)
    docker exec -e XDG_RUNTIME_DIR=/tmp/xdg -e WAYLAND_DISPLAY=wayland-0 "$NAME-kiosk" wtype "$@" ;;
  kiosk-down)
    docker stop "$NAME-kiosk" >/dev/null 2>&1 || true
    while docker inspect "$NAME-kiosk" >/dev/null 2>&1; do sleep 0.2; done
    echo "kiosk durduruldu" ;;
  lint)
    docker run --rm --platform linux/arm64 -e LANG=C.UTF-8 -v "$REPO:/repo:ro" -w /repo "$IMAGE" bash -c '
      shellcheck -x sim/*.sh sim/e2e/*.sh deploy/kiosk.sh deploy/machine.sh deploy/bootstrap-ssh.sh \
        deploy/kiosk-session deploy/bettercnc-display deploy/kiosk-browser sim/headless_display.sh \
        scripts/fetch-reference.sh scripts/detect-board.sh &&
      echo "shellcheck: temiz" &&
      sed -e "s|@USER@|cnc|g" -e "s|@HOME@|/home/cnc|g" -e "s|@REPO@|/repo|g" \
          -e "s|@INI@|/repo/configs/test-rig/test-rig.ini|g" -e "s|@PROFILE@|test-rig|g" -e "s|@LAYOUT@|tr|g" \
          -e "s|@TZ@|Europe/Istanbul|g" \
          deploy/files/bettercnc-kiosk.service.in > /tmp/bettercnc-kiosk.service &&
      systemd-analyze verify /tmp/bettercnc-kiosk.service && echo "servis dosyası: geçerli"' ;;
  *)
    sed -n '2,/^[^#]/p' "$0" | sed '$d; s/^# \{0,1\}//'; exit 2 ;;
esac
