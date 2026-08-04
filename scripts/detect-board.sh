#!/usr/bin/env bash
#
# detect-board.sh — find and interrogate the Zhulong V2.0 / Mesa 7i96-class
#                   Ethernet board, and archive the results into the repo.
#
# ############################################################################
# # READ-ONLY. This script NEVER writes to the board.                        #
# #                                                                          #
# # It issues only two mesaflash operations, both of which read:             #
# #     --readhmid     dump the HostMot2 IDROM / module / pin descriptors    #
# #     --info         dump board info                                       #
# #                                                                          #
# # It must never be extended to call --write, --reload, --verify, --fix,    #
# # --set, --recover, or anything else that touches the FPGA flash. On a     #
# # clone board with unknown pin assignments, an ill-advised flash write is  #
# # not recoverable without a JTAG programmer and the vendor's bitfile.      #
# # If you need a write operation, put it in a separate, clearly named       #
# # script so it can never be run by accident.                               #
# ############################################################################
#
# Usage:
#   scripts/detect-board.sh [IP]
#
#   IP   optional. If omitted, the candidate list below is tried in order.
#
# Environment overrides:
#   MESAFLASH_DEVICE   device string passed to mesaflash --device
#                      (default "ETHER"; see NOTE below)
#   PING_TIMEOUT       seconds to wait for the single ping (default 1)
#
# NOTE on --device: mesaflash is not vendored in reference/, so its exact
# accepted --device strings could not be verified against source. "ETHER" is
# tried first; if mesaflash rejects it the script automatically retries with
# "7I96". Override with MESAFLASH_DEVICE to force one.
#
# Target platform: Raspberry Pi OS / Debian (uses iputils ping semantics,
# where -W is a timeout in seconds).

set -euo pipefail

# --- Configuration ----------------------------------------------------------

# Tried in order when no IP is given on the command line.
#   10.10.10.10    the alternative suggested in hm2_eth(9)
#   192.168.1.121  the Mesa factory default per hm2_eth(9)
#   192.168.1.10   } commonly seen on clone boards; unconfirmed for the Zhulong
#   192.168.0.10   }
readonly DEFAULT_CANDIDATES=(
    "10.10.10.10"
    "192.168.1.121"
    "192.168.1.10"
    "192.168.0.10"
)

# Device strings to try, in order, if MESAFLASH_DEVICE is not set.
readonly DEVICE_CANDIDATES=("ETHER" "7I96")

PING_TIMEOUT="${PING_TIMEOUT:-1}"

# --- Paths ------------------------------------------------------------------

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly SCRIPT_DIR
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
readonly REPO_ROOT
readonly DUMP_DIR="${REPO_ROOT}/docs/board-dumps"

# --- Output helpers ---------------------------------------------------------

info()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
ok()    { printf '\033[1;32m  ✓\033[0m %s\n' "$*"; }
warn()  { printf '\033[1;33m  !\033[0m %s\n' "$*" >&2; }
fail()  { printf '\033[1;31mERROR:\033[0m %s\n' "$*" >&2; }

# --- Preflight --------------------------------------------------------------

if ! command -v mesaflash >/dev/null 2>&1; then
    fail "mesaflash not found in PATH."
    echo
    echo "Install it first. On Debian/Raspberry Pi OS with the LinuxCNC archive"
    echo "configured this is typically:"
    echo
    echo "    sudo apt install mesaflash"
    echo
    echo "If your LinuxCNC install did not bring it in, build it from"
    echo "https://github.com/LinuxCNC/mesaflash — it is a separate project and"
    echo "is deliberately not vendored into this repo."
    exit 2
fi

mkdir -p "${DUMP_DIR}"

# --- Argument handling ------------------------------------------------------

candidates=()
if [[ $# -gt 1 ]]; then
    fail "too many arguments."
    echo "Usage: $(basename "$0") [IP]" >&2
    exit 2
elif [[ $# -eq 1 ]]; then
    candidates=("$1")
    info "Probing user-supplied address: $1"
else
    candidates=("${DEFAULT_CANDIDATES[@]}")
    info "No address given. Trying ${#candidates[@]} default candidates."
fi

if [[ -n "${MESAFLASH_DEVICE:-}" ]]; then
    devices=("${MESAFLASH_DEVICE}")
else
    devices=("${DEVICE_CANDIDATES[@]}")
fi

timestamp="$(date +%Y%m%d-%H%M%S)"
readonly timestamp

# --- Core -------------------------------------------------------------------

# Ping a host once. Returns 0 if it answers.
host_responds() {
    local ip="$1"
    ping -c 1 -W "${PING_TIMEOUT}" -- "${ip}" >/dev/null 2>&1
}

# Run one read-only mesaflash operation, trying each device string in turn.
# On success, writes the captured output to $4 and echoes the device string
# that worked on stdout.
#
#   $1 ip   $2 operation flag (e.g. --readhmid)   $3 label   $4 output file
run_mesaflash_op() {
    local ip="$1" op="$2" label="$3" outfile="$4"
    local dev out rc

    for dev in "${devices[@]}"; do
        set +e
        out="$(mesaflash --addr "${ip}" --device "${dev}" "${op}" 2>&1)"
        rc=$?
        set -e

        if [[ ${rc} -eq 0 && -n "${out}" ]]; then
            {
                echo "# better-cnc board dump"
                echo "# operation : mesaflash --addr ${ip} --device ${dev} ${op}"
                echo "# label     : ${label}"
                echo "# host      : $(hostname)"
                echo "# date      : $(date '+%Y-%m-%d %H:%M:%S %z')"
                echo "# NOTE      : read-only operation, nothing was written to the board"
                echo "#"
                echo "${out}"
            } >"${outfile}"
            echo "${dev}"
            return 0
        fi

        warn "${op} with --device ${dev} failed (exit ${rc})"
        if [[ -n "${out}" ]]; then
            printf '      %s\n' "${out}" | head -n 5 >&2
        fi
    done

    return 1
}

found_any=0
found_ips=()

for ip in "${candidates[@]}"; do
    info "Pinging ${ip} (timeout ${PING_TIMEOUT}s)..."

    if ! host_responds "${ip}"; then
        warn "no reply from ${ip}"
        continue
    fi
    ok "${ip} responds to ping"

    hmid_file="${DUMP_DIR}/readhmid-${ip}-${timestamp}.txt"
    info_file="${DUMP_DIR}/info-${ip}-${timestamp}.txt"

    if working_dev="$(run_mesaflash_op "${ip}" "--readhmid" "HostMot2 ID / pin descriptors" "${hmid_file}")"; then
        ok "readhmid saved to ${hmid_file#"${REPO_ROOT}"/} (--device ${working_dev})"
        found_any=1
        found_ips+=("${ip}")
    else
        warn "${ip} answers ping but mesaflash --readhmid failed with every device string"
        warn "the host at ${ip} may not be the board at all"
        continue
    fi

    if run_mesaflash_op "${ip}" "--info" "board info" "${info_file}" >/dev/null; then
        ok "info saved to ${info_file#"${REPO_ROOT}"/}"
    else
        warn "--info failed for ${ip} (not fatal; readhmid is the important one)"
    fi

    # Surface the single most important line immediately.
    echo
    info "Board name line(s) from ${ip}:"
    grep -i -E 'board *name|^Board' "${hmid_file}" || warn "no obvious board-name line found — read the dump manually"
    echo
done

# --- Result -----------------------------------------------------------------

if [[ ${found_any} -eq 0 ]]; then
    fail "No board found on any candidate address."
    cat >&2 <<'HINTS'

Check, in this order:

  1. Power. Is 24V actually present at the board's power terminals, and is the
     polarity correct? Measure it — do not trust the supply's label.

  2. The right RJ45. A 7i96-class board has more than one RJ45-looking socket.
     Only the Ethernet port has link/activity LEDs in the jack. The others are
     RS-422 Smart Serial ports and will never answer a ping. If you are plugged
     into a Smart Serial port, nothing else in this list matters.

  3. The W1/W2 jumpers. On a Mesa 7i96 these select the boot/IP behaviour. We
     have no vendor documentation for the Zhulong, so their exact function is
     UNCONFIRMED — but if the board is silent, photograph their current
     positions, then try the other combinations one at a time, power-cycling
     between each, and re-run this script.

  4. The Pi's own interface. The Pi needs a static address on the same subnet
     as the board, on the interface the board is plugged into. Verify with:

         ip -brief address show
         ip route get 10.10.10.10

     If the board is 10.10.10.10, the Pi wants something like 10.10.10.1/24.
     A Pi with only a DHCP address from your house router cannot reach it.

  5. Link state. `ip -brief link show` — the interface must be UP with a
     carrier. No carrier means a cable, jack, or power problem, not a
     configuration problem.

If you know the board's address and it is not in the default list, pass it
explicitly:

    scripts/detect-board.sh 192.168.7.2

HINTS
    exit 1
fi

echo
ok "Found ${#found_ips[@]} board(s): ${found_ips[*]}"
info "Dumps written to ${DUMP_DIR#"${REPO_ROOT}"/}/"
echo
echo "Next: work through the decision tree in docs/02-board-bringup.md step 6,"
echo "using the readhmid dump you just captured. Commit the dump to the repo."
