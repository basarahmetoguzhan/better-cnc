"""Running a program (plan WP09): pause, resume, single step, the line on
the Program screen and the end message. Written before WP09 as a known gap
(expectedFailure); WP09 removed the marker.

Pause, resume and step as axis.py sends them (auto(AUTO_PAUSE),
AUTO_RESUME, AUTO_STEP: task_pause, task_resume, task_step in
reference/linuxcnc/src/emc/usr_intf/axis/scripts/axis.py:2361-2397).
"""

from __future__ import annotations

import os
import time
import unittest

import harness as h

PROGRAM = "faz8-kare.ngc"
PROGRAM_PATH = os.path.join(h.REPO, "nc_files", PROGRAM)

#: The whole of faz8-kare took 46.6 s in the simulator.
PROGRAM_RUN_TIMEOUT_S = 100.0


def program() -> dict:
    """The Program screen's view model (viewmodel.program_screen)."""
    return h.state("program")["program"]


def program_line() -> int:
    """The Program screen's current line (WP09: program.line)."""
    return int(program().get("line") or 0)


def paused() -> object:
    s = h.stat()
    return s if s.current_vel == 0 and s.interp_state == h._lc().INTERP_PAUSED else None


class Run(h.Scenario):

    def setUp(self):
        super().setUp()
        h.ensure_homed()
        h.post("load_program", PROGRAM, screen="program")
        h.wait_until(lambda: h.stat().file == PROGRAM_PATH, 2.0, "the program to load",
                     detail=lambda: repr(h.stat().file))

    def test_pause_resume_step_and_the_end(self):
        h.post("program_start", screen="program")
        h.wait_until(lambda: h.stat().interp_state != h._lc().INTERP_IDLE, 2.0,
                     "the program to start", detail=h.describe)
        lines = []
        t_end = time.monotonic() + 5.0
        while time.monotonic() < t_end:
            lines.append(program_line())
            time.sleep(0.5)
        view = h.state("program")
        self.assertTrue(view["status"]["label"].startswith("ÇALIŞIYOR · satır "),
                        view["status"]["label"])
        self.assertEqual(view["program"]["primary"]["action"], "program_pause")

        h.post("program_pause", screen="program")
        s = h.wait_until(paused, 1.0, "the pause", detail=h.describe)
        view = h.state("program")
        self.assertEqual(view["status"]["label"], "DURAKLATILDI")
        self.assertEqual(view["program"]["primary"]["action"], "program_resume")
        time.sleep(2.0)
        self.assertTrue(h.near(h.position(), h.position(s)), "moved while paused: " + h.describe())
        # «Başlat» while paused is refused, nothing sent: still paused there.
        h.post("program_start", screen="program")
        self.assertIn("«Devam»", h.message())
        self.assertTrue(paused(), "a start while paused changed something: " + h.describe())

        h.post("program_resume", screen="program")
        h.wait_until(lambda: h.stat().current_vel > 0, 2.0, "the run to go on", detail=h.describe)

        h.post("program_pause", screen="program")
        h.wait_until(paused, 1.0, "the second pause", detail=h.describe)
        before = h.stat().motion_line
        h.post("program_step", screen="program")
        h.wait_until(lambda: h.stat().motion_line > before, 15.0, "the step to start a line",
                     detail=h.describe)
        s = h.wait_until(paused, 15.0, "the step to stop again", detail=h.describe)
        self.assertEqual(s.motion_line, before + 1, h.describe(s))
        self.assertEqual(program_line(), before + 1)

        h.post("program_resume", screen="program")
        t_end = time.monotonic() + PROGRAM_RUN_TIMEOUT_S
        while h.stat().interp_state != h._lc().INTERP_IDLE:
            self.assertLess(time.monotonic(), t_end, "the program did not end: " + h.describe())
            lines.append(program_line())
            time.sleep(0.5)
        self.assertIn("Program bitti", h.message())
        self.assertEqual(lines, sorted(lines), "program.line went back")
        self.assertGreater(lines[-1], lines[0], "program.line did not move")
        view = h.state("program")
        self.assertEqual(view["program"]["primary"]["action"], "program_start")
        self.assertFalse(view["status"]["label"].startswith("ÇALIŞIYOR"))

    def test_step_from_idle_starts_paused_and_durdur_ends_it(self):
        """AUTO_STEP with nothing running: task runs the program and pauses
        it at once (emctaskmain.cc:1078-1091), nothing moves; DURDUR then
        ends the run and the message names the program."""
        start = h.position()
        h.post("program_step", screen="program")
        s = h.wait_until(paused, 2.0, "the program to start paused", detail=h.describe)
        self.assertEqual(s.task_mode, h._lc().MODE_AUTO, h.describe(s))
        self.assertEqual(h.state("program")["status"]["label"], "DURAKLATILDI")
        time.sleep(1.0)
        self.assertTrue(h.near(h.position(), start), "moved on the first step: " + h.describe())
        h.post("program_stop", screen="program")
        h.wait_until(lambda: h.stat().interp_state == h._lc().INTERP_IDLE, 2.0,
                     "the stop", detail=h.describe)
        self.assertEqual(h.message(), "Program durduruldu: " + PROGRAM)

    def step_until_still(self) -> object:
        """«Adım» once, then wait until motion is still again."""
        h.post("program_step", screen="program")
        time.sleep(0.3)
        return h.wait_until(lambda: (lambda s: s if s.current_vel == 0 else None)(h.stat()),
                            15.0, "the step to end", detail=h.describe)

    def test_step_step_resume_runs_to_the_end_with_no_error(self):
        """P0 cleanup item 15: after «Adım», «Adım» the interpreter has read
        the whole file and WAITS under motion's pause; a bare AUTO_RESUME
        put it back to READING (the state the first step saved,
        emctaskmain.cc:2344-2352) and LinuxCNC reported "File ended with no
        percent sign (%) or program end (M2)". The UI now sends a pause
        first (emctaskmain.cc:2312-2320): the program ends cleanly."""
        mark = h.alarm_mark()
        h.post("program_step", screen="program")
        h.wait_until(paused, 2.0, "the program to start paused", detail=h.describe)
        self.step_until_still()
        h.post("program_resume", screen="program")
        t_end = time.monotonic() + PROGRAM_RUN_TIMEOUT_S
        while h.stat().interp_state != h._lc().INTERP_IDLE:
            self.assertLess(time.monotonic(), t_end, "the program did not end: " + h.describe())
            time.sleep(0.5)
        time.sleep(0.5)
        self.assertEqual(h.message(), "Program bitti: " + PROGRAM)
        new = h.new_alarms(mark)
        self.assertFalse([t for t in new if "File ended" in t], new)
        self.assertFalse(h.stat().paused, h.describe())

    def test_stepping_to_the_very_end_leaves_the_pause_and_durdur_clears_it(self):
        """P0 cleanup item 3: «Adım» alone to the program's end leaves
        stat.paused set with the interpreter idle (EMCMOT_STEP sets paused
        again after each step, command.c:1200-1212). The UI says so, refuses
        «Devam» with nothing sent, and «Durdur» clears it."""
        h.post("program_step", screen="program")
        h.wait_until(paused, 2.0, "the program to start paused", detail=h.describe)
        for _ in range(30):
            if h.stat().interp_state == h._lc().INTERP_IDLE:
                break
            self.step_until_still()
        s = h.stat()
        self.assertEqual(s.interp_state, h._lc().INTERP_IDLE, h.describe(s))
        self.assertTrue(s.paused, "the pause did not stay: " + h.describe(s))
        view = h.state("program")
        self.assertEqual(view["status"]["label"], "PROGRAM SONU · DURDUR")
        self.assertIn("«Durdur»", h.message())
        self.assertFalse(view["program"]["primary"]["enabled"])
        at_end = h.position()
        h.post("program_resume", screen="program")
        self.assertIn("«Durdur» ile bitirin", h.message())
        time.sleep(1.0)
        s = h.stat()
        self.assertTrue(s.paused and s.interp_state == h._lc().INTERP_IDLE, h.describe(s))
        self.assertTrue(h.near(h.position(s), at_end), "moved: " + h.describe(s))
        h.post("program_stop", screen="program")
        h.wait_until(lambda: not h.stat().paused, 2.0, "the stop to clear the pause",
                     detail=h.describe)
        self.assertEqual(h.state("program")["status"]["label"], "HAZIR")

    def test_no_mdi_line_goes_out_behind_a_paused_mdi_line(self):
        """Final P0 review: in MDI mode task takes a new line behind a
        paused one (emctaskmain.cc:1422-1439), so «Devam» would run it too.
        The UI refuses it, sends nothing (queued_mdi_commands stays 0), and
        «Devam» finishes the first line alone: X ends at 40, Y does not move."""
        start = h.position()
        h.mdi("G1 X40 F300")
        h.wait_until(lambda: h.stat().current_vel > 0, 3.0, "the MDI line to move",
                     detail=h.describe)
        h.post("program_pause", screen="program")
        s = h.wait_until(paused, 1.0, "the MDI pause", detail=h.describe)
        self.assertEqual(s.task_mode, h._lc().MODE_MDI, h.describe(s))
        h.mdi("G0 Y60")
        entry = h.mdi_entry("G0 Y60")
        self.assertIsNotNone(entry)
        self.assertFalse(entry["accepted"], entry)
        self.assertIn("Duraklatılmış", entry["note"])
        self.assertIn("MDI kabul edilmedi", h.message())
        s = h.stat()
        self.assertEqual(s.queued_mdi_commands, 0, h.describe(s))
        self.assertTrue(paused(), "the refused line changed something: " + h.describe())
        h.post("program_resume", screen="program")
        s = h.wait_position((40.0, start[1], start[2]), timeout=15.0)
        h.wait_until(lambda: h.stat().interp_state == h._lc().INTERP_IDLE, 3.0,
                     "the MDI line to end", detail=h.describe)
        time.sleep(1.0)
        self.assertTrue(h.near(h.position(), (40.0, start[1], start[2])),
                        "something more ran after «Devam»: " + h.describe())


if __name__ == "__main__":
    unittest.main()
