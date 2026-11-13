#!/bin/bash
# The kiosk, on the machine (the test rig's live USB, on any PC, now; Pi 4 later).
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
#   sudo deploy/kiosk.sh board-network [--iface IF] [--addr 10.10.10.1/24]
#        the Mesa board's static address, so it no longer needs
#        `sudo ip addr add` after every boot (docs/DURUM.md:29-36). On any PC:
#        bound to no interface name, it takes whichever wired port the board's
#        cable is in (USB adapters, enx*, excluded). The Ethernet port is the
#        board's; home network over Wi-Fi. Never cuts a live connection.
#        --iface IF binds it to one port and brings it up (PCs with several).
#   deploy/kiosk.sh clean-shm
#        LinuxCNC's motion shared memory, if a stop left it behind (SysV key
#        0x00000064, nothing attached), is removed - only while no LinuxCNC
#        process runs. kiosk-session runs it before every LinuxCNC start.
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
# The board's NetworkManager profile. The live USB moves between PCs and each
# names its port differently - the August laptop enp3s0, the 2026-11-12 PC
# enp12s0 - so it matches wired ports by pattern, never by one name. udev
# names USB network adapters enx<MAC>: never the board's port (hm2_eth needs
# the PC's own NIC, docs/05). Keep board_port() below in step with the match.
BOARD_CON=bettercnc-board
BOARD_MATCH='en* eth* !enx*'
# Above a profile's default priority (0), which NetworkManager's own generated
# "Wired connection N" does not exceed either, so a board plugged into the port
# gets this profile, whatever PC it is.
BOARD_PRIORITY=100
# LinuxCNC's motion shared memory: SysV key 100, [EMCMOT]SHMEM_KEY's default
# (emcmotcfg.h:49; test-rig.ini sets none). After a TERM shutdown LinuxCNC
# sometimes leaves it behind with nothing attached, and every start after
# that fails: "A configuration error is preventing LinuxCNC from starting"
# (2026-11-12). While any of these runs - the linuxcnc script, or a process
# that creates or attaches the segment - it is no orphan.
LCNC_SHM_KEY=0x00000064
LCNC_PROCS='linuxcnc|rtapi_app|milltask|linuxcncsvr'

die() { echo "kiosk.sh: $*" >&2; exit 1; }
need_root() { [ "$(id -u)" = 0 ] || die "sudo ile çalıştırın"; }

cmd="${1:-}"; shift || true

install_files() {
  need_root
  local user="${SUDO_USER:-}" ini="$REPO/configs/test-rig/test-rig.ini" profile=test-rig layout=tr
  local tz=Europe/Istanbul
  while [ $# -gt 0 ]; do
    case "$1" in
      --user) user="$2"; shift 2 ;;
      --ini) ini="$(cd "$(dirname "$2")" && pwd)/$(basename "$2")"; shift 2 ;;
      --profile) profile="$2"; shift 2 ;;
      --layout) layout="$2"; shift 2 ;;
      --tz) tz="$2"; shift 2 ;;
      *) die "bilinmeyen seçenek: $1" ;;
    esac
  done
  if [ -z "$user" ] || [ "$user" = root ]; then
    die "--user verin (servis root olarak çalışmaz)"
  fi
  local home; home="$(getent passwd "$user" | cut -d: -f6)"
  [ -n "$home" ] || die "kullanıcı yok: $user"
  [ -f "$ini" ] || die "INI yok: $ini"
  # glibc falls back to UTC for a zone it does not know, silently
  [ -f "/usr/share/zoneinfo/$tz" ] || die "saat dilimi yok: $tz (bkz. /usr/share/zoneinfo)"
  case "$REPO" in "$home"/*) ;; *) echo "uyarı: repo $user kullanıcısının HOME'unda değil: $REPO" >&2 ;; esac

  echo "== paketler"
  apt-get update
  local browser=chromium
  apt-cache show chromium >/dev/null 2>&1 || browser=chromium-browser
  apt-get install -y --no-install-recommends cage "$browser" fonts-dejavu-core

  echo "== dosyalar"
  sed -e "s|@USER@|$user|g" -e "s|@HOME@|$home|g" -e "s|@REPO@|$REPO|g" \
      -e "s|@INI@|$ini|g" -e "s|@PROFILE@|$profile|g" -e "s|@LAYOUT@|$layout|g" -e "s|@TZ@|$tz|g" \
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
  printf 'USER=%s\nREPO=%s\nINI=%s\nPROFILE=%s\nLAYOUT=%s\nTZ=%s\n' "$user" "$REPO" "$ini" "$profile" "$layout" "$tz" > "$STATE/kiosk.env"
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
    if [ -n "$dm" ] && systemctl is-active --quiet "$dm"; then
      # Stopping the desktop ends every terminal on it, this script with it,
      # before the start line runs (audit 2026-10-21); a transient unit does
      # the switch from outside any session.
      systemd-run --quiet --collect --unit=bettercnc-kiosk-switch \
        --description="better-cnc: masaüstünden kiosk'a geçiş" \
        sh -c "systemctl stop '$dm' && systemctl start '$UNIT'"
      echo "masaüstü kapatılıp kiosk tty1'de başlatılıyor (birkaç saniye)"
    else
      systemctl start "$UNIT"
      echo "kiosk tty1'de başladı"
    fi
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

# Whether a device name can be the board's port: wired, not a USB adapter.
# The same rule as BOARD_MATCH, for the devices nmcli lists.
board_port() {
  case "$1" in
    enx*) return 1 ;;
    en*|eth*) return 0 ;;
    *) return 1 ;;
  esac
}

board_network() {
  need_root
  local iface="" addr=10.10.10.1/24
  while [ $# -gt 0 ]; do
    case "$1" in
      --iface|--addr) [ $# -ge 2 ] || die "$1 bir değer ister" ;;
    esac
    case "$1" in
      --iface) iface="$2"; shift 2 ;;
      --addr) addr="$2"; shift 2 ;;
      *) die "bilinmeyen seçenek: $1" ;;
    esac
  done
  if [ -n "$iface" ]; then
    ip link show "$iface" >/dev/null 2>&1 || die "arayüz yok: $iface"
  fi
  if ! command -v nmcli >/dev/null || ! systemctl is-active -q NetworkManager; then
    die "NetworkManager yok; adresi elle verin: ip addr add $addr dev ${iface:-<kartın takılı olduğu port>}"
  fi
  # never-default: the board's link must never take the default route - a
  # route there cut the internet once (docs/DURUM.md:38, 192.168.1.1/24).
  local settings=(ipv4.method manual ipv4.addresses "$addr" ipv4.never-default yes
                  ipv6.method disabled connection.autoconnect yes
                  connection.autoconnect-priority "$BOARD_PRIORITY")
  nmcli connection delete "$BOARD_CON" >/dev/null 2>&1 || true
  if [ -n "$iface" ]; then
    nmcli connection add type ethernet con-name "$BOARD_CON" ifname "$iface" "${settings[@]}"
    nmcli connection up "$BOARD_CON"
    ip -brief addr show "$iface"
    return
  fi
  # ifname '*': bound to no interface name; the match keeps it to wired,
  # non-USB ports.
  nmcli connection add type ethernet con-name "$BOARD_CON" ifname '*' \
    match.interface-name "$BOARD_MATCH" "${settings[@]}"
  # Never cut a live connection: this runs over SSH, and on a PC with one
  # port and no Wi-Fi the SSH link IS the port the board will use
  # (2026-11-12). Bring the profile up only on the one eligible port with a
  # cable in and nothing on it; otherwise NetworkManager brings it up by
  # itself when the board's cable goes in (autoconnect + priority).
  local dev type state free=()
  while IFS=: read -r dev type state; do
    if [ "$type" != ethernet ] || ! board_port "$dev"; then continue; fi
    # disconnected = a cable in, no connection on it; unavailable = no cable;
    # connected/connecting = in use (maybe this very SSH session).
    if [ "$state" = disconnected ]; then free+=("$dev"); fi
  done < <(LC_ALL=C nmcli -t -f DEVICE,TYPE,STATE device)
  if [ "${#free[@]}" = 1 ]; then
    nmcli connection up "$BOARD_CON" ifname "${free[0]}"
    ip -brief addr show "${free[0]}"
  else
    echo "$BOARD_CON kuruldu; açık bir bağlantıyı kesmemek için şimdi açılmadı."
    if [ "${#free[@]}" -gt 1 ]; then
      echo "Kablosu takılı, boşta birden çok port var (${free[*]}): kartınki için --iface <port>."
    fi
    echo "Kartın kablosu Ethernet portuna takılınca, hangi PC olursa olsun, kendiliğinden devreye girer."
    echo "Ev ağı Wi-Fi'dan gelir. Kabloyla internet gerekirse ağ menüsünden \"Wired connection\"ı seçin"
    echo "(ya da: nmcli connection up \"Wired connection 1\")."
  fi
}

# No root needed: rtapi_app hands the segment to the user LinuxCNC runs as
# (uspace_common.h:103-119), the kiosk's own user. Never while a LinuxCNC
# process runs or could not be ruled out, never a segment with anything
# attached.
clean_shm() {
  local running code=0
  running="$(pgrep -x "$LCNC_PROCS")" || code=$?
  if [ "$code" = 0 ]; then
    echo "LinuxCNC çalışıyor (pid ${running//$'\n'/ }); paylaşımlı belleğe dokunulmadı."
    return 0
  elif [ "$code" != 1 ]; then
    echo "LinuxCNC süreçlerine bakılamadı (pgrep: $code); paylaşımlı belleğe dokunulmadı." >&2
    return 0
  fi
  local key id owner nattch
  while read -r key id owner _ _ nattch _; do
    [ "$key" = "$LCNC_SHM_KEY" ] || continue
    if [ "$nattch" != 0 ]; then
      echo "paylaşımlı bellek $key (shmid $id) kullanımda ($nattch bağlı); dokunulmadı."
    elif ipcrm -m "$id"; then
      echo "sahipsiz paylaşımlı bellek silindi: key $key, shmid $id."
    else
      echo "sahipsiz paylaşımlı bellek silinemedi (sahibi $owner); elle: sudo ipcrm -m $id" >&2
      return 1
    fi
  done < <(LC_ALL=C ipcs -m)
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
  clean-shm) clean_shm ;;
  status) status ;;
  uninstall) uninstall ;;
  *) sed -n '2,/^[^#]/p' "$0" | sed '$d; s/^# \{0,1\}//'; exit 2 ;;
esac
