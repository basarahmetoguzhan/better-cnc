"""The kiosk's browser watch: keeps the browser running, responsive, and
showing nothing but our own pages.

    python3 -m bettercnc.browserwatch --ui-health http://127.0.0.1:8080/healthz \\
        --boot-health http://127.0.0.1:8079/healthz -- deploy/kiosk-browser URL

Starts the browser command and restarts it when it

- exits (closed, crashed), after RESTART_PAUSE_S, or
- stops asking: the UI and the boot screen both report, on /healthz, how
  long ago a browser last asked them (app.Session.client_age_s,
  bootpage.Contact). A page polls every second; when NEITHER server has
  heard from it for STALE_S, the page is dead - Chromium's "Aw, Snap"
  after a renderer crash, say - and the browser process, still alive, would
  show that forever. Not before GRACE_S after a start: the first page takes
  a while to load.

While the UI cannot be reached (LinuxCNC restarting, the UI hung), the page
on it is alive but has no one to ask: it covers the screen and goes to the
boot screen BOOT_MS after its last answer (app.js). So the boot screen's
silence counts only once the UI has been away longer than BOOT_HANDOFF_S.
The first version of this watch did not wait: in sim/ on 2026-10-20 it
restarted the browser on every UI restart. With neither server reachable
nothing is restarted.

And it keeps Chromium's own pages off the screen (Screen): a crashed tab
("Aw, Snap"), a connection error, a blocked page. The browser runs with
--remote-debugging-pipe - its DevTools protocol on two pipes only this
process holds, no network port - and the watch looks at its tabs every
GUARD_EVERY_S, and at once when one crashes. A tab is replaced when it
crashed, when it is on one of our servers without being our page and that
server does not answer (Chromium's "can't connect" page), or when it has
not been one of ours for NOT_OURS_S (anything else). It is replaced by the
UI if that answers, else by the boot screen if that answers, else by our
"Bir hata oluştu — lütfen bekleyin" page (deploy/files/wait.html), which
needs no server and gives way to the other two as soon as one answers.
Chromium's own pages cannot be changed; this is how they are kept from
being seen for more than a moment (sim/ 2026-10-20: a crashed tab was back
on the UI after ~80 ms, one white frame and no "Aw, Snap" captured; a
"can't connect" page lasted ~1.5 s before the server check).

stdlib only, no linuxcnc: it outlives LinuxCNC like the boot screen does.
Why these values: deploy/README.md, "Süreler".
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from typing import Any, Callable, Dict, List, Optional, Tuple

#: How often the servers are asked.
CHECK_EVERY_S = 2.0
#: No contact with either server for this long: the page is dead.
STALE_S = 10.0
#: After a (re)start, how long the browser gets before it is judged.
GRACE_S = 30.0
#: SIGTERM, then SIGKILL after this.
STOP_WAIT_S = 5.0
#: Between a browser exit and its restart.
RESTART_PAUSE_S = 1.0
#: One /healthz question; the servers are on this machine.
ASK_TIMEOUT_S = 0.5
#: Before a browser start, how long the boot screen gets to answer: at
#: power-on it starts alongside this watch, and the browser must not open
#: on the wait page's "Bir hata oluştu" for a normal start. The screen is
#: black meanwhile. Chosen; deploy/README.md "Süreler".
BOOT_WAIT_S = 5.0
#: How long a page on a UI that went away gets to reach the boot screen:
#: app.js BOOT_MS (10 s) after its last answer, plus the redirect and the
#: page load.
BOOT_HANDOFF_S = 15.0

#: A reachable server that has never been asked.
NEVER = float("inf")

#: How often the screen is looked at over the DevTools pipe.
GUARD_EVERY_S = 0.5
#: A tab that has not been ours this long is replaced (a crashed one, and
#: one on a server of ours that does not answer, at once). While a page of
#: ours loads its title is the host for a moment; our pages are local and
#: load in well under a second, Chromium's error and blocked pages stay.
#: Chosen; deploy/README.md "Süreler".
NOT_OURS_S = 1.5
#: Our pages' titles start with this (render.render_page, bootpage, wait.html).
TITLE_MARK = "better-cnc · "
#: Installed by deploy/kiosk.sh; the one file:// URL the policy allows.
WAIT_URL = "file:///usr/share/bettercnc/wait.html"
#: Chromium's own internal pages (the hidden omnibox popup, say): not tabs.
INTERNAL = ("chrome://", "chrome-untrusted://", "devtools://")
#: At most one replacement tab is opened per this long, so a guard that
#: misjudges can never flood the browser with tabs.
REPLACE_AGAIN_S = 5.0


class Screen:
    """What the browser shows, over Chromium's DevTools pipe.

    Browser-level Target commands only: under the kiosk policy
    (DeveloperToolsAvailability 2) attaching to a page is refused ("Not
    allowed", sim/ 2026-10-20), and that policy stays on. What that leaves
    is enough: Target.getTargets lists every tab's URL and title,
    Target.setDiscoverTargets brings Target.targetCrashed, Target.createTarget
    opens a tab in front and Target.closeTarget closes one. The tab list is
    asked for on every look: the discovery events do not report every title
    change (in sim/ a loaded page of ours kept its loading title, the host,
    and was taken for an error page).

    A tab is ours when its URL is on our origins and its title starts with
    TITLE_MARK - Chromium's error page keeps the URL it failed on but takes
    the host as title - or when it is the wait page, whatever its title, so
    the wait page can never be judged and replaced by itself (a missing
    wait.html, Chromium's file error at that URL, is left for the next
    server that answers). The last tab is never closed: the browser would
    exit with it.
    """

    def __init__(
        self,
        to_browser: int,
        from_browser: int,
        ui_url: str,
        boot_url: str,
        wait_url: str = WAIT_URL,
        clock: Callable[[], float] = time.monotonic,
        answers: Optional[Callable[[str], bool]] = None,
    ):
        self._w = to_browser
        self._r = from_browser
        self._origins = (ui_url, boot_url)
        # for the log: (opened, gone back to)
        self._names = {ui_url: ("arayüz açıldı", "arayüze dönüldü"),
                       boot_url: ("başlatma ekranı açıldı", "başlatma ekranına dönüldü"),
                       wait_url: ("bekleme ekranı açıldı", "")}
        self._ui_url = ui_url
        self._boot_url = boot_url
        self._wait_url = wait_url
        self._clock = clock
        # whether the server at a URL of ours answers its /healthz
        self._answers = answers or (lambda url: client_age(url + "healthz") is not None)
        self._lock = threading.Lock()
        self._tabs: Dict[str, Dict[str, Any]] = {}
        self._sent = 0
        self._asked: set = set()
        self._last_replace: Optional[float] = None
        self._crash = threading.Event()
        self.send("Target.setDiscoverTargets", {"discover": True})
        self._reader = threading.Thread(target=self._read, name="browserwatch-cdp", daemon=True)
        self._reader.start()

    def send(self, method: str, params: Optional[Dict[str, Any]] = None) -> int:
        self._sent += 1
        msg = {"id": self._sent, "method": method, "params": params or {}}
        try:
            os.write(self._w, json.dumps(msg).encode("utf-8") + b"\0")
        except OSError:
            pass  # the browser is gone; the watch restarts it
        return self._sent

    def _read(self) -> None:
        buf = b""
        while True:
            try:
                chunk = os.read(self._r, 65536)
            except OSError:
                return
            if not chunk:
                return
            buf += chunk
            while b"\0" in buf:
                raw, buf = buf.split(b"\0", 1)
                try:
                    self.handle(json.loads(raw.decode("utf-8")))
                except ValueError:
                    continue

    def handle(self, msg: Dict[str, Any]) -> None:
        """One message from the browser: keep the tab table current."""
        method = msg.get("method")
        params = msg.get("params") or {}
        with self._lock:
            if msg.get("id") in self._asked:
                self._asked.discard(msg["id"])
                infos = (msg.get("result") or {}).get("targetInfos")
                if isinstance(infos, list):
                    self._tabs = self._from_list(infos)
                return
            if method in ("Target.targetCreated", "Target.targetInfoChanged"):
                info = params.get("targetInfo") or {}
                if info.get("type") == "page" and "targetId" in info:
                    tab = self._tabs.setdefault(info["targetId"], {"crashed": False, "since": None})
                    tab["url"] = info.get("url", "")
                    tab["title"] = info.get("title", "")
            elif method == "Target.targetDestroyed":
                self._tabs.pop(params.get("targetId"), None)
            elif method == "Target.targetCrashed":
                tab = self._tabs.get(params.get("targetId"))
                if tab is not None:
                    tab["crashed"] = True
                    self._crash.set()

    def _from_list(self, infos: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        """A Target.getTargets answer as the tab table, keeping what is
        known of the tabs already in it (crashed, since). Under the lock."""
        fresh: Dict[str, Dict[str, Any]] = {}
        for info in infos:
            if info.get("type") != "page" or "targetId" not in info:
                continue
            tab = self._tabs.get(info["targetId"], {"crashed": False, "since": None})
            tab["url"] = info.get("url", "")
            tab["title"] = info.get("title", "")
            fresh[info["targetId"]] = tab
        return fresh

    def _server_of(self, url: str) -> Optional[str]:
        return next((o for o in self._origins if url.startswith(o)), None)

    def _ours(self, tab: Dict[str, Any]) -> bool:
        if tab["crashed"]:
            return False
        if tab.get("url", "") == self._wait_url:
            return True
        return tab.get("title", "").startswith(TITLE_MARK) and tab.get("url", "").startswith(
            self._origins
        )

    def wait(self, timeout: float) -> None:
        """Sleep until the next look: `timeout`, or a tab crashing."""
        self._crash.wait(timeout)
        self._crash.clear()

    def guard(self, ui_up: bool, boot_up: bool) -> List[str]:
        """One look at the screen; returns what was done, for the log. It
        judges the tab list of the previous look and asks for the next one.
        `ui_up`/`boot_up`: whether the UI and the boot screen answer."""
        now = self._clock()
        back = self._ui_url if ui_up else self._boot_url if boot_up else None
        with self._lock:  # before the send: the answer may come before it returns
            self._asked.add(self._sent + 1)
        self.send("Target.getTargets")
        with self._lock:
            tabs = {i: t for i, t in self._tabs.items() if not t.get("url", "").startswith(INTERNAL)}
            suspects = {self._server_of(t.get("url", "")) for t in tabs.values()
                        if not t["crashed"] and not self._ours(t)} - {None}
        # Asked outside the lock; loopback, so a server that is down refuses at once.
        down = {o for o in suspects if not self._answers(o)}
        with self._lock:
            bad, good, why = [], [], {}
            for i, tab in tabs.items():
                if self._ours(tab):
                    tab["since"] = None
                    good.append(i)
                    continue
                if tab["since"] is None:
                    tab["since"] = now
                if tab["crashed"]:
                    why[i] = "çöken sekme"
                elif self._server_of(tab.get("url", "")) in down:
                    why[i] = "hata sayfası"
                elif now - tab["since"] >= NOT_OURS_S:
                    why[i] = "bizim olmayan sayfa"
                else:
                    continue
                bad.append(i)
            waiting = [i for i in good if tabs[i]["url"] == self._wait_url]
            what = ["{} ({!r}, {!r})".format(why[i], tabs[i].get("url", "")[:60],
                                             tabs[i].get("title", "")[:40]) for i in bad]
        done: List[str] = []
        if bad:
            if not good:
                if self._last_replace is not None and now - self._last_replace < REPLACE_AGAIN_S:
                    return done  # one replacement is on its way
                self._last_replace = now
                url = back or self._wait_url
                self.send("Target.createTarget", {"url": url})
                done.append(self._names[url][0])
            for i in bad:
                self.send("Target.closeTarget", {"targetId": i})
            done.extend("kapatıldı: " + w for w in what)
        elif waiting and back and len(waiting) == len(good):
            self.send("Target.createTarget", {"url": back})
            for i in waiting:
                self.send("Target.closeTarget", {"targetId": i})
            done.append(self._names[back][1])
        return done

    def close(self) -> None:
        for fd in (self._w, self._r):
            try:
                os.close(fd)
            except OSError:
                pass


def _above(fd: int, low: int) -> int:
    """`fd` moved to the lowest free number >= `low`."""
    moved = fcntl.fcntl(fd, fcntl.F_DUPFD, low)
    os.close(fd)
    return moved


def start_browser(command: List[str], ui_url: str, boot_url: str):
    """Start the browser with its DevTools pipe; (process, Screen).

    deploy/kiosk-browser moves the two pipe ends to the fds 3 and 4 Chromium
    reads and writes for --remote-debugging-pipe (BETTERCNC_CDP_FDS), so no
    preexec_fn runs in a process that has threads.
    """
    cmd_r, cmd_w = os.pipe()   # we write commands, the browser reads (its fd 3)
    ev_r, ev_w = os.pipe()     # the browser writes (its fd 4), we read
    # The child's ends above 20, so moving them to 3 and 4 cannot overwrite
    # one with the other.
    cmd_r, ev_w = _above(cmd_r, 20), _above(ev_w, 20)
    env = dict(os.environ, BETTERCNC_CDP_FDS="{} {}".format(cmd_r, ev_w))
    proc = subprocess.Popen(command, env=env, pass_fds=(cmd_r, ev_w))
    os.close(cmd_r)
    os.close(ev_w)
    return proc, Screen(cmd_w, ev_r, ui_url, boot_url)


def client_age(url: str, timeout: float = ASK_TIMEOUT_S) -> Optional[float]:
    """Seconds since the server at `url` (/healthz) last heard from a
    browser: NEVER if it never has, None if it cannot be asked."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
    except (OSError, ValueError, urllib.error.URLError):
        return None
    if not isinstance(data, dict) or data.get("ok") is not True:
        return None
    age = data.get("client_age_s")
    return NEVER if age is None else float(age)


def answers_within(url: str, wait_s: float, every_s: float = 0.2) -> bool:
    """Whether the server at `url` (/healthz) answers within `wait_s`."""
    deadline = time.monotonic() + wait_s
    while client_age(url) is None:
        if time.monotonic() >= deadline:
            return False
        time.sleep(every_s)
    return True


def page_is_dead(
    ui_age: Optional[float], boot_age: Optional[float], running_s: float, ui_away_s: float
) -> bool:
    """Whether the browser's page has stopped asking. `ui_age`/`boot_age`:
    client_age() of each server (None: cannot be asked); `ui_away_s`: how
    long the UI has not been reachable (0 while it is)."""
    if running_s < GRACE_S:
        return False
    if ui_age is not None:
        known = [a for a in (ui_age, boot_age) if a is not None]
        return min(known) > STALE_S
    if boot_age is None:
        return False
    return ui_away_s > BOOT_HANDOFF_S and boot_age > STALE_S


def stop(proc: "subprocess.Popen", wait_s: float = STOP_WAIT_S) -> None:
    proc.terminate()
    try:
        proc.wait(timeout=wait_s)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


def log(text: str) -> None:
    sys.stderr.write("browserwatch: {}\n".format(text))


def watch(
    command: List[str],
    ui_health: str,
    boot_health: str,
    start: Callable[[List[str]], Tuple["subprocess.Popen", Optional[Screen]]],
    ask: Callable[[str], Optional[float]] = client_age,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    starts: Optional[int] = None,
) -> int:
    """Run `command` through `start` (start_browser in main), restarting it
    as described above; `starts` bounds the number of starts (tests), None
    runs forever. Returns the starts made."""
    made = 0
    while starts is None or made < starts:
        proc, screen = start(command)
        made += 1
        started = clock()
        ui_away_since: Optional[float] = None
        next_ask = started  # at the first look: where to go back to is known at once
        ui_up = boot_up = False
        while True:
            if screen is not None:
                screen.wait(GUARD_EVERY_S)
            else:
                sleep(GUARD_EVERY_S)
            if proc.poll() is not None:
                log("tarayıcı kapandı (çıkış kodu {}) — yeniden açılıyor".format(proc.returncode))
                break
            now = clock()
            if now >= next_ask:
                next_ask = now + CHECK_EVERY_S
                ui_age, boot_age = ask(ui_health), ask(boot_health)
                ui_up, boot_up = ui_age is not None, boot_age is not None
                if ui_age is None:
                    ui_away_since = now if ui_away_since is None else ui_away_since
                else:
                    ui_away_since = None
                away = 0.0 if ui_away_since is None else now - ui_away_since
                if page_is_dead(ui_age, boot_age, now - started, away):
                    log("tarayıcı {:g} s'dir hiçbir sayfayı sormadı — yeniden başlatılıyor".format(STALE_S))
                    stop(proc)
                    break
            if screen is not None:
                for what in screen.guard(ui_up, boot_up):
                    log(what)
        if screen is not None:
            screen.close()
        sleep(RESTART_PAUSE_S)
    return made


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description="better-cnc kiosk browser watch")
    ap.add_argument("--ui-health", required=True, metavar="URL",
                    help="the operator UI's /healthz")
    ap.add_argument("--boot-health", required=True, metavar="URL",
                    help="the boot screen's /healthz")
    ap.add_argument("command", nargs=argparse.REMAINDER,
                    help="-- then the browser command")
    args = ap.parse_args(argv)
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        ap.error("the browser command is missing (after --)")

    # Our pages: the two servers the health URLs belong to.
    ui_url, boot_url = (u.split("/healthz")[0] + "/" for u in (args.ui_health, args.boot_health))

    # On SIGTERM (the kiosk stopping) take the browser down too.
    current: List["subprocess.Popen"] = []

    def start(cmd: List[str]):
        # Onto the boot screen only if it answers, else onto the wait page:
        # Chromium's "can't connect" page otherwise, if only until the guard's
        # first look (sim/ 2026-10-20: ~0.3 s). The URL is the command's last
        # word (deploy/kiosk-session).
        if cmd[-1:] == [boot_url] and not answers_within(args.boot_health, BOOT_WAIT_S):
            cmd = cmd[:-1] + [WAIT_URL]
        proc, screen = start_browser(cmd, ui_url, boot_url)
        current[:] = [proc]
        return proc, screen

    def on_term(signum, frame):
        for proc in current:
            if proc.poll() is None:
                stop(proc)
        sys.exit(0)

    signal.signal(signal.SIGTERM, on_term)
    watch(command, args.ui_health, args.boot_health, start=start)
    return 0  # pragma: no cover - watch() runs forever


if __name__ == "__main__":
    sys.exit(main())
