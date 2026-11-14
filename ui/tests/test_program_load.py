"""Plan WP08: the program list and loading.

- configmodel.program_dir: [DISPLAY]PROGRAM_PREFIX resolved against the
  INI's directory, as LinuxCNC resolves it;
- programs.list_programs: what is listed, in which order, and the cache by
  the directory's mtime (no scan on a poll while it stands);
- programs.resolve: only a bare name the server listed gives a path;
- app.Session's load_program / reload_program / refresh_programs on the
  fake LinuxCNC (the NML sequence, nothing sent for a bad name) and on the
  mock (the same rule, programs.load_block);
- the Program screen: list, loaded mark, reasons as visible text;
- observer mode: no load reaches the adapter.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import unittest
from typing import List
from unittest import mock

import helpers  # noqa: F401  (puts ui/ on sys.path)
from helpers import REPO_ROOT, TEST_RIG_INI

import fake_linuxcnc as F
import test_linuxcnc_adapter as T
from bettercnc import app
from bettercnc import configmodel
from bettercnc import programs
from bettercnc import render as R
from bettercnc import viewmodel as VM
from bettercnc.adapter import MockAdapter
from bettercnc.configmodel import load_config_from_ini, load_machine_config
from bettercnc.iniload import parse_ini_text
from bettercnc.profiles import load_profile
from bettercnc.state import HomingPhase, Severity

C = F.CONSTANTS
PROFILE = load_profile("test-rig")
NC_FILES = os.path.join(REPO_ROOT, "nc_files")


def visible(markup: str) -> str:
    """As the kiosk shows it: app.js removes every title attribute."""
    return re.sub(r' title="[^"]*"', "", markup)


def program_card(markup: str) -> str:
    start = markup.index('<span class="card-title">Programlar</span>')
    return markup[start:markup.index("</section>", start)]


class TempDirCase(unittest.TestCase):
    """A fresh program directory per test; the list cache is emptied
    before and after so tests never see each other's scans."""

    def setUp(self):
        super().setUp()
        self.dir = tempfile.mkdtemp(prefix="bettercnc-prog-")
        self.addCleanup(shutil.rmtree, self.dir, True)
        programs._listings.clear()
        programs._counts.clear()
        self.addCleanup(programs._listings.clear)
        self.addCleanup(programs._counts.clear)

    def write(self, name: str, text: str = "G0 X0\nM2\n", mtime: float = None,
              where: str = None) -> str:
        path = os.path.join(where or self.dir, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        if mtime is not None:
            os.utime(path, (mtime, mtime))
        return path

    def settle(self, directory: str = None) -> None:
        """Put the directory's mtime well in the past, out of RACY_S, so a
        scan of it is trusted."""
        d = directory or self.dir
        old = os.stat(d).st_mtime - 3600
        os.utime(d, (old, old))

    def names(self, directory: str = None) -> List[str]:
        return [f.name for f in programs.list_programs(directory or self.dir).files]


# ---------------------------------------------------------------------------
# configmodel.program_dir
# ---------------------------------------------------------------------------


class TestProgramDir(unittest.TestCase):
    def test_the_rig_ini_gives_the_repos_nc_files(self):
        """../../nc_files/ from configs/test-rig/ - the directory AXIS and
        the interpreter use (linuxcnc.in:777, rs274ngc_pre.cc:946-957)."""
        cfg = load_machine_config(PROFILE)
        self.assertEqual(cfg.program_dir, NC_FILES)
        self.assertEqual(cfg.program_dir_reason, "")
        self.assertTrue(os.path.isfile(os.path.join(cfg.program_dir, "faz8-kare.ngc")))

    def resolve(self, text: str, ini_path: str = "/m/configs/rig/rig.ini"):
        return configmodel.program_dir(parse_ini_text(text), ini_path)

    def test_relative_absolute_tilde_and_missing(self):
        self.assertEqual(self.resolve("[DISPLAY]\nPROGRAM_PREFIX = ../../nc_files/\n"),
                         ("/m/nc_files", ""))
        self.assertEqual(self.resolve("[DISPLAY]\nPROGRAM_PREFIX = progs\n"),
                         ("/m/configs/rig/progs", ""))
        self.assertEqual(self.resolve("[DISPLAY]\nPROGRAM_PREFIX = /srv/gcode\n"),
                         ("/srv/gcode", ""))
        self.assertEqual(self.resolve("[DISPLAY]\nPROGRAM_PREFIX = ~/nc\n"),
                         (os.path.join(os.path.expanduser("~"), "nc"), ""))
        for text in ("[DISPLAY]\nDISPLAY = axis\n", "[DISPLAY]\nPROGRAM_PREFIX =\n", ""):
            with self.subTest(text=text):
                directory, reason = self.resolve(text)
                self.assertIsNone(directory)
                self.assertIn("PROGRAM_PREFIX", reason)

    def test_a_relative_ini_path_is_made_absolute(self):
        directory, _ = configmodel.program_dir(
            parse_ini_text("[DISPLAY]\nPROGRAM_PREFIX = nc\n"), "rig.ini")
        self.assertEqual(directory, os.path.join(os.getcwd(), "nc"))

    def test_no_ini_no_directory(self):
        cfg = load_config_from_ini(None)
        self.assertIsNone(cfg.program_dir)
        self.assertIn("INI yok", cfg.program_dir_reason)

    def test_the_kiosk_ini_resolves_to_the_same_directory(self):
        """deploy/make_kiosk_ini.py writes the kiosk INI next to its source,
        so its relative PROGRAM_PREFIX names the same directory."""
        kiosk = os.path.join(os.path.dirname(TEST_RIG_INI), "test-rig-kiosk.ini")
        with open(TEST_RIG_INI, encoding="utf-8") as fh:
            ini = parse_ini_text(fh.read())
        self.assertEqual(configmodel.program_dir(ini, kiosk)[0], NC_FILES)


# ---------------------------------------------------------------------------
# programs.list_programs
# ---------------------------------------------------------------------------


class TestListPrograms(TempDirCase):
    def test_newest_first_extensions_hidden_and_subdirectories(self):
        self.write("eski.ngc", mtime=1000)
        self.write("yeni.NGC", mtime=3000)
        self.write("orta.tap", mtime=2000)
        self.write("kesim.nc", mtime=2500)
        self.write("notlar.txt", mtime=4000)
        self.write("program.ngc.bak", mtime=4000)
        self.write(".gizli.ngc", mtime=4000)
        os.mkdir(os.path.join(self.dir, "alt.ngc"))
        sub = os.path.join(self.dir, "alt")
        os.mkdir(sub)
        self.write("icerde.ngc", where=sub, mtime=5000)
        self.assertEqual(self.names(), ["yeni.NGC", "kesim.nc", "orta.tap", "eski.ngc"])
        f = programs.list_programs(self.dir).files[0]
        self.assertEqual((f.size, f.mtime), (len("G0 X0\nM2\n"), 3000))

    def test_same_mtime_is_ordered_by_name(self):
        for name in ("b.ngc", "a.ngc", "c.ngc"):
            self.write(name, mtime=1000)
        self.assertEqual(self.names(), ["a.ngc", "b.ngc", "c.ngc"])

    def test_turkish_names_are_listed(self):
        self.write("çerçeve-ışık.ngc")
        self.assertEqual(self.names(), ["çerçeve-ışık.ngc"])

    def test_symlinks_outside_are_out_inside_are_in_broken_are_out(self):
        outside = tempfile.mkdtemp(prefix="bettercnc-out-")
        self.addCleanup(shutil.rmtree, outside, True)
        target = self.write("disari.ngc", where=outside)
        os.symlink(target, os.path.join(self.dir, "link-disari.ngc"))
        os.symlink("/etc/passwd", os.path.join(self.dir, "passwd.ngc"))
        inside = self.write("gercek.ngc")
        os.symlink(inside, os.path.join(self.dir, "link-icerde.ngc"))
        os.symlink(os.path.join(self.dir, "yok.ngc"), os.path.join(self.dir, "kirik.ngc"))
        os.symlink(outside, os.path.join(self.dir, "dizin.ngc"))
        self.assertEqual(sorted(self.names()), ["gercek.ngc", "link-icerde.ngc"])

    def test_names_that_cannot_be_shown_are_out(self):
        """A newline or escape would break a button; a name os.scandir
        hands over with lone surrogates (undecodable bytes) would break
        every later JSON answer."""
        self.write("iyi.ngc")
        self.write("satir\nsonu.ngc")
        self.write("kacis\x1b.ngc")
        self.assertFalse(programs._listable_name("bozuk\udcff.ngc"))
        self.assertEqual(self.names(), ["iyi.ngc"])

    def test_at_most_max_files_the_newest_and_a_note(self):
        for i in range(7):
            self.write("p{}.ngc".format(i), mtime=1000 + i)
        with mock.patch.object(programs, "MAX_FILES", 5):
            listing = programs.list_programs(self.dir)
            self.assertIsNone(programs.resolve(self.dir, "p0.ngc"))     # beyond the bound
        self.assertEqual([f.name for f in listing.files], ["p6.ngc", "p5.ngc", "p4.ngc",
                                                           "p3.ngc", "p2.ngc"])
        self.assertEqual(listing.total, 7)
        self.assertEqual(listing.note, programs.TRUNCATED.format(5, 7))
        self.assertIn("7 G-code dosyası var; en yeni 5", listing.note)
        # P0 cleanup: the scan's bound; the screen pages PAGE_SIZE at a time.
        self.assertEqual((programs.MAX_FILES, programs.PAGE_SIZE), (5000, 200))

    def test_pages(self):
        """P0 cleanup item 9 (B13 'sayfalama gerekir mi?' - decided: yes):
        PAGE_SIZE files a page, clamped, with the count line."""
        for i in range(5):
            self.write("p{}.ngc".format(i), mtime=1000 + i)
        with mock.patch.object(programs, "PAGE_SIZE", 2):
            listing = programs.list_programs(self.dir)
            self.assertEqual(listing.pages, 3)
            self.assertEqual([f.name for f in listing.page(1)], ["p2.ngc", "p1.ngc"])
            self.assertEqual([f.name for f in listing.page(99)], ["p0.ngc"])
            self.assertEqual([f.name for f in listing.page(-3)], ["p4.ngc", "p3.ngc"])
            self.assertEqual(listing.page_text(1), "Sayfa 2 / 3 · toplam 5 dosya")
            # Every listed file is loadable, whatever page shows it.
            self.assertEqual(programs.resolve(self.dir, "p0.ngc"),
                             os.path.join(self.dir, "p0.ngc"))
        self.assertEqual(programs.list_programs(self.dir).page_text(0), "toplam 5 dosya")
        self.assertEqual(programs.Listing(self.dir, (), 0, "").page_text(0), "")

    def test_empty_missing_not_a_directory_and_no_key_each_say_so(self):
        listing = programs.list_programs(self.dir)
        self.assertEqual((listing.files, listing.error), ((), ""))
        self.assertEqual(listing.empty_reason, programs.DIR_EMPTY.format(self.dir))
        self.assertIn("Dizin boş", listing.empty_reason)
        self.write("oku.txt")
        self.assertIn("Dizin boş", programs.list_programs(self.dir).empty_reason)
        missing = os.path.join(self.dir, "yok")
        self.assertEqual(programs.list_programs(missing).empty_reason,
                         "PROGRAM_PREFIX dizini yok: " + missing)
        afile = self.write("dosya.ngc")
        self.assertEqual(programs.list_programs(afile).empty_reason,
                         programs.DIR_NOT_A_DIR.format(afile))
        self.assertEqual(programs.list_programs(None).empty_reason, programs.NO_DIR_KEY)
        self.assertEqual(programs.list_programs(self.dir).empty_reason, "")

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "root reads anything")
    def test_an_unreadable_directory_says_so(self):
        locked = os.path.join(self.dir, "kilitli")
        os.mkdir(locked)
        os.chmod(locked, 0)
        self.addCleanup(os.chmod, locked, 0o755)
        reason = programs.list_programs(locked).empty_reason
        self.assertTrue(reason.startswith("Program dizini okunamadı: " + locked), reason)


class TestListingErrors(TempDirCase):
    """An OS error anywhere in a scan is a reason on the screen, never an
    exception out of a render."""

    def test_errors_become_reasons(self):
        self.write("a.ngc")
        with mock.patch.object(programs.os, "stat", side_effect=PermissionError(13, "izin")):
            self.assertEqual(programs.list_programs(self.dir).empty_reason,
                             programs.DIR_UNREADABLE.format(self.dir, "izin"))
        with mock.patch.object(programs.os, "scandir", side_effect=OSError(5, "G/Ç hatası")):
            self.assertEqual(programs.list_programs(self.dir).empty_reason,
                             programs.DIR_UNREADABLE.format(self.dir, "G/Ç hatası"))
        with mock.patch.object(programs, "_inside", side_effect=ValueError("x")):
            self.assertIsNone(programs.resolve(self.dir, "a.ngc"))

    def test_a_file_that_cannot_be_read_is_left_out_or_uncounted(self):
        path = self.write("a.ngc")
        real_scandir = os.scandir

        class Entry:
            def __init__(self, e):
                self._e, self.name, self.path = e, e.name, e.path

            def is_file(self):
                return True

            def is_symlink(self):
                return False

            def stat(self):
                raise OSError(5, "G/Ç hatası")

        class Entries:
            def __init__(self, d):
                self._it = real_scandir(d)

            def __enter__(self):
                return (Entry(e) for e in self._it)

            def __exit__(self, *exc):
                self._it.close()

        with mock.patch.object(programs.os, "scandir", Entries):
            self.assertEqual(self.names(), [])
        with mock.patch("builtins.open", side_effect=PermissionError(13, "izin")):
            self.assertIsNone(programs.line_count(path))

    def test_the_count_cache_stays_small(self):
        for i in range(20):
            programs.line_count(self.write("p{}.ngc".format(i)))
        self.assertLessEqual(len(programs._counts), 17)


class TestListingCache(TempDirCase):
    """The poll builds the Program screen every second on a single-core PC:
    while the directory's mtime stands, no scan."""

    def count_scans(self):
        calls = []
        real = os.scandir

        def counting(path):
            calls.append(path)
            return real(path)
        patcher = mock.patch.object(programs.os, "scandir", side_effect=counting)
        patcher.start()
        self.addCleanup(patcher.stop)
        return calls

    def test_a_second_call_does_not_scan(self):
        self.write("a.ngc")
        self.settle()
        scans = self.count_scans()
        first = programs.list_programs(self.dir)
        second = programs.list_programs(self.dir)
        self.assertEqual(len(scans), 1)
        self.assertIs(first, second)

    def test_a_change_to_the_directory_scans_again(self):
        self.write("a.ngc")
        self.settle()
        scans = self.count_scans()
        self.assertEqual(self.names(), ["a.ngc"])
        self.write("b.ngc", mtime=0)
        self.settle()        # a new mtime, still outside the racy window
        self.assertEqual(sorted(self.names()), ["a.ngc", "b.ngc"])
        self.assertEqual(len(scans), 2)

    def test_a_scan_close_to_the_mtime_is_not_trusted(self):
        """A file added in the same mtime tick as the scan would not move
        the mtime: within RACY_S the next call scans again."""
        self.write("a.ngc")
        scans = self.count_scans()
        mtime = os.stat(self.dir).st_mtime
        with mock.patch.object(programs, "_now", lambda: mtime + 0.5):
            programs.list_programs(self.dir)
            programs.list_programs(self.dir)
        self.assertEqual(len(scans), 2)
        with mock.patch.object(programs, "_now", lambda: mtime + programs.RACY_S + 1):
            programs.list_programs(self.dir)
            programs.list_programs(self.dir)
        self.assertEqual(len(scans), 3)

    def test_forget_scans_again(self):
        """«Listeyi yenile»: a file edited in place keeps the directory's
        mtime, so only forget() shows its new size."""
        path = self.write("a.ngc")
        self.settle()
        before = programs.list_programs(self.dir).files[0].size
        mtime = os.stat(self.dir).st_mtime
        with open(path, "a", encoding="utf-8") as fh:
            fh.write("G1 X1\n" * 10)
        os.utime(self.dir, (mtime, mtime))
        self.assertEqual(programs.list_programs(self.dir).files[0].size, before)
        programs.forget(self.dir)
        self.assertGreater(programs.list_programs(self.dir).files[0].size, before)

    def test_a_render_costs_no_scan(self):
        """viewmodel.program_screen, as the 1 Hz poll calls it."""
        self.write("a.ngc")
        self.settle()
        cfg = load_machine_config(PROFILE)._replace(program_dir=self.dir)
        state = MockAdapter(PROFILE).snapshot()
        scans = self.count_scans()
        for _ in range(5):
            VM.program_screen(PROFILE, cfg, state)
        self.assertEqual(scans, [self.dir])     # the first render's only


# ---------------------------------------------------------------------------
# programs.resolve
# ---------------------------------------------------------------------------


class TestResolve(TempDirCase):
    def test_a_listed_name_gives_the_absolute_path(self):
        self.write("faz8-kare.ngc")
        self.assertEqual(programs.resolve(self.dir, "faz8-kare.ngc"),
                         os.path.join(self.dir, "faz8-kare.ngc"))
        self.assertTrue(os.path.isabs(programs.resolve(self.dir, "faz8-kare.ngc")))

    def test_paths_and_names_not_listed_give_nothing(self):
        self.write("a.ngc")
        self.write(".gizli.ngc")
        sub = os.path.join(self.dir, "a")
        os.mkdir(sub)
        self.write("b.ngc", where=sub)
        for name in ("../x.ngc", "/etc/passwd", "a/b.ngc", "a\\b.ngc", ".gizli.ngc",
                     "yok.ngc", "", ".", "..", "a.ngc\0", "A.NGC", " a.ngc", "a.ngc/",
                     os.path.join(self.dir, "a.ngc"), "notlar.txt"):
            with self.subTest(name=name):
                self.assertIsNone(programs.resolve(self.dir, name))
        self.assertIsNone(programs.resolve(None, "a.ngc"))
        self.assertIsNone(programs.resolve(self.dir, None))

    def test_a_link_swapped_to_outside_after_the_scan_is_refused(self):
        outside = tempfile.mkdtemp(prefix="bettercnc-out-")
        self.addCleanup(shutil.rmtree, outside, True)
        inside = self.write("hedef.ngc")
        link = os.path.join(self.dir, "link.ngc")
        os.symlink(inside, link)
        self.settle()
        self.assertEqual(programs.resolve(self.dir, "link.ngc"), link)
        mtime = os.stat(self.dir).st_mtime
        os.unlink(link)
        os.symlink(self.write("disari.ngc", where=outside), link)
        os.utime(self.dir, (mtime, mtime))     # the cache still lists it
        self.assertIn("link.ngc", self.names())
        self.assertIsNone(programs.resolve(self.dir, "link.ngc"))

    def test_a_file_gone_since_the_scan_is_refused(self):
        path = self.write("a.ngc")
        self.settle()
        self.assertIsNotNone(programs.resolve(self.dir, "a.ngc"))
        mtime = os.stat(self.dir).st_mtime
        os.unlink(path)
        os.utime(self.dir, (mtime, mtime))
        self.assertIsNone(programs.resolve(self.dir, "a.ngc"))

    def test_listed_name_and_path_limit(self):
        self.assertEqual(programs.listed_name(self.dir, os.path.join(self.dir, "a.ngc")), "a.ngc")
        self.assertEqual(programs.listed_name(self.dir + "/", os.path.join(self.dir, "a.ngc")),
                         "a.ngc")
        self.assertIsNone(programs.listed_name(self.dir, "/elsewhere/a.ngc"))
        self.assertIsNone(programs.listed_name(self.dir, os.path.join(self.dir, "s", "a.ngc")))
        self.assertIsNone(programs.listed_name(None, "/a.ngc"))
        self.assertIsNone(programs.listed_name(self.dir, None))
        # Bytes, not characters (emcmodule.cc:1210-1214): ş is two.
        name = "ş" * 200 + ".ngc"
        self.assertEqual(programs.path_problem(self.dir, name),
                         programs.PATH_TOO_LONG.format(
                             len(os.path.join(self.dir, name).encode("utf-8")), 254))
        self.assertEqual(programs.path_problem(self.dir, "a.ngc"), "")

    def test_shown_name_is_safe_for_the_message_line(self):
        self.assertEqual(programs.shown_name("a\udcffb\n.ngc"), "a?b?.ngc")
        json.dumps(programs.shown_name("\ud800"), ensure_ascii=False).encode("utf-8")
        self.assertEqual(len(programs.shown_name("x" * 500)), programs._SHOWN_NAME_MAX + 1)


class TestLineCount(TempDirCase):
    def test_counts_like_readlines_and_caches_by_version(self):
        path = self.write("a.ngc", "G0 X0\nG1 X1\nM2")
        self.assertEqual(programs.line_count(path), 3)
        with open(path, encoding="utf-8") as fh:
            self.assertEqual(len(fh.readlines()), 3)
        with mock.patch("builtins.open", side_effect=AssertionError("read again")):
            self.assertEqual(programs.line_count(path), 3)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write("\nM30\n")
        self.assertEqual(programs.line_count(path), 4)
        self.assertEqual(programs.line_count(self.write("bos.ngc", "")), 0)
        self.assertIsNone(programs.line_count(os.path.join(self.dir, "yok.ngc")))
        self.assertIsNone(programs.line_count(None))

    def test_a_fifo_is_never_opened(self):
        """stat.file can name anything AXIS opened; reading a FIFO would
        block the poll forever."""
        fifo = os.path.join(self.dir, "boru.ngc")
        os.mkfifo(fifo)
        self.assertIsNone(programs.line_count(fifo))
        self.assertEqual(self.names(), [])

    def test_a_huge_file_is_not_read(self):
        path = self.write("a.ngc", "G0 X0\n" * 10)
        with mock.patch.object(programs, "MAX_COUNT_BYTES", 5):
            self.assertIsNone(programs.line_count(path))


# ---------------------------------------------------------------------------
# programs.load_block
# ---------------------------------------------------------------------------


class TestLoadBlock(unittest.TestCase):
    def state(self, **kw):
        return MockAdapter(PROFILE).snapshot()._replace(**kw)

    def test_each_reason(self):
        s = self.state()
        self.assertEqual(programs.load_block(s), "")
        homing = s.homing._replace(joints=tuple(
            j._replace(phase=HomingPhase.HOMING) if j.axis == "Z" else j for j in s.homing.joints))
        for kw, text in (
            (dict(status_known=False), programs.LOAD_UNKNOWN),
            (dict(program_paused=None), programs.LOAD_UNREAD),
            (dict(mdi_running=None), programs.LOAD_UNREAD),
            (dict(program_paused=True), programs.LOAD_PAUSED),
            (dict(program_running=True), programs.LOAD_RUNNING),
            (dict(task_mode="mdi", interp_state="reading"), programs.LOAD_RUNNING),
            (dict(mdi_running=True), programs.LOAD_RUNNING),
            (dict(homing=homing), programs.LOAD_HOMING),
            (dict(moving=True), programs.LOAD_MOVING),
            (dict(moving=None), programs.LOAD_MOVING_UNREAD),
        ):
            with self.subTest(kw=kw):
                self.assertEqual(programs.load_block(s._replace(**kw)), text)

    def test_the_machine_off_does_not_block(self):
        """Task takes PLAN_OPEN out of ON (emctaskmain.cc:807-844)."""
        s = MockAdapter(PROFILE).snapshot()
        self.assertTrue(s.estop.blocks_motion)   # premise: a fresh mock is off
        self.assertEqual(programs.load_block(s), "")


# ---------------------------------------------------------------------------
# Session on the fake LinuxCNC
# ---------------------------------------------------------------------------


class TestSessionLoadsOnTheFake(T.FakeCase):
    def setUp(self):
        super().setUp()
        self.dir = tempfile.mkdtemp(prefix="bettercnc-prog-")
        self.addCleanup(shutil.rmtree, self.dir, True)
        programs._listings.clear()
        self.addCleanup(programs._listings.clear)
        self.path = os.path.join(self.dir, "kare.ngc")
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write("G0 X10\nG0 X0\nM2\n")

    def session(self, adapter, observe=False):
        cfg = load_machine_config(self.profile)._replace(program_dir=self.dir)
        return app.Session(adapter, self.profile, cfg, observe=observe)

    def test_a_listed_name_loads_the_absolute_path_as_axis_does(self):
        """open_file_guts (axis.py:1174-1208): MANUAL, task_plan_synch,
        wait, program_open(path), wait - from AUTO here, so the mode
        switch comes first. The path is absolute: milltask runs in the
        INI's directory (linuxcnc.in:777)."""
        adapter = self.on(homed=True)
        self.machine.task_mode = C["MODE_AUTO"]
        sess = self.session(adapter)
        sess.act("load_program", "kare.ngc")
        self.assertNoLinuxCncErrors()
        W = (T.WAIT,)
        self.assertEqual(self.machine.log, [
            ("mode", (C["MODE_MANUAL"],)), ("wait_complete", W),
            ("task_plan_synch", ()), ("wait_complete", W),
            ("program_open", (self.path,)), ("wait_complete", W),
        ])
        self.assertTrue(os.path.isabs(self.machine.file))
        snap = adapter.snapshot()
        self.assertEqual(snap.program_file, self.path)
        self.assertEqual(snap.message, "kare.ngc yüklendi.")
        program = sess.view("program")["program"]
        self.assertEqual(program["loaded"], "kare.ngc")
        self.assertEqual(program["loaded_path"], self.path)
        self.assertEqual(program["loaded_lines"], 3)
        self.assertEqual([f["loaded"] for f in program["files"]], [True])

    def test_a_bad_name_sends_nothing_and_says_so(self):
        adapter = self.on(homed=True)
        sess = self.session(adapter)
        for name in ("../../etc/passwd", "/etc/passwd", self.path, "alt/kare.ngc", ".kare.ngc",
                     "yok.ngc", "", "kare.ngc\0", "\ud800.ngc"):
            with self.subTest(name=name):
                del self.machine.log[:]
                sess.act("load_program", name)
                self.assertEqual(self.machine.log, [])
                snap = adapter.snapshot()
                self.assertTrue(snap.message.startswith("Program listede yok: "), snap.message)
                self.assertIs(snap.message_severity, Severity.WARN)
                self.assertIsNone(snap.program_file)
                # The answer must still encode (a lone surrogate would not).
                json.dumps(sess.view("program"), ensure_ascii=False).encode("utf-8")

    def test_reload_takes_the_loaded_files_name(self):
        adapter = self.on(homed=True)
        sess = self.session(adapter)
        sess.act("load_program", "kare.ngc")
        del self.machine.log[:]
        sess.act("reload_program", "/etc/passwd")      # the value is ignored
        self.assertEqual([c for c in self.machine.calls() if c != "wait_complete"],
                         ["task_plan_synch", "program_open"])
        self.assertEqual(self.machine.file, self.path)

    def test_reload_refuses_nothing_loaded_and_a_file_from_elsewhere(self):
        adapter = self.on(homed=True)
        sess = self.session(adapter)
        sess.act("reload_program", "")
        self.assertEqual(self.machine.log, [])
        self.assertEqual(adapter.snapshot().message, programs.NOTHING_LOADED)
        self.machine.file = "/elsewhere/x.ngc"          # loaded from AXIS, say
        sess.act("reload_program", "")
        self.assertEqual(self.machine.log, [])
        self.assertEqual(adapter.snapshot().message,
                         programs.LOADED_OUTSIDE.format("/elsewhere/x.ngc"))
        program = sess.view("program")["program"]
        self.assertFalse(program["reload_enabled"])
        self.assertEqual(program["reload_disabled_reason"],
                         programs.LOADED_OUTSIDE.format("/elsewhere/x.ngc"))
        self.assertIsNone(program["loaded_listed"])

    def test_refused_while_a_program_runs_or_is_paused_and_the_buttons_say_why(self):
        adapter = self.on(homed=True)
        sess = self.session(adapter)
        sess.act("load_program", "kare.ngc")
        for name, prepare, text in (
            ("running", lambda m: setattr(m, "interp_state", C["INTERP_READING"]),
             programs.LOAD_RUNNING),
            ("paused", lambda m: setattr(m, "interp_state", C["INTERP_PAUSED"]),
             programs.LOAD_PAUSED),
            ("mdi", lambda m: (setattr(m, "task_mode", C["MODE_MDI"]),
                               setattr(m, "interp_state", C["INTERP_READING"])),
             programs.LOAD_RUNNING),
            ("moving", lambda m: setattr(m, "current_vel", 3.0), programs.LOAD_MOVING),
        ):
            with self.subTest(state=name):
                self.machine.task_mode = C["MODE_AUTO"]
                self.machine.interp_state = C["INTERP_IDLE"]
                self.machine.current_vel = 0.0
                # Plan WP09: the snapshot that finds the previous subtest's
                # run over says so ("Program bitti"); let it, before the
                # refusal this subtest reads.
                adapter.snapshot()
                prepare(self.machine)
                del self.machine.log[:]
                for action in ("load_program", "reload_program"):
                    sess.act(action, "kare.ngc")
                    self.assertEqual(self.machine.log, [])
                    self.assertEqual(adapter.snapshot().message, text)
                program = sess.view("program")["program"]
                self.assertFalse(program["load_enabled"])
                self.assertEqual([f["enabled"] for f in program["files"]], [False])
                self.assertFalse(program["reload_enabled"])
                card = visible(program_card(R.render_body(sess.view("program"))))
                self.assertIn(R.e(text), card)

    def test_loading_with_the_machine_off_works(self):
        """As AXIS: task takes PLAN_OPEN out of ON (emctaskmain.cc:807-844)."""
        adapter = self.make()
        sess = self.session(adapter)
        sess.act("load_program", "kare.ngc")
        self.assertEqual(self.machine.file, self.path)

    def test_observe_sends_nothing_for_a_load(self):
        adapter = self.make()
        self.machine.power_on(homed=True)
        sess = self.session(adapter, observe=True)
        for action in ("load_program", "reload_program", "refresh_programs"):
            sess.act(action, "kare.ngc")
        self.assertEqual(self.machine.log, [])
        self.assertEqual(self.machine.file, "")
        program = sess.view("program")["program"]
        self.assertFalse(program["load_enabled"])
        self.assertEqual(program["load_disabled_reason"], VM.OBSERVE_REASON)
        self.assertFalse(program["refresh_enabled"])
        self.assertFalse(program["reload_enabled"])

    def test_the_fake_limits_the_path_in_bytes(self):
        """emcmodule.cc:1210-1214: "s#" measures the UTF-8 bytes."""
        adapter = self.on(homed=True)
        adapter.load_program("/" + "ş" * 130 + ".ngc")     # 130 chars, 265 bytes
        self.assertEqual(adapter.snapshot().message, "Dosya yolu çok uzun — program yüklenmedi.")


class TestSessionOnTheMock(TempDirCase):
    def session(self, observe=False):
        cfg = load_machine_config(PROFILE)._replace(program_dir=self.dir)
        return app.Session(MockAdapter(PROFILE), PROFILE, cfg, observe=observe)

    def test_the_mock_follows_the_same_rule_and_shows_the_base_name(self):
        path = self.write("kare.ngc")
        sess = self.session()
        sess.act("load_program", "kare.ngc")
        snap = sess.adapter.snapshot()
        self.assertEqual(snap.program_file, path)
        self.assertEqual(snap.message, "kare.ngc yüklendi.")
        sess.adapter._running = True
        sess.act("load_program", "kare.ngc")
        self.assertEqual(sess.adapter.snapshot().message, programs.LOAD_RUNNING)
        sess.act("load_program", "../kare.ngc")
        self.assertTrue(sess.adapter.snapshot().message.startswith("Program listede yok"))

    def test_refresh_drops_the_cache(self):
        self.write("a.ngc")
        self.settle()
        sess = self.session()
        self.assertEqual([f["name"] for f in sess.view("program")["program"]["files"]], ["a.ngc"])
        path = os.path.join(self.dir, "a.ngc")
        mtime = os.stat(self.dir).st_mtime
        with open(path, "a", encoding="utf-8") as fh:
            fh.write("x" * 2000)
        os.utime(self.dir, (mtime, mtime))
        self.assertEqual(sess.view("program")["program"]["files"][0]["size_text"], "9 B")
        sess.act("refresh_programs", "")
        self.assertEqual(sess.view("program")["program"]["files"][0]["size_text"], "2,0 kB")

    def test_a_load_starts_no_motion_so_it_is_not_dropped_after_a_stop(self):
        self.assertNotIn("load_program", app.MOTION_ACTIONS)
        self.assertNotIn("reload_program", app.DROPPED_AFTER_A_STOP)


# ---------------------------------------------------------------------------
# What the load path relies on, against reference/linuxcnc
# ---------------------------------------------------------------------------


@T.NEEDS_REFERENCE
class TestLinuxCncFacts(unittest.TestCase):
    """The LinuxCNC 2.9 behaviour WP08 builds on, read from the source.
    Content, not line numbers. Skipped without reference/."""

    def test_the_binding_closes_first_and_limits_the_utf8_bytes(self):
        """program_open sends PLAN_CLOSE before it parses its argument and
        refuses more than sizeof(file) - 1 bytes of the "s#" (UTF-8)
        argument (emcmodule.cc:1202-1218) - the fake's rule and
        programs.PATH_LIMIT_BYTES."""
        text = T._src("emc/usr_intf/axis/extensions/emcmodule.cc")
        body = text[text.index("static PyObject *program_open("):]
        body = body[:body.index("\n}\n")]
        self.assertLess(body.index("EMC_TASK_PLAN_CLOSE m0;"), body.index("PyArg_ParseTuple"))
        self.assertIn('PyArg_ParseTuple(o, "s#", &file, &len)', body)
        self.assertIn("if(unsigned(len) > sizeof(m.file) - 1)", body)
        nml = T._src("emc/nml_intf/emc_nml.hh")
        plan_open = nml[nml.index("class EMC_TASK_PLAN_OPEN"):]
        self.assertIn("char file[LINELEN];", plan_open[:plan_open.index("\n};")])
        self.assertEqual(programs.PATH_LIMIT_BYTES, F.LINE_LIMIT)

    def test_axis_synchs_then_opens_and_task_takes_both_out_of_on(self):
        """open_file_guts: MANUAL, task_plan_synch, wait_complete,
        program_open (axis.py:1174-1208); task takes SET_MODE, PLAN_SYNCH
        and PLAN_OPEN in ESTOP / ESTOP_RESET / OFF (emctaskmain.cc:
        787-844), and PLAN_OPEN but not PLAN_SYNCH in AUTO idle (:1013-
        1080) - why the adapter switches to MANUAL first."""
        axis = T._src("emc/usr_intf/axis/scripts/axis.py")
        guts = axis[axis.index("def open_file_guts("):]
        guts = guts[:guts.index("\ndef ")]
        order = [guts.index(x) for x in ("ensure_mode(linuxcnc.MODE_MANUAL)",
                                         "c.task_plan_synch()", "c.wait_complete()",
                                         "c.program_open(f)")]
        self.assertEqual(order, sorted(order))
        task = T._src("emc/task/emctaskmain.cc")
        off = task[task.index("case EMC_TASK_STATE_ESTOP_RESET:"):]
        off = off[:off.index("case EMC_JOINT_OVERRIDE_LIMITS_TYPE:")]
        for cmd in ("EMC_TASK_SET_MODE_TYPE", "EMC_TASK_PLAN_OPEN_TYPE",
                    "EMC_TASK_PLAN_SYNCH_TYPE"):
            self.assertIn("case {}:".format(cmd), off)
        auto = task[task.index("case EMC_TASK_INTERP_IDLE:\t// ON, AUTO, IDLE"):]
        auto = auto[:auto.index("can't do that (%s) in auto mode with the interpreter idle")]
        self.assertIn("case EMC_TASK_PLAN_OPEN_TYPE:", auto)
        self.assertNotIn("case EMC_TASK_PLAN_SYNCH_TYPE:", auto)

    def test_program_prefix_is_read_relative_to_the_ini_directory(self):
        """The linuxcnc script changes to the INI's directory before it
        starts anything; the interpreter expands ~ and takes realpath() of
        PROGRAM_PREFIX (rs274ngc_pre.cc:946-957); AXIS's dialog opens there
        (axis.py:3347) - configmodel.program_dir's reading."""
        script = os.path.join(REPO_ROOT, "reference", "linuxcnc", "scripts", "linuxcnc.in")
        with open(script, encoding="utf-8", errors="replace") as fh:
            self.assertIn('\ncd "$INI_DIR"\n', fh.read())
        interp = T._src("emc/rs274ngc/rs274ngc_pre.cc")
        block = interp[interp.index('inifile.Find("PROGRAM_PREFIX", "DISPLAY")'):]
        block = block[:block.index("PROGRAM_PREFIX not found")]
        self.assertIn("TildeExpansion", block)
        self.assertIn("realpath(expandinistring, _setup.program_prefix)", block)
        axis = T._src("emc/usr_intf/axis/scripts/axis.py")
        self.assertIn('open_directory = inifile.find("DISPLAY", "PROGRAM_PREFIX")', axis)


# ---------------------------------------------------------------------------
# The Program screen
# ---------------------------------------------------------------------------


class TestProgramScreen(TempDirCase):
    def screen(self, state=None, observe=False, directory="__dir__"):
        cfg = load_machine_config(PROFILE)
        cfg = cfg._replace(program_dir=self.dir if directory == "__dir__" else directory)
        state = state if state is not None else MockAdapter(PROFILE).snapshot()
        vm = VM.build(PROFILE, cfg, state, "program", observe)
        return vm["program"], visible(R.render_body(vm))

    def test_rows_buttons_and_the_loaded_mark(self):
        self.write("a.ngc", mtime=1000)
        loaded = self.write("b.ngc", "G0\nG1\nM2\n", mtime=2000)
        state = MockAdapter(PROFILE).snapshot()._replace(program_file=loaded)
        program, page = self.screen(state)
        self.assertEqual([(f["name"], f["loaded"]) for f in program["files"]],
                         [("b.ngc", True), ("a.ngc", False)])
        card = program_card(page)
        self.assertIn('data-action="load_program" data-value="b.ngc"', card)
        self.assertIn('data-action="load_program" data-value="a.ngc"', card)
        self.assertIn('data-file="b.ngc" data-loaded="1"', card)
        self.assertIn("YÜKLÜ", card)
        self.assertIn(R.e(loaded), card)
        self.assertIn("3 satır", card)
        self.assertIn('data-action="reload_program"', card)
        self.assertIn('data-action="refresh_programs"', card)
        self.assertIn("Dizin: " + R.e(self.dir), card)

    def test_an_odd_mtime_does_not_break_the_render(self):
        self.write("a.ngc")
        self.assertEqual(VM._date_text(1e300), "—")
        self.assertEqual(VM._size_text(512), "512 B")
        self.assertEqual(VM._size_text(3 * 1024 * 1024), "3,0 MB")

    def test_reload_off_says_why_as_text(self):
        self.write("a.ngc")
        state = MockAdapter(PROFILE).snapshot()._replace(program_file="/elsewhere/x.ngc")
        program, page = self.screen(state)
        self.assertFalse(program["reload_enabled"])
        self.assertIn("Yeniden yükle kapalı: " + R.e(programs.LOADED_OUTSIDE.format(
            "/elsewhere/x.ngc")), program_card(page))
        self.assertEqual([f["loaded"] for f in program["files"]], [False])

    def test_a_cut_list_says_so(self):
        for i in range(3):
            self.write("p{}.ngc".format(i), mtime=1000 + i)
        with mock.patch.object(programs, "MAX_FILES", 2):
            program, page = self.screen()
        self.assertEqual(len(program["files"]), 2)
        self.assertIn(R.e(programs.TRUNCATED.format(2, 3)), program_card(page))

    def test_a_long_list_pages_with_the_buttons_and_the_count(self):
        """P0 cleanup item 9: «Önceki» / «Sonraki» and 'toplam N dosya';
        a page is view state, so observer mode pages too."""
        for i in range(5):
            self.write("p{}.ngc".format(i), mtime=1000 + i)
        with mock.patch.object(programs, "PAGE_SIZE", 2):
            for observe in (False, True):
                cfg = load_machine_config(PROFILE)._replace(program_dir=self.dir)
                vm = VM.build(PROFILE, cfg, MockAdapter(PROFILE).snapshot(), "program", observe,
                              program_page=1)
                program, page = vm["program"], visible(R.render_body(vm))
                with self.subTest(observe=observe):
                    self.assertEqual([f["name"] for f in program["files"]],
                                     ["p2.ngc", "p1.ngc"])
                    self.assertEqual((program["page"], program["pages"]), (1, 3))
                    card = program_card(page)
                    self.assertIn("Sayfa 2 / 3 · toplam 5 dosya", card)
                    self.assertIn('data-action="program_page" data-value="0">Önceki<', card)
                    self.assertIn('data-action="program_page" data-value="2">Sonraki<', card)
            program = VM.build(PROFILE, cfg, MockAdapter(PROFILE).snapshot(), "program",
                               program_page=2)["program"]
            self.assertIsNone(program["page_next"])
            self.assertIn("disabled", program_card(visible(R.render_body(VM.build(
                PROFILE, cfg, MockAdapter(PROFILE).snapshot(), "program", program_page=2)))
            ).split(">Sonraki<")[0].rsplit("<button", 1)[1])
        program, page = self.screen()
        self.assertIn("toplam 5 dosya", program_card(page))
        self.assertNotIn("program_page", page)                  # one page: no buttons

    def test_a_name_is_escaped(self):
        self.write('a"<b>.ngc')
        _, page = self.screen()
        self.assertIn('data-value="a&quot;&lt;b&gt;.ngc"', page)
        self.assertNotIn("<b>.ngc", page)

    def test_empty_reasons_are_visible_and_distinct(self):
        _, page = self.screen()
        self.assertIn("Dizin boş", program_card(page))
        missing = os.path.join(self.dir, "yok")
        _, page = self.screen(directory=missing)
        self.assertIn("PROGRAM_PREFIX dizini yok: " + R.e(missing), program_card(page))
        self.assertNotIn("Dizin boş", program_card(page))
        _, page = self.screen(directory=None)
        self.assertIn("PROGRAM_PREFIX yok", program_card(page))

    def test_load_off_reasons_are_text_not_only_titles(self):
        self.write("a.ngc")
        state = MockAdapter(PROFILE).snapshot()._replace(program_running=True)
        program, page = self.screen(state)
        self.assertEqual(program["load_disabled_reason"], programs.LOAD_RUNNING)
        self.assertIn(R.e(programs.LOAD_RUNNING), program_card(page))
        unknown = MockAdapter(PROFILE).snapshot().as_status_unknown()
        program, page = self.screen(unknown)
        self.assertFalse(program["load_enabled"])
        self.assertEqual([f["enabled"] for f in program["files"]], [False])
        self.assertIn(R.e(programs.LOAD_UNKNOWN), program_card(page))
        self.assertIsNone(program["loaded"])

    def test_a_too_long_path_is_off_with_its_own_reason(self):
        name = "ş" * 120 + ".ngc"
        self.write(name)
        program, page = self.screen()
        self.assertFalse(program["files"][0]["enabled"])
        self.assertIn("çok uzun", program["files"][0]["reason"])
        self.assertIn(R.e(program["files"][0]["reason"]), program_card(page))

    def test_start_reason_is_visible_text_and_the_gate_says_no_program(self):
        self.write("a.ngc")
        adapter = MockAdapter(PROFILE)
        adapter.software_estop_reset()
        adapter.home_all()
        program, page = self.screen(adapter.snapshot())
        self.assertFalse(program["can_start"])
        self.assertEqual(program["start_disabled_reason"], VM.START_NO_PROGRAM)
        self.assertIn("Başlat kapalı: Program yüklenmedi.", program_card(page))
        self.assertEqual(program["gate"]["program"], VM.NO_PROGRAM)
        self.assertEqual(program["gate"]["blocks"], [])     # start_blocks unchanged
        self.assertIn("PROGRAM YOK", page)
        self.assertNotIn("Program başlatılabilir.", page)
        adapter.load_program(os.path.join(self.dir, "a.ngc"))
        program, page = self.screen(adapter.snapshot())
        self.assertTrue(program["can_start"])
        self.assertEqual(program["gate"]["program"], "")
        self.assertIn("Program başlatılabilir.", page)
        self.assertNotIn("Başlat kapalı", page)

    def test_with_blocks_the_gate_still_names_the_missing_program(self):
        program, page = self.screen()
        self.assertTrue(program["gate"]["blocks"])
        self.assertIn('class="cap-reason gate-program"', page)
        self.assertIn("Başlat kapalı: " + R.e(program["gate"]["headline"]), page)

    def test_observe_turns_every_program_control_off_with_the_reason(self):
        self.write("a.ngc")
        program, page = self.screen(observe=True)
        self.assertFalse(program["load_enabled"] or program["refresh_enabled"]
                         or program["reload_enabled"] or program["can_start"])
        self.assertIn(R.e(VM.OBSERVE_REASON), program_card(page))
        self.assertNotRegex(program_card(page),
                            r'<button class="btn prog-btn" data-action="load_program"')

    def test_the_rig_shows_faz8(self):
        cfg = load_machine_config(PROFILE)
        vm = VM.build(PROFILE, cfg, MockAdapter(PROFILE).snapshot(), "program")
        self.assertIn("faz8-kare.ngc", [f["name"] for f in vm["program"]["files"]])


class TestTheProgramPageIsViewState(unittest.TestCase):
    """P0 cleanup item 9: the page travels with the render and the action
    like the screen does - never as an action (it sends nothing to
    LinuxCNC, so observer mode pages too and test_observe_and_live_hal's
    ACTIONS are unchanged)."""

    def test_the_server_reads_a_page_or_takes_0(self):
        from bettercnc import app as APP
        for raw, page in (("2", 2), (" 3 ", 3), ("-1", 0), ("x", 0), (None, 0), (1.5, 0),
                          ("99999999", 0), (True, 0), ("0", 0)):
            with self.subTest(raw=raw):
                self.assertEqual(APP._valid_page(raw), page)

    def test_the_session_view_passes_it_on(self):
        from bettercnc import app as APP
        sess = APP.build_session("test-rig")
        with mock.patch.object(VM, "build", wraps=VM.build) as build:
            sess.view("program", 4)
        self.assertEqual(build.call_args.kwargs["program_page"], 4)

    def test_app_js_keeps_it_and_never_posts_it_as_an_action(self):
        with open(os.path.join(os.path.dirname(R.__file__), "static", "app.js"),
                  encoding="utf-8") as fh:
            js = fh.read()
        self.assertIn('"&page=" + programPage', js)
        self.assertIn("page: programPage", js)
        block = js[js.index('if (action === "program_page")'):]
        block = block[:block.index("return;")]
        self.assertIn("refresh()", block)
        self.assertNotIn("post(", block)
        self.assertNotIn("program_page", APP_ACTIONS_SOURCE())


def APP_ACTIONS_SOURCE() -> str:
    import inspect
    from bettercnc import app as APP
    return inspect.getsource(APP.Session.act)


if __name__ == "__main__":
    unittest.main()
