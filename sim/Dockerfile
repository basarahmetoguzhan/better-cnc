# better-cnc simulator image: Debian 13 (trixie) + LinuxCNC 2.9.9 uspace.
#
# Built for linux/arm64, the Raspberry Pi 4's architecture, on an Apple
# Silicon Mac. No realtime kernel, no Mesa board: see sim/README.md for what
# this can and cannot stand in for.
#
#     sim/sim.sh build
#
# LinuxCNC comes from www.linuxcnc.org, not Debian: trixie's own package is
# 2.9.4, the linuxcnc.org repo has 2.9.9 for arm64 (checked 2026-10-19). The
# test rig's live USB runs 2.9.10, and the repo's source references are to
# 2.9.10 (reference/linuxcnc 18c5bb5b); 2.9.10 has no arm64 package.

FROM debian:trixie

ARG DEBIAN_FRONTEND=noninteractive
ARG LINUXCNC_VERSION=1:2.9.9

# The archive signing key, the same way www.linuxcnc.org/linuxcnc-install.sh
# fetches it (recv-key e43b5a8e78cc2927), then checked against the full
# fingerprint that signed dists/trixie/InRelease on 2026-07-11.
ARG LINUXCNC_SIGNER_FPR=0CE3E44425B11F871BBCC6F5E43B5A8E78CC2927

RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates gnupg dirmngr \
 && export GNUPGHOME="$(mktemp -d)" \
 && gpg --batch --keyserver hkps://keyserver.ubuntu.com --recv-keys "$LINUXCNC_SIGNER_FPR" \
 && gpg --batch --list-keys --with-colons "$LINUXCNC_SIGNER_FPR" | grep -q "^fpr:::::::::$LINUXCNC_SIGNER_FPR:" \
 && gpg --batch --export "$LINUXCNC_SIGNER_FPR" > /usr/share/keyrings/linuxcnc.gpg \
 && rm -rf "$GNUPGHOME" \
 && echo "deb [signed-by=/usr/share/keyrings/linuxcnc.gpg] https://www.linuxcnc.org/ trixie base 2.9-uspace" \
      > /etc/apt/sources.list.d/linuxcnc.list \
 && apt-get update \
 && apt-get install -y --no-install-recommends \
      "linuxcnc-uspace=$LINUXCNC_VERSION" python3 procps less \
 && rm -rf /var/lib/apt/lists/*

# The kiosk stack deploy/kiosk.sh installs on the machine (cage, chromium),
# so `sim/sim.sh kiosk` runs it headless; plus grim for screenshots, wtype to
# type into it, xkbcli for the kiosk keymap, systemd-analyze for the unit
# file and shellcheck for the scripts.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      cage chromium grim wtype libxkbcommon-tools fonts-dejavu-core systemd shellcheck \
 && rm -rf /var/lib/apt/lists/*

# A normal user, as on the live USB (`user`). rtapi_app is setuid root in the
# package, so LinuxCNC itself still gets what it needs.
RUN useradd --create-home --uid 1000 --shell /bin/bash cnc
USER cnc
WORKDIR /repo

# The repo is bind-mounted read-only at /repo by sim/sim.sh; nothing is copied
# in, so an edit on the Mac is live in the container.
CMD ["/repo/sim/start.sh"]
