"""HTTP server. stdlib only, so it runs on the live USB with nothing installed.

    python3 -m bettercnc.app --profile test-rig --port 8080
    python3 -m bettercnc.app --profile test-rig --adapter linuxcnc

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
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Dict, Optional, Tuple
from urllib.parse import parse_qs, urlparse

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
    software_estop - see act().
    """

    def __init__(
        self,
        adapter: MachineAdapter,
        profile: MachineProfile,
        config: MachineConfig,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.adapter = adapter
        self.profile = profile
        self.config = config
        self._lock = threading.Lock()
        self._clock = clock
        self._last_client: Optional[float] = None

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

    def view(self, screen: str) -> Dict[str, Any]:
        with self._lock:
            state = self.adapter.snapshot()
        return VM.build(self.profile, self.config, state, screen=screen)

    def act(self, action: str, value: str) -> None:
        """Apply an intent. Unknown actions are ignored rather than raising:
        a stale browser must not be able to 500 the controller UI.

        software_estop is the one action that does NOT take self._lock. On the
        test rig the GUI button is the only estop: no hardware estop input is
        wired (configs/test-rig/test-rig.hal, section "ESTOP CHAIN - GUI ONLY.
        THERE IS NO HARDWARE ESTOP INPUT."), so it must never queue behind
        another action. The price is that adapter.software_estop() must be
        safe to call while any other adapter method is running in another
        thread (LinuxCncAdapter's class docstring, "Threading").

        How long an action in progress can still hold an estop up, on
        LinuxCncAdapter. Read from the LinuxCNC source, not measured:

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

        The estop's HTTP response is later than the estop itself: do_POST
        re-renders through view(), which takes self._lock, so the page that
        shows the estop waits until the action in progress returns, all its
        bounded waits and sends included. The command has gone out by then;
        only the operator's feedback waits.

        Not wired yet: load_program exists on both adapters, but no action
        here calls it, because the program screen lists no files to pick
        (viewmodel.program_screen returns an empty "files").
        """
        a = self.adapter
        if action == "software_estop":
            a.software_estop()
            return
        with self._lock:
            if action == "software_reset":
                a.software_estop_reset()
            elif action == "home_all":
                a.home_all()
            elif action == "jog":
                # Wire format "<axis>:<1|-1>" (render.py render_jog). Any
                # other number is dropped here: MockAdapter.jog would move
                # that many steps while reporting one, and
                # LinuxCncAdapter.jog raises ValueError for it.
                axis, _, raw_direction = value.partition(":")
                try:
                    direction = int(raw_direction)
                except ValueError:
                    direction = 0
                if axis and direction in (1, -1):
                    try:
                        a.jog(axis, direction)
                    except (ValueError, KeyError):
                        pass  # LinuxCncAdapter: KeyError for an axis it lacks
            elif action in ("mdi", "mdi_preset"):
                a.send_mdi(value)
            elif action == "program_start":
                a.program_start()
            elif action == "program_stop":
                a.program_stop()
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


#: --adapter choices. "mock" is the default: a UI started without saying
#: otherwise never sends a command to LinuxCNC.
ADAPTERS = ("mock", "linuxcnc")


def build_session(profile_key: str = "test-rig", adapter_kind: str = "mock") -> Session:
    profile = load_profile(profile_key)
    validate_profile(profile)
    config = load_machine_config(profile)
    adapter: MachineAdapter
    if adapter_kind == "mock":
        adapter = MockAdapter(profile)
    elif adapter_kind == "linuxcnc":
        adapter = LinuxCncAdapter(profile)
    else:
        raise ValueError(
            "unknown adapter {!r} (valid: {})".format(adapter_kind, ", ".join(ADAPTERS))
        )
    return Session(adapter, profile, config)


def adapter_banner(adapter: MachineAdapter) -> str:
    """The banner line naming the adapter, decided from the adapter instance
    rather than from the flag, so it cannot claim "not connected" while
    commands reach LinuxCNC."""
    if isinstance(adapter, MockAdapter):
        return "  (mock adapter — makineye bağlı DEĞİL)\n"
    return "  ({} — komutlar LinuxCNC'ye GİDİYOR)\n".format(type(adapter).__name__)


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
            json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            "application/json; charset=utf-8",
        )

    def _screen_from_query(self, query: str) -> str:
        return _valid_screen(parse_qs(query).get("screen", ["kontrol"])[0])

    def log_message(self, fmt: str, *args: Any) -> None:  # pragma: no cover
        pass  # the controller console is for the machine, not access logs

    # -- routes --------------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path

        if path in ("/", "/api/render", "/api/state"):
            self.session.touch()

        if path == "/":
            vm = self.session.view(self._screen_from_query(parsed.query))
            html = R.render_page(
                vm, _read_static("app.css"), _read_static("app.js"), boot_url=self.boot_url
            )
            self._send(200, html.encode("utf-8"), "text/html; charset=utf-8")
            return

        if path == "/api/render":
            vm = self.session.view(self._screen_from_query(parsed.query))
            self._json({"html": R.render_body(vm), "screen": vm["screen"]})
            return

        if path == "/api/state":
            vm = self.session.view(self._screen_from_query(parsed.query))
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
        action = str(payload.get("action") or "")
        value = str(payload.get("value") or "")
        screen = _valid_screen(str(payload.get("screen") or "kontrol"))

        self.session.act(action, value)
        vm = self.session.view(screen)
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


def serve(
    profile: str = "test-rig",
    host: str = "127.0.0.1",
    port: int = 8080,
    adapter_kind: str = "mock",
    boot_url: Optional[str] = None,
    exit_if_lost_s: Optional[float] = None,
    exit_reason_file: Optional[str] = None,
) -> Optional[int]:
    """Run the server until Ctrl-C (None) or, with `exit_if_lost_s`, until
    supervise() ends it (EXIT_LINUXCNC_LOST)."""
    Handler.session = build_session(profile, adapter_kind)
    Handler.boot_url = boot_url
    httpd = ThreadingHTTPServer((host, port), Handler)
    sys.stderr.write(
        "better-cnc UI · profil={} · http://{}:{}/\n".format(profile, host, port)
    )
    sys.stderr.write(adapter_banner(Handler.session.adapter))
    ended: Dict[str, str] = {}
    if exit_if_lost_s is not None:
        session = Handler.session

        def watch() -> None:
            ended["why"] = supervise(session, httpd.shutdown, exit_if_lost_s, exit_reason_file)

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
        session = build_session(args.profile, args.adapter)
        vm = session.view(args.dump)
        sys.stdout.write(R.render_page(vm, _read_static("app.css"), _read_static("app.js")))
        return 0

    code = serve(
        args.profile, args.host, args.port, args.adapter, args.boot_url,
        args.exit_if_linuxcnc_lost, args.exit_reason_file,
    )
    return code or 0


if __name__ == "__main__":
    sys.exit(main())
