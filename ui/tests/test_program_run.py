"""Plan WP09: running a program - pause, resume, single step, the status
label, the line and the progress, and the end message.

- the fake's PLAN_PAUSE / PLAN_RESUME / PLAN_STEP model, against
  reference/linuxcnc where it is present;
- programs' run rules (start_busy, pause_refusal, resume_refusal,
  step_refusal), line_text and progress;
- LinuxCncAdapter on the fake: what goes out, what is refused with nothing
  sent, the stop sequence, the run followed to its end;
- MockAdapter: the same rules;
- MachineState: can_start_program, status_label, status_severity;
- the Program screen: one primary button by what runs, «Adım», the reasons
  as visible text, the run line and the gate.
"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
import unittest
from unittest import mock

import helpers  # noqa: F401  (puts ui/ on sys.path)

import fake_linuxcnc as F
import test_linuxcnc_adapter as T
from bettercnc import adapter as A
from bettercnc import app
from bettercnc import programs
from bettercnc import render as R
from bettercnc import viewmodel as VM
from bettercnc.adapter import MockAdapter
from bettercnc.configmodel import load_machine_config
from bettercnc.profiles import load_profile
from bettercnc.state import Severity
from test_jog_rules import state_of

C = F.CONSTANTS
WAIT = A.WAIT_TIMEOUT_S
PROFILE = load_profile("test-rig")
PAUSE = ("auto", (C["AUTO_PAUSE"],))
RESUME = ("auto", (C["AUTO_RESUME"],))
STEP = ("auto", (C["AUTO_STEP"],))
RUN = ("auto", (C["AUTO_RUN"], 0))
WAITED = ("wait_complete", (WAIT,))

#: Ten lines; line 5 is "G1 X5".
PROGRAM_TEXT = "".join("G1 X{}\n".format(n) for n in range(1, 10)) + "M2\n"


def visible(markup: str) -> str:
    """As the kiosk shows it: app.js removes every title attribute."""
    return re.sub(r' title="[^"]*"', "", markup)


def start_row(markup: str) -> str:
    start = markup.index('<div class="row prog-run-row">')
    return markup[start:markup.index("</section>", start)]


def button_of(markup: str, action: str) -> str:
    m = re.search(r'<button [^>]*data-action="{}"[^>]*>[^<]*</button>'.format(action), markup)
    assert m, action
    return m.group(0)


class ProgramFileCase(unittest.TestCase):
    """A ten-line program in a temporary directory; programs' caches empty
    before and after."""

    def setUp(self):
        super().setUp()
        self.dir = tempfile.mkdtemp(prefix="bettercnc-run-")
        self.addCleanup(shutil.rmtree, self.dir, True)
        for cache in (programs._listings, programs._counts):
            cache.clear()
            self.addCleanup(cache.clear)
        self.path = os.path.join(self.dir, "kare.ngc")
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write(PROGRAM_TEXT)


# ---------------------------------------------------------------------------
# The fake
# ---------------------------------------------------------------------------


class TestFakePauseResumeStep(unittest.TestCase):
    """FakeMachine.issue_pause / issue_resume / issue_step and the planner
    tables, through the fake binding."""

    def setUp(self):
        cm = F.installed()
        self.linuxcnc, _, self.m, _ = cm.__enter__()
        self.addCleanup(cm.__exit__, None, None, None)
        self.m.power_on(homed=True)
        self.c = self.linuxcnc.command()

    def running(self):
        self.m.task_mode = C["MODE_AUTO"]
        self.m.motion_mode = C["TRAJ_MODE_COORD"]
        self.m.interp_state = C["INTERP_READING"]

    def test_pause_then_resume_in_auto(self):
        self.running()
        self.c.auto(C["AUTO_PAUSE"])
        self.assertEqual((self.m.paused, self.m.interp_state), (True, C["INTERP_PAUSED"]))
        self.assertEqual(self.m.interp_resume_state, C["INTERP_READING"])
        self.c.auto(C["AUTO_PAUSE"])        # a second pause keeps the state to resume to
        self.assertEqual(self.m.interp_resume_state, C["INTERP_READING"])
        self.c.auto(C["AUTO_RESUME"])
        self.assertEqual((self.m.paused, self.m.interp_state), (False, C["INTERP_READING"]))
        self.assertEqual(list(self.m.errors), [])

    def test_pause_and_resume_are_taken_in_manual_and_mdi(self):
        """emctaskmain.cc:947, :950 (MANUAL) and :1402, :1407 (MDI)."""
        for mode in ("MODE_MANUAL", "MODE_MDI"):
            with self.subTest(mode=mode):
                self.m.task_mode = C[mode]
                self.m.interp_state = C["INTERP_IDLE"]
                self.c.auto(C["AUTO_PAUSE"])
                self.assertEqual(self.m.interp_state, C["INTERP_PAUSED"])
                self.assertTrue(self.m.paused)
                self.c.auto(C["AUTO_RESUME"])
                self.assertEqual(self.m.interp_state, C["INTERP_IDLE"])
                self.assertEqual(list(self.m.errors), [])

    def test_step_is_refused_in_manual_and_mdi(self):
        for mode, word in (("MODE_MANUAL", "manual mode"), ("MODE_MDI", "MDI mode")):
            with self.subTest(mode=mode):
                self.m.errors.clear()
                self.m.task_mode = C[mode]
                self.c.auto(C["AUTO_STEP"])
                self.assertEqual(self.m.interp_state, C["INTERP_IDLE"])
                (kind, text), = list(self.m.errors)
                self.assertEqual(text, "can't do that (EMC_TASK_PLAN_STEP:511) in {}".format(word))
                self.assertEqual(self.c.wait_complete(WAIT), C["RCS_ERROR"])

    def test_step_from_auto_idle_runs_the_program_paused(self):
        """emctaskmain.cc:1078-1091: PLAN_RUN, then paused at once."""
        self.m.task_mode = C["MODE_AUTO"]
        self.c.auto(C["AUTO_STEP"])
        self.assertEqual((self.m.paused, self.m.interp_state), (True, C["INTERP_PAUSED"]))
        self.assertEqual(self.m.interp_resume_state, C["INTERP_READING"])
        self.assertEqual(list(self.m.errors), [])

    def test_step_from_auto_idle_unhomed_is_refused_as_a_run(self):
        self.m.homed = [0] * C["MAX_JOINTS"]
        self.m.task_mode = C["MODE_AUTO"]
        self.c.auto(C["AUTO_STEP"])
        self.assertEqual(self.m.interp_state, C["INTERP_IDLE"])
        self.assertEqual([t for _, t in self.m.errors], ["Can't run a program when not homed"])

    def test_step_while_paused_steps_motion_or_the_interpreter(self):
        """emctaskmain.cc:1251-1261: motions queued -> emcTrajStep, motion
        stays paused (command.c:1200-1212); none -> the interpreter back."""
        self.running()
        self.c.auto(C["AUTO_PAUSE"])
        self.m.traj_queue = 3
        self.c.auto(C["AUTO_STEP"])
        self.assertEqual((self.m.motion_steps, self.m.paused, self.m.interp_state),
                         (1, True, C["INTERP_PAUSED"]))
        self.assertTrue(self.m.stepping)
        self.m.traj_queue = 0
        self.c.auto(C["AUTO_STEP"])
        self.assertEqual((self.m.motion_steps, self.m.interp_state), (1, C["INTERP_READING"]))
        self.c.auto(C["AUTO_RESUME"])
        self.assertFalse(self.m.stepping)

    def test_a_busy_interpreter_refuses_a_run_but_takes_a_step(self):
        self.running()
        self.c.auto(C["AUTO_STEP"])
        self.assertTrue(self.m.stepping)
        self.assertEqual(list(self.m.errors), [])
        self.c.auto(C["AUTO_RUN"], 0)
        self.assertEqual([t for _, t in self.m.errors],
                         ["can't do that (EMC_TASK_PLAN_RUN) in auto mode with the interpreter reading"])
        self.m.errors.clear()
        self.c.auto(C["AUTO_PAUSE"])
        self.c.auto(C["AUTO_RUN"], 0)
        self.assertEqual([t for _, t in self.m.errors],
                         ["can't do that (EMC_TASK_PLAN_RUN) in auto mode with the interpreter paused"])

    def test_abort_and_leaving_for_manual_clear_the_pause(self):
        for end in ("abort", "manual"):
            with self.subTest(end=end):
                self.running()
                self.c.auto(C["AUTO_PAUSE"])
                self.c.auto(C["AUTO_STEP"])
                if end == "abort":
                    self.c.abort()
                else:
                    # Leaving AUTO with the interpreter busy is refused
                    # (emctaskmain.cc:2127-2130); MDI to MANUAL is not.
                    self.m.task_mode = C["MODE_MDI"]
                    self.c.mode(C["MODE_MANUAL"])
                self.assertEqual((self.m.paused, self.m.stepping, self.m.interp_state),
                                 (False, False, C["INTERP_IDLE"]))

    def test_switching_to_auto_or_mdi_aborts(self):
        """emcTaskSetMode(AUTO or MDI) calls emcTaskAbort (emctask.cc:
        286-296): an MDI line still running ends."""
        self.m.task_mode = C["MODE_MDI"]
        self.m.motion_mode = C["TRAJ_MODE_COORD"]
        self.m.interp_state = C["INTERP_READING"]
        self.m.paused = True
        self.c.mode(C["MODE_AUTO"])
        self.assertEqual((self.m.task_mode, self.m.interp_state, self.m.paused),
                         (C["MODE_AUTO"], C["INTERP_IDLE"], False))


class TestFakeAgainstTheSource(unittest.TestCase):
    """The model above, checked against reference/linuxcnc: content, not
    line numbers."""

    @staticmethod
    def section(text: str, start: str, end: str) -> str:
        i = text.index(start)
        return text[i:text.index(end, i + len(start))]

    @T.NEEDS_REFERENCE
    def test_the_planner_lists_pause_resume_and_step_where_the_fake_does(self):
        text = T._src("emc/task/emctaskmain.cc")
        sections = {
            "manual": (self.section(text, "case EMC_TASK_MODE_MANUAL:\t// ON, MANUAL",
                                    "case EMC_TASK_MODE_AUTO:\t// ON, AUTO"), F._MANUAL_OK),
            "auto idle": (self.section(text, "// ON, AUTO, IDLE", "// ON, AUTO, READING"),
                          F._AUTO_IDLE_OK),
            "auto reading": (self.section(text, "// ON, AUTO, READING", "// ON, AUTO, PAUSED"),
                             F._AUTO_READING_OK),
            "auto paused": (self.section(text, "// ON, AUTO, PAUSED",
                                         "case EMC_TASK_INTERP_WAITING:"), F._AUTO_PAUSED_OK),
            "auto waiting": (self.section(text, "case EMC_TASK_INTERP_WAITING:",
                                          "case EMC_TASK_MODE_MDI:\t// ON, MDI"),
                             F._AUTO_WAITING_OK),
            "mdi": (self.section(text, "case EMC_TASK_MODE_MDI:\t// ON, MDI",
                                 "can't do that (%s:%d) in MDI mode"), F._MDI_OK),
        }
        for name, (body, table) in sections.items():
            for msg in ("EMC_TASK_PLAN_PAUSE", "EMC_TASK_PLAN_RESUME", "EMC_TASK_PLAN_STEP",
                        "EMC_TASK_PLAN_EXECUTE"):
                with self.subTest(section=name, msg=msg):
                    self.assertEqual("case {}_TYPE:".format(msg) in body, msg in table)

    @T.NEEDS_REFERENCE
    def test_the_issue_bodies_the_fake_follows(self):
        text = re.sub(r"\s+", " ", T._src("emc/task/emctaskmain.cc"))
        pause = self.section(text, "case EMC_TASK_PLAN_PAUSE_TYPE: emcTrajPause();", "break;")
        self.assertIn("interpResumeState = emcStatus->task.interpState;", pause)
        self.assertIn("emcStatus->task.interpState = EMC_TASK_INTERP_PAUSED;", pause)
        resume = self.section(text, "case EMC_TASK_PLAN_RESUME_TYPE: emcTrajResume();", "break;")
        self.assertIn("(enum EMC_TASK_INTERP_ENUM) interpResumeState;", resume)
        self.assertIn("stepping = 0;", resume)
        idle_step = self.section(text, "case EMC_TASK_PLAN_STEP_TYPE: // handles case where "
                                 "first action is to step the program", "retval = 0;")
        self.assertIn("retval = emcTaskIssueCommand(&taskPlanRunCmd);", idle_step)
        self.assertIn("emcTrajPause();", idle_step)
        paused_step = self.section(text, "case EMC_TASK_PLAN_STEP_TYPE: stepping = 1; "
                                   "steppingWait = 0; if (emcStatus->motion.traj.paused",
                                   "break;")
        self.assertIn("emcStatus->motion.traj.queue > 0", paused_step)
        self.assertIn("emcTrajStep();", paused_step)
        motion = re.sub(r"\s+", " ", T._src("emc/motion/command.c"))
        step = self.section(motion, "case EMCMOT_STEP:", "break;")
        self.assertIn("tpResume(&emcmotInternal->coord_tp); emcmotStatus->paused = 1;", step)

    @T.NEEDS_REFERENCE
    def test_the_binding_sends_pause_resume_and_step(self):
        text = re.sub(r"\s+", " ", T._src("emc/usr_intf/axis/extensions/emcmodule.cc"))
        for fn, var in (("PAUSE", "pause"), ("RESUME", "resume"), ("STEP", "step")):
            with self.subTest(fn=fn):
                self.assertIn("case LOCAL_AUTO_{}: emcSendCommand(s, {});".format(fn, var), text)

    @T.NEEDS_REFERENCE
    def test_axis_gates_the_adapters_follow(self):
        axis = re.sub(r"\s+", " ", T._src("emc/usr_intf/axis/scripts/axis.py"))
        pause = self.section(axis, "def task_pause(*event):", "def task_reverse")
        self.assertIn("s.task_mode != linuxcnc.MODE_AUTO or s.interp_state not in "
                      "(linuxcnc.INTERP_READING, linuxcnc.INTERP_WAITING)", pause)
        resume = self.section(axis, "def task_resume(*event):", "def task_pauseresume")
        self.assertIn("if not s.paused: return", resume)
        self.assertIn("if s.task_mode not in (linuxcnc.MODE_AUTO, linuxcnc.MODE_MDI): return",
                      resume)
        step = self.section(axis, "def task_step(*event):", "def task_pause")
        self.assertIn("ensure_mode(linuxcnc.MODE_AUTO) c.auto(linuxcnc.AUTO_STEP)", step)


# ---------------------------------------------------------------------------
# programs: pure
# ---------------------------------------------------------------------------


class TestRunRules(unittest.TestCase):
    def test_each_rule_by_what_runs(self):
        idle = state_of(homed="XYZ")
        running = idle._replace(task_mode="auto", interp_state="reading", program_running=True)
        waiting = running._replace(interp_state="waiting")
        paused = idle._replace(task_mode="auto", interp_state="paused", program_paused=True)
        mdi = idle._replace(task_mode="mdi", interp_state="reading", mdi_running=True)
        mdi_paused = mdi._replace(interp_state="paused", program_paused=True)
        manual_paused = idle._replace(interp_state="paused", program_paused=True)
        unread = idle._replace(program_paused=None)
        unknown = idle._replace(status_known=False)
        P = programs
        # state: (start_busy, pause_refusal, resume_refusal, step_refusal)
        table = (
            ("idle", idle, ("", P.PAUSE_IDLE, P.RESUME_NOT_PAUSED, "")),
            ("running", running, (P.START_RUNNING, "", P.RESUME_NOT_PAUSED, P.STEP_RUNNING)),
            ("waiting", waiting, (P.START_RUNNING, "", P.RESUME_NOT_PAUSED, P.STEP_RUNNING)),
            ("paused", paused, (P.START_PAUSED, P.PAUSE_ALREADY, "", "")),
            ("mdi", mdi, (P.START_MDI, "", P.RESUME_NOT_PAUSED, P.STEP_MDI)),
            ("mdi paused", mdi_paused, (P.START_PAUSED, P.PAUSE_ALREADY, "", P.STEP_MDI)),
            ("manual paused", manual_paused,
             (P.START_PAUSED, P.PAUSE_ALREADY, P.PAUSED_IN_MANUAL, P.PAUSED_IN_MANUAL)),
            ("unread", unread, (P.RUN_UNREAD,) * 4),
            ("unknown", unknown, (P.RUN_UNKNOWN,) * 4),
        )
        for name, state, want in table:
            with self.subTest(state=name):
                got = (P.start_busy(state), P.pause_refusal(state), P.resume_refusal(state),
                       P.step_refusal(state))
                self.assertEqual(got, want)

    def test_progress(self):
        self.assertEqual(programs.progress(5, 10), 0.5)
        self.assertEqual(programs.progress(12, 10), 1.0)
        for line, lines in ((None, 10), (0, 10), (5, None), (5, 0), (-1, 10)):
            with self.subTest(line=line, lines=lines):
                self.assertEqual(programs.progress(line, lines), 0.0)


class TestLineText(ProgramFileCase):
    def test_the_line_motion_runs_from_the_copy_made_at_load(self):
        self.assertIsNone(programs.line_text(self.path, 5))       # not counted yet
        self.assertEqual(programs.line_count(self.path), 10)
        self.assertEqual(programs.line_text(self.path, 5), "G1 X5")
        self.assertEqual(programs.line_text(self.path, 10), "M2")
        for line in (None, 0, -1, 11):
            with self.subTest(line=line):
                self.assertIsNone(programs.line_text(self.path, line))
        self.assertIsNone(programs.line_text(None, 1))

    def test_long_lines_are_cut_and_odd_bytes_replaced(self):
        with open(self.path, "wb") as fh:
            fh.write(b"G1 X1 (" + b"a" * 300 + b")\r\nG1 \xff X2\n")
        programs.line_count(self.path)
        first = programs.line_text(self.path, 1)
        self.assertEqual(len(first), programs.LINE_TEXT_MAX)
        self.assertTrue(first.startswith("G1 X1 (aaa"))
        self.assertEqual(programs.line_text(self.path, 2), "G1 � X2")

    def test_a_large_file_is_counted_but_keeps_no_copy(self):
        with mock.patch.object(programs, "MAX_TEXT_BYTES", 10):
            self.assertEqual(programs.line_count(self.path), 10)
        self.assertIsNone(programs.line_text(self.path, 1))


# ---------------------------------------------------------------------------
# MachineState
# ---------------------------------------------------------------------------


class TestStateLabelAndGate(unittest.TestCase):
    def setUp(self):
        self.idle = state_of(homed="XYZ")._replace(program_file="/n/kare.ngc")

    def test_labels(self):
        idle = self.idle
        cases = (
            ("idle", idle, "HAZIR"),
            ("running", idle._replace(task_mode="auto", interp_state="reading",
                                      program_running=True), "ÇALIŞIYOR"),
            ("running a line", idle._replace(task_mode="auto", interp_state="reading",
                                             program_running=True, program_line=7),
             "ÇALIŞIYOR · satır 7"),
            ("paused", idle._replace(task_mode="auto", interp_state="paused",
                                     program_paused=True, program_line=7), "DURAKLATILDI"),
            ("a step's motion", idle._replace(task_mode="auto", interp_state="waiting",
                                              program_paused=True, moving=True), "TEK ADIM"),
            ("mdi", idle._replace(task_mode="mdi", interp_state="reading", mdi_running=True),
             "MDI YÜRÜTÜLÜYOR"),
            ("mdi paused", idle._replace(task_mode="mdi", interp_state="paused",
                                         mdi_running=True, program_paused=True), "DURAKLATILDI"),
            ("paused, machine off", idle._replace(
                estop=idle.estop.software_estop(), program_paused=True), "DEVRE DIŞI"),
        )
        for name, state, label in cases:
            with self.subTest(state=name):
                self.assertEqual(state.status_label, label)

    def test_a_pause_is_a_warning(self):
        self.assertIs(self.idle.status_severity, Severity.INFO)
        self.assertIs(self.idle._replace(program_paused=True).status_severity, Severity.WARN)

    def test_start_is_off_while_anything_runs_or_is_paused_or_unread(self):
        self.assertTrue(self.idle.can_start_program)
        for name, kw in (
            ("running", dict(task_mode="auto", interp_state="reading", program_running=True)),
            ("paused", dict(task_mode="auto", interp_state="paused", program_paused=True)),
            ("mdi", dict(task_mode="mdi", interp_state="reading", mdi_running=True)),
            ("paused unread", dict(program_paused=None)),
            ("mdi unread", dict(mdi_running=None)),
            ("interp unread", dict(interp_state=None)),
            ("no file", dict(program_file=None)),
        ):
            with self.subTest(state=name):
                self.assertFalse(self.idle._replace(**kw).can_start_program)
                self.assertEqual(self.idle._replace(**kw).start_blocks, [])


class TestMapping(unittest.TestCase):
    def test_an_mdi_line_is_not_a_program_and_a_pause_is_not_running(self):
        def mapped(**over):
            stat = dict(task_state=4, task_mode=2, interp_state=2, paused=False, file="/a.ngc",
                        joint=[{"homed": 1, "homing": 0}] * 3, motion_line=4)
            stat.update(over)
            return A.map_linuxcnc_state(
                stat=type("S", (), stat)(), hal_values={}, profile=PROFILE,
                joint_axis_map=["X", "Y", "Z"], joint_config={})

        self.assertTrue(mapped().program_running)
        self.assertEqual(mapped().program_line, 4)
        self.assertFalse(mapped(task_mode=3).program_running)          # MDI, file loaded
        self.assertTrue(mapped(task_mode=3).mdi_running)
        self.assertFalse(mapped(paused=True).program_running)
        self.assertFalse(mapped(interp_state=3).program_running)
        self.assertTrue(mapped(interp_state=3).program_paused)
        self.assertFalse(mapped(interp_state=1).program_running)
        self.assertTrue(mapped(file="").program_running)                # stat.file aside


# ---------------------------------------------------------------------------
# LinuxCncAdapter on the fake
# ---------------------------------------------------------------------------


class FakeRunCase(T.FakeCase, ProgramFileCase):
    def setUp(self):
        ProgramFileCase.setUp(self)

    def loaded(self, homed: bool = True, **kw):
        adapter = self.on(homed=homed, **kw)
        adapter.load_program(self.path)
        adapter.snapshot()
        del self.machine.log[:]
        return adapter

    def started(self, **kw):
        adapter = self.loaded(**kw)
        adapter.program_start()
        adapter.snapshot()
        del self.machine.log[:]
        return adapter


class TestPause(FakeRunCase):
    def test_pause_goes_only_while_a_program_runs(self):
        adapter = self.loaded()
        adapter.program_pause()
        self.assertEqual(self.machine.log, [])
        self.assertEqual(adapter.snapshot().message, programs.PAUSE_IDLE)

        adapter.program_start()
        del self.machine.log[:]
        adapter.program_pause()
        self.assertEqual(self.machine.log, [PAUSE, WAITED])
        self.assertNoLinuxCncErrors()
        snap = adapter.snapshot()
        self.assertTrue(snap.program_paused)
        self.assertFalse(snap.program_running)
        self.assertEqual(snap.status_label, "DURAKLATILDI")
        self.assertEqual(snap.message, programs.PAUSED_TEXT)

        del self.machine.log[:]
        adapter.program_pause()
        self.assertEqual(self.machine.log, [])
        self.assertEqual(adapter.snapshot().message, programs.PAUSE_ALREADY)

    def test_an_mdi_line_pauses_too(self):
        adapter = self.loaded()
        self.run_mdi_program()
        adapter.program_pause()
        self.assertEqual(self.machine.log, [PAUSE, WAITED])
        self.assertEqual(adapter.snapshot().message, programs.MDI_PAUSED_TEXT)

    def test_a_pause_that_did_not_take_says_so(self):
        class Deaf(F.FakeMachine):
            def issue_pause(self):
                pass

        adapter = self.on(homed=True, machine=Deaf())
        adapter.load_program(self.path)
        adapter.program_start()
        adapter.program_pause()
        snap = adapter.snapshot()
        self.assertEqual((snap.message, snap.message_severity),
                         (programs.PAUSE_NOT_TAKEN, Severity.WARN))

    def test_pause_does_not_check_the_stop_sequence(self):
        """«Duraklat» starts no motion: a stop before it does not hold it."""
        adapter = self.started()
        adapter._note_stop(A.STOP_REFUSAL)
        adapter.program_pause()
        self.assertEqual(self.machine.log, [PAUSE, WAITED])


class TestResume(FakeRunCase):
    def paused(self):
        adapter = self.started()
        adapter.program_pause()
        del self.machine.log[:]
        return adapter

    def test_resume_goes_on_from_the_pause_in_the_mode_it_found(self):
        adapter = self.paused()
        adapter.program_resume()
        self.assertEqual(self.machine.log, [RESUME, WAITED])
        self.assertNoLinuxCncErrors()
        snap = adapter.snapshot()
        self.assertTrue(snap.program_running)
        self.assertEqual(snap.status_label, "ÇALIŞIYOR")
        self.assertEqual(snap.message, programs.RESUMED_TEXT)

    def test_an_m0_pause_resumes_the_same_way(self):
        """M0/M1: task queues PLAN_PAUSE itself - the same flags."""
        adapter = self.started()
        self.machine.issue_pause()
        adapter.program_resume()
        self.assertEqual(self.machine.log, [RESUME, WAITED])
        self.assertTrue(adapter.snapshot().program_running)

    def test_an_mdi_pause_resumes_in_mdi(self):
        adapter = self.loaded()
        self.run_mdi_program()
        adapter.program_pause()
        del self.machine.log[:]
        adapter.program_resume()
        self.assertEqual(self.machine.log, [RESUME, WAITED])
        self.assertEqual(self.machine.task_mode, C["MODE_MDI"])
        self.assertEqual(adapter.snapshot().message, programs.MDI_RESUMED_TEXT)

    def test_no_mdi_line_goes_out_behind_a_pause(self):
        """Final P0 review: task takes an MDI line in MDI mode while a line
        is paused - queued, or issued at once into motion's paused queue
        (emctaskmain.cc:1422-1439) - so «Devam» would run it too, motion
        the resume did not name. Refused, nothing sent, the reason said;
        a program paused in AUTO says the same."""
        for name in ("mdi paused", "program paused"):
            with self.subTest(state=name):
                adapter = self.loaded()
                if name == "mdi paused":
                    self.run_mdi_program()
                else:
                    adapter.program_start()
                adapter.program_pause()
                self.assertTrue(adapter.snapshot().program_paused)
                del self.machine.log[:]
                entry = adapter.send_mdi("G0 X10")
                self.assertFalse(entry.accepted)
                self.assertEqual(entry.note, A.MDI_WHILE_PAUSED)
                self.assertEqual(self.machine.log, [])
                self.assertEqual(adapter.snapshot().message,
                                 "MDI: G0 X10 — " + A.MDI_WHILE_PAUSED)
                adapter.stop_motion()
                adapter.stop_followup()
                adapter.snapshot()

    def test_nothing_paused_is_refused(self):
        for name, adapter in (("idle", self.loaded()), ("running", None)):
            with self.subTest(state=name):
                if adapter is None:
                    adapter = self.started()
                adapter.program_resume()
                self.assertEqual(self.machine.log, [])
                self.assertEqual(adapter.snapshot().message, programs.RESUME_NOT_PAUSED)

    def test_a_pause_in_manual_is_refused_and_no_mode_is_switched(self):
        """axis.py's task_resume returns then (:2390-2397); switching to
        AUTO would abort (emctask.cc:293-296)."""
        adapter = self.loaded()
        self.machine.issue_pause()          # PLAN_PAUSE in MANUAL (emctaskmain.cc:947)
        adapter.program_resume()
        self.assertEqual(self.machine.log, [])
        self.assertEqual(adapter.snapshot().message, programs.PAUSED_IN_MANUAL)

    def test_refused_when_the_machine_may_not_move(self):
        adapter = self.paused()
        self.hal.pins.pop(A.LinkHealth.HAL_PACKET_ERROR_TOTAL, None)
        with mock.patch.object(A.LinuxCncAdapter, "_read_hal", return_value={}):
            adapter.program_resume()
        self.assertEqual(self.machine.log, [])
        self.assertEqual(adapter.snapshot().message, "Sürdürülemez: Karta bağlantı yok.")

    def test_nothing_goes_after_a_stop(self):
        """«Devam» starts motion: a stop between its checks and its send
        holds it (_stopped_since)."""
        adapter = self.paused()

        def stop_meanwhile(state):
            adapter.stop_motion()
            return ""

        with mock.patch.object(programs, "resume_refusal", stop_meanwhile):
            adapter.program_resume()
        self.assertNotIn(RESUME, self.machine.log)
        self.assertEqual(adapter._message, A.STOP_REFUSAL)


class TestResumeNotTaken(FakeRunCase):
    def test_a_resume_that_did_not_take_says_so(self):
        class Deaf(F.FakeMachine):
            def issue_resume(self):
                pass

        adapter = self.on(homed=True, machine=Deaf())
        adapter.load_program(self.path)
        adapter.program_start()
        adapter.program_pause()
        adapter.program_resume()
        snap = adapter.snapshot()
        self.assertEqual((snap.message, snap.message_severity),
                         (programs.RESUME_NOT_TAKEN, Severity.WARN))


class TestStep(FakeRunCase):
    def test_a_mode_switch_task_ignores_refuses_the_step(self):
        """Task ignores a mode change while a jog runs (emctask.cc:269-272):
        no AUTO, no step."""
        adapter = self.loaded()
        self.machine.jogging_active = True
        adapter.program_step()
        self.assertNotIn(STEP, self.machine.log)
        self.assertEqual(adapter.snapshot().message, "AUTO moduna geçilemedi — adım atılmadı.")

    def test_from_idle_auto_then_one_step_starts_the_program_paused(self):
        adapter = self.loaded()
        adapter.program_step()
        self.assertEqual(self.machine.log, [("mode", (C["MODE_AUTO"],)), WAITED, STEP, WAITED])
        self.assertNoLinuxCncErrors()
        snap = adapter.snapshot()
        self.assertTrue(snap.program_paused)
        self.assertEqual(snap.message, programs.STEP_FIRST_TEXT)
        del self.machine.log[:]
        self.machine.traj_queue = 1
        adapter.program_step()
        self.assertEqual(self.machine.log, [STEP, WAITED])
        self.assertEqual(self.machine.motion_steps, 1)
        self.assertEqual(adapter.snapshot().message, programs.STEP_TEXT)

    def test_a_step_while_paused_needs_no_mode_switch(self):
        adapter = self.started()
        adapter.program_pause()
        del self.machine.log[:]
        adapter.program_step()
        self.assertEqual(self.machine.log, [STEP, WAITED])

    def test_gates(self):
        for name, prepare, text in (
            ("unhomed", lambda: self.loaded(homed=False),
             "Adım atılamaz: Referans alınmadı: X, Y, Z. Sıra: Z → Y → X."),
            ("no file", lambda: self.on(homed=True), "Program yüklenmedi."),
            ("running", self.started, programs.STEP_RUNNING),
            ("mdi", lambda: (self.loaded(), self.run_mdi_program())[0], programs.STEP_MDI),
            ("estop", lambda: self.make(), None),
        ):
            with self.subTest(state=name):
                adapter = prepare()
                del self.machine.log[:]
                adapter.program_step()
                self.assertEqual(self.machine.log, [])
                message = adapter.snapshot().message
                if text is None:
                    self.assertTrue(message.startswith("Adım atılamaz: "), message)
                else:
                    self.assertEqual(message, text)

    def test_nothing_goes_after_a_stop(self):
        adapter = self.loaded()

        def stop_meanwhile(state):
            adapter.stop_motion()
            return ""

        with mock.patch.object(programs, "step_refusal", stop_meanwhile):
            adapter.program_step()
        self.assertNotIn(STEP, self.machine.log)
        self.assertEqual(adapter._message, A.STOP_REFUSAL)

    def test_a_step_that_did_not_pause_says_so(self):
        class Deaf(F.FakeMachine):
            def issue_step(self):
                pass

        adapter = self.on(homed=True, machine=Deaf())
        adapter.load_program(self.path)
        adapter.program_step()
        self.assertEqual(adapter.snapshot().message, programs.STEP_NOT_TAKEN)
        self.assertIsNone(adapter._run)     # nothing to follow: no "Program bitti" later


class TestStartNotTaken(FakeRunCase):
    """START_NOT_TAKEN only when nothing shows the program ran (P0 cleanup):
    PLAN_RUN's only refusal is RCS_ERROR (emctaskmain.cc:2294-2299); a
    PLAN_RUN dropped silently is DONE, but only in MANUAL (:995-1000)."""

    def test_a_start_linuxcnc_refused_says_so_and_is_not_followed(self):
        class Refusing(F.FakeMachine):
            def issue_run(self, line):
                raise F.Rejected("Can't run a program when not homed")

        adapter = self.on(homed=True, machine=Refusing())
        adapter.load_program(self.path)
        del self.machine.log[:]
        adapter.program_start()
        self.assertEqual(self.machine.log, [("mode", (C["MODE_AUTO"],)), WAITED, RUN, WAITED])
        snap = adapter.snapshot()
        self.assertEqual((snap.message, snap.message_severity),
                         (programs.START_NOT_TAKEN, Severity.WARN))
        self.assertIn("not homed", snap.last_error.raw)
        self.assertIsNone(adapter._run)

    def test_a_start_dropped_in_manual_says_so(self):
        class DroppedInManual(F.FakeMachine):
            def issue_run(self, line):
                # Another GUI switched to MANUAL meanwhile: task drops
                # PLAN_RUN there without a word, status DONE.
                self.task_mode = C["MODE_MANUAL"]
                raise F._Silent()

        adapter = self.on(homed=True, machine=DroppedInManual())
        adapter.load_program(self.path)
        adapter.program_start()
        self.assertEqual(adapter.snapshot().message, programs.START_NOT_TAKEN)
        self.assertIsNone(adapter._run)

    def test_a_program_that_ends_within_the_send_says_bitti(self):
        """WP09 review: an "M2" alone is idle again before the read-back
        (sim/, 2026-11-14); it ran, so the end is said, not a refusal."""
        adapter = self.loaded()
        self.machine.program_ends_at_once = True
        adapter.program_start()
        self.assertEqual(self.machine.log, [("mode", (C["MODE_AUTO"],)), WAITED, RUN, WAITED])
        snap = adapter.snapshot()
        self.assertEqual((snap.message, snap.message_severity),
                         (programs.FINISHED_TEXT.format("kare.ngc"), Severity.INFO))
        self.assertEqual(snap.alarms[0].text, programs.FINISHED_TEXT.format("kare.ngc"))
        self.assertIsNone(adapter._run)

    def test_a_run_task_picks_up_late_is_a_start(self):
        """Idle at the read-back but running after the bounded wait (task
        busy for a cycle): started, followed."""
        class Late(F.FakeMachine):
            late = False

            def issue_run(self, line):
                super().issue_run(line)
                self.interp_state = C["INTERP_IDLE"]
                self.late = True

            def wait_status(self):
                if self.late:
                    self.late = False
                    self.interp_state = C["INTERP_READING"]
                return super().wait_status()

        adapter = self.on(homed=True, machine=Late())
        adapter.load_program(self.path)
        adapter.program_start()
        self.assertEqual(adapter.snapshot().message, programs.STARTED_TEXT.format("kare.ngc"))
        self.assertIsNotNone(adapter._run)

    def test_a_program_that_ends_within_the_send_on_an_error_says_so(self):
        class EndsOnAnError(F.FakeMachine):
            def issue_run(self, line):
                self.operator_error("Linear move on line 1 would exceed X's positive limit")

        adapter = self.on(homed=True, machine=EndsOnAnError())
        adapter.load_program(self.path)
        adapter.program_start()
        snap = adapter.snapshot()
        self.assertEqual((snap.message, snap.message_severity),
                         (programs.FAILED_TEXT.format("kare.ngc"), Severity.WARN))


class TestStepMotionOrPauseDeceleration(FakeRunCase):
    """P0 cleanup item 1: paused-and-moving reads TEK ADIM only after
    «Adım»; right after «Duraklat» motion's paused flag is set while tpPause
    still decelerates (command.c:1168-1175)."""

    def test_a_pause_decelerating_reads_paused_and_a_step_reads_tek_adim(self):
        adapter = self.started()
        adapter.program_pause()
        self.machine.current_vel = 4.0           # still braking
        snap = adapter.snapshot()
        self.assertEqual(snap.run_command, "pause")
        self.assertEqual(snap.status_label, "DURAKLATILDI")
        self.assertEqual(VM.run_state(snap), "paused")
        self.machine.traj_queue = 1
        adapter.program_step()
        snap = adapter.snapshot()
        self.assertEqual(snap.status_label, "TEK ADIM")
        self.assertEqual(VM.run_state(snap), "step")
        self.machine.current_vel = 0.0
        self.assertEqual(adapter.snapshot().status_label, "DURAKLATILDI")

    def test_the_memory_ends_with_the_run(self):
        adapter = self.started()
        adapter.program_pause()
        self.machine.interp_state = C["INTERP_IDLE"]
        self.machine.paused = False
        self.assertIsNone(adapter.snapshot().run_command)

    def test_observer_mode_keeps_the_old_reading(self):
        adapter = self.make()
        adapter._observe = True
        self.machine.power_on(homed=True)
        self.machine.task_mode = C["MODE_AUTO"]
        self.machine.interp_state = C["INTERP_PAUSED"]
        self.machine.paused = True
        self.machine.current_vel = 4.0
        snap = adapter.snapshot()
        self.assertIsNone(snap.run_command)
        self.assertEqual(snap.status_label, "TEK ADIM")

    def test_the_rule_on_machine_state(self):
        paused = state_of(homed="XYZ")._replace(
            task_mode="auto", interp_state="paused", program_paused=True, moving=True)
        for cmd, label in ((None, "TEK ADIM"), ("step", "TEK ADIM"), ("pause", "DURAKLATILDI"),
                           ("start", "DURAKLATILDI"), ("resume", "DURAKLATILDI")):
            with self.subTest(run_command=cmd):
                self.assertEqual(paused._replace(run_command=cmd).status_label, label)

    def test_the_mock_reports_its_last_command(self):
        m = MockAdapter(PROFILE)
        m.software_estop_reset()
        m.home_all()
        m.load_program("/n/kare.ngc")
        m.program_start()
        self.assertEqual(m.snapshot().run_command, "start")
        m.program_pause()
        self.assertEqual(m.snapshot().run_command, "pause")
        m.stop_motion()
        self.assertIsNone(m.snapshot().run_command)


class TestPauseLeftAtTheEnd(FakeRunCase):
    """P0 cleanup item 3: a program stepped to its very end with «Adım»
    alone leaves motion's paused flag set with the interpreter idle (sim/,
    LinuxCNC 2.9.9, 2026-11-14; fake: FakeMachine.end_program). The UI says
    so and offers only «Durdur»; it sends nothing to clear it."""

    def stepped_to_the_end(self):
        adapter = self.loaded()
        adapter.program_step()
        self.machine.traj_queue = 1
        adapter.program_step()
        self.machine.traj_queue = 0
        self.machine.end_program()
        self.assertTrue(self.machine.paused)
        return adapter

    def test_the_state_the_label_and_the_end_message(self):
        adapter = self.stepped_to_the_end()
        snap = adapter.snapshot()
        self.assertTrue(snap.pause_left_at_end)
        self.assertEqual(snap.status_label, "PROGRAM SONU · DURDUR")
        self.assertEqual(snap.message, programs.FINISHED_PAUSED_TEXT.format("kare.ngc"))
        self.assertEqual(VM.run_state(snap), "end_paused")
        gate = VM.run_gate(snap)
        self.assertEqual((gate["text"], gate["kind"]), (programs.PAUSED_AT_END, "warn"))

    def test_resume_start_step_and_pause_are_refused_and_nothing_goes(self):
        for action in ("program_resume", "program_start", "program_step", "program_pause"):
            with self.subTest(action=action):
                adapter = self.stepped_to_the_end()
                adapter.snapshot()
                del self.machine.log[:]
                getattr(adapter, action)()
                self.assertEqual(self.machine.log, [])
                self.assertEqual(adapter.snapshot().message, programs.PAUSED_AT_END)
                self.assertTrue(self.machine.paused)

    def test_durdur_clears_it(self):
        adapter = self.stepped_to_the_end()
        adapter.snapshot()
        adapter.stop_motion()
        adapter.stop_followup()
        snap = adapter.snapshot()
        self.assertFalse(snap.program_paused)
        self.assertEqual(snap.status_label, "HAZIR")

    def test_the_program_screen_shows_devam_off_with_the_reason(self):
        adapter = self.stepped_to_the_end()
        cfg = load_machine_config(self.profile)._replace(program_dir=self.dir)
        vm = VM.build(self.profile, cfg, adapter.snapshot(), "program")
        self.assertEqual(vm["program"]["primary"]["action"], "program_resume")
        self.assertFalse(vm["program"]["primary"]["enabled"])
        self.assertEqual(vm["program"]["primary"]["reason"], programs.PAUSED_AT_END)
        self.assertIn(programs.PAUSED_AT_END, visible(R.render_body(vm)))


class TestResumeAfterSteps(FakeRunCase):
    """P0 cleanup item 15: «Adım», «Adım», «Devam» made LinuxCNC report
    "File ended with no percent sign (%) or program end (M2)": PLAN_RESUME
    restores the interpreter state the last PLAN_PAUSE saved
    (emctaskmain.cc:2344-2352) - READING from the first step - while the
    interpreter has read to the end and WAITS (:524-534). A PLAN_PAUSE
    first saves the state it is in (:2312-2320)."""

    def test_a_pause_goes_first_when_stepping_left_the_interpreter_unpaused(self):
        adapter = self.loaded()
        adapter.program_step()                     # PAUSED, resume state READING
        adapter.program_step()                     # queue 0: back to READING
        self.machine.interp_state = C["INTERP_WAITING"]   # read to M2
        del self.machine.log[:]
        adapter.program_resume()
        self.assertEqual(self.machine.log, [PAUSE, WAITED, RESUME, WAITED])
        self.assertEqual(self.machine.interp_state, C["INTERP_WAITING"])  # not READING again
        self.assertFalse(self.machine.paused)
        self.assertNoLinuxCncErrors()
        self.assertEqual(adapter.snapshot().message, programs.RESUMED_TEXT)

    def test_a_plain_pause_resumes_with_no_pause_first(self):
        adapter = self.started()
        adapter.program_pause()
        del self.machine.log[:]
        adapter.program_resume()
        self.assertEqual(self.machine.log, [RESUME, WAITED])

    def test_a_stop_between_the_pause_and_the_resume_sends_no_resume(self):
        adapter = self.loaded()
        adapter.program_step()
        adapter.program_step()
        self.machine.interp_state = C["INTERP_WAITING"]
        real_wait = adapter._wait

        def stop_during_the_pause_wait():
            adapter.stop_motion()
            return real_wait()

        adapter._wait = stop_during_the_pause_wait
        del self.machine.log[:]
        adapter.program_resume()
        self.assertNotIn(RESUME, self.machine.log)


class TestStart(FakeRunCase):
    def test_start_says_the_base_name_and_is_refused_while_anything_runs(self):
        adapter = self.loaded()
        adapter.program_start()
        self.assertEqual(self.machine.log, [("mode", (C["MODE_AUTO"],)), WAITED, RUN])
        self.assertEqual(adapter.snapshot().message, programs.STARTED_TEXT.format("kare.ngc"))
        for name, prepare, text in (
            ("running", lambda: None, programs.START_RUNNING),
            ("paused", lambda: adapter.program_pause(), programs.START_PAUSED),
        ):
            with self.subTest(state=name):
                prepare()
                del self.machine.log[:]
                adapter.program_start()
                self.assertEqual(self.machine.log, [])
                self.assertEqual(adapter.snapshot().message, text)

    def test_start_while_an_mdi_line_runs_is_refused(self):
        adapter = self.loaded()
        self.run_mdi_program()
        adapter.program_start()
        self.assertEqual(self.machine.log, [])
        self.assertEqual(adapter.snapshot().message, programs.START_MDI)


class TestProgress(FakeRunCase):
    def test_line_and_progress_from_motion_line_over_the_line_count(self):
        adapter = self.started()
        self.machine.motion_line = 5
        snap = adapter.snapshot()
        self.assertEqual((snap.program_line, snap.program_progress), (5, 0.5))
        self.assertEqual(snap.status_label, "ÇALIŞIYOR · satır 5")
        adapter.program_pause()
        self.assertEqual(adapter.snapshot().program_progress, 0.5)
        adapter.stop_motion()
        adapter.stop_followup()
        self.assertEqual(adapter.snapshot().program_progress, 0.0)

    def test_an_mdi_line_has_no_progress(self):
        adapter = self.loaded()
        self.run_mdi_program()
        self.machine.motion_line = 5
        self.assertEqual(adapter.snapshot().program_progress, 0.0)


class TestTheEnd(FakeRunCase):
    def end(self):
        """The interpreter idle again in AUTO, as after M2/M30."""
        self.machine.interp_state = C["INTERP_IDLE"]
        self.machine.paused = False

    def test_a_normal_end_says_bitti_once(self):
        adapter = self.started()
        self.end()
        snap = adapter.snapshot()
        self.assertEqual((snap.message, snap.message_severity),
                         (programs.FINISHED_TEXT.format("kare.ngc"), Severity.INFO))
        self.assertEqual(snap.alarms[0].text, programs.FINISHED_TEXT.format("kare.ngc"))
        adapter.refuse("sonraki söz")
        self.assertEqual(adapter.snapshot().message, "sonraki söz")     # said once, not again

    def test_a_short_program_that_ends_before_any_snapshot_still_says_so(self):
        adapter = self.loaded()
        adapter.program_start()
        self.end()
        self.assertEqual(adapter.snapshot().message, programs.FINISHED_TEXT.format("kare.ngc"))

    def test_after_durdur_it_says_durduruldu(self):
        adapter = self.started()
        adapter.stop_motion()
        adapter.stop_followup()
        snap = adapter.snapshot()
        self.assertEqual(snap.message, programs.STOPPED_TEXT.format("kare.ngc"))
        self.assertEqual(snap.alarms[0].text, programs.STOPPED_TEXT.format("kare.ngc"))

    def test_a_linuxcnc_error_ends_it_with_the_error_in_the_box(self):
        adapter = self.started()
        self.machine.operator_error("Linear move on line 5 would exceed X's positive limit")
        self.end()
        snap = adapter.snapshot()
        self.assertEqual((snap.message, snap.message_severity),
                         (programs.FAILED_TEXT.format("kare.ngc"), Severity.WARN))
        self.assertIn("X", snap.last_error.raw)

    def test_a_late_report_of_an_error_from_before_the_run_is_no_failure(self):
        """Review of WP09: the box's error closed by its last report
        (LINEAR_INVALID, a follow-up) after the run began is the same error,
        not a new one: the run that then ends normally says "bitti"."""
        adapter = self.loaded()
        self.machine.operator_error("Linear move on line 0 would exceed X's positive limit")
        adapter.snapshot()          # the box takes it; its last report is still to come
        adapter.program_start()
        adapter.snapshot()
        self.machine.operator_error("invalid params in linear command")
        adapter.snapshot()          # the follow-up: the box keeps the first
        self.end()
        self.assertEqual(adapter.snapshot().message, programs.FINISHED_TEXT.format("kare.ngc"))

    def test_an_error_not_yet_drained_when_start_is_pressed_is_the_runs(self):
        """Review of WP09: «Başlat» (and «Adım») right after a run ended
        with an error, before any snapshot drained it, says how that run
        ended from the error - not "bitti"."""
        for action in ("program_start", "program_step"):
            with self.subTest(action=action):
                adapter = self.started()
                self.machine.operator_error(
                    "Linear move on line 5 would exceed X's positive limit")
                self.end()
                getattr(adapter, action)()
                texts = [a.text for a in adapter.snapshot().alarms]
                self.assertIn(programs.FAILED_TEXT.format("kare.ngc"), texts)
                self.assertNotIn(programs.FINISHED_TEXT.format("kare.ngc"), texts)
                adapter.stop_motion()
                adapter.stop_followup()
                adapter.snapshot()

    def test_an_estop_keeps_its_own_words(self):
        adapter = self.started()
        adapter.software_estop()
        snap = adapter.snapshot()
        self.assertNotIn("Program bitti", snap.message)
        self.assertNotIn("Program durduruldu", snap.message)
        self.assertIs(snap.message_severity, Severity.ERROR)

    def test_after_an_estop_that_left_the_machine_on_nothing_is_said(self):
        """An estop and a reset before the snapshot: the estop's own words
        stand, no "durduruldu"."""
        adapter = self.started()
        adapter._note_stop(A.ESTOP_REFUSAL)
        adapter.refuse("acil stop sözü")
        self.end()
        self.assertEqual(adapter.snapshot().message, "acil stop sözü")

    def test_a_run_started_elsewhere_is_followed_too(self):
        adapter = self.loaded()
        self.machine.task_mode = C["MODE_AUTO"]
        self.machine.interp_state = C["INTERP_READING"]
        adapter.snapshot()
        self.end()
        self.assertEqual(adapter.snapshot().message, programs.FINISHED_TEXT.format("kare.ngc"))

    def test_observer_mode_says_nothing_about_an_end(self):
        adapter = self.make()
        adapter._observe = True     # what LinuxCncAdapter(observe=True) sets
        self.machine.power_on(homed=True)
        self.machine.file = self.path
        self.machine.task_mode = C["MODE_AUTO"]
        self.machine.interp_state = C["INTERP_READING"]
        before = adapter.snapshot().message
        self.end()
        self.assertEqual(adapter.snapshot().message, before)

    def test_a_pause_is_not_an_end(self):
        adapter = self.started()
        adapter.program_pause()
        self.assertEqual(adapter.snapshot().message, programs.PAUSED_TEXT)


class TestSessionOnTheFake(FakeRunCase):
    def test_the_actions_reach_the_adapter_and_resume_and_step_are_dropped_after_a_stop(self):
        adapter = self.started()
        cfg = load_machine_config(self.profile)._replace(program_dir=self.dir)
        sess = app.Session(adapter, self.profile, cfg)
        sess.act("program_pause", "")
        self.assertEqual(self.machine.log, [PAUSE, WAITED])
        del self.machine.log[:]
        sess.act("program_resume", "")
        self.assertEqual(self.machine.log, [RESUME, WAITED])
        self.assertIn("program_resume", app.DROPPED_AFTER_A_STOP)
        self.assertIn("program_step", app.DROPPED_AFTER_A_STOP)
        self.assertNotIn("program_pause", app.MOTION_ACTIONS)


# ---------------------------------------------------------------------------
# MockAdapter
# ---------------------------------------------------------------------------


class TestMock(unittest.TestCase):
    def homed_mock(self):
        m = MockAdapter(PROFILE)
        m.software_estop_reset()
        m.home_all()
        m.load_program("/n/kare.ngc")
        return m

    def test_the_same_rules(self):
        m = self.homed_mock()
        m.program_pause()
        self.assertEqual(m.snapshot().message, programs.PAUSE_IDLE)
        m.program_resume()
        self.assertEqual(m.snapshot().message, programs.RESUME_NOT_PAUSED)
        m.program_start()
        snap = m.snapshot()
        self.assertTrue(snap.program_running)
        self.assertEqual(snap.message, programs.STARTED_TEXT.format("kare.ngc"))
        m.program_step()
        self.assertEqual(m.snapshot().message, programs.STEP_RUNNING)
        m.program_pause()
        snap = m.snapshot()
        self.assertEqual((snap.program_paused, snap.status_label), (True, "DURAKLATILDI"))
        m.program_start()
        self.assertEqual(m.snapshot().message, programs.START_PAUSED)
        entry = m.send_mdi("G0 X1")
        self.assertEqual((entry.accepted, entry.note), (False, A.MDI_WHILE_PAUSED))
        m.program_step()
        self.assertEqual(m.snapshot().message, programs.STEP_TEXT)
        m.program_resume()
        self.assertEqual(m.snapshot().message, programs.RESUMED_TEXT)
        self.assertTrue(m.snapshot().program_running)
        m.stop_motion()
        snap = m.snapshot()
        self.assertFalse(snap.program_running or snap.program_paused)

    def test_step_from_idle_and_its_gates(self):
        m = MockAdapter(PROFILE)
        m.program_step()
        self.assertTrue(m.snapshot().message.startswith("Adım atılamaz: "))
        m.software_estop_reset()
        m.home_all()
        m.program_step()
        self.assertEqual(m.snapshot().message, "Program yüklenmedi.")
        m.load_program("/n/kare.ngc")
        m.program_step()
        snap = m.snapshot()
        self.assertEqual((snap.program_paused, snap.message), (True, programs.STEP_FIRST_TEXT))
        m.software_estop()
        snap = m.snapshot()
        self.assertFalse(snap.program_paused)
        m.program_resume()
        self.assertTrue(m.snapshot().message.startswith("Sürdürülemez: "))


# ---------------------------------------------------------------------------
# The Program screen
# ---------------------------------------------------------------------------


class TestProgramScreen(ProgramFileCase):
    def setUp(self):
        super().setUp()
        self.config = load_machine_config(PROFILE)._replace(program_dir=self.dir)
        programs.line_count(self.path)
        self.idle = state_of(homed="XYZ")._replace(program_file=self.path)

    def markup(self, state, observe=False):
        vm = VM.build(PROFILE, self.config, state, "program", observe=observe)
        return R.render_body(vm), vm["program"]

    def test_the_primary_button_follows_what_runs(self):
        running = self.idle._replace(task_mode="auto", interp_state="reading",
                                     program_running=True, program_line=5)
        paused = running._replace(interp_state="paused", program_running=False,
                                  program_paused=True)
        step = paused._replace(moving=True)
        mdi = self.idle._replace(task_mode="mdi", interp_state="reading", mdi_running=True)
        for name, state, label, action in (
            ("idle", self.idle, "Başlat", "program_start"),
            ("running", running, "Duraklat", "program_pause"),
            ("paused", paused, "Devam", "program_resume"),
            ("a step's motion", step, "Devam", "program_resume"),
            ("mdi", mdi, "Duraklat", "program_pause"),
        ):
            with self.subTest(state=name):
                markup, p = self.markup(state)
                row = start_row(markup)
                first = re.search(r"<button [^>]*>([^<]*)</button>", row)
                self.assertEqual(first.group(1), label)
                self.assertIn('data-action="{}"'.format(action), first.group(0))
                self.assertNotIn("disabled", first.group(0))
                self.assertEqual(p["primary"]["action"], action)
                self.assertIn('data-action="program_step"', row)
                self.assertIn('data-action="program_stop"', row)

    def test_step_off_with_its_reason_as_text(self):
        running = self.idle._replace(task_mode="auto", interp_state="reading",
                                     program_running=True)
        markup, p = self.markup(running)
        self.assertIn("disabled", button_of(markup, "program_step"))
        self.assertIn("Adım kapalı: " + programs.STEP_RUNNING, visible(markup))
        markup, p = self.markup(self.idle)
        self.assertNotIn("disabled", button_of(markup, "program_step"))

    def test_the_run_line_and_the_gate_while_running_and_paused(self):
        running = self.idle._replace(task_mode="auto", interp_state="reading",
                                     program_running=True, program_line=5,
                                     program_progress=0.5)
        markup, p = self.markup(running)
        self.assertEqual((p["line"], p["line_text"], p["progress_pct"], p["run"]),
                         (5, "G1 X5", 50.0, "running"))
        self.assertIn("Satır 5 / 10 · %50,0", visible(markup))
        self.assertIn('<code class="prog-line">G1 X5</code>', markup)
        self.assertIn(VM.GATE_RUNNING_LINE.format(5), markup)
        self.assertNotIn("Program başlatılabilir.", markup)
        paused = running._replace(interp_state="paused", program_running=False,
                                  program_paused=True)
        markup, p = self.markup(paused)
        self.assertIn(VM.GATE_PAUSED, markup)
        self.assertIn(">DURAKLATILDI<", markup)
        self.assertNotIn(">HAZIR<", markup)
        self.assertEqual(p["line"], 5)
        mdi_paused = self.idle._replace(task_mode="mdi", interp_state="paused",
                                        mdi_running=True, program_paused=True, program_line=5)
        markup, p = self.markup(mdi_paused)
        self.assertIsNone(p["line"])
        self.assertIsNone(p["progress_pct"])
        self.assertIn(programs.MDI_PAUSED_TEXT, markup)
        self.assertNotIn("prog-run\"", markup)
        markup, p = self.markup(self.idle)
        self.assertIsNone(p["line"])
        self.assertNotIn('class="prog-run"', markup)

    def test_off_with_the_reason_when_unknown_and_in_observer_mode(self):
        for name, state, observe, reason in (
            ("unknown", self.idle._replace(status_known=False), False, None),
            ("observe running", self.idle._replace(task_mode="auto", interp_state="reading",
                                                   program_running=True), True,
             VM.OBSERVE_REASON),
            ("observe paused", self.idle._replace(task_mode="auto", interp_state="paused",
                                                  program_paused=True), True,
             VM.OBSERVE_REASON),
        ):
            with self.subTest(state=name):
                markup, p = self.markup(state, observe)
                self.assertFalse(p["primary"]["enabled"])
                self.assertFalse(p["step_enabled"])
                self.assertTrue(p["primary"]["reason"])
                if reason:
                    self.assertEqual(p["primary"]["reason"], reason)
                self.assertIn("{} kapalı: ".format(p["primary"]["label"]), visible(markup))

    def test_paused_start_reason_says_devam(self):
        paused = self.idle._replace(task_mode="auto", interp_state="paused", program_paused=True)
        p = VM.program_screen(PROFILE, self.config, paused)
        self.assertFalse(p["can_start"])
        self.assertEqual(p["start_disabled_reason"], programs.START_PAUSED)


if __name__ == "__main__":
    unittest.main()
