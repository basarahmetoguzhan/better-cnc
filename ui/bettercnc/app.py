"""HTTP server. stdlib only, so it runs on the live USB with nothing installed.

    python3 -m bettercnc.app --profile test-rig --port 8080

Routes:
    GET  /                 full page
    GET  /api/render       re-render one screen (the 1 Hz poll)
    POST /api/action       an intent, then re-render
    GET  /api/state        the raw view model as JSON, for debugging

The server holds a MachineAdapter. Swapping MockAdapter for LinuxCncAdapter is a
one-line change in `build_session`; nothing else in the stack knows which it is.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Optional, Tuple
from urllib.parse import parse_qs, urlparse

from . import render as R
from . import viewmodel as VM
from .adapter import MachineAdapter, MockAdapter
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

    def __init__(self, adapter: MachineAdapter, profile: MachineProfile, config: MachineConfig):
        self.adapter = adapter
        self.profile = profile
        self.config = config
        self._lock = threading.Lock()

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


def build_session(profile_key: str = "test-rig") -> Session:
    profile = load_profile(profile_key)
    validate_profile(profile)
    config = load_machine_config(profile)
    # Swap this line for LinuxCncAdapter(profile) on the machine.
    adapter: MachineAdapter = MockAdapter(profile)
    return Session(adapter, profile, config)


class Handler(BaseHTTPRequestHandler):
    session: Session = None  # type: ignore[assignment]
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

        if path == "/":
            vm = self.session.view(self._screen_from_query(parsed.query))
            html = R.render_page(vm, _read_static("app.css"), _read_static("app.js"))
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
            self._json({"ok": True})
            return

        self._send(404, b"not found", "text/plain; charset=utf-8")

    def do_POST(self) -> None:  # noqa: N802
        if urlparse(self.path).path != "/api/action":
            self._send(404, b"not found", "text/plain; charset=utf-8")
            return
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


def serve(profile: str = "test-rig", host: str = "127.0.0.1", port: int = 8080) -> None:
    Handler.session = build_session(profile)
    httpd = ThreadingHTTPServer((host, port), Handler)
    sys.stderr.write(
        "better-cnc UI · profil={} · http://{}:{}/\n".format(profile, host, port)
    )
    sys.stderr.write("  (mock adapter — makineye bağlı DEĞİL)\n")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description="better-cnc operator UI (mock)")
    ap.add_argument("--profile", default="test-rig", choices=("test-rig", "marble"))
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8080)
    screens = [s[0] for s in VM.SCREENS]
    ap.add_argument(
        "--dump",
        metavar="SCREEN",
        help="render one screen to stdout and exit (no server); one of: {}".format(
            ", ".join(screens)
        ),
    )
    args = ap.parse_args(argv)

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
        session = build_session(args.profile)
        vm = session.view(args.dump)
        sys.stdout.write(R.render_page(vm, _read_static("app.css"), _read_static("app.js")))
        return 0

    serve(args.profile, args.host, args.port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
