#!/bin/bash
# Run ONCE at the machine, as its normal user (not root). The Mac serves it:
#
#   deploy/machine.sh bootstrap        (on the Mac) prints the line to type here:
#   curl -fsSL http://<mac>:8765/b | bash
#
# - installs the SSH server (and rsync, which deploy/machine.sh sync uses)
# - key login ONLY: the live USB user's password is "live", public knowledge,
#   so password login over the network must never work
# - authorises the public keys of github.com/basarahmetoguzhan, which include
#   the Mac's (checked 2026-10-20)
# - prints what the Mac needs: user, address, whether sudo asks a password
set -eu

GITHUB_USER=basarahmetoguzhan

if [ "$(id -u)" = 0 ]; then
  echo "root olarak değil, makinenin normal kullanıcısıyla çalıştırın" >&2
  exit 1
fi

# Order matters (audit 2026-10-21): Debian starts sshd the moment the
# package is installed, with password login on. So the keys are fetched and
# the key-only config written FIRST; if the keys cannot be had, nothing has
# been opened. Debian's sshd_config includes sshd_config.d/*.conf first, and
# the first value sshd reads for a key wins.
keys="$(curl -fsSL "https://github.com/$GITHUB_USER.keys")"
[ -n "$keys" ] || { echo "GitHub'dan anahtar gelmedi; sshd kurulmadı" >&2; exit 1; }
mkdir -p ~/.ssh
chmod 700 ~/.ssh
touch ~/.ssh/authorized_keys
chmod 600 ~/.ssh/authorized_keys
while IFS= read -r key; do
  [ -n "$key" ] || continue
  grep -qxF "$key" ~/.ssh/authorized_keys || echo "$key" >> ~/.ssh/authorized_keys
done <<< "$keys"

sudo install -d -m 0755 /etc/ssh/sshd_config.d
sudo tee /etc/ssh/sshd_config.d/10-bettercnc.conf >/dev/null <<'EOF'
# deploy/bootstrap-ssh.sh: key login only - the live USB password is public.
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin no
EOF

sudo apt-get update
sudo apt-get install -y openssh-server rsync curl
sudo sshd -t
sudo systemctl enable ssh
sudo systemctl restart ssh
# The effective value as sshd parses the whole configuration (our drop-in
# through the Include), not just our file.
if ! sudo sshd -T 2>/dev/null | grep -qi '^passwordauthentication no$'; then
  echo "UYARI: sshd hâlâ şifreyle giriş kabul ediyor — ssh'ı durduruyorum: sudo systemctl stop ssh" >&2
  sudo systemctl stop ssh
  exit 1
fi

echo
echo "=== better-cnc: SSH hazır ==="
echo "kullanıcı : $(id -un)"
echo "adres     : $(hostname -I)"
if sudo -n true 2>/dev/null; then echo "sudo      : şifre sormuyor"; else echo "sudo      : şifre soruyor"; fi
echo "Bu dört satırı Mac'teki Claude oturumuna yazın."
