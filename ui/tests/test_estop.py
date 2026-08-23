"""Estop: hardware and software are separate, and software cannot clear hardware.

The property under test is a safety property, so it is asserted at three levels:
the state type, the adapter, and the rendered HTML.
"""

from __future__ import annotations

import unittest

from helpers import html, session, view

from bettercnc.capabilities import Capability
from bettercnc.profiles import load_profile
from bettercnc.state import DrivePower, EstopState, initial_estop
from bettercnc.viewmodel import estop_panel


class TestEstopStateType(unittest.TestCase):
    def test_software_reset_cannot_clear_a_hardware_cut(self):
        """The central assertion of this file."""
        e = EstopState(DrivePower.CUT, software_disabled=True, hardware_input_present=True)
        after = e.software_reset()
        self.assertIs(after.drive_power, DrivePower.CUT)
        self.assertTrue(after.software_disabled)

    def test_repeated_software_resets_never_clear_hardware(self):
        e = EstopState(DrivePower.CUT, True, True)
        for _ in range(25):
            e = e.software_reset()
        self.assertIs(e.drive_power, DrivePower.CUT)
        self.assertTrue(e.software_disabled)

    def test_software_reset_works_when_hardware_is_live(self):
        e = EstopState(DrivePower.LIVE, software_disabled=True, hardware_input_present=True)
        after = e.software_reset()
        self.assertFalse(after.software_disabled)
        self.assertIs(after.drive_power, DrivePower.LIVE)

    def test_software_estop_does_not_cut_drive_power(self):
        """A GUI estop stops motion. It does not remove power from anything."""
        e = EstopState(DrivePower.LIVE, False, True)
        after = e.software_estop()
        self.assertTrue(after.software_disabled)
        self.assertIs(after.drive_power, DrivePower.LIVE)

    def test_hardware_cut_also_disables_software(self):
        e = EstopState(DrivePower.LIVE, False, True)
        after = e.hardware_chain_opened()
        self.assertIs(after.drive_power, DrivePower.CUT)
        self.assertTrue(after.software_disabled)

    def test_closing_the_chain_does_not_auto_enable(self):
        """Restoring power must not silently re-enable the machine."""
        e = EstopState(DrivePower.CUT, True, True).hardware_chain_closed()
        self.assertIs(e.drive_power, DrivePower.LIVE)
        self.assertTrue(e.software_disabled)

    def test_drive_power_is_never_software_clearable(self):
        for dp in DrivePower:
            with self.subTest(drive_power=dp.value):
                e = EstopState(dp, True, True)
                self.assertFalse(e.drive_power_clearable_by_software)

    def test_both_states_block_motion_independently(self):
        self.assertTrue(EstopState(DrivePower.CUT, False, True).blocks_motion)
        self.assertTrue(EstopState(DrivePower.LIVE, True, True).blocks_motion)
        self.assertFalse(EstopState(DrivePower.LIVE, False, True).blocks_motion)


class TestNoHardwareInputMeansUnknown(unittest.TestCase):
    """The test rig has no estop input, so drive power is UNKNOWN - not LIVE.

    Reporting LIVE would be inventing a measurement, and a green light on an
    unsensed safety chain is worse than an honest question mark.
    """

    def test_initial_state_without_input_is_unknown(self):
        e = initial_estop(hardware_input_present=False)
        self.assertIs(e.drive_power, DrivePower.UNKNOWN)

    def test_initial_state_starts_software_disabled(self):
        self.assertTrue(initial_estop(False).software_disabled)
        self.assertTrue(initial_estop(True).software_disabled)

    def test_test_rig_profile_has_no_estop_input(self):
        self.assertFalse(load_profile("test-rig").has(Capability.ESTOP_INPUT))

    def test_test_rig_reports_unknown_drive_power(self):
        _, _, adapter = session("test-rig")
        self.assertIs(adapter.snapshot().estop.drive_power, DrivePower.UNKNOWN)

    def test_unknown_is_explained_not_just_shown(self):
        e = initial_estop(False)
        self.assertIn("girişi yok", e.drive_power_detail)


class TestAdapterEnforcement(unittest.TestCase):
    def test_adapter_software_reset_cannot_clear_hardware_cut(self):
        _, _, adapter = session("test-rig")
        adapter.hardware_chain_open()
        self.assertIs(adapter.snapshot().estop.drive_power, DrivePower.CUT)

        for _ in range(5):
            adapter.software_estop_reset()

        snap = adapter.snapshot()
        self.assertIs(snap.estop.drive_power, DrivePower.CUT)
        self.assertTrue(snap.estop.software_disabled)

    def test_refused_reset_is_reported_to_the_operator(self):
        _, _, adapter = session("test-rig")
        adapter.hardware_chain_open()
        adapter.software_estop_reset()
        self.assertIn("reddedildi", adapter.snapshot().message)

    def test_refused_reset_is_logged(self):
        _, _, adapter = session("test-rig")
        adapter.hardware_chain_open()
        adapter.software_estop_reset()
        texts = [a.text for a in adapter.snapshot().alarms]
        self.assertTrue(any("reddedildi" in t for t in texts))

    def test_no_ui_action_can_clear_a_hardware_cut(self):
        """Exhaustive over the action surface the server exposes."""
        from bettercnc.app import build_session

        sess = build_session("test-rig")
        sess.adapter.hardware_chain_open()
        actions = [
            ("software_reset", ""), ("software_estop", ""), ("home_all", ""),
            ("jog", "X:1"), ("mdi", "G0 X1"), ("mdi_preset", "G0 Z5"),
            ("program_start", ""), ("program_stop", ""), ("jog_step", "10"),
            ("feed", "150"), ("wcs", "G55"), ("bogus", "whatever"),
        ]
        for action, value in actions:
            with self.subTest(action=action):
                sess.act(action, value)
                self.assertIs(
                    sess.adapter.snapshot().estop.drive_power, DrivePower.CUT,
                    "action {!r} cleared a hardware estop".format(action),
                )

    def test_motion_is_refused_while_cut(self):
        _, _, adapter = session("test-rig")
        adapter.hardware_chain_open()
        before = adapter.snapshot().positions
        adapter.jog("X", 1)
        self.assertEqual(adapter.snapshot().positions, before)

    def test_mdi_is_refused_while_cut(self):
        _, _, adapter = session("test-rig")
        adapter.hardware_chain_open()
        entry = adapter.send_mdi("G0 X10")
        self.assertFalse(entry.accepted)


class TestEstopPanel(unittest.TestCase):
    def test_panel_exposes_two_separate_states(self):
        _, _, adapter = session("test-rig")
        p = estop_panel(adapter.snapshot())
        self.assertIn("drive_power", p)
        self.assertIn("software_disabled", p)
        self.assertNotEqual(p["drive_power_label"], p["software_label"])

    def test_panel_declares_reset_does_not_clear_drive_power(self):
        _, _, adapter = session("test-rig")
        self.assertFalse(estop_panel(adapter.snapshot())["reset_clears_drive_power"])

    def test_reset_button_disabled_when_cut(self):
        _, _, adapter = session("test-rig")
        adapter.hardware_chain_open()
        p = estop_panel(adapter.snapshot())
        self.assertFalse(p["reset_enabled"])
        self.assertTrue(p["reset_blocked_reason"].strip())


class TestEstopMarkup(unittest.TestCase):
    def test_markup_carries_both_rows(self):
        markup = html("test-rig", "kontrol")
        self.assertIn('data-estop="drive-power"', markup)
        self.assertIn('data-estop="software"', markup)

    def test_markup_labels_drive_power_as_hardware(self):
        markup = html("test-rig", "kontrol")
        self.assertIn("donanım", markup)

    def test_cut_state_renders_as_cut_and_unclearable(self):
        _, _, adapter = session("test-rig")
        adapter.hardware_chain_open()
        markup = html("test-rig", "kontrol", adapter=adapter)
        self.assertIn('data-value="cut"', markup)
        self.assertIn("KESİLDİ (donanım)", markup)
        self.assertIn("Yazılımdan geri alınamaz", markup)

    def test_missing_hardware_input_is_called_out(self):
        markup = html("test-rig", "kontrol")
        self.assertIn("GİRİŞ YOK", markup)

    def test_never_claims_hardware_cut_from_a_software_estop(self):
        """The prototype's message said drive power was hardware-cut when the
        user clicked a software button. That must not happen here."""
        _, _, adapter = session("test-rig")
        adapter.software_estop()
        snap = adapter.snapshot()
        self.assertIs(snap.estop.drive_power, DrivePower.UNKNOWN)
        self.assertIn("DEĞİŞMEDİ", snap.message)


if __name__ == "__main__":
    unittest.main(verbosity=2)
