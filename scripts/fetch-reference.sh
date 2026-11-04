#!/bin/bash
# Fetch the read-only upstream sources into reference/ (gitignored), at the
# exact commits every file:line citation in this repo was checked against.
#
#   scripts/fetch-reference.sh          or   make reference
#
# reference/linuxcnc           LinuxCNC, branch 2.9 (2.9.10)   ~340 MB
# reference/hostmot2-firmware  Mesa's hostmot2 firmware        ~6 MB
#
# Shallow (one commit each) and pinned by full hash: cloning the 2.9 branch
# instead would bring whatever 2.9 has become, and the line numbers cited
# across docs/ and ui/ would drift. Without reference/ the suite still runs;
# 21 tests that check the fakes, the citations and these pins against the source skip.
#
# A tree already there at the pinned commit is left alone; one at another
# commit is reported and left alone too - move it away to fetch again.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
REF="$REPO/reference"

# name  url  commit (what docs/00-upstream-reference-map.md records)
PINS=(
  "linuxcnc https://github.com/LinuxCNC/linuxcnc.git 18c5bb5b1cd9ac1e63cc5e40998db712ad8ff50d"
  "hostmot2-firmware https://github.com/LinuxCNC/hostmot2-firmware.git c15c0c3c0af826fd2996aa1eddb8669f154bb2a5"
)

mkdir -p "$REF"
status=0
for pin in "${PINS[@]}"; do
  read -r name url commit <<< "$pin"
  dir="$REF/$name"
  if [ -d "$dir/.git" ]; then
    have="$(git -C "$dir" rev-parse HEAD 2>/dev/null || echo "?")"
    if [ "$have" = "$commit" ]; then
      echo "$name: zaten var ($commit)"
    else
      echo "$name: $dir başka bir commit'te ($have, beklenen $commit) — dokunulmadı;" \
           "yeniden indirmek için klasörü kaldırın" >&2
      status=1
    fi
    continue
  fi
  if [ -e "$dir" ]; then
    echo "$name: $dir var ama git deposu değil — dokunulmadı" >&2
    status=1
    continue
  fi
  echo "$name: $commit indiriliyor…"
  tmp="$dir.partial"
  rm -rf "$tmp"
  git init -q "$tmp"
  git -C "$tmp" remote add origin "$url"
  # GitHub serves a commit by its hash; one commit, no history.
  git -C "$tmp" fetch -q --depth 1 origin "$commit"
  git -C "$tmp" -c advice.detachedHead=false checkout -q FETCH_HEAD
  [ "$(git -C "$tmp" rev-parse HEAD)" = "$commit" ] || { echo "$name: indirilen commit farklı" >&2; exit 1; }
  mv "$tmp" "$dir"
  echo "$name: hazır ($(du -sh "$dir" | cut -f1))"
done
exit "$status"
