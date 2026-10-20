#!/bin/bash
# The kiosk, on the machine (test rig laptop's live USB now, Pi 4 later).
# Run from the repo, with sudo. deploy/README.md has the whole picture.
#
#   sudo deploy/kiosk.sh install [--user U] [--ini PATH] [--profile P] [--layout tr]
#        packages (cage, chromium), the service, PAM, Chromium policy, sysctl,
#        logind (lid and suspend keys do nothing). --layout: the keyboard (tr).
#        Changes nothing about what the machine boots into.
#   sudo deploy/kiosk.sh enable [--now]
#        boot into the kiosk: desktop off, bettercnc-kiosk on. --now switches
#        tty1 to the kiosk at once instead of at the next boot.
#   sudo deploy/kiosk.sh disable [--now]
#        back to the desktop the machine had before `enable`.
#   sudo deploy/kiosk.sh board-network [--iface enp3s0] [--addr 10.10.10.1/24]
#        the Mesa board's static address, so it no longer needs
#        `sudo ip addr add` after every boot (docs/DURUM.md:29-36).
#   sudo deploy/kiosk.sh status
#   sudo deploy/kiosk.sh uninstall
set -euo pipefail

DEPLOY_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO="$(dirname "$DEPLOY_DIR")"
FILES="$DEPLOY_DIR/files"
UNIT=bettercnc-kiosk.service
UNIT_PATH=/etc/systemd/system/$UNIT
PAM_PATH=/etc/pam.d/bettercnc-kiosk
POLICY_PATH=/etc/chromium/policies/managed/bettercnc-kiosk.json
SYSCTL_PATH=/etc/sysctl.d/90-bettercnc-kiosk.conf
LOGIND_PATH=/etc/systemd/logind.conf.d/90-bettercnc-kiosk.conf
# The "please wait" page browserwatch shows in place of Chromium's own pages;
# the policy allows exactly this file:// URL.
WAIT_PATH=/usr/share/bettercnc/wait.html
STATE=/etc/bettercnc
PREV_DM="$STATE/previous-display-manager"

die() { echo "kiosk.sh: $*" >&2; exit 1; }
need_root() { [ "$(id -u)" = 0 ] || die "sudo ile çalıştırın"; }

cmd="${1:-}"; shift || true

install_files() {
  need_root
  local user="${SUDO_USER:-}" ini="$REPO/configs/test-rig/test-rig.ini" profile=test-rig layout=tr
  while [ $# -gt 0 ]; do
    case "$1" in
      --user) user="$2"; shift 2 ;;
      --ini) ini="$(cd "$(dirname "$2")" && pwd)/$(basename "$2")"; shift 2 ;;
      --profile) profile="$2"; shift 2 ;;
      --layout) layout="$2"; shift 2 ;;
      *) die "bilinmeyen seçenek: $1" ;;
    esac
  done
  if [ -z "$user" ] || [ "$user" = root ]; then
    die "--user verin (servis root olarak çalışmaz)"
  fi
  local home; home="$(getent passwd "$user" | cut -d: -f6)"
  [ -n "$home" ] || die "kullanıcı yok: $user"
  [ -f "$ini" ] || die "INI yok: $ini"
  case "$REPO" in "$home"/*) ;; *) echo "uyarı: repo $user kullanıcısının HOME'unda değil: $REPO" >&2 ;; esac

  echo "== paketler"
  apt-get update
  local browser=chromium
  apt-cache show chromium >/dev/null 2>&1 || browser=chromium-browser
  apt-get install -y --no-install-recommends cage "$browser" fonts-dejavu-core

  echo "== dosyalar"
  sed -e "s|@USER@|$user|g" -e "s|@HOME@|$home|g" -e "s|@REPO@|$REPO|g" \
      -e "s|@INI@|$ini|g" -e "s|@PROFILE@|$profile|g" -e "s|@LAYOUT@|$layout|g" \
      "$FILES/bettercnc-kiosk.service.in" > "$UNIT_PATH"
  install -m 0644 "$FILES/bettercnc-kiosk.pam" "$PAM_PATH"
  install -d -m 0755 "$(dirname "$POLICY_PATH")"
  install -m 0644 "$FILES/chromium-policy.json" "$POLICY_PATH"
  install -m 0644 "$FILES/90-bettercnc-kiosk.conf" "$SYSCTL_PATH"
  sysctl -q -p "$SYSCTL_PATH"
  install -D -m 0644 "$FILES/wait.html" "$WAIT_PATH"
  install -d -m 0755 "$(dirname "$LOGIND_PATH")"
  install -m 0644 "$FILES/logind-bettercnc-kiosk.conf" "$LOGIND_PATH"
  install -d -m 0755 "$STATE"
  printf 'USER=%s\nREPO=%s\nINI=%s\nPROFILE=%s\nLAYOUT=%s\n' "$user" "$REPO" "$ini" "$profile" "$layout" > "$STATE/kiosk.env"
  systemctl daemon-reload
  systemd-analyze verify "$UNIT_PATH" || die "servis dosyası doğrulanamadı"
  echo "kuruldu, AÇILMADI. Açmak için: sudo deploy/kiosk.sh enable"
}

current_dm() {
  local link
  link="$(readlink /etc/systemd/system/display-manager.service 2>/dev/null || true)"
  if [ -n "$link" ]; then basename "$link"; fi
}

enable_kiosk() {
  need_root
  [ -f "$UNIT_PATH" ] || die "önce: sudo deploy/kiosk.sh install"
  local now=0; [ "${1:-}" = --now ] && now=1
  install -d -m 0755 "$STATE"
  local dm; dm="$(current_dm)"
  if [ -n "$dm" ] && [ "$dm" != "$UNIT" ]; then
    echo "$dm" > "$PREV_DM"
    systemctl disable "$dm"
    echo "masaüstü kapatıldı: $dm (disable ile geri gelir)"
  fi
  rm -f "$STATE/kiosk-disabled"
  systemctl mask ctrl-alt-del.target
  systemctl set-default graphical.target
  systemctl enable "$UNIT"
  if [ "$now" = 1 ]; then
    if [ -n "$dm" ]; then systemctl stop "$dm"; fi
    systemctl start "$UNIT"
    echo "kiosk tty1'de başladı"
  else
    echo "bir sonraki açılışta kiosk başlar (hemen için: enable --now)"
  fi
}

disable_kiosk() {
  need_root
  local now=0; [ "${1:-}" = --now ] && now=1
  systemctl disable "$UNIT" 2>/dev/null || true
  if [ "$now" = 1 ]; then systemctl stop "$UNIT" || true; fi
  systemctl unmask ctrl-alt-del.target || true
  if [ -s "$PREV_DM" ]; then
    local dm; dm="$(cat "$PREV_DM")"
    systemctl enable "$dm"
    if [ "$now" = 1 ]; then systemctl start "$dm"; fi
    echo "masaüstü geri açıldı: $dm"
  fi
}

board_network() {
  need_root
  local iface=enp3s0 addr=10.10.10.1/24
  while [ $# -gt 0 ]; do
    case "$1" in
      --iface) iface="$2"; shift 2 ;;
      --addr) addr="$2"; shift 2 ;;
      *) die "bilinmeyen seçenek: $1" ;;
    esac
  done
  ip link show "$iface" >/dev/null 2>&1 || die "arayüz yok: $iface"
  if ! command -v nmcli >/dev/null || ! systemctl is-active -q NetworkManager; then
    die "NetworkManager yok; adresi elle verin: ip addr add $addr dev $iface"
  fi
  # never-default: the board's link must never take the default route - a
  # route there cut the internet once (docs/DURUM.md:38, 192.168.1.1/24).
  nmcli connection delete bettercnc-board >/dev/null 2>&1 || true
  nmcli connection add type ethernet con-name bettercnc-board ifname "$iface" \
    ipv4.method manual ipv4.addresses "$addr" ipv4.never-default yes \
    ipv6.method disabled connection.autoconnect yes
  nmcli connection up bettercnc-board
  ip -brief addr show "$iface"
}

status() {
  echo "servis   : $(systemctl is-enabled "$UNIT" 2>/dev/null || echo yok) / $(systemctl is-active "$UNIT" 2>/dev/null || true)"
  echo "masaüstü : $(current_dm || true) (kiosk öncesi: $(cat "$PREV_DM" 2>/dev/null || echo -))"
  echo "kapalı mı: $([ -e "$STATE/kiosk-disabled" ] && echo "evet ($STATE/kiosk-disabled)" || echo hayır)"
  grep -o 'bettercnc.kiosk=[^ ]*' /proc/cmdline || true
  [ -f "$STATE/kiosk.env" ] && sed 's/^/ayar     : /' "$STATE/kiosk.env"
  journalctl -u "$UNIT" -n 15 --no-pager 2>/dev/null || true
}

uninstall() {
  need_root
  disable_kiosk
  rm -f "$UNIT_PATH" "$PAM_PATH" "$POLICY_PATH" "$SYSCTL_PATH" "$LOGIND_PATH" "$WAIT_PATH" \
        "$STATE/kiosk.env" "$PREV_DM"
  systemctl daemon-reload
  echo "kaldırıldı (cage ve chromium paketleri duruyor)"
}

case "$cmd" in
  install) install_files "$@" ;;
  enable) enable_kiosk "$@" ;;
  disable) disable_kiosk "$@" ;;
  board-network) board_network "$@" ;;
  status) status ;;
  uninstall) uninstall ;;
  *) sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'; exit 2 ;;
esac
