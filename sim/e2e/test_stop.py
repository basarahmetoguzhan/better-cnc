"""DURDUR (stop = abort) and the stop rail: plan WP03's scenarios.

POST "stop" is what the rail's DURDUR and Esc send. app.Session sends the
first abort at once on a command channel of its own, without the session
lock (adapter.LinuxCncAdapter.stop_motion), and the second under it
(stop_followup). Stop is abort: it ends a teleop or free jog, cancels
homing and a coordinated move (reference/linuxcnc/src/emc/motion/
command.c:493-554), and task takes EMC_TASK_ABORT in every state
(emctaskmain.cc:832, :944).

Written before WP03 as known gaps (expected failures); WP03 removed the
markers. Classes run in name order on one LinuxCNC: StopHoming first,
while nothing is homed (a first Home All asks no confirmation, plan WP07).
"""

from __future__ import annotations

import re
import threading
import time
import unittest
import urllib.request
from typing import Any, Dict

import harness as h

#: stop -> current_vel 0, from the POST (the plan's figure for WP03).
STOP_S = 0.5

#: The screens the rail must be on (bettercnc.viewmodel.SCREENS).
SCREENS = ("kontrol", "program", "probing", "takimlar", "alarmlar", "makine")


def _moving() -> bool:
    return h.stat().current_vel > 0


def _still() -> object:
    s = h.stat()
    return s if s.current_vel == 0 else None


class _Post(object):
    """POST an action from another thread, so LinuxCNC is watched while the
    request is in flight: the first abort goes out as the request arrives,
    while its answer waits for the stop's second half (a bounded wait) and
    a re-render - 0.38-0.43 s for an MDI G0 in the simulator (2026-11-13).
    The plan's 0.5 s runs from the POST to the machine standing still, not
    to the answer."""

    def __init__(self, action: str) -> None:
        self.error: Dict[str, Any] = {}
        self.thread = threading.Thread(target=self._run, args=(action,), daemon=True)
        self.thread.start()

    def _run(self, action: str) -> None:
        try:
            h.post(action)
        except Exception as exc:  # re-raised by join(), in the test's thread
            self.error["exc"] = exc

    def join(self) -> None:
        self.thread.join(h.HTTP_TIMEOUT_S)
        if self.thread.is_alive():
            raise AssertionError("the POST never answered")
        if self.error:
            raise self.error["exc"]


def _page(screen: str) -> str:
    """GET /?screen=...: the whole page, as the browser loads it."""
    req = urllib.request.Request(h.UI + "/?screen=" + screen)
    with urllib.request.urlopen(req, timeout=h.HTTP_TIMEOUT_S) as resp:
        return resp.read().decode("utf-8")


class StopHoming(h.Scenario):

    def setUp(self):
        super().setUp()
        h.ensure_on()
        if any(h.homed()):
            self.fail("needs a LinuxCNC never homed: " + h.describe())

    def test_stop_cancels_home_all_and_home_all_then_completes(self):
        h.post("home_all")
        # Z homes first: stopped while it searches, away from its switch
        # (harness.wait_homing_search says why not later).
        h.wait_homing_search(2)
        t0 = time.monotonic()
        stop = _Post("stop")
        h.wait_until(lambda: not any(h.homing()), STOP_S, "homing to stop", start=t0,
                     every=0.01, detail=h.describe)
        stop.join()
        s = h.wait_homing_over(2.0)
        self.assertFalse(all(h.homed(s)), "stop must leave joints unhomed: " + h.describe(s))
        order = h.home_all()
        self.assertEqual(sorted(order), [0, 1, 2])
        self.assertTrue(h.near(h.position(h.wait_still()), h.HOME_POSITION), h.describe())


class StopMotion(h.Scenario):

    def setUp(self):
        super().setUp()
        h.ensure_homed()

    def test_stop_ends_a_50_mm_jog(self):
        start_x = h.position()[0]
        h.jog("X", 1, 50)
        h.wait_until(_moving, 2.0, "the jog to start", detail=h.describe)
        time.sleep(0.5)
        t0 = time.monotonic()
        stop = _Post("stop")
        s = h.wait_until(_still, STOP_S, "the jog to stop", start=t0, every=0.01,
                         detail=h.describe)
        stop.join()
        self.assertLess(h.position(s)[0], start_x + 49.0, h.describe(s))
        s = h.wait_still()
        self.assertLess(h.position(s)[0], start_x + 49.0, "stays stopped: " + h.describe(s))

    def test_stop_ends_an_mdi_move(self):
        h.mdi("G0 X200")
        h.wait_until(_moving, 3.0, "the move to start", detail=h.describe)
        time.sleep(0.5)
        t0 = time.monotonic()
        stop = _Post("stop")
        s = h.wait_until(_still, STOP_S, "the move to stop", start=t0, every=0.01,
                         detail=h.describe)
        stop.join()
        self.assertLess(h.position(s)[0], 199.0, h.describe(s))
        h.wait_until(lambda: h.stat().interp_state == h._lc().INTERP_IDLE, 1.0, "interp IDLE",
                     detail=h.describe)


class StopRail(h.Scenario):
    """The rail on the page the real server sends, and DURDUR's answer when
    nothing moves."""

    def test_the_rail_is_on_every_screen_outside_the_polled_part(self):
        for screen in SCREENS:
            with self.subTest(screen=screen):
                page = _page(screen)
                self.assertEqual(page.count('<aside id="rail"'), 1)
                rail = page[page.index('<aside id="rail"'):page.index("</aside>")]
                self.assertLess(page.index('<aside id="rail"'), page.index('<div id="app">'))
                for action in ("software_estop", "stop"):
                    button = re.search(r'<button [^>]*data-action="{}"[^>]*>'.format(action), rail)
                    self.assertIsNotNone(button, action)
                    self.assertIn('data-instant="1"', button.group(0))
                    self.assertNotIn("disabled", button.group(0))

    def test_durdur_with_nothing_moving_says_stopped(self):
        mark = h.alarm_mark()
        h.post("stop")
        vm = h.state()
        self.assertEqual(vm["status"]["message"], "Durduruldu.")
        self.assertIn("Durdur gönderildi", h.new_alarms(mark, vm))
        self.assertEqual(h.task_state_name(), "ON", h.describe())


if __name__ == "__main__":
    unittest.main()
