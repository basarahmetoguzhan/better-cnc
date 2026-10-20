"""The kiosk's boot screen: what the operator sees while LinuxCNC starts, or
why it stopped. See deploy/README.md for where it sits.

    python3 -m bettercnc.bootpage --port 8079 --status-file F --ui-url http://127.0.0.1:8080/

stdlib only, and it never imports `linuxcnc` or `hal`: the operator UI holds
a HAL component and is killed with every LinuxCNC shutdown (docs/11, "Reading
the HAL pins needs a HAL component"); this server has to outlive those, so
the screen always has something true to say.

Routes:
    GET /            the page; it polls /api/status and goes to --ui-url once
                     the UI answers
    GET /api/status  {"text": the status file, "ui_up": bool}
    GET /healthz     {"ok": true, "client_age_s": seconds since the browser
                     last asked (/ or /api/status), or null}

deploy/kiosk-session writes the status file; this only reads it.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Optional

#: Read at most this much of the status file: it carries a log tail, and the
#: page has to stay small.
MAX_STATUS_BYTES = 8 * 1024

#: Shown before kiosk-session has written anything.
NO_STATUS = "Başlatılıyor…"

#: How long one UI health check may take. Chosen, not measured: the UI is on
#: the same machine, and the page asks again a second later.
UI_CHECK_TIMEOUT_S = 0.5


def read_status(path: Optional[str]) -> str:
    """The status file's text, or NO_STATUS when there is none yet."""
    if not path:
        return NO_STATUS
    try:
        with open(path, "rb") as fh:
            raw = fh.read(MAX_STATUS_BYTES)
    except OSError:
        return NO_STATUS
    text = raw.decode("utf-8", errors="replace").strip()
    return text or NO_STATUS


def ui_is_up(ui_url: str, timeout: float = UI_CHECK_TIMEOUT_S) -> bool:
    """Whether the operator UI answers its /healthz with {"ok": true}."""
    try:
        with urllib.request.urlopen(ui_url.rstrip("/") + "/healthz", timeout=timeout) as r:
            return r.status == 200 and json.loads(r.read().decode("utf-8")).get("ok") is True
    except (OSError, ValueError, urllib.error.URLError):
        return False


def status(status_file: Optional[str], ui_url: str) -> Dict[str, Any]:
    return {"text": read_status(status_file), "ui_up": ui_is_up(ui_url)}


_PAGE = """<!doctype html>
<html lang="tr" translate="no"><head><meta charset="utf-8">
<meta name="google" content="notranslate">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>better-cnc · başlatılıyor</title>
<style>
body {{ margin: 0; min-height: 100vh; display: flex; align-items: center; justify-content: center;
       background: #161826; color: #e9e9ed;
       font: 16px/1.5 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; }}
main {{ max-width: 760px; padding: 24px; text-align: center; }}
body {{ -webkit-user-select: none; user-select: none; overscroll-behavior: none;
       touch-action: manipulation; }}
h1 {{ font-size: 14px; letter-spacing: .12em; color: #9397ab; font-weight: 600; margin: 0 0 16px; }}
#status {{ white-space: pre-wrap; text-align: left; font-size: 18px; }}
.hint {{ margin-top: 20px; color: #f0a92b; font-size: 14px; }}
</style></head>
<body><main>
<h1>BETTER-CNC</h1>
<div id="status">{text}</div>
<div class="hint">Arayüz hazır olunca bu ekran kendiliğinden geçer.
Acil durumda makinenin gücünü kesin.</div>
</main>
<script>
(function () {{
  "use strict";
  var ui = {ui_json};
  var box = document.getElementById("status");
  // Kiosk only, like app.js's kiosk block: nothing of the browser shows.
  var stop = function (ev) {{ ev.preventDefault(); }};
  document.addEventListener("contextmenu", stop);
  document.addEventListener("dragstart", stop);
  document.addEventListener("auxclick", stop);
  document.addEventListener("wheel", function (ev) {{ if (ev.ctrlKey) ev.preventDefault(); }},
                            {{ passive: false }});
  document.addEventListener("keydown", function (ev) {{
    if (ev.ctrlKey || ev.metaKey || ev.altKey || /^F\\d{{1,2}}$/.test(ev.key) || ev.key === "Tab") {{
      ev.preventDefault();
      ev.stopPropagation();
    }}
  }}, true);
  function tick() {{
    fetch("/api/status")
      .then(function (r) {{ return r.json(); }})
      .then(function (s) {{
        box.textContent = s.text;
        if (s.ui_up) location.replace(ui);
      }})
      .catch(function () {{
        box.textContent = "Başlatma ekranının sunucusu cevap vermiyor.";
      }});
  }}
  setInterval(tick, 1000);
}})();
</script></body></html>"""


def render(status_text: str, ui_url: str) -> str:
    # json.dumps of a URL cannot hold "</script": the URL comes from our own
    # command line, and "/" is not escaped, so refuse it rather than escape.
    ui_json = json.dumps(ui_url)
    if "</" in ui_json:
        raise ValueError("ui_url must not contain '</': {!r}".format(ui_url))
    return _PAGE.format(text=escape(status_text), ui_json=ui_json)


class Contact:
    """When a browser last asked this server; read by
    bettercnc.browserwatch through /healthz, as for the UI (app.Session)."""

    clock = staticmethod(time.monotonic)
    last: Optional[float] = None

    @classmethod
    def touch(cls) -> None:
        cls.last = cls.clock()

    @classmethod
    def age_s(cls) -> Optional[float]:
        return None if cls.last is None else round(cls.clock() - cls.last, 1)


class Handler(BaseHTTPRequestHandler):
    status_file: Optional[str] = None
    ui_url: str = "http://127.0.0.1:8080/"
    server_version = "better-cnc-boot/0.1"
    timeout = 10.0  # same reasoning as app.REQUEST_TIMEOUT_S

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args: Any) -> None:  # pragma: no cover
        pass

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path in ("/", "/api/status"):
            Contact.touch()
        if path == "/":
            page = render(read_status(self.status_file), self.ui_url)
            self._send(200, page.encode("utf-8"), "text/html; charset=utf-8")
        elif path == "/api/status":
            body = json.dumps(status(self.status_file, self.ui_url), ensure_ascii=False)
            self._send(200, body.encode("utf-8"), "application/json; charset=utf-8")
        elif path == "/healthz":
            body = json.dumps({"ok": True, "client_age_s": Contact.age_s()})
            self._send(200, body.encode("utf-8"), "application/json; charset=utf-8")
        else:
            self._send(404, b"not found", "text/plain; charset=utf-8")


def serve(host: str, port: int, status_file: Optional[str], ui_url: str) -> None:
    Handler.status_file = status_file
    Handler.ui_url = ui_url
    render(NO_STATUS, ui_url)  # refuse a bad --ui-url before binding
    httpd = ThreadingHTTPServer((host, port), Handler)
    sys.stderr.write("better-cnc boot screen · http://{}:{}/ → {}\n".format(host, port, ui_url))
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description="better-cnc kiosk boot screen")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8079)
    ap.add_argument("--status-file", default=None)
    ap.add_argument("--ui-url", default="http://127.0.0.1:8080/")
    args = ap.parse_args(argv)
    serve(args.host, args.port, args.status_file, args.ui_url)
    return 0


if __name__ == "__main__":
    sys.exit(main())
