#!/bin/bash
# The machine (test rig laptop now, Pi 4 later), from the Mac, over SSH.
# deploy/README.md has the whole picture.
#
#   deploy/machine.sh bootstrap        serve deploy/bootstrap-ssh.sh on the LAN for
#                                      15 min and print the one line to type at the machine
#   deploy/machine.sh ssh [CMD...]     a shell there, or one command
#   deploy/machine.sh info             OS, kernel, LinuxCNC, sudo, NICs, repo state, kiosk
#   deploy/machine.sh sync             what `sync --apply` would copy (dry run)
#   deploy/machine.sh sync --apply     back up the machine's own changes, then copy
#                                      this working tree there (no .git, no reference/,
#                                      nothing deleted there)
#   deploy/machine.sh ui [PORT]        the machine's UI (127.0.0.1:8080 there) at
#                                      http://localhost:PORT here (8781); Ctrl-C ends it
#
# Settings in deploy/machine.local (gitignored), or the environment:
#   CNC_HOST=192.168.1.42                       required, from bootstrap's output
#   CNC_USER=user                               (user)
#   CNC_REPO=better-cnc                         relative to that user's home (better-cnc)
#   CNC_KEY=$HOME/.ssh/basarahmetoguzhan-GitHub (that)
set -euo pipefail

DEPLOY_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO="$(dirname "$DEPLOY_DIR")"
# shellcheck disable=SC1091
[ -f "$DEPLOY_DIR/machine.local" ] && . "$DEPLOY_DIR/machine.local"
CNC_USER="${CNC_USER:-user}"
CNC_REPO="${CNC_REPO:-better-cnc}"
CNC_KEY="${CNC_KEY:-$HOME/.ssh/basarahmetoguzhan-GitHub}"

# accept-new: the first connection records the machine's host key; a changed
# key later is refused, as with a normal ssh.
SSH_OPTS=(-i "$CNC_KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new
          -o ConnectTimeout=5 -o ServerAliveInterval=15)

need_host() {
  [ -n "${CNC_HOST:-}" ] || { echo "CNC_HOST yok: deploy/machine.local'e CNC_HOST=<adres> yazın" >&2; exit 1; }
}
# shellcheck disable=SC2029  # the command is meant to run, and expand, there
remote() { need_host; ssh "${SSH_OPTS[@]}" "$CNC_USER@$CNC_HOST" "$@"; }

cmd="${1:-}"; shift || true
case "$cmd" in
  bootstrap)
    ip="$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || true)"
    [ -n "$ip" ] || { echo "Mac'in yerel ağ adresi bulunamadı" >&2; exit 1; }
    dir="$(mktemp -d)"; trap 'rm -rf "$dir"' EXIT
    cp "$DEPLOY_DIR/bootstrap-ssh.sh" "$dir/b"
    echo "Makinede, normal kullanıcıyla bir terminalde şunu yazın:"
    echo
    echo "    curl -fsSL http://$ip:8765/b | bash"
    echo
    echo "(15 dakika açık; macOS gelen bağlantı izni sorarsa izin verin)"
    # Only this one file, only on the LAN address, only for 15 minutes.
    ( cd "$dir" && exec python3 -m http.server 8765 --bind "$ip" ) &
    srv=$!
    sleep 900 || true
    kill "$srv" 2>/dev/null || true ;;
  ssh)
    remote "$@" ;;
  info)
    remote bash -s -- "$CNC_REPO" <<'EOF'
repo="$HOME/$1"
echo "== sistem";   . /etc/os-release; echo "$PRETTY_NAME · $(uname -m) · $(uname -r)"
echo "== linuxcnc"; dpkg-query -W -f='${Package} ${Version}\n' 'linuxcnc*' 2>/dev/null | grep -v '^linuxcnc-doc' || echo yok
echo "== sudo";     sudo -n true 2>/dev/null && echo "şifre sormuyor" || echo "şifre soruyor"
echo "== açılış";   cat /proc/cmdline
echo "== persistence"; ls /run/live/persistence 2>/dev/null || echo "(live persistence dizini yok)"
echo "== ağ";       ip -brief addr
echo "== masaüstü"; readlink /etc/systemd/system/display-manager.service || echo yok
echo "== kiosk";    systemctl is-enabled bettercnc-kiosk.service 2>/dev/null || echo kurulu değil
echo "== repo ($repo)"
if [ -d "$repo/.git" ]; then
  git -C "$repo" log -1 --format='%h %ad %s' --date=short
  git -C "$repo" status --short
else
  echo "repo yok"
fi
EOF
    ;;
  sync)
    need_host
    apply=0; [ "${1:-}" = --apply ] && apply=1
    excludes=(--exclude .git/ --exclude reference/ --exclude __pycache__/ --exclude .DS_Store
              --exclude .claude/ --exclude deploy/machine.local --exclude '*-kiosk.ini')
    if [ "$apply" = 0 ]; then
      echo "Kopyalanacaklar (deneme, hiçbir şey değişmedi):"
      rsync -rlptzi --dry-run "${excludes[@]}" -e "ssh ${SSH_OPTS[*]}" \
        "$REPO/" "$CNC_USER@$CNC_HOST:$CNC_REPO/"
      echo "Uygulamak için: deploy/machine.sh sync --apply"
      exit 0
    fi
    ts="$(date +%Y%m%d-%H%M%S)"
    echo "== makinedeki yerel değişiklikler yedekleniyor: ~/better-cnc-yedek/$ts"
    remote bash -s -- "$CNC_REPO" "$ts" <<'EOF'
set -e
repo="$HOME/$1"; out="$HOME/better-cnc-yedek/$2"
[ -d "$repo/.git" ] || { echo "(repo yok, yedek gerekmiyor)"; exit 0; }
mkdir -p "$out"
git -C "$repo" status --short > "$out/status.txt"
git -C "$repo" diff > "$out/diff.patch"
git -C "$repo" ls-files -m -o --exclude-standard -z \
  | (cd "$repo" && xargs -0 -r tar -czf "$out/files.tar.gz")
cat "$out/status.txt"
EOF
    rsync -rlptz "${excludes[@]}" -e "ssh ${SSH_OPTS[*]}" "$REPO/" "$CNC_USER@$CNC_HOST:$CNC_REPO/"
    echo "== kopyalandı; makinede git status:"
    remote git -C "$CNC_REPO" status --short ;;
  ui)
    need_host
    port="${1:-8781}"
    echo "Makinenin arayüzü: http://localhost:$port/  (Ctrl-C bitirir)"
    exec ssh "${SSH_OPTS[@]}" -N -L "127.0.0.1:$port:127.0.0.1:8080" "$CNC_USER@$CNC_HOST" ;;
  *)
    sed -n '2,24p' "$0" | sed 's/^# \{0,1\}//'; exit 2 ;;
esac
