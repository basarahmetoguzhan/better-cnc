"""The message band: plan WP04's scenarios.

Written before WP04 as known gaps (expected failures); WP04 removed the
markers. LinuxCNC's refusal reaches the alarm log as raw English; since
WP04 the error box (GET /api/state status.last_error) says it in Turkish,
naming the axis, the switch and the soft-limit window, keeps it when the
generic "invalid params in linear command" follows on a later poll, and
the MDI history marks the line rejected or done.

Classes run in name order on one LinuxCNC: LimitTripMessage first, while X
is unhomed.
"""

from __future__ import annotations

import time
import unittest
import urllib.request

import harness as h

#: The error box after a target past Z's window (bettercnc.messages; the
#: harness's norm() makes the signs plain).
Z_TEXT = "Z hedefi yumuşak limit dışında (izinli −96.5…−3"

#: The error box after X's left switch trips (bettercnc.messages.
#: limit_trip_text; sim/e2e/test_limits.py checks the same for each switch).
TRIP_TEXT = "X sol uç switch'ine çarptı — makine durdu. «Limitten kurtar» ile switch'ten çıkın."

#: The polls after the refusal that must not change the box: the generic
#: refusal and the joint report can come a poll after the first report
#: (task forwards motion's errors one per cycle).
STICKY_POLLS_S = 2.0


def _render(screen: str = "kontrol") -> str:
    """GET /api/render: the markup the 1 Hz poll swaps in."""
    req = urllib.request.Request(h.UI + "/api/render?screen=" + screen)
    return str(h._json_request(req)["html"])


def _box(vm=None):
    vm = vm or h.state()
    return vm["status"].get("last_error")


class LimitTripMessage(h.Scenario):
    """Unhomed X driven onto its left switch, moved 2 mm out
    (place_switch_ahead): motion trips, "joint 0 on limit switch error"
    (reference/linuxcnc/src/emc/motion/control.c:807-826)."""

    def setUp(self):
        super().setUp()
        h.ensure_on()
        if h.homed()[0]:
            self.fail("needs X unhomed: " + h.describe())
        self.mark = h.alarm_mark()
        h.place_switch_ahead("x-min", 2.0)
        s = h.drive_into_switch("X", -1, 1.0, max_steps=4)
        self.assertNotEqual(int(s.task_state), h.STATE_ON)
        h.wait_alarm("joint 0 on limit switch error", self.mark)

    def test_trip_names_the_switch_in_the_error_box(self):
        # One text for the trip (bettercnc.messages.limit_trip_text, plan
        # WP05): the switch, which names the axis, and the way off.
        self.assertIn(h.norm(TRIP_TEXT), h.norm(h.last_error_text()))
        # The stop the trip made (the jog's POST saw the machine ON, the
        # next snapshot off) is logged as the trip it is, and the box keeps
        # LinuxCNC's report of it - never "komut bu ekrandan gelmedi".
        box = _box()
        time.sleep(STICKY_POLLS_S)
        self.assertEqual(_box(), box)
        new = h.new_alarms(self.mark)
        self.assertIn(TRIP_TEXT, new)
        self.assertFalse(any("komut bu ekrandan gelmedi" in t for t in new), new)
        self.assertNotIn("komut bu ekrandan gelmedi", h.last_error_text())


class MdiResult(h.Scenario):
    """Z0 is above Z's MAX_LIMIT -3: motion refuses the move after the
    line was sent, "Linear move on line 0 would exceed Z's positive limit",
    the same for joint 2, then "invalid params in linear command"
    (command.c:213-224, :259-268, :1002-1003). Until WP04 the last of them
    won the message line."""

    def setUp(self):
        super().setUp()
        h.ensure_homed()
        self.mark = h.alarm_mark()
        self.before = h.position()
        h.mdi("G0 Z0")
        h.wait_alarm("would exceed Z's positive limit", self.mark)   # raw, in the log

    def test_soft_limit_refusal_is_turkish_and_the_line_rejected(self):
        # LinuxCNC's own word first: nothing moved, the interpreter is idle,
        # and the refusal left the machine ON (bettercnc.messages.is_refusal:
        # a refusal never explains a stop).
        s = h.stat()
        self.assertEqual(int(s.task_state), h.STATE_ON, h.describe(s))
        self.assertEqual(s.interp_state, h._lc().INTERP_IDLE, h.describe(s))
        self.assertTrue(h.near(h.position(s), self.before), h.describe(s))
        vm = h.state()
        self.assertIn(h.norm(Z_TEXT), h.norm(h.last_error_text(vm)))
        entry = h.mdi_entry("G0 Z0", vm)
        self.assertIsNotNone(entry)
        self.assertEqual(entry.get("status"), "rejected", entry)
        self.assertIn(h.norm(Z_TEXT), h.norm(entry.get("note")))
        # The generic refusal arrives and stays in the log, raw; the box
        # keeps the specific error over the next polls.
        h.wait_alarm("invalid params in linear command", self.mark)
        box = _box()
        deadline = time.monotonic() + STICKY_POLLS_S
        while time.monotonic() < deadline:
            self.assertEqual(_box(), box)
            time.sleep(0.25)
        self.assertNotIn("Geçersiz doğrusal", h.last_error_text())
        markup = _render()
        self.assertIn('class="errbox-text"', markup)
        self.assertIn("LinuxCNC: Linear move on line 0 would exceed Z&#x27;s positive limit", markup)
        self.assertIn('data-action="ack_error" data-value="{}"'.format(box["id"]), markup)

    def test_the_next_good_line_is_done(self):
        h.mdi("G0 X50")
        h.wait_position((50.0, None, None), h.move_time(50.0, 10.0))

        def done():
            entry = h.mdi_entry("G0 X50")
            return entry if entry and entry.get("status") == "done" else None
        h.wait_until(done, 2.0, "the G0 X50 history entry to read done",
                     detail=lambda: str(h.mdi_history()[:3]))
        self.assertEqual(h.mdi_entry("G0 Z0").get("status"), "rejected")

    def test_tamam_clears_the_box_and_only_that_one(self):
        box = _box()
        self.assertIsNotNone(box)
        h.post("ack_error", int(box["id"]) + 1)          # not the one on the screen
        self.assertEqual(_box(), box)
        h.post("ack_error", box["id"])
        self.assertIsNone(_box())
        self.assertNotIn('class="errbox"', _render())


if __name__ == "__main__":
    unittest.main()
