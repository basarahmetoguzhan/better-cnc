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


def _read_static(name: str) -> str:
    with open(os.path.join(STATIC_DIR, name), "r", encoding="utf-8") as fh:
        return fh.read()


class Session:
    """One machine, one adapter, one config. Thread-safe enough for this server."""

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
        a stale browser must not be able to 500 the controller UI."""
        a = self.adapter
        with self._lock:
            if action == "software_estop":
                a.software_estop()
            elif action == "software_reset":
                a.software_estop_reset()
            elif action == "home_all":
                a.home_all()
            elif action == "jog":
                axis, _, direction = value.partition(":")
                if axis and direction:
                    try:
                        a.jog(axis, int(direction))
                    except (ValueError, KeyError):
                        pass
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
        got = parse_qs(query).get("screen", ["kontrol"])[0]
        return got if got in dict((s[0], s) for s in VM.SCREENS) else "kontrol"

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
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8") or "{}")
        except ValueError:
            payload = {}
        action = str(payload.get("action") or "")
        value = str(payload.get("value") or "")
        screen = str(payload.get("screen") or "kontrol")

        self.session.act(action, value)
        vm = self.session.view(screen if screen in dict((s[0], s) for s in VM.SCREENS) else "kontrol")
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
    ap.add_argument(
        "--dump",
        metavar="SCREEN",
        help="render one screen to stdout and exit (no server)",
    )
    args = ap.parse_args(argv)

    if args.dump:
        session = build_session(args.profile)
        vm = session.view(args.dump)
        sys.stdout.write(R.render_page(vm, _read_static("app.css"), _read_static("app.js")))
        return 0

    serve(args.profile, args.host, args.port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
