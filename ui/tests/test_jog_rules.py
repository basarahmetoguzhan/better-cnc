"""Plan WP06: jog safety - one rule, decided before anything is sent.

Until WP06 the UI sent every jog click as it came: during a homing (motion
refuses the jog with "Can't jog any joint while homing.", command.c:853-857,
and task's abort after that error cuts the homing short - in sim/ mid-search,
or with Y left on its switch), unhomed with any step (Z has no switch below
it, and the unhomed jog window re-centres at every jog, command.c:161-175),
past a soft limit after homing (motion drops it without a word,
axis.c:289-290, command.c:879-885, while the UI said "gönderildi") and on top
of a jog still running (the increment adds to the target, command.c:873-877,
axis.c:283-287). Now:

  - motionrules.jog_refusal is the one rule: both adapters send by it and
    the jog card's buttons follow it (jog_plan per button);
  - MachineState.jog_block / can_jog turn the whole card off while a joint
    homes or a program or MDI line runs or is paused, with the reason as
    visible text;
  - unhomed, the step is capped (10 mm, 1 mm toward the Z bottom with no
    switch); homed, a step longer than the room to the soft limit is cut to
    it and said, one on the limit refused;
  - a click while the machine moves is refused, not added;
  - the line says "Jog X+1 mm." only once the jog went out.

LinuxCncAdapter runs against fake_linuxcnc, which models motion's soft-limit
checks when a test gives `soft_limits` (premise tests) and ends a homing at
HOME as homing.c does; test_linuxcnc_adapter.TestJogFidelity ties both to
reference/linuxcnc.
"""

from __future__ import annotations

import html as htmllib
import math
import re
import unittest
from typing import Optional, Sequence

import helpers  # noqa: F401  (puts ui/ on sys.path)

import fake_linuxcnc as F
# The module, not its names: a TestCase class imported by name would run
# here a second time. Only its FakeCase base is reused.
import test_linuxcnc_adapter as T
from bettercnc import motionrules as MR
from bettercnc import render as R
from bettercnc import adapter as A
from bettercnc import viewmodel as VM
from bettercnc.adapter import MockAdapter
from bettercnc.app import Session
from bettercnc.configmodel import load_machine_config
from bettercnc.profiles import load_profile
from bettercnc.state import (
    JOG_BLOCK_HOMING,
    JOG_BLOCK_OFF,
    JOG_BLOCK_PAUSED,
    JOG_BLOCK_RUNNING,
    JOG_BLOCK_UNKNOWN,
    JOG_BLOCK_UNREAD,
    AxisPosition,
    HardLimit,
    HomingPhase,
    MachineState,
    Severity,
)

C = F.CONSTANTS
PROFILE = load_profile("test-rig")
CONFIG = load_machine_config(PROFILE)
WINDOWS = MR.axis_windows(CONFIG)
JOG = C["JOG_INCREMENT"]
#: [DISPLAY]DEFAULT_LINEAR_VELOCITY in configs/test-rig/test-rig.ini.
JOG_VEL = 3.0
#: The rig's HOME, where Home All leaves the machine (test-rig.ini).
HOME = (5.0, 5.0, -5.0)
MARGIN = MR.JOG_LIMIT_MARGIN_MM

HOMING_TEXT = "Referans alma sürüyor — jog yok. Durdurmak için «Durdur»."
RUNNING_TEXT = "Program çalışırken jog yok."
PAUSED_TEXT = "Program duraklatılmışken jog yok."
MOVING_TEXT = "Hareket sürüyor — bitmesini bekleyin ya da «Durdur»."
OFF_TEXT = "Makine devre dışı — jog yok."
Z_DOWN_10 = "Z referanssız ve Z alt switch'i yok — Z− adımı en çok 1 mm (seçili 10 mm)."
X_50 = "X referanssız — jog adımı en çok 10 mm (seçili 50 mm)."
Z_CUT = "Z+ en çok 2.000 mm gidebilir (yumuşak limit −3.000 mm) — adım 2.000 mm'ye kısaltıldı."
Z_AT_LIMIT = "Z yumuşak limitte (−3.000 mm) — Z+ yönünde jog yok."


def esc(text: str) -> str:
    return htmllib.escape(text, quote=True)


def state_of(homed: str = "", homing: str = "", at: Sequence[float] = (0.0, 0.0, 0.0),
             commanded: Optional[Sequence[float]] = None, ends: Sequence[str] = (),
             **kw) -> MachineState:
    """The rig ON as LinuxCncAdapter maps it: MANUAL, idle, still; the axes
    in `homed` homed and those in `homing` homing; the machine position
    `at` and the commanded one `commanded` (default: the same); the
    switches `ends` ("X-", "Z+") pressed; then `kw` over it. Motion in
    teleop once every joint is homed, free before, as task's MANUAL puts
    it (emctask.cc:276-280)."""
    base = MockAdapter(PROFILE).snapshot()
    phases = {a: (HomingPhase.HOMING if a in homing else
                  HomingPhase.HOMED if a in homed else HomingPhase.NOT_HOMED) for a in "XYZ"}
    cmd = at if commanded is None else commanded
    all_homed = all(phases[a] is HomingPhase.HOMED for a in "XYZ")
    state = base._replace(
        estop=base.estop.software_reset(),
        homing=base.homing._replace(joints=tuple(
            j._replace(phase=phases[j.axis]) for j in base.homing.joints)),
        positions=tuple(AxisPosition(a, at[i], at[i], phases[a] is HomingPhase.HOMED, cmd[i])
                        for i, a in enumerate("XYZ")),
        hard_limits=tuple(HardLimit(j, a, (a + "-") in ends, (a + "+") in ends)
                          for j, a in enumerate("XYZ")),
        override_limits=bool(ends), moving=False, task_mode="manual", interp_state="idle",
        motion_mode="teleop" if all_homed else "free", program_paused=False,
        mdi_running=False, program_running=False,
    )
    return state._replace(**kw)


def decide(state: MachineState, axis: str, direction: int, step: float,
           teleop: Optional[bool] = None) -> MR.JogDecision:
    return MR.jog_refusal(state, PROFILE, WINDOWS, axis, direction, step, teleop)


# ---------------------------------------------------------------------------
# motionrules: pure
# ---------------------------------------------------------------------------


class TestWhenNoJogMayGo(unittest.TestCase):
    """Test (1), (2), (3): the card-level blocks and the click's own
    'still moving', in the rule's order."""

    def test_homing_refuses_and_turns_the_card_off(self):
        state = state_of(homed="Z", homing="Y")
        self.assertEqual(state.jog_block, JOG_BLOCK_HOMING)
        self.assertFalse(state.can_jog)
        for axis, direction in (("Z", 1), ("Y", -1), ("X", 1)):
            with self.subTest(axis=axis):
                self.assertEqual(decide(state, axis, direction, 1.0), (HOMING_TEXT, None, ""))

    def test_a_program_running_or_paused_or_an_mdi_line_refuses(self):
        cases = (
            (dict(program_running=True, task_mode="auto", interp_state="reading",
                  motion_mode="coord"), JOG_BLOCK_RUNNING, RUNNING_TEXT),
            (dict(task_mode="auto", interp_state="paused", program_paused=True,
                  motion_mode="coord"), JOG_BLOCK_PAUSED, PAUSED_TEXT),
            (dict(task_mode="mdi", interp_state="reading", mdi_running=True,
                  motion_mode="coord"), JOG_BLOCK_RUNNING, RUNNING_TEXT),
            # An MDI line paused (M0 in MDI, or a single step) reads as paused.
            (dict(task_mode="mdi", interp_state="paused", mdi_running=True,
                  program_paused=True, motion_mode="coord"), JOG_BLOCK_PAUSED, PAUSED_TEXT),
        )
        for kw, block, text in cases:
            with self.subTest(case=kw):
                state = state_of(homed="XYZ", at=HOME, **kw)
                self.assertEqual(state.jog_block, block)
                self.assertFalse(state.can_jog)
                self.assertEqual(decide(state, "X", 1, 1.0).refusal, text)

    def test_moving_refuses_the_click_but_leaves_the_card_on(self):
        """A pad that went off and on with every jog would move under the
        finger: moving is the click's question (JOG_MOVING), not the card's."""
        state = state_of(homed="XYZ", at=HOME, moving=True)
        self.assertTrue(state.can_jog)
        self.assertEqual(state.jog_block, "")
        self.assertEqual(decide(state, "X", 1, 1.0), (MOVING_TEXT, None, ""))
        self.assertEqual(MR.jog_plan(state, PROFILE, WINDOWS, "X", 1, 1.0).refusal, "")

    def test_a_reading_not_made_refuses_never_allows(self):
        for name, block in (("program_paused", JOG_BLOCK_UNREAD), ("mdi_running", JOG_BLOCK_UNREAD)):
            with self.subTest(field=name):
                state = state_of(homed="XYZ", at=HOME, **{name: None})
                self.assertEqual(state.jog_block, block)
                self.assertFalse(state.can_jog)
                self.assertEqual(decide(state, "X", 1, 1.0).refusal,
                                 "Program durumu okunamadı — jog yok.")
        state = state_of(homed="XYZ", at=HOME, moving=None)
        self.assertEqual(decide(state, "X", 1, 1.0).refusal, "Hareket durumu okunamadı — jog yok.")

    def test_status_unknown_and_machine_off_refuse_first(self):
        unknown = state_of(homed="XYZ", at=HOME).as_status_unknown()
        self.assertEqual(unknown.jog_block, JOG_BLOCK_UNKNOWN)
        self.assertEqual(decide(unknown, "X", 1, 1.0).refusal, "Makine durumu bilinmiyor — jog yok.")
        off = state_of(homed="XYZ", homing="", at=HOME)
        off = off._replace(estop=off.estop.software_estop(), program_paused=None)
        self.assertEqual(off.jog_block, JOG_BLOCK_OFF)      # before the unread fields
        self.assertEqual(decide(off, "X", 1, 1.0).refusal, OFF_TEXT)
        # The machine off comes before a homing (a stale flag) too.
        homing_off = off._replace(homing=state_of(homing="Y").homing)
        self.assertEqual(homing_off.jog_block, JOG_BLOCK_OFF)


class TestUnhomedSteps(unittest.TestCase):
    """Test (4): the caps while the joint is not homed."""

    def test_z_down_has_no_switch_one_mm_at_most(self):
        state = state_of()
        self.assertEqual(decide(state, "Z", -1, 10.0), (Z_DOWN_10, None, ""))
        self.assertEqual(decide(state, "Z", -1, 1.0), ("", 1.0, ""))
        self.assertEqual(decide(state, "Z", -1, 0.1), ("", 0.1, ""))
        self.assertIn("en çok 1 mm", Z_DOWN_10)

    def test_every_other_way_ten_mm_at_most(self):
        state = state_of()
        self.assertEqual(decide(state, "X", 1, 50.0), (X_50, None, ""))
        for axis, direction in (("X", 1), ("X", -1), ("Y", 1), ("Y", -1), ("Z", 1)):
            with self.subTest(axis=axis, direction=direction):
                self.assertEqual(decide(state, axis, direction, 10.0), ("", 10.0, ""))
                self.assertIn("en çok 10 mm", decide(state, axis, direction, 50.0).refusal)

    def test_repeated_z_down_clicks_each_capped(self):
        """Unhomed, motion re-centres its window at every jog (command.c:
        161-175): only the cap bounds a click, wherever Z stands."""
        for z in (0.0, -50.0, -300.0):
            with self.subTest(z=z):
                self.assertEqual(decide(state_of(at=(0.0, 0.0, z)), "Z", -1, 10.0).refusal,
                                 Z_DOWN_10.replace("(seçili 10 mm)", "(seçili 10 mm)"))

    def test_the_cap_is_per_joint(self):
        """Homing cut short: Z homed, X and Y not - X capped, Z checked
        against its soft limits instead."""
        state = state_of(homed="Z", at=(0.0, 0.0, -50.0))
        self.assertIn("en çok 10 mm", decide(state, "X", 1, 50.0).refusal)
        self.assertEqual(decide(state, "Z", -1, 10.0), ("", 10.0, ""))      # homed: no cap
        self.assertEqual(decide(state, "Z", 1, 50.0).step_mm, 47.0 - MARGIN)  # its room

    def test_unhomed_joint_jog_is_not_checked_against_the_soft_limits(self):
        """Unhomed in free mode the soft limits do not apply yet
        (command.c:161-175): X at 0, below its MIN_LIMIT 3, jogs left."""
        self.assertEqual(decide(state_of(), "X", -1, 1.0), ("", 1.0, ""))

    def test_unhomed_window_is_the_whole_travel_around_the_joint(self):
        """The joint's position plus or minus its whole travel
        (command.c:161-175) - never the bound with the rig's steps, here
        with a window narrower than the step."""
        narrow = {"X": (0.0, 5.0), "Y": (3.0, 387.0), "Z": (-96.5, -3.0)}
        refused = MR.jog_refusal(state_of(), PROFILE, narrow, "X", 1, 10.0)
        self.assertIn("referanssız jog sınırının (5 mm) dışına çıkar", refused.refusal)
        self.assertEqual(MR.jog_refusal(state_of(), PROFILE, narrow, "X", 1, 5.0).step_mm, 5.0)


class TestHomedSoftLimits(unittest.TestCase):
    """Test (5): homed, the target inside the soft limits; a longer step
    cut to the room, said; one on the limit refused."""

    def test_z_at_minus_five_ten_up_is_cut_to_two(self):
        d = decide(state_of(homed="XYZ", at=HOME), "Z", 1, 10.0)
        self.assertEqual(d.refusal, "")
        self.assertAlmostEqual(d.step_mm, 2.0 - MARGIN, places=9)
        self.assertEqual(d.note, Z_CUT)

    def test_on_the_limit_no_jog_that_way(self):
        for z in (-3.0, -3.0 + 0.0009, -3.0 - MARGIN):
            with self.subTest(z=z):
                state = state_of(homed="XYZ", at=(5.0, 5.0, z))
                self.assertEqual(decide(state, "Z", 1, 1.0), (Z_AT_LIMIT, None, ""))
                self.assertEqual(decide(state, "Z", -1, 1.0), ("", 1.0, ""))

    def test_a_step_that_fits_goes_whole(self):
        self.assertEqual(decide(state_of(homed="XYZ", at=HOME), "X", -1, 1.0), ("", 1.0, ""))
        self.assertEqual(decide(state_of(homed="XYZ", at=HOME), "X", 1, 50.0), ("", 50.0, ""))

    def test_a_step_ending_on_the_limit_stops_a_hair_short_unsaid(self):
        """Motion adds the step to its planner's target, which the read
        commanded position trails by up to tiny_dp (simple_tp.c:33-45): a
        target exactly on the limit could land past it and be dropped. The
        cut is below the DRO's last digit, so nothing is said."""
        d = decide(state_of(homed="XYZ", at=(5.0, 5.0, -4.0)), "Z", 1, 1.0)
        self.assertEqual((d.refusal, d.note), ("", ""))
        self.assertAlmostEqual(d.step_mm, 1.0 - MARGIN, places=9)

    def test_the_target_counts_from_the_commanded_position(self):
        """Where motion's planner stands (stat.position), not the feedback."""
        state = state_of(homed="XYZ", at=(5.0, 5.0, -5.0), commanded=(5.0, 5.0, -4.0))
        self.assertAlmostEqual(decide(state, "Z", 1, 10.0).step_mm, 1.0 - MARGIN, places=9)

    def test_without_a_commanded_position_the_machine_position(self):
        state = state_of(homed="XYZ", at=HOME)
        state = state._replace(positions=tuple(p._replace(commanded=None) for p in state.positions))
        self.assertEqual(decide(state, "Z", 1, 10.0).note, Z_CUT)

    def test_each_axis_and_end(self):
        state = state_of(homed="XYZ", at=(397.0, 386.5, -95.5))
        cases = (("X", 1, 2.0, 399.0), ("Y", 1, 0.5, 387.0), ("Z", -1, 1.0, -96.5))
        for axis, direction, room, limit in cases:
            with self.subTest(axis=axis):
                d = decide(state, axis, direction, 10.0)
                self.assertAlmostEqual(d.step_mm, room - MARGIN, places=9)
                self.assertIn("en çok {:.3f} mm gidebilir".format(room), d.note)
                self.assertIn(MR._pos(limit), d.note)

    def test_a_teleop_jog_is_checked_homed_or_not(self):
        """axis.c:289-290 checks the axis's limits in teleop, homed or not
        (motion left in teleop by another GUI, say): unhomed in teleop both
        the cap and the window apply."""
        state = state_of(at=(397.0, 0.0, 0.0), motion_mode="teleop")
        d = decide(state, "X", 1, 10.0)
        self.assertAlmostEqual(d.step_mm, 2.0 - MARGIN, places=9)
        self.assertIn("kısaltıldı", d.note)
        self.assertEqual(decide(state, "X", 1, 50.0).refusal,
                         "X referanssız — jog adımı en çok 10 mm (seçili 50 mm).")
        self.assertIn("yumuşak limitin", decide(state, "Z", 1, 1.0).refusal)   # Z at 0 > -3
        # The adapter's second look, with the mode it switched to.
        self.assertEqual(decide(state_of(at=(397.0, 0.0, 0.0)), "X", 1, 10.0, teleop=True).step_mm,
                         2.0 - MARGIN)
        self.assertEqual(decide(state_of(at=(397.0, 0.0, 0.0)), "X", 1, 10.0), ("", 10.0, ""))

    def test_no_window_in_the_ini_no_cut(self):
        """An end the INI does not give is LinuxCNC's +-1e99
        (inijoint.cc:131-140): nothing to cut against."""
        d = MR.jog_refusal(state_of(homed="XYZ", at=HOME), PROFILE, {}, "Z", 1, 50.0)
        self.assertEqual(d, ("", 50.0, ""))


class TestOutsideTheWindow(unittest.TestCase):
    """Test (6): a homed axis outside its soft limits (as a homed limit
    recovery can leave it): no jog further out; in, only a step that lands
    back inside."""

    def setUp(self):
        self.state = state_of(homed="XYZ", at=(402.72, 5.0, -5.0))

    def test_further_out_is_refused(self):
        self.assertEqual(
            decide(self.state, "X", 1, 1.0).refusal,
            "X yumuşak limitin 3.720 mm dışında (limit 399.000 mm) — X+ yönünde jog yok.")

    def test_in_by_too_little_is_refused_with_the_least_that_would_do(self):
        self.assertEqual(
            decide(self.state, "X", -1, 1.0).refusal,
            "X yumuşak limitin 3.720 mm dışında (limit 399.000 mm) — X− adımı en az 3.721 mm "
            "olmalı.")

    def test_in_by_enough_goes(self):
        self.assertEqual(decide(self.state, "X", -1, 10.0), ("", 10.0, ""))

    def test_below_the_window_the_same(self):
        state = state_of(homed="XYZ", at=(-0.64, 5.0, -5.0))
        self.assertIn("X− yönünde jog yok", decide(state, "X", -1, 1.0).refusal)
        self.assertIn("X+ adımı en az 3.641 mm", decide(state, "X", 1, 1.0).refusal)
        self.assertEqual(decide(state, "X", 1, 10.0), ("", 10.0, ""))


class TestTheClickItself(unittest.TestCase):

    def test_onto_a_pressed_switch_one_rule_with_the_recovery(self):
        """Plan WP05's rule, folded in: refused in every mode, homed or not."""
        for homed in ("", "XYZ"):
            with self.subTest(homed=homed):
                state = state_of(homed=homed, at=(398.0, 5.0, -5.0), ends=("X+",))
                self.assertEqual(decide(state, "X", 1, 1.0).refusal, "X sağ uç basılı — yalnız X−.")
                self.assertEqual(decide(state, "X", -1, 1.0), ("", 1.0, ""))

    def test_a_step_that_is_not_a_positive_number_is_refused(self):
        for bad in (0, -1.0, float("nan"), float("inf")):
            with self.subTest(step=bad):
                d = decide(state_of(), "X", 1, bad)
                self.assertTrue(d.refusal.startswith("Geçersiz jog adımı"), d)
                self.assertIsNone(d.step_mm)

    def test_an_axis_the_machine_lacks_and_a_bad_direction(self):
        self.assertEqual(decide(state_of(), "A", 1, 1.0).refusal, "Bu makinede A ekseni yok — jog yok.")
        for bad in (0, 2, -5):
            with self.subTest(direction=bad):
                with self.assertRaises(ValueError):
                    decide(state_of(), "X", bad, 1.0)

    def test_the_sent_line_names_axis_direction_and_mm(self):
        self.assertEqual(MR.jog_sent_text("X", 1, 1.0), "Jog X+1 mm.")
        self.assertEqual(MR.jog_sent_text("Z", -1, 0.01), "Jog Z−0.01 mm.")

    def test_the_constants_are_named_and_marked_chosen(self):
        """Unmeasured numbers carry 'Chosen, not measured' (plan safety
        rules); the docs list them (docs/11)."""
        import inspect
        source = inspect.getsource(MR)
        for name in ("UNHOMED_MAX_STEP_MM", "UNHOMED_NO_SWITCH_MAX_MM", "AT_SOFT_LIMIT_MM",
                     "JOG_LIMIT_MARGIN_MM"):
            with self.subTest(constant=name):
                block = source[:source.index("\n{} = ".format(name))]
                comment = block[block.rindex("\n\n"):]
                self.assertIn("Chosen, not measured", comment)
        self.assertEqual((MR.UNHOMED_MAX_STEP_MM, MR.UNHOMED_NO_SWITCH_MAX_MM), (10.0, 1.0))


# ---------------------------------------------------------------------------
# LinuxCncAdapter on the fake
# ---------------------------------------------------------------------------


class _Case(T.FakeCase):

    def sent(self):
        return self.machine.calls_except("wait_complete")

    def rig(self, **kw) -> F.FakeMachine:
        """A fake with motion's soft-limit checks on (the rig's windows)."""
        return F.FakeMachine(soft_limits=WINDOWS, **kw)


class TestAdapterRefusesDuringHoming(_Case):
    """Test (1) on the real adapter: nothing reaches LinuxCNC, the homing
    goes on and completes."""

    def test_no_command_the_message_and_the_homing_completes(self):
        adapter = self.on(homed=False, machine=F.FakeMachine(home_instantly=False))
        adapter.home_all()
        self.machine.log.clear()
        # Each group of the sequence in turn (Z, Y, X), every axis tried.
        for homing in ("Z", "Y", "X"):
            j = "XYZ".index(homing)
            self.assertEqual(self.machine.homing[j], 1)
            for axis, direction in (("Z", 1), ("Y", -1), ("X", 1)):
                with self.subTest(homing=homing, axis=axis):
                    adapter.jog(axis, direction)
                    self.assertEqual(self.machine.log, [])      # not even a mode change
                    snap = adapter.snapshot()
                    self.assertEqual(snap.message, HOMING_TEXT)
                    self.assertIs(snap.message_severity, Severity.WARN)
                    self.assertFalse(snap.can_jog)
            self.machine.advance_homing()        # this group ends: the gap
            if homing != "X":
                self.machine.advance_homing()    # the next group starts
        self.assertNoLinuxCncErrors()       # motion never saw a jog to refuse
        self.machine.finish_homing()
        snap = adapter.snapshot()
        self.assertTrue(snap.homing.all_homed)
        self.assertEqual([p.machine for p in snap.positions], list(HOME))
        self.assertTrue(snap.can_jog)

    def _between_z_and_y(self):
        """Home All between Z's end and Y's start, as a poll can show it:
        Z homed, X and Y not, no joint homing (homing.c HOME_FINISHED, then
        the sequence moves on two servo cycles later; sim/e2e/harness.py
        wait_homing_over)."""
        adapter = self.on(homed=False, machine=F.FakeMachine(home_instantly=False))
        self.machine.homed[2] = 1
        self.machine.axis_pos[2] = self.machine.joint_pos[2] = HOME[2]
        self.machine.log.clear()
        return adapter

    def test_between_two_joints_of_home_all_it_looks_again(self):
        """Review of WP06: in that gap no joint reads homing, yet motion's
        homing is active - the jog would be refused and task's abort would
        cancel the joint just started. The adapter looks once more,
        HOMING_GAP_RECHECK_S later; Y homes by then: refused, nothing sent."""
        adapter = self._between_z_and_y()
        waits = []

        def y_starts(seconds):
            waits.append(seconds)
            self.machine.homing[1] = 1

        adapter._sleep = y_starts
        adapter.jog("X", 1)
        self.assertEqual(waits, [A.HOMING_GAP_RECHECK_S])
        self.assertEqual(self.machine.log, [])          # not even a mode change
        self.assertEqual(adapter.snapshot().message, HOMING_TEXT)
        self.assertNoLinuxCncErrors()

    def test_in_the_fakes_real_gap_between_two_groups_it_looks_again(self):
        """P0 cleanup: the same, with the gap the fake's Home All leaves
        between Z's group and Y's (advance_homing) instead of flags set by
        hand: Y starts during the second look; without it the jog would go
        out and motion would refuse it (homing still active)."""
        adapter = self.on(homed=False, machine=F.FakeMachine(home_instantly=False))
        adapter.home_all()
        self.machine.advance_homing()                   # Z homed: the gap
        self.assertTrue(self.machine.homing_active)
        self.machine.log.clear()
        adapter._sleep = lambda seconds: self.machine.advance_homing()   # Y starts
        adapter.jog("X", 1)
        self.assertEqual(self.machine.homing[:3], [0, 1, 0])
        self.assertEqual(self.machine.log, [])
        self.assertEqual(adapter.snapshot().message, HOMING_TEXT)
        self.assertNoLinuxCncErrors()

    def test_a_homing_left_partial_costs_one_look_more(self):
        """The same picture for good (a homing cut short): the second look
        finds it unchanged and the jog goes - a joint jog, X unhomed."""
        adapter = self._between_z_and_y()
        adapter.jog("X", 1)
        self.assertEqual(self.clock.sleeps, [A.HOMING_GAP_RECHECK_S])
        self.assertEqual(self.sent(), [("jog", (JOG, 1, 0, JOG_VEL, 1.0))])
        self.assertEqual(adapter.snapshot().message, "Jog X+1 mm.")

    def test_no_second_look_where_no_gap_can_be(self):
        """Nothing homed, everything homed, the machine off, or motion out
        of free mode (homing runs only in free mode, command.c:1374-1378):
        decided on the first look."""
        for name, homed, setup in (
            ("none homed", False, {}),
            ("all homed", True, {}),
            ("off", False, dict(traj_enabled=False)),       # ESTOP_RESET
            ("teleop", False, dict(motion_mode=C["TRAJ_MODE_TELEOP"])),
        ):
            with self.subTest(case=name):
                adapter = self.on(homed=homed, machine=F.FakeMachine(home_instantly=False))
                if name in ("off", "teleop"):
                    self.machine.homed[2] = 1
                    self.machine.axis_pos[2] = self.machine.joint_pos[2] = HOME[2]
                for attr, value in setup.items():
                    setattr(self.machine, attr, value)
                adapter.jog("Z", -1)
                self.assertEqual(self.clock.sleeps, [], name)

    def test_the_gap_predicate(self):
        """adapter._homing_sequence_gap_possible on MachineState."""
        self.assertTrue(A._homing_sequence_gap_possible(state_of(homed="Z")))
        self.assertTrue(A._homing_sequence_gap_possible(state_of(homed="ZY")))
        for state in (state_of(), state_of(homed="XYZ", at=HOME),
                      state_of(homed="Z", homing="Y"),
                      state_of(homed="Z", motion_mode="teleop"),
                      state_of(homed="Z").as_status_unknown()):
            with self.subTest(state=[j.phase.value for j in state.homing.joints]):
                self.assertFalse(A._homing_sequence_gap_possible(state))
        off = state_of(homed="Z")
        self.assertFalse(A._homing_sequence_gap_possible(
            off._replace(estop=off.estop.software_estop())))
        # An unmeasured number, named and marked so (plan safety rules).
        import inspect
        source = inspect.getsource(A)
        block = source[:source.index("\nHOMING_GAP_RECHECK_S = ")]
        self.assertIn("Chosen, not measured", block[block.rindex("\n\n"):])

    def test_premise_a_jog_sent_during_homing_is_refused_with_a_joint_error(self):
        """What the refusal saves: motion's own refusal sets the joint's
        error flag (command.c:853-857), which task turns into an abort."""
        self.make(machine=F.FakeMachine(home_instantly=False))
        self.machine.power_on()
        self.machine.issue_home(-1)
        cmd = __import__("linuxcnc").command()
        cmd.jog(JOG, 1, 2, JOG_VEL, 1.0)
        self.assertEqual(list(self.machine.errors)[-1],
                         (C["OPERATOR_ERROR"], "Can't jog any joint while homing."))
        self.assertEqual(cmd.wait_complete(), C["RCS_ERROR"])


class TestAdapterRefusesWhileBusy(_Case):
    """Test (2) and (3) on the real adapter."""

    def test_program_running_paused_mdi(self):
        path = self.ngc()
        for name, setup, text in (
            ("running", dict(task_mode=C["MODE_AUTO"], interp_state=C["INTERP_READING"]),
             RUNNING_TEXT),
            ("paused", dict(task_mode=C["MODE_AUTO"], interp_state=C["INTERP_PAUSED"],
                            paused=True), PAUSED_TEXT),
            ("mdi", dict(task_mode=C["MODE_MDI"], interp_state=C["INTERP_READING"]),
             RUNNING_TEXT),
        ):
            with self.subTest(case=name):
                adapter = self.on(homed=True)
                self.machine.file = path
                self.machine.motion_mode = C["TRAJ_MODE_COORD"]
                for attr, value in setup.items():
                    setattr(self.machine, attr, value)
                adapter.jog("X", 1)
                self.assertEqual(self.machine.log, [])
                snap = adapter.snapshot()
                self.assertEqual(snap.message, text)
                self.assertFalse(snap.can_jog)

    def test_moving_teleop_and_free(self):
        for name, homed, setup in (
            ("teleop", True, dict(current_vel=3.0)),
            ("free", False, dict(jogging_active=True)),
        ):
            with self.subTest(mode=name):
                adapter = self.on(homed=homed)
                for attr, value in setup.items():
                    setattr(self.machine, attr, value)
                adapter.jog("X", 1)
                self.assertEqual(self.machine.log, [])
                snap = adapter.snapshot()
                self.assertEqual(snap.message, MOVING_TEXT)
                self.assertTrue(snap.can_jog)              # the card stays on


class TestAdapterSteps(_Case):
    """Test (4) and (5) on the real adapter, with the fake's soft limits on."""

    def test_unhomed_caps(self):
        adapter = self.on(homed=False, machine=self.rig())
        adapter.set_jog_step(10.0)
        adapter.jog("Z", -1)
        self.assertEqual(self.sent(), [])
        self.assertEqual(adapter.snapshot().message, Z_DOWN_10)
        adapter.set_jog_step(1.0)
        adapter.jog("Z", -1)
        self.assertEqual(self.sent(), [("jog", (JOG, 1, 2, -JOG_VEL, 1.0))])
        self.assertEqual(adapter.snapshot().message, "Jog Z−1 mm.")
        self.machine.log.clear()
        adapter.set_jog_step(50.0)
        adapter.jog("X", 1)
        self.assertEqual(self.sent(), [])
        self.assertEqual(adapter.snapshot().message, X_50)
        adapter.set_jog_step(10.0)
        adapter.jog("X", 1)
        self.assertEqual(self.sent(), [("jog", (JOG, 1, 0, JOG_VEL, 10.0))])
        self.assertEqual(self.machine.joint_pos[0], 10.0)
        self.assertNoLinuxCncErrors()

    def test_homed_z_up_ten_from_minus_five_goes_two_and_says_so(self):
        adapter = self.on(homed=True, machine=self.rig())
        adapter.set_jog_step(10.0)
        adapter.jog("Z", 1)
        (name, (fn, jjogmode, index, vel, incr)), = self.sent()
        self.assertEqual((name, fn, jjogmode, index, vel), ("jog", JOG, 0, 2, JOG_VEL))
        self.assertAlmostEqual(incr, 2.0, delta=0.001)
        self.assertLess(incr, 2.0)
        snap = adapter.snapshot()
        self.assertEqual(snap.message, Z_CUT)
        self.assertIs(snap.message_severity, Severity.WARN)
        self.assertAlmostEqual(self.machine.axis_pos[2], -3.0, delta=0.001)   # motion took it
        self.assertNoLinuxCncErrors()
        # Now on the limit: Z+ refused, nothing sent; Z− goes.
        self.machine.log.clear()
        adapter.jog("Z", 1)
        self.assertEqual(self.sent(), [])
        self.assertEqual(adapter.snapshot().message, Z_AT_LIMIT)
        adapter.set_jog_step(1.0)
        adapter.jog("Z", -1)
        self.assertEqual([n for n, _ in self.sent()], ["jog"])

    def test_premise_motion_drops_the_uncut_step_without_a_word(self):
        """What 'gönderildi' used to hide: a teleop jog past the limit is
        dropped, no error (axis.c:289-290)."""
        self.make(machine=self.rig())
        self.machine.power_on(homed=True)
        cmd = __import__("linuxcnc").command()
        cmd.jog(JOG, 0, 2, JOG_VEL, 10.0)
        self.assertEqual(self.machine.axis_pos[2], -5.0)
        self.assertNoLinuxCncErrors()
        self.assertEqual(cmd.wait_complete(), C["RCS_DONE"])

    def test_premise_both_sides_overridden_only_the_silent_drop_is_left(self):
        """The fake as motion: from beyond a homed joint's soft limit a jog
        further out is refused with an error (command.c:139-149) - unless
        both of the joint's sides are overridden, when joint_jog_ok runs
        none of its tests (:118-124) and only JOG_INCR's silent drop of a
        target past the limit is left (:879-885). Review of WP06: the fake
        refused it with an error then too."""
        self.make(machine=self.rig())
        self.machine.power_on(homed=True)
        self.machine.motion_mode = C["TRAJ_MODE_FREE"]
        self.machine.axis_pos[0] = self.machine.joint_pos[0] = 402.0
        cmd = __import__("linuxcnc").command()
        cmd.jog(JOG, 1, 0, JOG_VEL, 1.0)
        self.assertEqual(list(self.machine.errors),
                         [(C["OPERATOR_ERROR"], "Can't jog joint 0 further past max soft limit.")])
        self.assertEqual(cmd.wait_complete(), C["RCS_ERROR"])
        self.machine.errors.clear()
        self.machine.override_mask = 0b11              # joint 0, both sides
        cmd.jog(JOG, 1, 0, JOG_VEL, 1.0)                # 403: past 399, dropped
        self.assertNoLinuxCncErrors()
        self.assertEqual(cmd.wait_complete(), C["RCS_DONE"])
        self.assertEqual(self.machine.joint_pos[0], 402.0)
        cmd.jog(JOG, 1, 0, -JOG_VEL, 10.0)              # 392: inside, goes
        self.assertNoLinuxCncErrors()
        self.assertEqual(self.machine.joint_pos[0], 392.0)

    def test_outside_the_window_in_only_by_enough(self):
        adapter = self.on(homed=True, machine=self.rig())
        self.machine.axis_pos[0] = self.machine.joint_pos[0] = 402.0
        adapter.jog("X", 1)
        adapter.jog("X", -1)
        self.assertEqual(self.sent(), [])
        self.assertIn("X− adımı en az 3.001 mm olmalı", adapter.snapshot().message)
        adapter.set_jog_step(10.0)
        adapter.jog("X", -1)
        self.assertEqual(self.sent(), [("jog", (JOG, 0, 0, -JOG_VEL, 10.0))])
        self.assertEqual(self.machine.axis_pos[0], 392.0)
        self.assertNoLinuxCncErrors()

    def test_unhomed_teleop_checked_against_the_axis_limits(self):
        """Motion left in teleop with the joints unhomed (another GUI; the
        link reset, plan WP11): the jog goes as a teleop jog, which motion
        checks against the axis's limits (axis.c:289-290) - cut, and said."""
        adapter = self.on(homed=False, machine=self.rig())
        self.machine.motion_mode = C["TRAJ_MODE_TELEOP"]
        self.machine.axis_pos[0] = self.machine.joint_pos[0] = 395.0
        adapter.set_jog_step(10.0)
        adapter.jog("X", 1)
        (name, (fn, jjogmode, index, vel, incr)), = self.sent()
        self.assertEqual((jjogmode, index), (0, 0))
        self.assertAlmostEqual(incr, 4.0 - MARGIN, places=9)
        self.assertAlmostEqual(self.machine.axis_pos[0], 399.0, delta=0.001)
        self.assertIn("X+ en çok 4.000 mm gidebilir", adapter.snapshot().message)

    def test_the_rule_looks_again_after_the_mode_switch(self):
        """The rule runs on the status after MANUAL and the jog mode too:
        here X reached its limit meanwhile (another GUI), so the jog is
        refused, the line says why - never "Jog X+1 mm." - and no jog
        leaves."""
        adapter = self.on(homed=True, machine=self.rig())
        self.machine.task_mode = C["MODE_MDI"]
        self.machine.motion_mode = C["TRAJ_MODE_COORD"]
        self.machine.axis_pos[0] = self.machine.joint_pos[0] = 398.5
        switch = self.machine.issue_set_mode

        def and_x_moves(mode):
            switch(mode)
            self.machine.axis_pos[0] = self.machine.joint_pos[0] = 399.0

        self.machine.issue_set_mode = and_x_moves
        adapter.jog("X", 1)
        self.assertEqual([n for n, _ in self.sent()], ["mode"])
        self.assertEqual(adapter.snapshot().message,
                         "X yumuşak limitte (399.000 mm) — X+ yönünde jog yok.")


class TestAdapterSaysSentOnlyWhenSent(_Case):

    def test_a_stop_during_the_switch_sends_nothing_and_says_so(self):
        adapter = self.on(homed=True)
        self.machine.task_mode = C["MODE_MDI"]
        self.machine.motion_mode = C["TRAJ_MODE_COORD"]
        switch = self.machine.issue_set_mode

        def and_a_stop(mode):
            switch(mode)
            adapter.stop_motion()

        self.machine.issue_set_mode = and_a_stop
        adapter.jog("X", 1)
        self.assertNotIn("jog", self.machine.calls())
        self.assertNotIn("Jog", adapter.snapshot().message)


# ---------------------------------------------------------------------------
# MockAdapter: the same rule
# ---------------------------------------------------------------------------


class TestMockFollowsTheRule(unittest.TestCase):

    def mock(self, homed: bool = False) -> MockAdapter:
        adapter = MockAdapter(PROFILE)
        adapter.software_estop_reset()
        if homed:
            adapter.home_all()
        return adapter

    def pos(self, adapter: MockAdapter, axis: str) -> float:
        return {p.axis: p.machine for p in adapter.snapshot().positions}[axis]

    def test_home_all_leaves_the_axes_at_home(self):
        snap = self.mock(homed=True).snapshot()
        self.assertEqual([(p.machine, p.work) for p in snap.positions],
                         [(h, h) for h in HOME])
        self.assertEqual(snap.motion_mode, "teleop")

    def test_refused_while_a_program_runs(self):
        """Known gap closed: the mock jogged while its program ran."""
        adapter = self.mock(homed=True)
        adapter.load_program("deneme.ngc")
        adapter.program_start()
        adapter.jog("X", 1)
        self.assertEqual(self.pos(adapter, "X"), 5.0)
        snap = adapter.snapshot()
        self.assertEqual(snap.message, RUNNING_TEXT)
        self.assertFalse(snap.can_jog)

    def test_refused_while_homing(self):
        adapter = self.mock()
        adapter._homing = adapter._homing.with_phase("Y", HomingPhase.HOMING)
        adapter.jog("Z", 1)
        self.assertEqual(self.pos(adapter, "Z"), 0.0)
        self.assertEqual(adapter.snapshot().message, HOMING_TEXT)

    def test_unhomed_caps_and_homed_cut(self):
        adapter = self.mock()
        adapter.set_jog_step(10.0)
        adapter.jog("Z", -1)
        self.assertEqual((self.pos(adapter, "Z"), adapter.snapshot().message), (0.0, Z_DOWN_10))
        adapter.home_all()
        adapter.jog("Z", 1)
        self.assertAlmostEqual(self.pos(adapter, "Z"), -3.0, delta=0.001)
        self.assertEqual(adapter.snapshot().message, Z_CUT)
        adapter.jog("Z", 1)
        self.assertEqual(adapter.snapshot().message, Z_AT_LIMIT)

    def test_bad_input_like_the_real_adapter(self):
        adapter = self.mock()
        with self.assertRaises(KeyError):
            adapter.jog("Q", 1)
        for bad in (0, 2):
            with self.subTest(direction=bad):
                with self.assertRaises(ValueError):
                    adapter.jog("X", bad)
        for bad in (0, -1, float("nan"), float("inf")):
            with self.subTest(step=bad):
                adapter.set_jog_step(bad)
                self.assertEqual(adapter.snapshot().jog_step_mm, 1.0)
                self.assertIn("Geçersiz jog adımı", adapter.snapshot().message)

    def test_session_drops_what_the_adapter_raises(self):
        adapter = self.mock()
        session = Session(adapter, PROFILE, CONFIG)
        for value in ("Q:1", "X:0", "X:abc"):
            with self.subTest(value=value):
                session.act("jog", value)
        self.assertEqual([p.machine for p in adapter.snapshot().positions], [0.0, 0.0, 0.0])


class TestSameTableSameDecisions(_Case):
    """Test (8): one table, both adapters. Rows the mock can stand in: it
    has no moving, paused or MDI-running state (its moves complete at
    once), so those rows are the real adapter's alone (above)."""

    #: (name, homed, step, axis, direction, expected message)
    ROWS = (
        ("off", None, 1.0, "X", 1, OFF_TEXT),
        ("homing", "homing", 1.0, "Z", 1, HOMING_TEXT),
        ("running", "running", 1.0, "X", 1, RUNNING_TEXT),
        ("unhomed Z- 10", False, 10.0, "Z", -1, Z_DOWN_10),
        ("unhomed Z- 1", False, 1.0, "Z", -1, "Jog Z−1 mm."),
        ("unhomed X 50", False, 50.0, "X", 1, X_50),
        ("unhomed X 10", False, 10.0, "X", 1, "Jog X+10 mm."),
        ("homed Z+10 at -5", True, 10.0, "Z", 1, Z_CUT),
        ("homed X-1 at 5", True, 1.0, "X", -1, "Jog X−1 mm."),
        ("homed X-10 at 5", True, 10.0, "X", -1,
         "X− en çok 2.000 mm gidebilir (yumuşak limit 3.000 mm) — adım 2.000 mm'ye kısaltıldı."),
    )

    def real(self, homed, step):
        adapter = self.make(machine=F.FakeMachine(home_instantly=homed != "homing",
                                                  soft_limits=WINDOWS))
        if homed is not None:
            self.machine.power_on(homed=homed is True or homed == "running")
        if homed == "homing":
            self.machine.issue_home(-1)
        if homed == "running":
            self.machine.file = self.ngc()
            self.machine.task_mode, self.machine.interp_state = C["MODE_AUTO"], C["INTERP_READING"]
            self.machine.motion_mode = C["TRAJ_MODE_COORD"]
        adapter.set_jog_step(step)
        return adapter

    @staticmethod
    def mock(homed, step):
        adapter = MockAdapter(PROFILE)
        if homed is not None:
            adapter.software_estop_reset()
        if homed is True or homed == "running":
            adapter.home_all()
        if homed == "homing":
            adapter._homing = adapter._homing.with_phase("Y", HomingPhase.HOMING)
        if homed == "running":
            adapter.load_program("deneme.ngc")
            adapter.program_start()
        adapter.set_jog_step(step)
        return adapter

    def test_rows(self):
        for name, homed, step, axis, direction, message in self.ROWS:
            with self.subTest(row=name):
                real, mock = self.real(homed, step), self.mock(homed, step)
                before = {
                    "real": [p.machine for p in real.snapshot().positions],
                    "mock": [p.machine for p in mock.snapshot().positions],
                }
                real.jog(axis, direction)
                mock.jog(axis, direction)
                moved = {}
                for who, adapter in (("real", real), ("mock", mock)):
                    snap = adapter.snapshot()
                    self.assertEqual(snap.message, message, who)
                    moved[who] = [round(a - b, 3) for a, b in
                                  zip([p.machine for p in snap.positions], before[who])]
                self.assertEqual(moved["real"], moved["mock"])


# ---------------------------------------------------------------------------
# The jog card
# ---------------------------------------------------------------------------


def _visible(markup: str) -> str:
    """The markup as the kiosk shows it: title attributes removed (app.js
    noTooltips), tags dropped, entities decoded."""
    markup = re.sub(r'\stitle="[^"]*"', "", markup)
    return htmllib.unescape(re.sub(r"<[^>]+>", " ", markup))


def _jog_card(markup: str) -> str:
    start = markup.index('<span class="card-title">Jog</span>')
    return markup[start:markup.index("</section>", start)]


def _buttons(markup: str):
    """{data-value: disabled?} of the jog pad's buttons."""
    out = {}
    for attrs in re.findall(r"<button ([^>]*)>", markup):
        if 'data-action="jog"' in attrs:
            value = re.search(r'data-value="([^"]*)"', attrs).group(1)
            out[value] = " disabled" in attrs or attrs.endswith("disabled")
    return out


def _chips(markup: str):
    out = {}
    for attrs in re.findall(r"<button ([^>]*)>", markup):
        if 'data-action="jog_step"' in attrs:
            value = re.search(r'data-value="([^"]*)"', attrs).group(1)
            out[value] = "disabled" in attrs.split()
    return out


class TestTheJogCard(unittest.TestCase):
    """Test (7): each closed button and chip says why, as text."""

    def vm(self, state: MachineState, observe: bool = False):
        return VM.build(PROFILE, CONFIG, state, "kontrol", observe)

    def card(self, state: MachineState, observe: bool = False) -> str:
        return _jog_card(R.render_body(self.vm(state, observe)))

    def test_homing_turns_every_button_off_with_the_reason_visible(self):
        state = state_of(homed="Z", homing="Y", at=(0.0, 0.0, -5.0))
        c = self.vm(state)["kontrol"]
        self.assertFalse(c["can_jog"])
        reason = "Referans alma sürüyor — jog kapalı. Durdurmak için «Durdur»."
        self.assertEqual(c["jog_disabled_reason"], reason)
        card = self.card(state)
        self.assertTrue(all(_buttons(card).values()), _buttons(card))
        self.assertIn(reason, _visible(card))

    def test_running_and_paused_say_so(self):
        for kw, reason in (
            (dict(program_running=True, task_mode="auto", interp_state="reading"),
             "Program çalışıyor — jog kapalı."),
            (dict(task_mode="auto", interp_state="paused", program_paused=True),
             "Program duraklatıldı — jog kapalı."),
        ):
            with self.subTest(reason=reason):
                state = state_of(homed="XYZ", at=HOME, **kw)
                self.assertIn(reason, _visible(self.card(state)))
                self.assertTrue(all(_buttons(self.card(state)).values()))

    def test_unhomed_ten_mm_only_z_down_is_off_and_says_why(self):
        state = state_of()._replace(jog_step_mm=10.0)
        card = self.card(state)
        buttons = _buttons(card)
        self.assertEqual({v for v, off in buttons.items() if off}, {"Z:-1"})
        self.assertIn(Z_DOWN_10, _visible(card))
        self.assertEqual(self.vm(state)["kontrol"]["jog_pad"]["reasons"], [Z_DOWN_10])

    def test_unhomed_the_fifty_mm_chip_is_off_and_says_why(self):
        card = self.card(state_of())
        self.assertTrue(_chips(card)["50.0"])
        self.assertFalse(_chips(card)["10.0"])
        self.assertIn("Referans yok — 50 mm adım kapalı; referanssız en çok 10 mm.", _visible(card))
        homed = self.card(state_of(homed="XYZ", at=HOME))
        self.assertFalse(any(_chips(homed).values()))
        partly = self.card(state_of(homed="Z", at=HOME))
        self.assertTrue(_chips(partly)["50.0"])

    def test_a_stale_fifty_mm_step_unhomed_turns_every_pad_button_off(self):
        """Chosen while homed, then unhomed (a link reset): every button off
        with a reason per axis, each written once."""
        state = state_of()._replace(jog_step_mm=50.0)
        c = self.vm(state)["kontrol"]
        self.assertTrue(all(not b["enabled"] for b in c["jog_pad"]["buttons"].values()))
        reasons = c["jog_pad"]["reasons"]
        self.assertEqual(len(reasons), len(set(reasons)))
        self.assertEqual(reasons[0], X_50)
        visible = _visible(self.card(state))
        for r in reasons:
            self.assertIn(r, visible)

    def test_homed_on_the_soft_limit_the_button_says_so(self):
        state = state_of(homed="XYZ", at=(3.0, 5.0, -3.0))
        card = self.card(state)
        self.assertEqual({v for v, off in _buttons(card).items() if off}, {"X:-1", "Z:1"})
        visible = _visible(card)
        self.assertIn(Z_AT_LIMIT, visible)
        self.assertIn("X yumuşak limitte (3.000 mm) — X− yönünde jog yok.", visible)

    def test_a_cut_step_leaves_the_button_on(self):
        """A step longer than the room is cut, not refused: the button
        stays on (the message says the cut after the click)."""
        state = state_of(homed="XYZ", at=HOME)._replace(jog_step_mm=10.0)
        self.assertFalse(any(_buttons(self.card(state)).values()))

    def test_moving_does_not_flicker_the_pad(self):
        card = self.card(state_of(homed="XYZ", at=HOME, moving=True))
        self.assertFalse(any(_buttons(card).values()))
        self.assertNotIn("Hareket sürüyor", _visible(card))

    def test_the_soft_limit_line(self):
        self.assertIn("Yumuşak limitler: etkin.", _visible(self.card(state_of(homed="XYZ", at=HOME))))
        self.assertIn("Yumuşak limitler: etkin değil (referans yok) — adım en çok 10 mm, Z− en "
                      "çok 1 mm (Z alt switch'i yok).", _visible(self.card(state_of())))
        self.assertIn("Yumuşak limitler: yalnız Z için etkin (X, Y referanssız) — adım en çok "
                      "10 mm.", _visible(self.card(state_of(homed="Z", at=HOME))))
        unknown = state_of().as_status_unknown()
        self.assertIn("Yumuşak limitler: bilinmiyor", _visible(self.card(unknown)))
        marble = load_profile("marble")
        vm = VM.build(marble, load_machine_config(marble), MockAdapter(marble).snapshot())
        self.assertEqual(vm["kontrol"]["soft_limits"], "Yumuşak limitler: INI'de tanımsız.")

    def test_status_unknown_no_chip_reads_a_homing_flag(self):
        unknown = state_of(homed="XYZ", at=HOME).as_status_unknown()
        chips = {c["mm"]: c for c in self.vm(unknown)["kontrol"]["jog_step_chips"]}
        self.assertFalse(chips[50.0]["enabled"])
        self.assertEqual(chips[50.0]["reason"], "Makine durumu bilinmiyor — 50 mm adım kapalı.")
        self.assertTrue(chips[1.0]["enabled"])

    def test_observer_mode_says_its_reason_once(self):
        card = self.card(state_of(homed="XYZ", at=HOME), observe=True)
        self.assertTrue(all(_buttons(card).values()))
        self.assertTrue(all(_chips(card).values()))
        self.assertEqual(_visible(card).count(VM.OBSERVE_REASON), 1)

    def test_the_mdi_panel_keeps_its_own_rule(self):
        """LinuxCNC queues an MDI line behind a running one
        (emctaskmain.cc:1422-1439): the jog card is off while an MDI line
        runs, the MDI panel is not."""
        state = state_of(homed="XYZ", at=HOME, task_mode="mdi", interp_state="reading",
                         mdi_running=True, motion_mode="coord")
        c = self.vm(state)["kontrol"]
        self.assertFalse(c["can_jog"])
        self.assertTrue(c["mdi"]["enabled"])
        self.assertTrue(state.motion_enabled)

    def test_the_mdi_panel_is_off_while_something_is_paused(self):
        """P0 cleanup item 4 (final P0 review): off while a program or an
        MDI line is paused - mdi_refusal's `paused` - with MDI_WHILE_PAUSED
        as visible text."""
        from bettercnc import render as R
        from bettercnc.mdi import MDI_WHILE_PAUSED
        for name, kw in (
            ("program", dict(task_mode="auto", interp_state="paused", program_paused=True)),
            ("mdi line", dict(task_mode="mdi", interp_state="paused", program_paused=True,
                              mdi_running=True)),
            ("step left over", dict(task_mode="auto", interp_state="idle",
                                    program_paused=True)),
        ):
            with self.subTest(paused=name):
                state = state_of(homed="XYZ", at=HOME, motion_mode="coord", **kw)
                vm = self.vm(state)
                m = vm["kontrol"]["mdi"]
                self.assertFalse(m["enabled"])
                self.assertEqual(m["disabled_reason"], MDI_WHILE_PAUSED)
                self.assertIn(MDI_WHILE_PAUSED, _visible(R.render_body(vm)))
        running = state_of(homed="XYZ", at=HOME, task_mode="auto", interp_state="reading",
                           program_running=True, motion_mode="coord")
        self.assertTrue(self.vm(running)["kontrol"]["mdi"]["enabled"])

    def test_a_link_unread_is_named_as_such(self):
        """Known gap closed: with the board link not read, the jog card and
        the MDI panel said 'Makine devre dışı'."""
        from bettercnc.state import LinkHealth
        state = state_of(homed="XYZ", at=HOME, link=LinkHealth(False, None, None, None, None))
        c = self.vm(state)["kontrol"]
        self.assertEqual(c["jog_disabled_reason"], "Kart bağlantısı okunamıyor — jog kapalı.")
        self.assertEqual(c["mdi"]["disabled_reason"],
                         "Kart bağlantısı okunamıyor — MDI kabul edilmiyor.")


class TestTheMockCardIsLive(unittest.TestCase):
    """The default adapter after «Yazılım sıfırla» and Home All: every
    button on with the default step (it stands at HOME, inside the soft
    limits), the 50 mm chip on."""

    def test_ready_mock(self):
        adapter = MockAdapter(PROFILE)
        adapter.software_estop_reset()
        adapter.home_all()
        markup = _jog_card(R.render_body(VM.build(PROFILE, CONFIG, adapter.snapshot())))
        self.assertFalse(any(_buttons(markup).values()))
        self.assertFalse(any(_chips(markup).values()))
        self.assertTrue(math.isclose(adapter.snapshot().positions[2].machine, -5.0))


if __name__ == "__main__":
    unittest.main()
