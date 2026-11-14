"""HTTP server. stdlib only, so it runs on the live USB with nothing installed.

    python3 -m bettercnc.app --profile test-rig --port 8080
    python3 -m bettercnc.app --profile test-rig --adapter linuxcnc
    python3 -m bettercnc.app --adapter linuxcnc --observe --ini FILE   # beside AXIS

Routes:
    GET  /                 full page
    GET  /api/render       re-render one screen (the 1 Hz poll)
    POST /api/action       an intent, then re-render
    GET  /api/state        the raw view model as JSON, for debugging

The server holds a MachineAdapter: MockAdapter by default, LinuxCncAdapter with
`--adapter linuxcnc` (on the machine, or in sim/). Nothing else in the stack
knows which it is; the startup banner says which, from the adapter itself.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Dict, NamedTuple, Optional, Tuple
from urllib.parse import parse_qs, urlparse

from . import programs
from . import render as R
from . import viewmodel as VM
from .adapter import LinuxCncAdapter, LinuxCncUnavailable, MachineAdapter, MockAdapter
from .configmodel import MachineConfig, load_machine_config
from .profiles import MachineProfile, load_profile, validate_profile

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

#: Largest POST /api/action body accepted. app.js sends one small JSON object
#: ({action, value, screen}, its post() function), so anything near this is
#: not the UI.
MAX_ACTION_BODY = 64 * 1024

#: Socket timeout for every request connection, set as Handler.timeout.
#: socketserver.StreamRequestHandler.setup puts it on the accepted socket, and
#: BaseHTTPRequestHandler.handle_one_request answers a read or write that runs
#: out of time by closing the connection (CPython Lib/socketserver.py,
#: Lib/http/server.py). Without it, a client that declares a Content-Length
#: and then stalls parks its handler thread until it hangs up.
#:
#: Why 10 s (chosen, not measured on the rig): app.js sends a few hundred
#: bytes per request, so a working client never pauses this long between
#: reads; and since Python 3.5 the timeout bounds a whole sendall(), so the
#: largest response - the full page from GET /, about 31 KB for test-rig
#: `makine` as `--dump` wrote it on 2026-10-18 - must go out at 3.1 KB/s or
#: better. A client that cannot manage that is ten 1 Hz polls behind anyway.
#:
#: What it does not bound: the timeout is per socket operation, so a client
#: that trickles one byte at a time, each inside the timeout, still holds its
#: thread for as long as it keeps trickling. No timeout applies to
#: Session.act or Session.view; they are not socket operations.
REQUEST_TIMEOUT_S = 10.0

#: Kiosk supervision (--exit-if-linuxcnc-lost): how often LinuxCNC's
#: liveness is checked. One check costs a stat poll and a /proc listing.
SUPERVISE_EVERY_S = 1.0

#: main()'s exit status when supervision ended the UI. LinuxCNC's display
#: program exiting is what makes LinuxCNC shut down (deploy/bettercnc-display).
EXIT_LINUXCNC_LOST = 3

#: Screen keys the routes accept, from VM.SCREENS.
_SCREEN_IDS = frozenset(s[0] for s in VM.SCREENS)

#: Actions that can start motion. One that is still waiting for the session
#: lock when DURDUR or the software estop arrives is dropped (Session.act).
#: «Limitten kurtar» (recover_limit, plan WP05) switches the machine on and
#: jogs: one of them. So are one axis's homing (home_joint) and «Evet» on
#: the confirmation band (confirm), which runs the homing it asked about
#: (plan WP07). Plan WP09: «Devam» (program_resume) and «Adım»
#: (program_step) set a program moving again; «Duraklat» (program_pause)
#: stops motion and is not one.
MOTION_ACTIONS = frozenset((
    "home_all", "home_joint", "jog", "mdi", "mdi_preset", "program_start", "wcs",
    "recover_limit", "confirm", "program_resume", "program_step",
))
#: Everything Session.act drops that way: the motion actions, and «Yazılım
#: sıfırla», which switches the machine back on - queued before an estop, it
#: would run right after it and undo it (review of plan WP03).
DROPPED_AFTER_A_STOP = MOTION_ACTIONS | frozenset(("software_reset",))

#: How long the confirmation band (Session.request_confirm, plan WP07)
#: waits for «Evet». Past it the question is gone and «Evet» sends nothing:
#: an answer must belong to the situation the question described. Long
#: enough to read the question and look at the machine.
#: Chosen, not measured.
CONFIRM_TIMEOUT_S = 15.0

#: How long the band says that an «Evet» came for a question that was gone
#: (timed out, a stop since, another action first). Chosen, not measured.
CONFIRM_REFUSED_S = 5.0

#: How long after its question «Evet» starts to count (Session._answer):
#: until then the band shows it off, and an «Evet» that comes anyway sends
#: nothing and leaves the question standing. Otherwise a double click or a
#: double tap on the button that asked answers it: the band opens at the
#: top of the block that stays in view, over whatever stood under it - on a
#: narrow screen (one column, under about 840 px) scrolled down to the
#: homing card, the homing buttons themselves (review of plan WP07, browser
#: at 800x600: a second tap 0.25 s after «Tümünü referansla» landed on
#: «Evet» and re-homed every joint). Longer than a double click, short
#: beside reading the question. Chosen, not measured.
CONFIRM_MIN_S = 1.0


class Confirmation(NamedTuple):
    """A question Session asks before an action (plan WP07): the adapter
    returned it instead of acting (MachineAdapter.home_all, home_joint),
    and «Evet» carrying `token` runs `action` with `value`, confirmed,
    from CONFIRM_MIN_S after `asked` until `deadline` (Session's clock) -
    unless a stop or the software estop came since it was asked (`stops`:
    Session._stops then) or any other action came first."""

    token: str
    action: str
    value: str
    text: str
    deadline: float
    stops: int
    asked: float


#: The highest Program list page a request may name; programs.MAX_FILES /
#: PAGE_SIZE pages exist at most, and the view clamps anyway.
_MAX_PAGE = 10000


def _valid_page(raw: Any) -> int:
    """The Program list's page from a request ("page": the browser's own
    view state, P0 cleanup): a whole number 0.._MAX_PAGE, else 0. Never an
    action: it sends nothing and the view clamps it into the list."""
    try:
        page = int(str(raw).strip()) if raw is not None else 0
    except ValueError:
        return 0
    return page if 0 <= page <= _MAX_PAGE else 0


def _valid_screen(name: str) -> str:
    """`name` if it is a screen key, else "kontrol". Every route goes through
    this, so the JSON and the page never carry a screen the UI does not have
    (VM.build would echo any string into "screen", the nav and data-screen)."""
    return name if name in _SCREEN_IDS else "kontrol"


def _read_static(name: str) -> str:
    with open(os.path.join(STATIC_DIR, name), "r", encoding="utf-8") as fh:
        return fh.read()


class Session:
    """One machine, one adapter, one config.

    Every adapter call runs under self._lock, one at a time, except
    software_estop and DURDUR's first half (stop_motion) - see act().

    `observe` (--observe) is the read-only mode for running beside AXIS,
    which then runs the machine: act() passes the software estop alone (and
    the error box's local «Tamam»), and every screen says so and shows its
    other controls disabled (VM.build with observe). The adapter gets the
    flag too (build_session): the real one then leaves LinuxCNC's error
    messages to AXIS and refuses actions on its own as well, while its
    link-fault estop stays.

    The confirmation band (plan WP07) is this session's own state, never
    the adapter's: one question at a time (request_confirm), shown on every
    screen until «Evet», «Vazgeç», any other action, a stop or
    CONFIRM_TIMEOUT_S. One kiosk and one operator are assumed; the time
    limit keeps a band left standing from being answered later.
    """

    def __init__(
        self,
        adapter: MachineAdapter,
        profile: MachineProfile,
        config: MachineConfig,
        clock: Callable[[], float] = time.monotonic,
        observe: bool = False,
    ):
        self.adapter = adapter
        self.profile = profile
        self.config = config
        self.observe = observe
        self._lock = threading.Lock()
        self._clock = clock
        self._last_client: Optional[float] = None
        #: DURDUR and software-estop presses so far (act). Written under
        #: _stops_lock, held for the increment alone; read without it.
        self._stops = 0
        self._stops_lock = threading.Lock()
        #: The question standing (request_confirm), and until when the band
        #: says an «Evet» came too late; both under _lock.
        self._confirm: Optional[Confirmation] = None
        self._confirm_refused_until: Optional[float] = None

    def touch(self) -> None:
        """A browser asked for something. /healthz does not count: the
        kiosk's browser watch (bettercnc.browserwatch) is what asks it."""
        self._last_client = self._clock()

    def client_age_s(self) -> Optional[float]:
        """Seconds since a browser last asked, None if none ever did."""
        if self._last_client is None:
            return None
        return round(self._clock() - self._last_client, 1)

    def linuxcnc_lost(self) -> Optional[str]:
        """The adapter's linuxcnc_lost(), under the session lock; None for
        an adapter with nothing to lose (MockAdapter)."""
        check = getattr(self.adapter, "linuxcnc_lost", None)
        if check is None:
            return None
        with self._lock:
            return check()

    def view(self, screen: str, program_page: int = 0) -> Dict[str, Any]:
        with self._lock:
            state = self.adapter.snapshot()
            confirm = self._confirm_view()
        return VM.build(self.profile, self.config, state, screen=screen, observe=self.observe,
                        confirm=confirm, program_page=program_page)

    def view_unless_busy(self, screen: str, program_page: int = 0) -> Optional[Dict[str, Any]]:
        """view(), or None at once when an action holds the lock: the
        software estop's answer (do_POST) must not wait for a bounded action
        in progress - the estop itself went out without the lock (act). The
        page then keeps its band ('ACİL STOP GÖNDERİLDİ — onay bekleniyor')
        until the next render shows the machine (P0 cleanup)."""
        if not self._lock.acquire(blocking=False):
            return None
        try:
            state = self.adapter.snapshot()
            confirm = self._confirm_view()
        finally:
            self._lock.release()
        return VM.build(self.profile, self.config, state, screen=screen, observe=self.observe,
                        confirm=confirm, program_page=program_page)

    def request_confirm(self, action: str, value: str, text: str,
                        stops: Optional[int] = None) -> str:
        """Ask `text` before running `action` with `value` (plan WP07), in
        place of any question standing; its token, which «Evet» must carry
        (render.render_confirm: data-action="confirm", data-value=<token>).
        `stops`: the stop count the asking action began with, so a stop
        that came while it ran leaves no question behind. Under _lock."""
        token = secrets.token_hex(8)
        now = self._clock()
        self._confirm = Confirmation(
            token, action, value, text, now + CONFIRM_TIMEOUT_S,
            self._stops if stops is None else stops, now,
        )
        return token

    def _live_confirm(self, pending: Optional[Confirmation]) -> bool:
        """`pending` may still be answered: asked, no stop or estop since,
        not timed out."""
        return (pending is not None and pending.stops == self._stops
                and self._clock() < pending.deadline)

    def _confirm_ready(self, pending: Confirmation) -> bool:
        """«Evet» counts for `pending` by now: CONFIRM_MIN_S since it was
        asked."""
        return self._clock() >= pending.asked + CONFIRM_MIN_S

    def _confirm_view(self) -> Optional[Dict[str, Any]]:
        """VM.build's `confirm`: the question standing ({token, text,
        action, value, timeout_s, ready, wait_s, ready_in_s} - `ready` False
        for the first CONFIRM_MIN_S, while «Evet» does not count yet, and
        `ready_in_s` the time left until it does), or for
        CONFIRM_REFUSED_S after an «Evet» that came for none,
        {"refused": True}; None for neither. Under _lock."""
        pending = self._confirm
        if self._live_confirm(pending):
            # ready_in_s: how long until «Evet» counts, for app.js to re-render
            # right then instead of at the next 1 Hz poll (P0 cleanup).
            return {"token": pending.token, "text": pending.text, "action": pending.action,
                    "value": pending.value, "timeout_s": CONFIRM_TIMEOUT_S,
                    "ready": self._confirm_ready(pending), "wait_s": CONFIRM_MIN_S,
                    "ready_in_s": max(0.0, pending.asked + CONFIRM_MIN_S - self._clock())}
        until = self._confirm_refused_until
        if until is not None and self._clock() < until:
            return {"refused": True}
        return None

    def _ask(self, question: Optional[str], action: str, value: str, stops: int) -> None:
        """An adapter's answer to a first press: a question to ask first
        (MachineAdapter.home_all, home_joint) or None, when it acted."""
        if question:
            self.request_confirm(action, value, question, stops)

    def _answer(self, pending: Optional[Confirmation], token: str) -> None:
        """«Evet» with `token`: run the action `pending` asked about,
        confirmed - the adapter checks every rule again on a fresh poll.
        For a question that is gone, nothing is sent and the band says so
        for a moment (CONFIRM_REFUSED_S). Within CONFIRM_MIN_S of the
        question it is no answer yet - the press that asked, pressed twice:
        nothing is sent and the question stands. Under _lock."""
        # surrogatepass: a JSON body can carry a lone surrogate ("\ud800"),
        # which plain UTF-8 refuses to encode - the request must not die.
        if not (self._live_confirm(pending) and token and secrets.compare_digest(
                token.encode("utf-8", "surrogatepass"), pending.token.encode("utf-8"))):
            self._confirm_refused_until = self._clock() + CONFIRM_REFUSED_S
            return
        if not self._confirm_ready(pending):
            self._confirm = pending
            return
        a = self.adapter
        try:
            if pending.action == "home_all":
                a.home_all(confirmed=True)
            elif pending.action == "home_joint":
                a.home_joint(pending.value, confirmed=True)
        except (ValueError, KeyError):
            pass  # an axis the machine lacks

    def act(self, action: str, value: str) -> None:
        """Apply an intent. Unknown actions are ignored rather than raising:
        a stale browser must not be able to 500 the controller UI.

        software_estop does NOT take self._lock. On the test rig the GUI
        button is the only estop: no hardware estop input is wired
        (configs/test-rig/test-rig.hal, section "ESTOP CHAIN - GUI ONLY.
        THERE IS NO HARDWARE ESTOP INPUT."), so it must never queue behind
        another action. The price is that adapter.software_estop() must be
        safe to call while any other adapter method is running in another
        thread (LinuxCncAdapter's class docstring, "Threading").

        DURDUR ("stop", the stop rail and Esc; "program_stop", the Program
        screen's «Durdur») takes the same road for its first half:
        adapter.stop_motion() aborts at once, without the lock, under the
        same contract. Its second half, adapter.stop_followup(), runs under
        the lock, so right after the action that held it returns: a second
        abort for whatever that action sent just before the first one, and
        the operator's message. Observer mode drops both, like every action
        but the estop.

        Nothing that began before a stop moves after it. An adapter action
        in progress takes the adapter's own stop count as it begins and
        sends no motion command once it changed
        (LinuxCncAdapter._stopped_since). A motion action (MOTION_ACTIONS)
        still waiting for the lock is dropped here, unseen by the adapter,
        if a stop or an estop came while it waited: it must not run after
        the stop's second half, nor after an estop and the reset that
        followed it. So is a waiting «Yazılım sıfırla» (DROPPED_AFTER_A_STOP):
        it would switch the machine back on right behind the estop. The
        stop's own message stands.

        How long an action in progress can still hold an estop (or the
        stop's first abort) up, on LinuxCncAdapter. Read from the LinuxCNC
        source, not measured:

        * Every command.wait_complete() it makes is bounded by
          adapter.WAIT_TIMEOUT_S, and homing is never waited for: home_all
          returns once home(-1) is sent.
        * Every command send blocks too: the binding's emcSendCommand spins
          until task echoes the command (emcmodule.cc:233-250), and task
          echoes it after running it (emctaskmain.cc:3540-3544). Task picks
          it up on its next cycle, up to one [TASK]CYCLE_TIME away (its loop
          waits on a timer, emctaskmain.cc:3596; 0.010 on the rig). For
          state() - estop, reset, ON - running it adds up to about one
          [EMCIO]CYCLE_TIME (0.100 on the rig; both in
          configs/test-rig/test-rig.ini): emcTaskSetState hands iocontrol a
          command that waits for iocontrol to finish the one before it
          (emctask.cc:337 ON, :342-343 reset, :355-360 estop;
          taskclass.cc:176-189), and iocontrol sleeps one cycle after each
          command (ioControl.cc:1138). If task or iocontrol stalls, a send
          gives up only after EMC_COMMAND_TIMEOUT = 5 s (emcmodule.cc:209,
          :240).
        * Both keep the GIL: neither releases it (the module's only
          Py_BEGIN_ALLOW_THREADS is in logger.start, emcmodule.cc:2125).
          While an action sits in one, no Python thread runs, the HTTP thread
          carrying this estop included. Skipping the lock removes the queue;
          it cannot preempt a call holding the GIL.

        So in normal operation an estop that arrives mid-action is delayed by
        at most one bounded wait plus one command echo; with task or
        iocontrol stalled, by up to 5 s per send.

        The estop's HTTP response does not wait for the action in progress
        either (P0 cleanup): do_POST renders it through view_unless_busy,
        which does not wait for self._lock - with an action holding it the
        answer carries no page ("html": null), the rail's band keeps saying
        the estop went, and the next poll (which does wait) shows the
        machine. Until then the answer queued behind the action, all its
        bounded waits and sends included, and with task stalled could time
        out in app.js as "Komut gönderilemedi" although the estop had gone.

        Programs (plan WP08). «Yükle» on a listed file ("load_program", the
        file's name as the value) is looked up in the server's own list of
        the PROGRAM_PREFIX directory (programs.resolve, from self.config's
        program_dir): only a bare name listed there gives a path, the
        absolute one built from the INI's directory, and only that path
        reaches adapter.load_program. Anything else - a path, '..', a
        hidden or unlisted name, a symlink leading out - reaches no command:
        adapter.refuse says "Program listede yok: <ad>" on the message line.
        «Yeniden yükle» ("reload_program", no value) takes the loaded
        file's name from a fresh snapshot (stat.file) and goes the same
        way, so a file loaded from outside the directory is refused too.
        «Listeyi yenile» ("refresh_programs") drops the list's cache
        (programs.forget) and reaches no adapter. A load starts no motion:
        it is not in MOTION_ACTIONS.

        Running a program (plan WP09): «Başlat» ("program_start"),
        «Duraklat» ("program_pause"), «Devam» ("program_resume") and «Adım»
        ("program_step") go to the adapter, which refuses what LinuxCNC
        would not take (programs.start_busy, pause_refusal, resume_refusal,
        step_refusal). «Devam» and «Adım» set a program moving: they are
        motion actions, dropped after a stop like the rest. «Duraklat» runs
        under the lock like any action; DURDUR stays the lockless stop.

        «Tamam» on the error box ("ack_error", the error's id as the value)
        is local: adapter.ack_error() forgets the error box
        (MachineState.last_error) if it is still that error, and sends
        nothing to LinuxCNC and reads nothing from it. So it is handled
        BEFORE observer mode's drop and is the one action besides the
        estop that an observer's screen offers and the adapter sees; it
        runs under the lock like every other action.

        Observer mode (self.observe): every action after the estop and the
        acknowledgement is dropped unseen by the adapter, as an unknown one
        is - the screens offer none of them then, so only a stale or
        hand-made request gets here. No question is ever asked then.

        The confirmation band (plan WP07). «Tümünü referansla» ("home_all")
        and one axis's button ("home_joint", the axis letter as the value)
        go to the adapter, which checks its rules first: a refusal is said
        on the message line, and where a homing again - or one axis while
        the tool axis is unhomed - must be asked about, the adapter sends
        nothing and returns the question (motionrules.home_question).
        request_confirm keeps it with a token; the band shows it with
        «Evet» ("confirm", the token as the value) and «Vazgeç»
        ("cancel_confirm"). «Evet» runs that action with confirmed=True -
        the adapter checks every rule again on a fresh poll - only while the
        question stands: before CONFIRM_TIMEOUT_S, with no stop or estop
        since it was asked, and with no other action in between - every
        action under the lock ends the question standing, so an answer
        always belongs to the question on the screen. A refused «Evet»
        sends nothing and says so (CONFIRM_REFUSED_S). An «Evet» with the
        right token within CONFIRM_MIN_S of the question is no answer yet:
        nothing is sent and the question stands (the band shows «Evet» off
        until then) - a double tap on the button that asked must not answer
        it. «Evet» is a motion action (MOTION_ACTIONS): queued behind a busy
        action when a stop came, it is dropped like the rest.
        """
        a = self.adapter
        if action == "software_estop":
            self._note_stop()
            a.software_estop()
            return
        if action == "ack_error":
            with self._lock:
                a.ack_error(value)
            return
        if self.observe:
            return
        if action in ("stop", "program_stop"):
            self._note_stop()
            a.stop_motion()
            with self._lock:
                a.stop_followup()
            return
        stops = self._stops
        with self._lock:
            if action in DROPPED_AFTER_A_STOP and self._stops != stops:
                return  # overtaken by a stop or an estop while it waited
            # Every action ends the question standing: «Evet» answers the
            # band on the screen, right after it, and nothing else.
            pending, self._confirm = self._confirm, None
            self._confirm_refused_until = None
            if action == "software_reset":
                a.software_estop_reset()
            elif action == "home_all":
                self._ask(a.home_all(), action, value, stops)
            elif action == "home_joint":
                # The axis letter (render.py render_homing); both adapters
                # raise KeyError for an axis they lack.
                try:
                    self._ask(a.home_joint(value), action, value, stops)
                except (ValueError, KeyError):
                    pass  # an axis the machine lacks
            elif action == "confirm":
                self._answer(pending, value)
            elif action == "cancel_confirm":
                pass  # «Vazgeç»: the question went above
            elif action == "jog":
                # Wire format "<axis>:<1|-1>" (render.py render_jog). Any
                # other number is dropped here; both adapters raise
                # ValueError for it, and KeyError for an axis they lack.
                axis, _, raw_direction = value.partition(":")
                try:
                    direction = int(raw_direction)
                except ValueError:
                    direction = 0
                if axis and direction in (1, -1):
                    try:
                        a.jog(axis, direction)
                    except (ValueError, KeyError):
                        pass  # an axis the machine lacks
            elif action in ("mdi", "mdi_preset"):
                a.send_mdi(value)
            elif action == "program_start":
                a.program_start()
            elif action == "program_pause":
                a.program_pause()
            elif action == "program_resume":
                a.program_resume()
            elif action == "program_step":
                a.program_step()
            elif action == "load_program":
                self._load(value)
            elif action == "reload_program":
                self._reload()
            elif action == "refresh_programs":
                programs.forget(self.config.program_dir)
            elif action == "recover_limit":
                # «Limitten kurtar»: override, ON, one jog off the switch
                # (MachineAdapter.recover_limit).
                a.recover_limit()
            elif action == "jog_step" and hasattr(a, "set_jog_step"):
                try:
                    a.set_jog_step(float(value))
                except ValueError:
                    pass
            elif action == "feed" and hasattr(a, "set_feed_override"):
                try:
                    a.set_feed_override(int(value))
                except ValueError:
                    pass
            elif action == "wcs" and hasattr(a, "set_wcs"):
                a.set_wcs(value)
            # Deliberately absent: any action that could clear a hardware estop.

    def _load(self, name: str) -> None:
        """load_program (act): `name` must be one the server lists from its
        program directory; only the path programs.resolve builds reaches
        the adapter. The loaded file's line count is taken here, once, so
        no poll reads it. Under _lock."""
        path = programs.resolve(self.config.program_dir, name)
        if path is None:
            self.adapter.refuse(programs.NOT_LISTED.format(programs.shown_name(name)))
            return
        self.adapter.load_program(path)
        programs.line_count(path)

    def _reload(self) -> None:
        """reload_program (act): the loaded file (stat.file, from a fresh
        snapshot) by its name in the program directory, through _load like
        any name; a file loaded from elsewhere is refused. Under _lock."""
        loaded = self.adapter.snapshot().program_file
        if not loaded:
            self.adapter.refuse(programs.NOTHING_LOADED)
            return
        name = programs.listed_name(self.config.program_dir, loaded)
        if name is None:
            self.adapter.refuse(programs.LOADED_OUTSIDE.format(programs.shown_name(loaded)))
            return
        self._load(name)

    def _note_stop(self) -> None:
        """A stop or the software estop arrived: motion actions waiting for
        the lock now are dropped (act)."""
        with self._stops_lock:
            self._stops += 1


#: --adapter choices. "mock" is the default: a UI started without saying
#: otherwise never sends a command to LinuxCNC.
ADAPTERS = ("mock", "linuxcnc")


def build_session(
    profile_key: str = "test-rig",
    adapter_kind: str = "mock",
    ini_path: Optional[str] = None,
    observe: bool = False,
) -> Session:
    """`ini_path` replaces the profile's INI: the kiosk passes the INI
    LinuxCNC was started with (deploy/bettercnc-display), so the UI never
    describes one machine while LinuxCNC drives another. The profile is
    built from it (load_profile), not merely pointed at it: its SOFT_LIMITS,
    travel and switch facts come from the INI too.

    `observe`: the read-only mode beside AXIS (Session). The mock takes it
    as well - it sends nothing anyway - so the observer screens can be
    looked at off the machine (`--dump kontrol --observe`)."""
    profile = load_profile(profile_key, ini_path)
    validate_profile(profile)
    config = load_machine_config(profile)
    adapter: MachineAdapter
    if adapter_kind == "mock":
        adapter = MockAdapter(profile)
    elif adapter_kind == "linuxcnc":
        adapter = LinuxCncAdapter(profile, observe=observe)
    else:
        raise ValueError(
            "unknown adapter {!r} (valid: {})".format(adapter_kind, ", ".join(ADAPTERS))
        )
    return Session(adapter, profile, config, observe=observe)


def adapter_banner(adapter: MachineAdapter) -> str:
    """The banner line naming the adapter, decided from the adapter instance
    rather than from the flag, so it cannot claim "not connected" while
    commands reach LinuxCNC."""
    if isinstance(adapter, MockAdapter):
        return "  (mock adapter — makineye bağlı DEĞİL)\n"
    return "  ({} — komutlar LinuxCNC'ye GİDİYOR)\n".format(type(adapter).__name__)


#: serve()'s extra banner line in observer mode (--observe).
OBSERVE_BANNER = (
    "  SALT İZLEME — makine AXIS'ten kullanılıyor: yalnız acil stop gönderilir, "
    "LinuxCNC'nin hata mesajları AXIS'te kalır\n"
)


def _wire_text(raw: Any) -> str:
    """A field of a posted action as text that encodes as UTF-8.

    json.loads turns a "\\ud800" escape into a lone surrogate, which no
    UTF-8 encoder takes: kept in the MDI history or a message, it made every
    later answer raise UnicodeEncodeError, and the screen stayed covered
    until the UI restarted (review of plan WP07; final P0 review). A lone
    surrogate becomes U+FFFD here, once, before Session.act sees the value;
    valid text is unchanged (a valid pair is one character after
    json.loads)."""
    text = str(raw or "")
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        text = text.encode("utf-16", "surrogatepass").decode("utf-16", "replace")
    return text


class Handler(BaseHTTPRequestHandler):
    session: Session = None  # type: ignore[assignment]
    #: Kiosk mode (--boot-url): handed to app.js through the page, which goes
    #: there when the UI stops answering (deploy/README.md).
    boot_url: Optional[str] = None
    server_version = "better-cnc-ui/0.1"
    timeout = REQUEST_TIMEOUT_S  # see REQUEST_TIMEOUT_S

    # -- helpers -------------------------------------------------------------

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, payload: Dict[str, Any]) -> None:
        self._send(
            200,
            # "replace": a lone surrogate that got into the state anyway must
            # cost one character, not every later answer (_wire_text).
            json.dumps(payload, ensure_ascii=False).encode("utf-8", "replace"),
            "application/json; charset=utf-8",
        )

    def _screen_from_query(self, query: str) -> str:
        return _valid_screen(parse_qs(query).get("screen", ["kontrol"])[0])

    def _page_from_query(self, query: str) -> int:
        return _valid_page(parse_qs(query).get("page", ["0"])[0])

    def log_message(self, fmt: str, *args: Any) -> None:  # pragma: no cover
        pass  # the controller console is for the machine, not access logs

    # -- routes --------------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path

        if path in ("/", "/api/render", "/api/state"):
            self.session.touch()

        if path == "/":
            vm = self.session.view(self._screen_from_query(parsed.query),
                                   self._page_from_query(parsed.query))
            html = R.render_page(
                vm, _read_static("app.css"), _read_static("app.js"), boot_url=self.boot_url
            )
            self._send(200, html.encode("utf-8", "replace"), "text/html; charset=utf-8")
            return

        if path == "/api/render":
            vm = self.session.view(self._screen_from_query(parsed.query),
                                   self._page_from_query(parsed.query))
            self._json({"html": R.render_body(vm), "screen": vm["screen"]})
            return

        if path == "/api/state":
            vm = self.session.view(self._screen_from_query(parsed.query),
                                   self._page_from_query(parsed.query))
            self._json(vm)
            return

        if path == "/healthz":
            self._json({"ok": True, "client_age_s": self.session.client_age_s()})
            return

        self._send(404, b"not found", "text/plain; charset=utf-8")

    def do_POST(self) -> None:  # noqa: N802
        if urlparse(self.path).path != "/api/action":
            self._send(404, b"not found", "text/plain; charset=utf-8")
            return
        self.session.touch()
        # A bad length is refused before anything is read: int() would raise
        # on "abc", rfile.read(-1) would block until the client hangs up, and
        # any other negative raises ValueError (all three measured against
        # this handler with a raw socket). The body is left unread, so the
        # connection must not be reused.
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        if length < 0:
            self.close_connection = True
            self._send(400, b"bad Content-Length", "text/plain; charset=utf-8")
            return
        if length > MAX_ACTION_BODY:
            self.close_connection = True
            self._send(413, b"request body too large", "text/plain; charset=utf-8")
            return
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8") or "{}")
        except ValueError:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}  # valid JSON but not an object, e.g. [] or null
        action = _wire_text(payload.get("action"))
        value = _wire_text(payload.get("value"))
        screen = _valid_screen(_wire_text(payload.get("screen")) or "kontrol")
        page = _valid_page(payload.get("page"))

        self.session.act(action, value)
        if action == "software_estop":
            # The estop went out lockless (Session.act); its answer does not
            # queue behind an action holding the lock either. "html": null
            # leaves the page as it is (app.js swap), the rail's band saying
            # the estop went until the next poll renders the machine.
            vm = self.session.view_unless_busy(screen, page)
            if vm is None:
                self._json({"html": None, "screen": screen, "pending": True})
                return
        else:
            vm = self.session.view(screen, page)
        self._json({"html": R.render_body(vm), "screen": vm["screen"]})


def supervise(
    session: Session,
    stop: Callable[[], None],
    lost_s: float,
    reason_file: Optional[str] = None,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> str:
    """Kiosk supervision: return once LinuxCNC has been gone or half gone
    (Session.linuxcnc_lost) for `lost_s` without a break, after writing why
    to `reason_file` and calling `stop` (the server's shutdown).

    Why the UI should exit then: LinuxCNC shuts down when its display
    program exits (deploy/bettercnc-display), the board's watchdog then
    drops the step outputs, and deploy/kiosk-session starts LinuxCNC clean
    and shows the reason. Left alone, a LinuxCNC whose task died stays half
    up: the screen still answers, but its estop no longer reaches the
    machine. The value deploy/ passes, and why, is in deploy/README.md
    ("Süreler").
    """
    since: Optional[float] = None
    while True:
        why = session.linuxcnc_lost()
        now = clock()
        if why is None:
            since = None
        else:
            if since is None:
                since = now
            if now - since >= lost_s:
                if reason_file:
                    try:
                        with open(reason_file, "w", encoding="utf-8") as fh:
                            fh.write(why + "\n")
                    except OSError:
                        pass  # the reason is on stderr too, in LinuxCNC's log
                sys.stderr.write(
                    "better-cnc UI kapanıyor: {} ({:g} s)\n".format(why, lost_s)
                )
                stop()
                return why
        sleep(SUPERVISE_EVERY_S)


def read_startup_note(path: Optional[str]) -> str:
    """The first line of `path`, or "" (no file, empty, unreadable)."""
    if not path:
        return ""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.readline().strip()
    except OSError:
        return ""


def serve(
    profile: str = "test-rig",
    host: str = "127.0.0.1",
    port: int = 8080,
    adapter_kind: str = "mock",
    boot_url: Optional[str] = None,
    exit_if_lost_s: Optional[float] = None,
    exit_reason_file: Optional[str] = None,
    ini_path: Optional[str] = None,
    startup_note_file: Optional[str] = None,
    observe: bool = False,
) -> Optional[int]:
    """Run the server until Ctrl-C (None) or, with `exit_if_lost_s`, until
    supervise() ends it (EXIT_LINUXCNC_LOST). `startup_note_file`: a line
    the kiosk left about why the control system last stopped, shown as the
    UI comes up (deploy/kiosk-session). `observe`: read-only beside AXIS
    (Session)."""
    Handler.session = build_session(profile, adapter_kind, ini_path, observe)
    Handler.boot_url = boot_url
    note = read_startup_note(startup_note_file)
    if note:
        Handler.session.adapter.note_startup(note)
    httpd = ThreadingHTTPServer((host, port), Handler)
    sys.stderr.write(
        "better-cnc UI · profil={} · http://{}:{}/\n".format(profile, host, port)
    )
    sys.stderr.write(adapter_banner(Handler.session.adapter))
    if Handler.session.observe:
        sys.stderr.write(OBSERVE_BANNER)
    ended: Dict[str, str] = {}
    if exit_if_lost_s is not None:
        session = Handler.session

        def stop() -> None:
            # Marked BEFORE the shutdown: serve_forever returns as soon as it
            # is asked, and the main thread raced this thread to the return
            # value - exit 0 instead of EXIT_LINUXCNC_LOST about a third of
            # the time with a real ThreadingHTTPServer (audit, 2026-10-21).
            ended["why"] = "linuxcnc lost"
            httpd.shutdown()

        def watch() -> None:
            supervise(session, stop, exit_if_lost_s, exit_reason_file)

        threading.Thread(target=watch, name="bettercnc-supervise", daemon=True).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return EXIT_LINUXCNC_LOST if ended else None


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description="better-cnc operator UI")
    ap.add_argument("--profile", default="test-rig", choices=("test-rig", "marble"))
    ap.add_argument(
        "--adapter",
        default="mock",
        choices=ADAPTERS,
        help="mock (default): simulated, sends nothing; linuxcnc: drives a running LinuxCNC",
    )
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument(
        "--ini",
        default=None,
        metavar="FILE",
        help="the machine INI, in place of the profile's (the kiosk passes LinuxCNC's)",
    )
    ap.add_argument(
        "--boot-url",
        default=None,
        help="kiosk mode: the page goes here when the UI stops answering (deploy/)",
    )
    ap.add_argument(
        "--exit-if-linuxcnc-lost",
        type=float,
        default=None,
        metavar="SECONDS",
        help="kiosk mode: exit when LinuxCNC has been gone or half gone this long (deploy/)",
    )
    ap.add_argument(
        "--exit-reason-file",
        default=None,
        help="kiosk mode: write why the UI exited here, for the boot screen",
    )
    ap.add_argument(
        "--startup-note-file",
        default=None,
        metavar="FILE",
        help="kiosk mode: show the line in FILE (why the control system last stopped) at start",
    )
    ap.add_argument(
        "--observe",
        action="store_true",
        help="read-only beside AXIS: sends nothing but the software estop (and the "
        "link-fault estop), leaves LinuxCNC's error messages to AXIS, disables the other buttons",
    )
    screens = [s[0] for s in VM.SCREENS]
    ap.add_argument(
        "--dump",
        metavar="SCREEN",
        help="render one screen to stdout and exit (no server); one of: {}".format(
            ", ".join(screens)
        ),
    )
    args = ap.parse_args(argv)
    if args.exit_if_linuxcnc_lost is not None and not args.exit_if_linuxcnc_lost > 0:
        ap.error("argument --exit-if-linuxcnc-lost: must be a positive number of seconds")
    try:
        return _run(ap, args, screens)
    except LinuxCncUnavailable as exc:
        # The operator's message, not a traceback: --adapter linuxcnc with
        # LinuxCNC not running, or no linuxcnc module (adapter._probe_status).
        sys.stderr.write("better-cnc UI başlatılamadı: {}\n".format(exc))
        return 1


def _run(ap: argparse.ArgumentParser, args: argparse.Namespace, screens: list) -> int:
    """main() after parsing: one dump, or the server."""
    # "is not None", not truthiness: `--dump ""` was given and must be refused
    # below, not mistaken for "no --dump" and start the server.
    if args.dump is not None:
        # The HTTP routes fall back to "kontrol" for an unknown screen; a dump
        # is a file someone reviews later, so a typo is refused (exit 2)
        # rather than written out as a page labelled with a screen that does
        # not exist.
        if args.dump not in screens:
            ap.error(
                "argument --dump: unknown screen {!r} (valid screens: {})".format(
                    args.dump, ", ".join(screens)
                )
            )
        session = build_session(args.profile, args.adapter, args.ini, args.observe)
        vm = session.view(args.dump)
        sys.stdout.write(R.render_page(vm, _read_static("app.css"), _read_static("app.js")))
        return 0

    code = serve(
        args.profile, args.host, args.port, args.adapter, args.boot_url,
        args.exit_if_linuxcnc_lost, args.exit_reason_file, args.ini,
        args.startup_note_file, args.observe,
    )
    return code or 0


if __name__ == "__main__":
    sys.exit(main())
