"""Homing is a gate: program start is blocked until every joint is homed.

The prototype showed all three axes permanently green while its own status line
said "Referans alinmadi". These tests make that state impossible.
"""

from __future__ import annotations

import unittest

from helpers import html, ini_raw_values, session, view

from bettercnc.state import HomingPhase


class TestGateBlocksStart(unittest.TestCase):
    def setUp(self):
        self.profile, self.config, self.adapter = session("test-rig")

    def test_unhomed_machine_cannot_start(self):
        snap = self.adapter.snapshot()
        self.assertFalse(snap.homing.all_homed)
        self.assertFalse(snap.can_start_program)

    def test_block_reason_mentions_homing(self):
        # clear the software estop so homing is the remaining blocker
        self.adapter.software_estop_reset()
        blocks = self.adapter.snapshot().start_blocks
        self.assertTrue(any("Referans" in b for b in blocks))

    def test_block_reason_names_the_unhomed_axes(self):
        self.adapter.software_estop_reset()
        reason = " ".join(self.adapter.snapshot().start_blocks)
        for axis in ("X", "Y", "Z"):
            with self.subTest(axis=axis):
                self.assertIn(axis, reason)

    def test_program_start_is_refused_while_unhomed(self):
        self.adapter.software_estop_reset()
        self.adapter.load_program("test.ngc")
        self.adapter.program_start()
        self.assertFalse(self.adapter.snapshot().program_running)

    def test_refusal_explains_itself(self):
        self.adapter.software_estop_reset()
        self.adapter.load_program("test.ngc")
        self.adapter.program_start()
        self.assertIn("Başlatılamaz", self.adapter.snapshot().message)

    def test_start_allowed_once_homed_and_enabled(self):
        self.adapter.software_estop_reset()
        self.adapter.home_all()
        self.adapter.load_program("test.ngc")
        snap = self.adapter.snapshot()
        self.assertTrue(snap.homing.all_homed)
        self.assertEqual(snap.start_blocks, [])
        self.assertTrue(snap.can_start_program)
        self.adapter.program_start()
        self.assertTrue(self.adapter.snapshot().program_running)

    def test_homed_but_no_file_still_cannot_start(self):
        self.adapter.software_estop_reset()
        self.adapter.home_all()
        snap = self.adapter.snapshot()
        self.assertEqual(snap.start_blocks, [])
        self.assertFalse(snap.can_start_program)

    def test_estop_takes_priority_over_homing_in_the_headline(self):
        blocks = self.adapter.snapshot().start_blocks
        self.assertIn("devre dışı", blocks[0])

    def test_partial_homing_still_blocks(self):
        self.adapter.software_estop_reset()
        snap = self.adapter.snapshot()
        partial = snap.homing.with_phase("Z", HomingPhase.HOMED)
        self.assertFalse(partial.all_homed)
        self.assertEqual([j.axis for j in partial.unhomed], ["X", "Y"])

    def test_jog_is_allowed_while_unhomed(self):
        """You have to be able to jog to find the switches in the first place."""
        self.adapter.software_estop_reset()
        snap = self.adapter.snapshot()
        self.assertFalse(snap.homing.all_homed)
        self.assertTrue(snap.can_jog)


class TestHomingStateConsistency(unittest.TestCase):
    """No state may show a joint homed while claiming it is not, or vice versa."""

    def test_fresh_machine_reports_every_joint_unhomed(self):
        _, _, adapter = session("test-rig")
        vm = view("test-rig", "kontrol", adapter=adapter)
        for j in vm["kontrol"]["homing"]["joints"]:
            with self.subTest(axis=j["axis"]):
                self.assertFalse(j["homed"])
                self.assertEqual(j["phase"], "not_homed")

    def test_dro_home_flags_agree_with_homing_panel(self):
        for stage in ("before", "after"):
            _, _, adapter = session("test-rig")
            if stage == "after":
                adapter.software_estop_reset()
                adapter.home_all()
            vm = view("test-rig", "kontrol", adapter=adapter)
            panel = {j["axis"]: j["homed"] for j in vm["kontrol"]["homing"]["joints"]}
            dro = {a["axis"]: a["homed"] for a in vm["kontrol"]["axes"]}
            with self.subTest(stage=stage):
                self.assertEqual(panel, dro)

    def test_status_label_reflects_homing(self):
        _, _, adapter = session("test-rig")
        adapter.software_estop_reset()
        self.assertEqual(adapter.snapshot().status_label, "REFERANS GEREKLİ")
        adapter.home_all()
        self.assertEqual(adapter.snapshot().status_label, "HAZIR")

    def test_all_homed_is_false_for_an_empty_joint_set(self):
        """Guard against a vacuous all() reporting a joint-less machine homed."""
        from bettercnc.state import HomingState

        self.assertFalse(HomingState(joints=()).all_homed)


class TestHomingOrder(unittest.TestCase):
    def test_order_comes_from_home_sequence(self):
        ini = ini_raw_values()
        seqs = [
            (int(float(ini["JOINT_{}.HOME_SEQUENCE".format(j)])), a)
            for j, a in ((0, "X"), (1, "Y"), (2, "Z"))
        ]
        expected = " → ".join(a for _, a in sorted(seqs))
        vm = view("test-rig", "kontrol")
        self.assertEqual(vm["kontrol"]["homing"]["order_label"], expected)

    def test_z_is_homed_first(self):
        """Z must retract before X and Y move, whatever the rest of the order."""
        vm = view("test-rig", "kontrol")
        self.assertTrue(vm["kontrol"]["homing"]["order_label"].startswith("Z"))

    def test_order_is_rendered(self):
        markup = html("test-rig", "kontrol")
        self.assertIn("Gerekli sıra", markup)

    def test_discrepancy_with_prose_docs_is_surfaced(self):
        """The INI gives Z->Y->X; docs/DURUM.md:141 says Z->X->Y. The UI shows
        the INI (LinuxCNC obeys it) and flags the disagreement rather than
        silently picking a side."""
        vm = view("test-rig", "kontrol")
        note = vm["kontrol"]["homing"]["order_note"]
        self.assertTrue(note, "the known INI/doc discrepancy is not surfaced")
        self.assertIn("HOME_SEQUENCE", note)
        self.assertIn("docs/DURUM.md", note)

    def test_discrepancy_note_is_rendered(self):
        markup = html("test-rig", "kontrol")
        self.assertIn("TUTARSIZLIK", markup)


class TestSharedHomeAndLimitWarning(unittest.TestCase):
    """HOME_IGNORE_LIMITS = YES means nothing stops the axis during homing."""

    def test_shared_switch_is_detected_from_the_ini(self):
        vm = view("test-rig", "kontrol")
        self.assertTrue(vm["kontrol"]["homing"]["shares_home_and_limit"])

    def test_warning_explains_the_consequence(self):
        vm = view("test-rig", "kontrol")
        warning = vm["kontrol"]["homing"]["shares_warning"]
        self.assertIn("HOME_IGNORE_LIMITS", warning)
        self.assertIn("durduracak hiçbir şey", warning)

    def test_warning_cites_the_doc(self):
        vm = view("test-rig", "kontrol")
        self.assertIn("ini-homing.adoc:46", vm["kontrol"]["homing"]["shares_warning"])

    def test_warning_is_rendered_prominently(self):
        markup = html("test-rig", "kontrol")
        self.assertIn("HOME = LIMIT", markup)


class TestGateMarkup(unittest.TestCase):
    def test_start_button_is_disabled_while_blocked(self):
        markup = html("test-rig", "program")
        idx = markup.index('data-action="program_start"')
        tag_start = markup.rindex("<button", 0, idx)
        tag = markup[tag_start: markup.index(">", idx) + 1]
        self.assertIn("disabled", tag)

    def test_blocked_reason_is_visible_not_only_a_tooltip(self):
        markup = html("test-rig", "program")
        self.assertIn("ENGELLİ", markup)
        self.assertIn("Referans alınmadı", markup)

    def test_start_button_enabled_once_ready(self):
        _, _, adapter = session("test-rig")
        adapter.software_estop_reset()
        adapter.home_all()
        adapter.load_program("x.ngc")
        markup = html("test-rig", "program", adapter=adapter)
        idx = markup.index('data-action="program_start"')
        tag_start = markup.rindex("<button", 0, idx)
        tag = markup[tag_start: markup.index(">", idx) + 1]
        self.assertNotIn("disabled", tag)


if __name__ == "__main__":
    unittest.main(verbosity=2)
