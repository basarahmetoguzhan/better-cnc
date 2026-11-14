"""The program list and loading (plan WP08).

Only names the server lists from its own PROGRAM_PREFIX are accepted, and
LinuxCNC gets the absolute path: milltask runs in the INI's directory, so a
relative path does not open (sim lens; reference/linuxcnc/scripts/
linuxcnc.in:777). The UI here runs on the repo's INI (start.sh passes no
--ini), where ../../nc_files/ resolves to the same /repo/nc_files as the sim
INI's absolute PROGRAM_PREFIX. Everything after loading worked in the sim
with the file loaded by hand before WP08: the whole run took 46.6 s, Durdur
stopped it in 0.16 s.
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

#: Durdur -> still and the interpreter idle, from the POST (the plan's
#: figure for WP08; the sim lens measured 0.16 s).
PROGRAM_STOP_S = 0.2


def load(name: str) -> None:
    h.post("load_program", name, screen="program")


def wait_loaded() -> None:
    h.wait_until(lambda: h.stat().file == PROGRAM_PATH, 2.0, "stat.file " + PROGRAM_PATH,
                 detail=lambda: repr(h.stat().file))


def running() -> bool:
    s = h.stat()
    return s.task_mode == h._lc().MODE_AUTO and s.interp_state != h._lc().INTERP_IDLE


class Program(h.Scenario):

    def setUp(self):
        super().setUp()
        h.ensure_on()

    def test_1_the_program_directory_is_listed(self):
        vm = h.state("program")
        self.assertIn(PROGRAM, h.program_files(vm))
        self.assertEqual(vm["program"]["dir"], os.path.dirname(PROGRAM_PATH))
        row = next(f for f in vm["program"]["files"] if f["name"] == PROGRAM)
        self.assertTrue(row["enabled"], row)

    def test_2_loading_opens_the_absolute_path(self):
        load(PROGRAM)
        wait_loaded()
        program = h.state("program")["program"]
        self.assertEqual((program["loaded"], program["loaded_path"]), (PROGRAM, PROGRAM_PATH))
        with open(PROGRAM_PATH, encoding="utf-8") as fh:
            self.assertEqual(program["loaded_lines"], len(fh.readlines()))
        self.assertEqual([f["name"] for f in program["files"] if f["loaded"]], [PROGRAM])
        self.assertIn(PROGRAM + " yüklendi.", h.message())

    def test_3_a_name_not_listed_is_refused(self):
        before = h.stat().file
        for name in ("../../etc/passwd", "/etc/passwd", "../nc_files/" + PROGRAM):
            with self.subTest(name=name):
                load(name)
                time.sleep(0.5)
                self.assertEqual(h.stat().file, before)
                self.assertIn("Program listede yok", h.message())

    def test_4_reload_opens_the_same_file_again(self):
        load(PROGRAM)
        wait_loaded()
        h.post("reload_program", screen="program")
        wait_loaded()
        self.assertIn(PROGRAM + " yüklendi.", h.message())


class ProgramRun(h.Scenario):

    def setUp(self):
        super().setUp()
        h.ensure_homed()

    def test_runs_to_the_end_and_durdur_stops_a_second_run(self):
        load(PROGRAM)
        wait_loaded()
        h.post("program_start", screen="program")
        h.wait_until(running, 2.0, "the program to start", detail=h.describe)
        # While it runs the list is off, with the reason as text.
        program = h.state("program")["program"]
        self.assertFalse(program["load_enabled"])
        self.assertEqual(program["load_disabled_reason"],
                         "Program çalışırken yeni program yüklenemez.")
        load(PROGRAM)
        self.assertIn("Program çalışırken yeni program yüklenemez.", h.message())
        self.assertTrue(running(), h.describe())
        h.wait_until(lambda: h.stat().interp_state == h._lc().INTERP_IDLE, PROGRAM_RUN_TIMEOUT_S,
                     "the program to end", every=0.5, detail=h.describe)

        h.post("program_start", screen="program")
        h.wait_until(lambda: running() and h.stat().current_vel > 0, 5.0, "the second run to move",
                     detail=h.describe)
        time.sleep(3.0)
        t0 = time.monotonic()
        h.post("program_stop", screen="program")

        def stopped():
            s = h.stat()
            return s if s.current_vel == 0 and s.interp_state == h._lc().INTERP_IDLE else None
        h.wait_until(stopped, PROGRAM_STOP_S, "Durdur to stop the run", start=t0,
                     every=0.005, detail=h.describe)


if __name__ == "__main__":
    unittest.main()
