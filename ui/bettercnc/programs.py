"""The program directory (plan WP08): which G-code files the Program screen
offers, which name may be loaded, and when a load may go.

Only names this module lists from the server's own PROGRAM_PREFIX directory
are ever loaded (resolve): a name from the browser is looked up in that
list, never used as a path, and LinuxCNC gets the absolute path built here.
milltask runs in the INI's directory (reference/linuxcnc/scripts/
linuxcnc.in:777 `cd "$INI_DIR"`), so a relative path would open against
that directory, or not at all (sim lens).

The listing is cached by the directory's mtime (list_programs): the 1 Hz
poll builds the Program screen on every render, and the rig's PC is single
core, so a poll costs one os.stat of the directory and no scan unless the
directory changed. A file edited in place does not change its directory's
mtime, so its size and date can be stale until «Listeyi yenile» (forget).
The loaded file's line count (line_count, for WP09's progress) is cached by
(path, mtime, size) the same way.

How LinuxCNC itself reads PROGRAM_PREFIX: AXIS opens its file dialog there
(reference/linuxcnc/src/emc/usr_intf/axis/scripts/axis.py:3347), relative
to its working directory, the INI's; the interpreter expands a leading ~ and
takes realpath() of it (reference/linuxcnc/src/emc/rs274ngc/
rs274ngc_pre.cc:946-957). configmodel.program_dir resolves it the same way
against the INI's directory.
"""

from __future__ import annotations

import os
import stat
import threading
import time
from typing import Dict, FrozenSet, NamedTuple, Optional, Tuple

from .state import MachineState

#: G-code extensions listed, compared case-insensitively (the plan's list).
#: The interpreter opens a file whatever its name (Interp::open, a plain
#: fopen: reference/linuxcnc/src/emc/rs274ngc/rs274ngc_pre.cc:1387-1404);
#: AXIS's open dialog shows .ngc plus any [FILTER]PROGRAM_EXTENSION
#: (axis.py:2233-2239; the rig's INI has none), and .nc / .tap are common
#: G-code names from CAM post-processors.
EXTENSIONS = (".ngc", ".nc", ".tap")

#: Files per page of the Program screen's list, the newest first (P0
#: cleanup: paged, so every listed file can be loaded; until then the list
#: stopped here). Chosen, not measured: a screen of buttons.
PAGE_SIZE = 200

#: At most this many files are listed - and so can be loaded - the newest
#: first: the scan's upper bound. A scan runs once per directory change
#: (list_programs' cache), never on the 1 Hz poll, and a page renders
#: PAGE_SIZE rows whatever the total. Beyond it the list says how many
#: files it left out (Listing.note). Chosen, not measured.
MAX_FILES = 5000

#: The longest path LinuxCNC takes: EMC_TASK_PLAN_OPEN::file is
#: char[LINELEN], LINELEN = 255 (emc_nml.hh:1309, linuxcnc.h:18), and the
#: binding refuses more than sizeof(file) - 1 bytes of the UTF-8 argument
#: ("s#", reference/linuxcnc/src/emc/usr_intf/axis/extensions/
#: emcmodule.cc:1210-1214). Bytes, not characters: "ş" is two.
PATH_LIMIT_BYTES = 254

#: A directory changed less than this before its scan may change again
#: within the same mtime tick (1 s on ext3, 2 s on FAT) without its mtime
#: moving: such a scan is not trusted and the next call scans again (the
#: "racy" rule git uses for its index). Chosen, not measured.
RACY_S = 2.0

#: A loaded file larger than this is not read to count its lines
#: (line_count returns None): the count runs at most once per file version,
#: but a poll must never read a huge file. Chosen, not measured.
MAX_COUNT_BYTES = 32 * 1024 * 1024

#: A loaded file larger than this keeps no copy of its lines for the
#: running line's text (line_text returns None): the copy is made once per
#: file version, at load time, and held in memory. Chosen, not measured.
MAX_TEXT_BYTES = 1024 * 1024

#: A line's text is cut to this many characters on the Program screen.
#: Chosen, not measured.
LINE_TEXT_MAX = 120

#: Shown in place of a name that cannot be shown (refuse texts).
_SHOWN_NAME_MAX = 80

# -- operator texts -----------------------------------------------------------

NOT_LISTED = "Program listede yok: {} — yalnız program dizinindeki dosyalar yüklenir."
NO_DIR_KEY = "INI'de [DISPLAY]PROGRAM_PREFIX yok — program dizini bilinmiyor."
DIR_MISSING = "PROGRAM_PREFIX dizini yok: {}"
DIR_NOT_A_DIR = "PROGRAM_PREFIX bir dizin değil: {}"
DIR_UNREADABLE = "Program dizini okunamadı: {} ({})"
DIR_EMPTY = "Dizin boş — .ngc, .nc ya da .tap dosyası yok: {}"
TRUNCATED = ("Dizinde {1} G-code dosyası var; en yeni {0} tanesi listelendi — ötekiler "
             "buradan yüklenemez.")
#: The list's count line (Listing.count_text), shown whenever it has files.
TOTAL = "toplam {} dosya"
#: With more than one page: which page this is (Listing.page_text).
PAGE = "Sayfa {} / {} · toplam {} dosya"
PATH_TOO_LONG = "Dosya yolu LinuxCNC için çok uzun ({} bayt, sınır {})."
NOTHING_LOADED = "Yüklü program yok."
LOADED_OUTSIDE = "Yüklü dosya program dizininde değil: {}"

# Why no program may be loaded now (load_block): the Program screen's
# buttons and both adapters' refusals.
LOAD_UNKNOWN = "LinuxCNC durumu bilinmiyor — program yüklenemez."
LOAD_UNREAD = "Program durumu okunamadı — program yüklenemez."
LOAD_PAUSED = "Program duraklatılmış — yeni program için önce «Durdur»."
LOAD_RUNNING = "Program çalışırken yeni program yüklenemez."
LOAD_HOMING = "Referans alma sürüyor — program yüklenemez."
LOAD_MOVING = "Hareket sürüyor — bitmesini bekleyin; program yüklenemez."
LOAD_MOVING_UNREAD = "Hareket durumu okunamadı — program yüklenemez."


# Running a program (plan WP09): why «Başlat», «Duraklat», «Devam» and
# «Adım» may not go now (start_busy, pause_refusal, resume_refusal,
# step_refusal) - the Program screen's buttons and both adapters' refusals.
RUN_UNKNOWN = "LinuxCNC durumu bilinmiyor."
RUN_UNREAD = "Programın çalışma durumu okunamadı."
START_PAUSED = "Program duraklatıldı — sürdürmek için «Devam», bitirmek için «Durdur»."
START_RUNNING = "Program çalışıyor."
START_MDI = "MDI satırı çalışıyor — bitmesini bekleyin."
PAUSE_IDLE = "Çalışan program yok — duraklatılacak bir şey yok."
PAUSE_ALREADY = "Program zaten duraklatıldı — «Devam» ya da «Durdur»."
RESUME_NOT_PAUSED = "Duraklamış program yok."
#: A pause with LinuxCNC in MANUAL - sent by another GUI or halui: task
#: takes PLAN_PAUSE in MANUAL too (emctaskmain.cc:947). There is no
#: program to resume (leaving AUTO or MDI for MANUAL aborts, :2132-2162);
#: an abort clears motion's pause (command.c:530).
PAUSED_IN_MANUAL = "LinuxCNC MANUAL kipinde duraklatılmış — sürecek program yok; «Durdur» temizler."
STEP_RUNNING = "Program çalışıyor — tek adım için önce «Duraklat»."
#: A program stepped to its very end with «Adım» alone: LinuxCNC still
#: shows motion paused with nothing left to run (MachineState.
#: pause_left_at_end). «Devam», «Başlat» and «Adım» are refused with it;
#: «Durdur» (an abort, command.c:530) clears it. P0 cleanup.
PAUSED_AT_END = ("Program adım adım sonuna kadar yürüdü ama LinuxCNC hâlâ duraklatılmış "
                 "gösteriyor — «Durdur» ile bitirin.")
STEP_MDI = "MDI satırı çalışıyor — tek adım yalnız programda."

# Messages after an action or at a run's end (the adapters).
PAUSED_TEXT = "Program duraklatıldı — «Devam» ile sürer, «Durdur» bitirir."
PAUSE_NOT_TAKEN = "Duraklatma gönderildi — LinuxCNC henüz duraklamadı."
RESUMED_TEXT = "Program sürüyor."
MDI_PAUSED_TEXT = "MDI satırı duraklatıldı — «Devam» ile sürer, «Durdur» bitirir."
MDI_RESUMED_TEXT = "MDI satırı sürüyor."
RESUME_NOT_TAKEN = "Devam gönderildi — LinuxCNC henüz sürmedi."
STEP_FIRST_TEXT = "Tek adım: program başında duraklatıldı — her «Adım» bir satır ilerletir."
STEP_TEXT = "Tek adım: bir satır."
STEP_NOT_TAKEN = "Adım gönderildi — LinuxCNC duraklamış görünmüyor."
STARTED_TEXT = "Program başlatıldı: {}"
START_NOT_TAKEN = "Başlatma gönderildi — LinuxCNC programı başlatmadı."
FINISHED_TEXT = "Program bitti: {}"
#: FINISHED_TEXT when the end leaves motion's pause set (PAUSED_AT_END).
FINISHED_PAUSED_TEXT = ("Program bitti: {} — LinuxCNC hâlâ duraklatılmış gösteriyor; "
                        "«Durdur» ile bitirin.")
STOPPED_TEXT = "Program durduruldu: {}"
FAILED_TEXT = "Program hatayla durdu: {} — ayrıntı hata kutusunda."


class ProgramFile(NamedTuple):
    """One listed file: its name in the directory, size in bytes, mtime."""

    name: str
    size: int
    mtime: float


class Listing(NamedTuple):
    """What list_programs found in `directory`: up to MAX_FILES files,
    newest first, `total` of them in all; or `error`, the operator's words
    for why nothing could be listed ("" when the scan worked, even with no
    file - files () and total 0 then). The screen shows it a page of
    PAGE_SIZE at a time (page); resolve takes any name in `files`."""

    directory: Optional[str]
    files: Tuple[ProgramFile, ...]
    total: int
    error: str
    #: The names in `files`, for resolve's exact membership test.
    names: FrozenSet[str] = frozenset()

    @property
    def pages(self) -> int:
        """How many pages the list has (at least 1)."""
        return max(1, -(-len(self.files) // PAGE_SIZE))

    def page_index(self, page: int) -> int:
        """`page` (0-based) clamped into the list's pages."""
        return max(0, min(int(page), self.pages - 1))

    def page(self, page: int) -> Tuple[ProgramFile, ...]:
        """The files of page `page` (0-based, clamped: page_index)."""
        start = self.page_index(page) * PAGE_SIZE
        return self.files[start:start + PAGE_SIZE]

    def page_text(self, page: int) -> str:
        """The count line under the list: 'toplam N dosya', with the page
        when there is more than one; "" with no file."""
        if not self.files:
            return ""
        if self.pages == 1:
            return TOTAL.format(len(self.files))
        return PAGE.format(self.page_index(page) + 1, self.pages, len(self.files))

    @property
    def empty_reason(self) -> str:
        """Why the list shows no file: the directory is missing, unreadable
        or empty - never one for another."""
        if self.error:
            return self.error
        if not self.files:
            return DIR_EMPTY.format(self.directory)
        return ""

    @property
    def note(self) -> str:
        """Said under the list when the scan stopped at MAX_FILES."""
        return TRUNCATED.format(len(self.files), self.total) if self.total > len(self.files) else ""


class _Cached(NamedTuple):
    key: Tuple[int, int, int]
    scanned_at: float
    mtime: float
    listing: Listing


_lock = threading.Lock()
_listings: Dict[str, _Cached] = {}
#: path -> ((mtime_ns, size), line count, the lines' texts or None).
_counts: Dict[str, Tuple[Tuple[int, int], Optional[int], Optional[Tuple[str, ...]]]] = {}

#: The wall clock compared with mtimes (RACY_S); tests replace it.
_now = time.time


def shown_name(name: str) -> str:
    """`name` as a message may carry it: valid UTF-8 (a lone surrogate from
    a hand-made request would break every later JSON answer), no control
    characters, at most _SHOWN_NAME_MAX characters."""
    text = str(name).encode("utf-8", "replace").decode("utf-8")
    text = "".join(c if c.isprintable() else "?" for c in text)
    if len(text) > _SHOWN_NAME_MAX:
        text = text[:_SHOWN_NAME_MAX] + "…"
    return text


def _listable_name(name: str) -> bool:
    """A name the list may carry: not hidden, a G-code extension, encodable
    as UTF-8 (os.scandir hands an undecodable byte name over with lone
    surrogates) and printable (no newline or escape in a button)."""
    if not name or name.startswith("."):
        return False
    if not name.lower().endswith(EXTENSIONS):
        return False
    try:
        name.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return name.isprintable()


def _inside(path: str, root_real: str) -> bool:
    """realpath(path) lies inside root_real (itself a realpath), not on it."""
    real = os.path.realpath(path)
    return real != root_real and os.path.commonpath([real, root_real]) == root_real


def _scan(directory: str) -> Listing:
    root_real = os.path.realpath(directory)
    found = []
    with os.scandir(directory) as entries:
        for entry in entries:
            if not _listable_name(entry.name):
                continue
            try:
                # is_file follows a symlink: directories, devices and broken
                # links are out. A link must lead inside the directory.
                if not entry.is_file():
                    continue
                if entry.is_symlink() and not _inside(entry.path, root_real):
                    continue
                st = entry.stat()
            except OSError:
                continue
            found.append(ProgramFile(entry.name, st.st_size, st.st_mtime))
    found.sort(key=lambda f: (-f.mtime, f.name))
    kept = tuple(found[:MAX_FILES])
    return Listing(directory, kept, len(found), "", frozenset(f.name for f in kept))


def list_programs(directory: Optional[str]) -> Listing:
    """The G-code files in `directory` (configmodel.MachineConfig.program_dir):
    regular files - a symlink only if it leads inside the directory - named
    *.ngc, *.nc or *.tap in any case, not hidden; size and mtime; newest
    first; at most MAX_FILES (the screen pages them, Listing.page). No
    subdirectory is entered.

    Cached by the directory's (device, inode, mtime): while those stay, a
    call costs one os.stat and no scan - unless the last scan came within
    RACY_S of the directory's mtime, when a change in the same mtime tick
    could have been missed, or forget() dropped the entry. Thread safe."""
    if not directory:
        return Listing(None, (), 0, NO_DIR_KEY)
    try:
        st = os.stat(directory)
    except FileNotFoundError:
        return Listing(directory, (), 0, DIR_MISSING.format(directory))
    except OSError as exc:
        return Listing(directory, (), 0, DIR_UNREADABLE.format(directory, exc.strerror or exc))
    if not stat.S_ISDIR(st.st_mode):
        return Listing(directory, (), 0, DIR_NOT_A_DIR.format(directory))
    key = (st.st_dev, st.st_ino, st.st_mtime_ns)
    with _lock:
        cached = _listings.get(directory)
        if cached is not None and cached.key == key and not (
                0 <= cached.scanned_at - cached.mtime < RACY_S):
            return cached.listing
        scanned_at = _now()
        try:
            listing = _scan(directory)
        except PermissionError:
            return Listing(directory, (), 0, DIR_UNREADABLE.format(directory, "izin yok"))
        except OSError as exc:
            return Listing(directory, (), 0, DIR_UNREADABLE.format(directory, exc.strerror or exc))
        _listings[directory] = _Cached(key, scanned_at, st.st_mtime, listing)
        return listing


def forget(directory: Optional[str]) -> None:
    """«Listeyi yenile»: the next list_programs(directory) scans again."""
    with _lock:
        _listings.pop(directory or "", None)


def program_path(directory: str, name: str) -> str:
    """The absolute path LinuxCNC gets for `name` listed in `directory`."""
    return os.path.join(directory, name)


def path_problem(directory: str, name: str) -> str:
    """Why LinuxCNC could not take the path for `name`, "" when it can."""
    size = len(program_path(directory, name).encode("utf-8"))
    return PATH_TOO_LONG.format(size, PATH_LIMIT_BYTES) if size > PATH_LIMIT_BYTES else ""


def resolve(directory: Optional[str], name: str) -> Optional[str]:
    """The absolute path to hand LinuxCNC for `name`, or None.

    `name` is a bare file name the server listed itself: no '/' or '\\',
    not starting with '.' (so neither '.' nor '..'), no NUL, and present
    in list_programs(directory) exactly - any of its files, on whatever
    page the screen shows it (Listing.names). Its realpath must lie inside the
    directory's realpath (a symlink swapped since the scan, leading out, is
    refused) and be a regular file. The browser's string is only compared
    against that list; the path is built here, from the INI's directory."""
    if not directory or not isinstance(name, str) or not name:
        return None
    if "/" in name or "\\" in name or "\0" in name or name.startswith("."):
        return None
    listing = list_programs(directory)
    if listing.error or name not in listing.names:
        return None
    path = program_path(directory, name)
    try:
        if not _inside(path, os.path.realpath(directory)) or not os.path.isfile(path):
            return None
    except (OSError, ValueError):
        return None
    return path


def listed_name(directory: Optional[str], path: Optional[str]) -> Optional[str]:
    """The name `path` (LinuxCNC's stat.file) has in `directory`, if it is
    a file directly in it as program_path spells it; else None."""
    if not directory or not path:
        return None
    head, tail = os.path.split(path)
    return tail if tail and os.path.normpath(head) == os.path.normpath(directory) else None


def line_count(path: Optional[str]) -> Optional[int]:
    """Lines in the file at `path`, as len(open(path).readlines()) counts
    them (axis.py's open_file_guts, :1209): newlines, plus a last line
    without one. Cached by (path, mtime, size): one os.stat per call, the
    file read once per version. None when it cannot be read, is not a
    regular file (stat.file can name anything AXIS opened) or is larger
    than MAX_COUNT_BYTES.

    A file up to MAX_TEXT_BYTES also keeps its lines' texts for line_text
    (plan WP09), from the same single read."""
    if not path:
        return None
    try:
        st = os.stat(path)
    except (OSError, ValueError):
        return None
    if not stat.S_ISREG(st.st_mode):
        return None    # a FIFO or device would block the poll forever
    key = (st.st_mtime_ns, st.st_size)
    with _lock:
        cached = _counts.get(path)
        if cached is not None and cached[0] == key:
            return cached[1]
    count: Optional[int] = None
    texts: Optional[Tuple[str, ...]] = None
    if st.st_size <= MAX_COUNT_BYTES:
        try:
            count = 0
            last = b"\n"
            keep = st.st_size <= MAX_TEXT_BYTES
            parts = []
            with open(path, "rb") as fh:
                for chunk in iter(lambda: fh.read(1 << 20), b""):
                    count += chunk.count(b"\n")
                    last = chunk[-1:]
                    if keep:
                        parts.append(chunk)
            if last != b"\n":
                count += 1
            if keep:
                texts = tuple(
                    line.decode("utf-8", "replace").strip()[:LINE_TEXT_MAX]
                    for line in b"".join(parts).splitlines()
                )
        except OSError:
            count, texts = None, None
    with _lock:
        if len(_counts) > 16:
            _counts.clear()
        _counts[path] = (key, count, texts)
    return count


def line_text(path: Optional[str], line: Optional[int]) -> Optional[str]:
    """The text of line `line` (1-based, as stat.motion_line numbers the
    file's lines) of the file line_count last read at `path`, stripped and
    cut to LINE_TEXT_MAX; None when it was not kept (a larger file, an
    unreadable one, line_count not called yet) or the line is not in it.
    Reads nothing and stats nothing: call it after line_count(path), which
    checks the cached version, in the same render."""
    if not path or not line or line < 1:
        return None
    with _lock:
        cached = _counts.get(path)
    if cached is None or cached[2] is None or line > len(cached[2]):
        return None
    return cached[2][line - 1]


def progress(line: Optional[int], lines: Optional[int]) -> float:
    """The share of the file motion has reached, 0.0 to 1.0: `line`
    (stat.motion_line) over `lines` (line_count). 0.0 when either is
    unknown. A line past the count (a file changed since it was counted)
    is capped at 1.0. Motion's line runs behind the interpreter's
    (emctask.cc:705-707), and a program with loops or subroutines visits
    lines out of order: an indication, not a measure of time left."""
    if not line or not lines or line < 0 or lines <= 0:
        return 0.0
    return min(1.0, float(line) / float(lines))


def load_block(state: MachineState) -> str:
    """Why no program may be loaded now, "" when one may. Both adapters
    refuse by it and the Program screen's buttons follow it. In order:

      - the status unknown, or whether a program or MDI line runs not read;
      - a program paused - AXIS offers no open then either (its open
        commands follow running(), axis.py:962-965);
      - a program or MDI line running: leaving MDI aborts the line, and
        task refuses to leave AUTO with the interpreter busy
        (emctaskmain.cc:2125-2171);
      - a joint homing, or the machine moving (MachineState.moving): the
        load would not stop motion, but task answers nothing DONE while
        motion is busy (emctaskmain.cc:3546-3561), so the load's bounded
        waits could only run out.

    The machine may be off: task takes SET_MODE, PLAN_SYNCH and PLAN_OPEN
    out of ON too (emctaskmain.cc:807-844), as AXIS does."""
    if not state.status_known:
        return LOAD_UNKNOWN
    if state.program_paused is None or state.mdi_running is None:
        return LOAD_UNREAD
    if state.program_paused:
        return LOAD_PAUSED
    if state.program_busy or state.mdi_running:
        return LOAD_RUNNING
    if state.homing.any_homing:
        return LOAD_HOMING
    if state.moving is None:
        return LOAD_MOVING_UNREAD
    if state.moving:
        return LOAD_MOVING
    return ""


# -- running a program (plan WP09) ---------------------------------------------


def run_unread(state: MachineState) -> str:
    """RUN_UNKNOWN or RUN_UNREAD when what runs cannot be said; "" when it
    can: the status known and the task mode, the interpreter, the pause
    and the MDI flag read."""
    if not state.status_known:
        return RUN_UNKNOWN
    if (state.task_mode is None or state.interp_state is None
            or state.program_paused is None or state.mdi_running is None):
        return RUN_UNREAD
    return ""


def start_busy(state: MachineState) -> str:
    """Why «Başlat» may not go because something already runs, "" when
    nothing does: a pause - a program's (AXIS's task_pause, an M0/M1) or an
    MDI line's - says «Devam» (START_PAUSED); a program or an MDI line
    running. LinuxCNC would drop a second PLAN_RUN with the interpreter
    busy anyway (emctaskmain.cc:1120-1182: not listed while READING)."""
    unread = run_unread(state)
    if unread:
        return unread
    if state.pause_left_at_end:
        return PAUSED_AT_END
    if state.program_paused:
        return START_PAUSED
    if state.mdi_running:
        return START_MDI
    if state.program_busy:
        return START_RUNNING
    return ""


def pause_refusal(state: MachineState) -> str:
    """Why «Duraklat» may not go now, "" when it may: a program in AUTO, or
    an MDI line, with the interpreter READING or WAITING and not paused
    already. axis.py's task_pause sends AUTO_PAUSE from AUTO with the
    interpreter READING or WAITING (:2368-2372); its task_pauseresume from
    MDI too (:2399-2409); task takes PLAN_PAUSE in both (emctaskmain.cc:
    1147, :1306, :1402)."""
    unread = run_unread(state)
    if unread:
        return unread
    if state.pause_left_at_end:
        return PAUSED_AT_END
    if state.program_paused:
        return PAUSE_ALREADY
    if state.task_mode in ("auto", "mdi") and state.interp_state in ("reading", "waiting"):
        return ""
    return PAUSE_IDLE


def resume_refusal(state: MachineState) -> str:
    """Why «Devam» may not go now, "" when it may: something paused - motion
    paused or the interpreter PAUSED - with the task in AUTO or MDI, as
    axis.py's task_resume requires (:2390-2397; it does not switch mode
    then: ensure_mode(AUTO, MDI) leaves either as it is). A pause with the
    task in MANUAL has nothing to resume (PAUSED_IN_MANUAL); switching to
    AUTO would abort anyway (emcTaskSetMode, emctask.cc:293-296)."""
    unread = run_unread(state)
    if unread:
        return unread
    if not state.program_paused:
        return RESUME_NOT_PAUSED
    if state.pause_left_at_end:
        # Task would put the interpreter back to READING past the file's
        # end (emctaskmain.cc:2344-2352): an error, nothing to run.
        return PAUSED_AT_END
    if state.task_mode not in ("auto", "mdi"):
        return PAUSED_IN_MANUAL
    return ""


def step_refusal(state: MachineState) -> str:
    """Why «Adım» may not go now for what runs, "" when it may: nothing
    running (the step starts the program paused at its start, emctaskmain.cc:
    1078-1091), or a program paused in AUTO (one line more, :1251-1261).
    Not while a program runs unpaused (pause first: STEP_RUNNING), nor for
    an MDI line (task takes no PLAN_STEP in MDI, :1360-1468, and switching
    to AUTO would abort the line), nor a pause in MANUAL. The machine's own
    gates (homed, ON, the link, a file) are the caller's: start_blocks."""
    unread = run_unread(state)
    if unread:
        return unread
    if state.mdi_running:
        return STEP_MDI
    if state.pause_left_at_end:
        # The interpreter idle: task would run the program again from its
        # start (emctaskmain.cc:1078-1091).
        return PAUSED_AT_END
    if state.program_paused:
        if state.task_mode == "auto":
            return ""
        return STEP_MDI if state.task_mode == "mdi" else PAUSED_IN_MANUAL
    if state.program_busy:
        return STEP_RUNNING
    return ""
