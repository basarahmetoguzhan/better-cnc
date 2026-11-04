#!/usr/bin/env python3
"""SIMULATOR ONLY: stands in for the three hm2_eth link-health HAL items the
UI reads, so LinuxCncAdapter._read_hal runs against real HAL pins.

There is no board in the simulator. The values here are not readings of
anything: they start "healthy" and change only when you change them.

Same names, types and directions as the driver creates on the rig:

    hm2_7i92.0.packet-error-total   u32 pin, HAL_IO    hm2_eth.c:1558-1561
    hm2_7i92.0.watchdog.has_bit     bit pin, HAL_IO    watchdog.c:140-144
    hm2_7i92.0.io_error             bit param, HAL_RW  hostmot2.c:1446

(paths under reference/linuxcnc/src/hal/drivers/mesa-hostmot2/).

Fault injection, from `sim/sim.sh halcmd ...`:

    setp hm2_7i92.0.packet-error-total 7   # link errors counted
    setp hm2_7i92.0.io_error 1             # driver I/O error flag, by hand
    sets watchdog TRUE                     # a bite by hand: has_bit is on the
                                           # `watchdog` net, which trips the
                                           # estop latch, as on the rig
    setp hm2_7i92.0.sim-link-down 1        # the cable is out: what the real
                                           # driver does, below
    setp hm2_7i92.0.sim-link-down 0        # the cable is back

What the real driver does on a cable pull, as this stub plays it
(reference/linuxcnc/src/hal/drivers/mesa-hostmot2/, audit 2026-10-21):

  - after packet_error_limit misses the driver sets io_error, and nothing
    else (hm2_eth.c:874-883); from then on it reads and writes nothing
    (hostmot2.c, watchdog.c:38), so has_bit stays 0, the estop latch does
    not trip and LinuxCNC goes on saying ON. The board's own watchdog bit
    meanwhile and dropped the outputs.
  - io_error cleared while the cable is still out: back within a few
    servo periods (sim-link-down keeps setting it).
  - io_error cleared with the cable back: the next read finds the bite the
    board recorded and raises has_bit (watchdog.c:48-51) - now the latch
    trips. Clearing has_bit is what resets the board (watchdog.c:259-271).

The UI's «Yazılım sıfırla» plays the recovery itself with halcmd
(adapter.LinuxCncAdapter.software_estop_reset): ESTOP first, io_error
cleared, the has_bit that follows cleared, unhome, reset, ON. The first
version of this stub held has_bit instead of io_error - the opposite of the
driver - and the recovery it passed in sim/ would have done nothing on the
rig with the machine ON.

Loaded by sim/test-rig-sim.hal with `loadusr -Wn hm2_7i92.0`. Exits on the
SIGTERM that `halcmd unload all` sends when LinuxCNC shuts down.
"""

import signal
import sys
import time

import hal

COMPONENT = "hm2_7i92.0"


def main() -> int:
    comp = hal.component(COMPONENT)
    comp.newpin("packet-error-total", hal.HAL_U32, hal.HAL_IO)
    comp.newpin("watchdog.has_bit", hal.HAL_BIT, hal.HAL_IO)
    comp.newparam("io_error", hal.HAL_BIT, hal.HAL_RW)
    # SIMULATOR ONLY, not a driver item: holds the link down.
    comp.newparam("sim-link-down", hal.HAL_BIT, hal.HAL_RW)
    comp.ready()
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    bite_recorded = False   # the board's watchdog bit while unreachable
    while True:
        if comp["sim-link-down"]:
            # hm2_eth.c:883, within packet_error_limit misses (5 ms at 1 kHz).
            comp["io_error"] = 1
            bite_recorded = True
            time.sleep(0.005)
        else:
            if bite_recorded and not comp["io_error"]:
                # The first read after the clear sees the bite (watchdog.c:48-51).
                comp["watchdog.has_bit"] = 1
                bite_recorded = False
            time.sleep(0.005)


if __name__ == "__main__":
    sys.exit(main())
