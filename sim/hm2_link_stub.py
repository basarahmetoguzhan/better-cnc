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
    setp hm2_7i92.0.io_error 1             # driver I/O error flag
    sets watchdog TRUE                     # watchdog bite: has_bit is on the
                                           # `watchdog` net, which trips the
                                           # estop latch, as on the rig
    setp hm2_7i92.0.sim-link-down 1        # the link stays down: has_bit is
                                           # set again every few ms, so a
                                           # reset cannot hold (cable still out)

The UI's «Yazılım sıfırla» clears has_bit and io_error itself with halcmd
(adapter.LinuxCncAdapter._clear_link_faults), as on the rig.

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
    while True:
        if comp["sim-link-down"]:
            # The real watchdog bites again within its timeout (5 ms by
            # default, watchdog.c:172) when the link is still down.
            comp["watchdog.has_bit"] = 1
            time.sleep(0.005)
        else:
            time.sleep(0.1)


if __name__ == "__main__":
    sys.exit(main())
