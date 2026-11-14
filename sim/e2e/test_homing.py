"""Homing (plan WP07): one axis at a time, the question before a homing
again, and DURDUR during a homing - against LinuxCNC 2.9.9, checked through
linuxcnc.stat, not the UI's picture of it.

One scenario on one fresh LinuxCNC, in steps that build on each other
(harness.OrderedSteps): the UI cannot unhome (plan WP20), so the steps that
need an unhomed machine come first.

  - X alone while Z is unhomed: the UI asks first (a lowered tool would be
    dragged along) and «Vazgeç» moves nothing;
  - Z alone, then Y alone: no question - Z is the tool axis, and then it is
    homed. Only that joint homes (do_home_one_joint with a sequence that is
    not negative, reference/linuxcnc/src/emc/motion/homing.c:265-281,
    :342-353; the rig's HOME_SEQUENCE is Z 0, Y 1, X 2), and motion stays
    in free mode until every joint is homed (homing.c:1400,
    reference/linuxcnc/src/emc/motion/control.c:261-263);
  - Y again, and Home All with joints homed: the UI asks first - AXIS does
    too (reference/linuxcnc/src/emc/usr_intf/axis/scripts/axis.py:
    2693-2701, :2708-2728) - and nothing moves before «Evet»; an «Evet»
    with a wrong token sends nothing either, nor one right after the
    question (a double tap's second press; the question stands);
  - DURDUR while Z searches during a Home All: every joint unhomed (a
    homing unhomes its joints as it starts, homing.c:401-410, and abort
    cancels the one homing, reference/linuxcnc/src/emc/motion/command.c:
    509-520); a Home All then asks nothing and completes. Stopped while Z
    searches, away from its switch (harness.wait_homing_search).
"""

from __future__ import annotations

import time
import unittest

import harness as h

#: The questions on the rig (motionrules.home_question; the profile's switch
#: labels, the INI's HOME_SEQUENCE and HOME_SEARCH_VEL signs).
ROUTE = "Z üst switch'ine (Z+), sonra Y ön uç switch'ine (Y−), sonra X sol uç switch'ine (X−)"
REHOME_ALL = "Yeniden referans: " + ROUTE + " gidecek — yol açık mı?"
X_ALONE = ("Z referanssız: takım aşağıdaysa X hareketi çarpabilir. X sol uç switch'ine (X−) "
           "gidecek — yol açık mı?")
REHOME_Y = "Y yeniden referanslanacak: Y ön uç switch'ine (Y−) gidecek — yol açık mı?"
#: motionrules.HOME_BUSY: the homing card's reason while a joint homes.
HOME_BUSY = "Referans alma sürüyor — durdurmak için «Durdur»."

#: How long nothing may move after a press that only asks.
QUIET_S = 0.8


def band(vm=None):
    """The confirmation band of GET /api/state (viewmodel.confirm_band)."""
    return (vm or h.state()).get("confirm")


class Homing(h.OrderedSteps):

    def assert_still(self, before, what):
        """Nothing homing, nothing moved since `before` (a stat), for
        QUIET_S."""
        deadline = time.monotonic() + QUIET_S
        while time.monotonic() < deadline:
            s = h.stat()
            self.assertFalse(any(h.homing(s)), what + ": " + h.describe(s))
            self.assertTrue(h.near(h.position(s), h.position(before)), what + ": " + h.describe(s))
            time.sleep(0.05)

    def home_one(self, axis, want_homed, again=False):
        """Wait for `axis`'s homing to start and end; check where it left
        the joints. `again`: the joint was homed before, so only its homing
        flag says the homing started."""
        j = h.JOINT_OF_AXIS[axis]
        if again:
            started = lambda: h.homing()[j]          # noqa: E731
        else:
            started = lambda: h.homing()[j] or h.homed()[j]   # noqa: E731
        h.wait_until(started, 2.0, "{} to start homing".format(axis), detail=h.describe)
        s = h.wait_homing_over()
        self.assertEqual(h.homed(s), want_homed, h.describe(s))
        self.assertAlmostEqual(h.position(s)[j], h.HOME_POSITION[j], delta=h.POSITION_TOL)
        return s

    def test_01_x_alone_while_z_is_unhomed_asks_first_and_vazgec_moves_nothing(self):
        before = h.ensure_on()
        self.assertFalse(any(h.homed(before)), "needs a LinuxCNC never homed: " + h.describe())
        card = h.state()["kontrol"]["homing"]
        self.assertTrue(card["home_enabled"], card)
        self.assertTrue(card["axis_buttons"]["X"]["enabled"], card)
        h.post("home_joint", "X")
        b = band()
        self.assertTrue(b and b["token"], "no question: {}".format(b))
        self.assertEqual(h.norm(b["text"]), h.norm(X_ALONE))
        self.assert_still(before, "moved before «Evet»")
        h.post("cancel_confirm")
        self.assertIsNone(band())
        self.assert_still(before, "moved after «Vazgeç»")

    def test_02_z_alone_asks_nothing_and_homes_only_z(self):
        before = h.stat()
        h.post("home_joint", "Z")
        self.assertIsNone(band(), "the tool axis first: no question")
        h.wait_until(lambda: h.homing()[2], 2.0, "Z to start homing", detail=h.describe)
        vm = h.state()
        self.assertEqual(h.norm(vm["status"]["message"]),
                         h.norm("Referans alma başlatıldı: yalnız Z."))
        card = vm["kontrol"]["homing"]
        # While it homes the card is off, says why, and what DURDUR does.
        self.assertFalse(card["home_enabled"], card)
        self.assertEqual(h.norm(card["home_disabled_reason"]), h.norm(HOME_BUSY))
        self.assertFalse(any(b["enabled"] for b in card["axis_buttons"].values()), card)
        self.assertIn("Limitten kurtar", card["stop_note"])
        s = self.home_one("Z", (False, False, True))
        self.assertTrue(h.near(h.position(s)[:2], h.position(before)[:2]),
                        "X or Y moved: " + h.describe(s))
        self.assertEqual(s.motion_mode, h._lc().TRAJ_MODE_FREE, h.describe(s))

    def test_03_y_alone_with_z_homed_asks_nothing(self):
        x_before = h.position()[0]
        h.post("home_joint", "Y")
        self.assertIsNone(band())
        s = self.home_one("Y", (False, True, True))
        self.assertAlmostEqual(h.position(s)[0], x_before, delta=h.POSITION_TOL)
        self.assertEqual(s.motion_mode, h._lc().TRAJ_MODE_FREE, h.describe(s))

    def test_04_y_again_is_asked_about_and_evet_homes_it_again(self):
        before = h.stat()
        h.post("home_joint", "Y")
        b = band()
        self.assertTrue(b and b["token"], "no question: {}".format(b))
        self.assertEqual(h.norm(b["text"]), h.norm(REHOME_Y))
        # «Evet» at once - a double tap whose second press lands on the band
        # - is no answer: nothing moves and the question stands
        # (app.CONFIRM_MIN_S, review of plan WP07).
        self.assertFalse(b["ready"], b)
        h.post("confirm", b["token"])
        self.assert_still(before, "moved on an «Evet» right after the question")
        self.assertEqual((band() or {}).get("token"), b["token"], "the question went")
        self.assertTrue(h.confirm_if_asked(), "no question to answer")
        self.assertIsNone(band())
        self.home_one("Y", (False, True, True), again=True)

    def test_05_home_all_with_joints_homed_is_asked_about_then_completes(self):
        before = h.stat()
        h.post("home_all")
        b = band()
        self.assertTrue(b and b["token"], "no question: {}".format(b))
        self.assertEqual(h.norm(b["text"]), h.norm(REHOME_ALL))
        self.assert_still(before, "moved before «Evet»")
        # An «Evet» for no question standing sends nothing, and says so.
        h.post("confirm", "0" * 16)
        b = band()
        self.assertTrue(b and b.get("refused"), "no refusal: {}".format(b))
        self.assert_still(before, "moved after a wrong «Evet»")
        h.post("home_all")
        self.assertTrue(h.confirm_if_asked(), "no question the second time")
        h.wait_until(lambda: all(h.homed()) and not any(h.homing()), h.HOME_TIMEOUT_S,
                     "the confirmed Home All to finish", detail=h.describe)
        s = h.wait_still()
        self.assertTrue(h.near(h.position(s), h.HOME_POSITION), h.describe(s))
        # Every joint homed: motion went to teleop by itself (homing.c:1400).
        self.assertEqual(s.motion_mode, h._lc().TRAJ_MODE_TELEOP, h.describe(s))

    def test_06_durdur_during_home_all_unhomes_every_joint_then_home_all_completes(self):
        h.post("home_all")
        self.assertTrue(h.confirm_if_asked(), "a homed machine must be asked about")
        h.wait_homing_search(2)
        card = h.state()["kontrol"]["homing"]
        self.assertEqual(h.norm(card["home_disabled_reason"]), h.norm(HOME_BUSY))
        h.post("stop")
        s = h.wait_homing_over(2.0)
        self.assertEqual(h.homed(s), (False, False, False),
                         "a homing unhomes its joints as it starts: " + h.describe(s))
        self.assertFalse(any(h.hard_limits(s)[j][k] for j in range(3) for k in range(2)),
                         "stopped away from the switch: " + h.describe(s))
        order = h.home_all()        # nothing homed: no question
        self.assertEqual(order, [2, 1, 0])
        self.assertTrue(h.near(h.position(h.wait_still()), h.HOME_POSITION), h.describe())


if __name__ == "__main__":
    unittest.main()
