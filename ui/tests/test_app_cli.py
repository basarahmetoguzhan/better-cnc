"""CLI and server entry points: `main()` and `serve()` in bettercnc/app.py.

docs/11-ui-implementation.md ("What is NOT covered — plainly", item 2) named
these as the untested part of the server: the request handlers run through a
real HTTP server in test_server_and_iniload.py, but the outer wiring did not.
That wiring is what the operator actually types (`make ui`, `make dump`: the
Makefile's `ui` and `dump` targets; ui/README.md, "Çalıştırma"), and two of its
promises matter beyond convenience:

* `--dump` must produce the same page the live server sends, so a dumped
  screen can be reviewed offline as a record of what the operator will see;
* the startup banner must say the UI drives a mock adapter and is NOT connected
  to the machine (app.serve's startup banner; the "Status" line at the top of
  docs/11-ui-implementation.md). An operator who believes a mock is live may
  trust numbers nothing measured.

No test here binds a socket. `serve()` gets a stub in place of
ThreadingHTTPServer, and run_main() always replaces `serve()` for the duration
of the call: with the test's _ServeRecorder if it installed one, otherwise
with a stub that fails at once ("main() started the server"). A regression in
main() therefore fails a test instead of hanging `make test` on a real socket.
Every patch is undone (addCleanup or finally), because Handler.session and
module attributes are process-wide and test_server_and_iniload.py relies on
them.

Production code is cited by symbol, not by line, so a citation cannot drift
onto unrelated code when app.py or render.py is edited.

Known defects are recorded with unittest.expectedFailure: the test states the
behaviour we want, the suite stays green, and when a fix lands the test turns
into an "unexpected success", which TestResult.wasSuccessful() counts as a
failed run since Python 3.4. The decorator therefore cannot outlive the bug.
"""

from __future__ import annotations

import collections
import contextlib
import inspect
import io
import json
import os
import re
import subprocess
import sys
import unittest
from html.parser import HTMLParser

from helpers import ALL_SCREENS, REPO_ROOT, UI_DIR, view

from bettercnc import app
from bettercnc import render as R
from bettercnc.adapter import MockAdapter
from bettercnc.profiles import PROFILES, load_profile

# Located independently of app.STATIC_DIR so that a wrong STATIC_DIR in app.py
# cannot make the "embeds the real files" checks compare a file with itself.
STATIC_DIR = os.path.join(UI_DIR, "bettercnc", "static")

#: What the banner must say, verbatim from app.serve. Turkish on purpose: the
#: operator console is Turkish, and "DEĞİL" is capitalised for emphasis there.
NOT_CONNECTED = "makineye bağlı DEĞİL"


def read_static(name: str) -> str:
    with open(os.path.join(STATIC_DIR, name), "r", encoding="utf-8") as fh:
        return fh.read()


Run = collections.namedtuple("Run", "exited code out err")


SERVER_STARTED = "main() started the server"


def _server_must_not_start(*args, **kwargs):
    """app.serve while run_main runs, unless the test installed a recorder."""
    raise AssertionError(SERVER_STARTED)


def run_main(argv):
    """Run app.main(argv) with stdout and stderr captured.

    `exited` is True when main() raised SystemExit (argparse errors, --help);
    `code` is then the SystemExit code, otherwise main()'s return value - the
    same number `sys.exit(main())` (app.py's `__main__` block) would hand the
    shell.

    app.serve is never the real one during the call: a test that expects the
    server installs a _ServeRecorder first, and for every other test serve()
    raises AssertionError(SERVER_STARTED) instead of binding 127.0.0.1:8080
    and blocking forever.
    """
    out, err = io.StringIO(), io.StringIO()
    original = app.serve
    if not isinstance(original, _ServeRecorder):
        app.serve = _server_must_not_start
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = app.main(argv)
                exited = False
            except SystemExit as exc:
                code = exc.code
                exited = True
    finally:
        app.serve = original
    return Run(exited, code, out.getvalue(), err.getvalue())


def data_screen(page: str) -> str:
    """The `data-screen` attribute of <main> (render.render_body)."""
    found = re.findall(r'<main class="main" data-screen="([^"]*)">', page)
    if len(found) != 1:
        raise AssertionError("expected one <main data-screen>, found {}".format(found))
    return found[0]


def client_vm(page: str):
    """The JSON the page hands app.js as window.__VM__ (render.render_page)."""
    m = re.search(r"<script>window\.__VM__=(.*?);</script>", page)
    if m is None:
        raise AssertionError("window.__VM__ assignment not found")
    return json.loads(m.group(1))


def active_nav_values(page: str):
    """data-value of every nav button marked active (render.render_nav)."""
    return re.findall(
        r'<button class="nav-item active" data-action="screen" data-value="([^"]*)"', page
    )


class _ServeRecorder(object):
    """Stands in for app.serve: records the arguments main() passes."""

    def __init__(self, test: unittest.TestCase):
        self.calls = []
        original = app.serve
        self._signature = inspect.signature(original)
        app.serve = self
        test.addCleanup(setattr, app, "serve", original)

    def __call__(self, *args, **kwargs):
        # Normalise positional/keyword so the test does not depend on how
        # main() happens to spell the call.
        bound = self._signature.bind(*args, **kwargs)
        bound.apply_defaults()
        self.calls.append(dict(bound.arguments))


# ---------------------------------------------------------------------------
# --dump
# ---------------------------------------------------------------------------


class TestDumpWritesOnePage(unittest.TestCase):
    """`make dump SCREEN=makine > /tmp/makine.html` (ui/README.md, "Çalıştırma")."""

    def setUp(self):
        self.css = read_static("app.css")
        self.js = read_static("app.js")
        # A blank static file would make every assertIn below pass vacuously.
        self.assertTrue(self.css.strip())
        self.assertTrue(self.js.strip())

    def test_every_screen_of_every_profile(self):
        """One complete page per (profile, screen), the real CSS and JS inlined,
        and a body identical to what the live server renders for that screen.

        Profiles come from the registry (profiles.PROFILES), not a literal list,
        so a profile added there but not accepted by the CLI fails here.
        """
        self.assertIn("marble", PROFILES)  # the placeholder profile is a real choice
        for key in sorted(PROFILES):
            label = load_profile(key).label
            for screen in ALL_SCREENS:
                with self.subTest(profile=key, screen=screen):
                    run = run_main(["--profile", key, "--dump", screen])
                    self.assertFalse(run.exited, run.err)
                    self.assertEqual(run.code, 0)
                    page = run.out

                    # exactly one document, nothing before or after it
                    self.assertTrue(page.startswith("<!doctype html>\n"))
                    self.assertTrue(page.endswith("</html>"))
                    self.assertEqual(page.count("<!doctype html>"), 1)
                    self.assertEqual(page.count("</html>"), 1)

                    self.assertIn("<style>{}</style>".format(self.css), page)
                    self.assertIn("<script>{}</script>".format(self.js), page)

                    # Same body as GET / and the 1 Hz poll would produce
                    # (Handler.do_GET, the "/" and "/api/render" branches),
                    # built here without app.py.
                    body = R.render_body(view(key, screen))
                    self.assertIn('<div id="app">{}</div>'.format(body), page)

                    self.assertIn("<title>better-cnc · {}</title>".format(R.e(label)), page)
                    self.assertEqual(data_screen(page), screen)
                    self.assertEqual(active_nav_values(page), [screen])
                    # app.js reads window.__VM__.screen at load to decide
                    # which screen to poll; a wrong value would switch
                    # screens on the first refresh.
                    self.assertEqual(client_vm(page), {"screen": screen})

    def test_inlined_static_files_cannot_close_their_own_element(self):
        """render_page pastes app.css and app.js raw into <style>/<script>
        (render.render_page) with no escaping. A literal `</script` in app.js or
        `</style` in app.css would end the element early and spill the rest of
        the file into the page as markup."""
        self.assertNotIn("</script", self.js.lower())
        self.assertNotIn("</style", self.css.lower())

    def test_dump_does_not_start_the_server(self):
        recorder = _ServeRecorder(self)
        run = run_main(["--dump", "makine"])
        self.assertEqual(run.code, 0)
        self.assertEqual(recorder.calls, [])


class TestDumpEntryPoint(unittest.TestCase):
    def test_python_dash_m_dump_exits_zero_with_the_page(self):
        """The exact command `make dump` runs (the Makefile's `dump` target),
        in a child process, so the `if __name__ == "__main__": sys.exit(main())`
        block at the end of app.py and the shell-visible exit status are what
        is checked.

        A child process is not seen by ui/tools/covreport.py's in-process
        tracer, so that block's sys.exit(main()) line stays "uncovered" there
        even though it runs here.
        """
        env = dict(os.environ)
        env["PYTHONPATH"] = os.pathsep.join(
            p for p in (UI_DIR, env.get("PYTHONPATH", "")) if p
        )
        proc = subprocess.run(
            [sys.executable, "-m", "bettercnc.app", "--dump", "makine"],
            cwd=REPO_ROOT,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=60,
        )
        err = proc.stderr.decode("utf-8", "replace")
        self.assertEqual(proc.returncode, 0, err)
        self.assertNotIn("Traceback", err)
        page = proc.stdout.decode("utf-8")  # Turkish text: must be valid UTF-8
        self.assertTrue(page.startswith("<!doctype html>\n"))
        self.assertTrue(page.endswith("</html>"))
        self.assertEqual(data_screen(page), "makine")
        self.assertIn("Makine", page)


class TestDumpBadScreenName(unittest.TestCase):
    """What `--dump` does with a name that is not in VM.SCREENS.

    The HTTP routes whitelist the screen and fall back to "kontrol"
    (app.py, Handler._screen_from_query and do_POST). `--dump` refuses the name
    instead (app.py main): a dumped page is reviewed later as a record of what
    the operator sees, so a typo such as `make dump SCREEN=makina` must not
    produce a page labelled with a screen the UI does not have. Before the fix
    it exited 0 with data-screen="makina", window.__VM__ saying "makina", no
    nav button active, and the Kontrol content underneath.
    """

    BOGUS = "makina"

    def run_refused(self, value):
        """Run `--dump <value>`; it must be a usage error with no page and no
        server. Returns the Run for further checks."""
        recorder = _ServeRecorder(self)
        run = run_main(["--dump", value])
        self.assertTrue(run.exited, "main() returned {!r} instead of a usage error".format(run.code))
        self.assertEqual(run.code, 2)  # argparse's usage-error status
        self.assertEqual(run.out, "")  # no page at all, mislabelled or otherwise
        self.assertEqual(recorder.calls, [])
        return run

    def test_unknown_screen_is_rejected(self):
        run = self.run_refused(self.BOGUS)
        self.assertIn("--dump", run.err)
        self.assertIn(repr(self.BOGUS), run.err)
        self.assertNotIn("Traceback", run.err)

    def test_rejection_lists_the_valid_screens(self):
        """The error names every screen, in VM.SCREENS order, so the operator
        can correct the typo without reading the source."""
        run = self.run_refused(self.BOGUS)
        self.assertIn("valid screens: {}".format(", ".join(ALL_SCREENS)), run.err)

    def test_screen_names_are_case_sensitive(self):
        """The routes compare exactly (app.py _screen_from_query), so --dump
        does too: "Makine" is the nav label, not a screen key."""
        self.run_refused("Makine")

    def test_dump_argument_cannot_inject_markup(self):
        """The screen name used to reach `<script>window.__VM__=...</script>`
        unchecked. Now it is refused before anything is rendered; render.py
        additionally escapes the inline JSON (TestInlineVmJson below)."""
        payload = "</script><script>window.pwned=1</script>"
        run = self.run_refused(payload)
        self.assertNotIn("<script>window.pwned", run.out)

    def test_empty_dump_argument_does_not_start_the_server(self):
        """`if args.dump:` was a truthiness test, so `--dump ""` counted as no
        --dump at all and main() started the blocking server on
        127.0.0.1:8080. `is not None` makes it a given-but-invalid screen."""
        self.run_refused("")

    def test_help_lists_the_screens(self):
        run = run_main(["--help"])
        self.assertTrue(run.exited)
        self.assertEqual(run.code, 0)
        help_text = " ".join(run.out.split())  # argparse wraps lines
        self.assertIn("one of: {}".format(", ".join(ALL_SCREENS)), help_text)


class TestRunMainNeverServes(unittest.TestCase):
    """The guard in run_main() itself: a main() that reaches serve() without a
    recorder installed must fail the test, not bind a port."""

    def setUp(self):
        # If the guard were missing, the real serve() would run: make that
        # fail loudly too instead of binding 127.0.0.1:8080 and blocking.
        class RealServeReached(Exception):
            pass

        self.RealServeReached = RealServeReached

        def no_bind(*args, **kwargs):
            raise RealServeReached("the real serve() was reached")

        self.addCleanup(setattr, app, "ThreadingHTTPServer", app.ThreadingHTTPServer)
        self.addCleanup(setattr, app.Handler, "session", app.Handler.session)
        app.ThreadingHTTPServer = no_bind

    def test_serve_without_a_recorder_fails_the_test(self):
        original = app.serve
        with self.assertRaises(AssertionError) as ctx:
            run_main([])
        self.assertEqual(str(ctx.exception), SERVER_STARTED)
        self.assertIs(app.serve, original)  # restored even though main() raised

    def test_a_recorder_installed_by_the_test_is_left_in_place(self):
        recorder = _ServeRecorder(self)
        run = run_main(["--port", "9091"])
        self.assertEqual(run.code, 0)
        self.assertEqual(len(recorder.calls), 1)
        self.assertEqual(recorder.calls[0]["port"], 9091)
        self.assertIs(app.serve, recorder)


class _ScriptBodies(HTMLParser):
    """Text of every <script> element, split where a browser-like parser
    splits it (html.parser ends a script at `</script>`)."""

    def __init__(self):
        HTMLParser.__init__(self, convert_charrefs=False)
        self.bodies = []
        self._open = False

    def handle_starttag(self, tag, attrs):
        if tag == "script":
            self._open = True
            self.bodies.append("")

    def handle_endtag(self, tag):
        if tag == "script":
            self._open = False

    def handle_data(self, data):
        if self._open:
            self.bodies[-1] += data

    @classmethod
    def of(cls, page):
        parser = cls()
        parser.feed(page)
        parser.close()
        return parser.bodies


class TestInlineVmJson(unittest.TestCase):
    """render_page's `window.__VM__=...` (render.py _script_json): defence in
    depth, so that no value - whatever reaches vm["screen"] in future - can end
    the script element or change how it is parsed."""

    #: "</" and "<!--" (HTML Standard 4.12.1.3), ">" and "&", the two JS line
    #: separators, and Turkish text that must survive unescaped-but-intact.
    CRAFTED = "ç</script><script>window.pwned=1</script><!--<script>&amp;-->\u2028\u2029"

    def page_for(self, screen):
        vm = view("test-rig", "kontrol")
        vm["screen"] = screen
        return R.render_page(vm, "/*css*/", "/*js*/")

    def vm_literal(self, page):
        bodies = _ScriptBodies.of(page)
        # exactly the two scripts render_page writes, nothing smuggled in
        self.assertEqual(len(bodies), 2, bodies)
        self.assertEqual(bodies[1], "/*js*/")
        prefix, suffix = "window.__VM__=", ";"
        self.assertTrue(bodies[0].startswith(prefix), bodies[0])
        self.assertTrue(bodies[0].endswith(suffix), bodies[0])
        return bodies[0][len(prefix):-len(suffix)]

    def test_crafted_value_cannot_close_the_script_element(self):
        literal = self.vm_literal(self.page_for(self.CRAFTED))
        self.assertNotIn("<script>window.pwned", self.page_for(self.CRAFTED))
        for raw in ("<", ">", "&", "\u2028", "\u2029"):
            with self.subTest(char=repr(raw)):
                self.assertNotIn(raw, literal)

    def test_the_escaped_value_is_the_same_string_to_javascript(self):
        """The escapes are JSON escapes, so the client still reads exactly the
        value the server meant (app.js reads window.__VM__.screen)."""
        literal = self.vm_literal(self.page_for(self.CRAFTED))
        self.assertEqual(json.loads(literal), {"screen": self.CRAFTED})
        self.assertIn("ç", literal)  # UTF-8 page: no need to \\u-escape Turkish

    def test_real_screens_are_inlined_unchanged(self):
        for screen in ALL_SCREENS:
            with self.subTest(screen=screen):
                literal = self.vm_literal(self.page_for(screen))
                self.assertEqual(literal, json.dumps({"screen": screen}))


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------


class TestArguments(unittest.TestCase):
    def test_unknown_profile_is_a_usage_error_not_a_traceback(self):
        """argparse must stop it (exit 2) before build_session would raise a
        bare KeyError (profiles.load_profile) - and before any server starts."""
        recorder = _ServeRecorder(self)
        for argv in (["--profile", "nope"], ["--profile", "nope", "--dump", "kontrol"]):
            with self.subTest(argv=argv):
                run = run_main(argv)
                self.assertTrue(run.exited)
                self.assertEqual(run.code, 2)
                self.assertIn("invalid choice", run.err)
                self.assertEqual(run.out, "")
        self.assertEqual(recorder.calls, [])

    def test_cli_profile_choices_match_the_profile_registry(self):
        """The CLI keeps its own literal tuple (main, the --profile choices).
        A CLI choice with no profile behind it would crash with KeyError; a
        registered profile missing from the CLI would be unreachable from
        `make ui`."""
        run = run_main(["--help"])
        self.assertTrue(run.exited)
        self.assertEqual(run.code, 0)
        m = re.search(r"--profile \{([^}]*)\}", run.out)
        self.assertIsNotNone(m, run.out)
        self.assertEqual(set(m.group(1).split(",")), set(PROFILES))

    def test_non_numeric_port_is_a_usage_error(self):
        recorder = _ServeRecorder(self)
        run = run_main(["--port", "http"])
        self.assertTrue(run.exited)
        self.assertEqual(run.code, 2)
        self.assertEqual(recorder.calls, [])


class TestMainHandsOffToServe(unittest.TestCase):
    def test_defaults(self):
        """Without flags: test-rig on loopback port 8080, which is what
        ui/README.md ("Çalıştırma", `make ui`) tells the operator to open
        (http://127.0.0.1:8080/)."""
        recorder = _ServeRecorder(self)
        run = run_main([])
        self.assertFalse(run.exited, run.err)
        self.assertEqual(run.code, 0)
        self.assertEqual(
            recorder.calls, [{"profile": "test-rig", "host": "127.0.0.1", "port": 8080}]
        )

    def test_flags_reach_serve_and_the_port_is_an_int(self):
        """A string port would only fail later, inside the socket bind."""
        recorder = _ServeRecorder(self)
        run = run_main(["--profile", "marble", "--host", "0.0.0.0", "--port", "9090"])
        self.assertEqual(run.code, 0)
        self.assertEqual(
            recorder.calls, [{"profile": "marble", "host": "0.0.0.0", "port": 9090}]
        )
        self.assertIsInstance(recorder.calls[0]["port"], int)


# ---------------------------------------------------------------------------
# serve()
# ---------------------------------------------------------------------------


class TestServe(unittest.TestCase):
    """serve() with ThreadingHTTPServer replaced by a stub that never binds.

    The stub's serve_forever raises immediately, standing in for Ctrl-C (or a
    crash) at some point after startup.
    """

    def setUp(self):
        self.record = {"constructed": [], "served": 0, "closed": 0, "stderr_at_serve": None}
        self.out = io.StringIO()
        self.err = io.StringIO()
        self.addCleanup(setattr, app, "ThreadingHTTPServer", app.ThreadingHTTPServer)
        self.addCleanup(setattr, app.Handler, "session", app.Handler.session)

    def install_stub(self, raised: BaseException) -> None:
        record, err = self.record, self.err

        class StubServer(object):
            def __init__(self, address, handler):
                record["constructed"].append((address, handler))

            def serve_forever(self):
                record["served"] += 1
                record["stderr_at_serve"] = err.getvalue()
                raise raised

            def server_close(self):
                record["closed"] += 1

        app.ThreadingHTTPServer = StubServer

    def serve(self, *args):
        """Call app.serve; a KeyboardInterrupt escaping it fails this test
        instead of aborting the whole unittest run."""
        with contextlib.redirect_stdout(self.out), contextlib.redirect_stderr(self.err):
            try:
                return app.serve(*args)
            except KeyboardInterrupt:
                self.fail("KeyboardInterrupt escaped serve()")

    def test_ctrl_c_closes_the_server_and_returns_normally(self):
        """Ctrl-C ends `make ui` with exit 0 and no traceback, and the
        listening socket is released (serve's try/except/finally)."""
        self.install_stub(KeyboardInterrupt())
        self.assertIsNone(self.serve("test-rig", "127.0.0.1", 8080))
        self.assertEqual(self.record["served"], 1)
        self.assertEqual(self.record["closed"], 1)
        self.assertEqual(self.out.getvalue(), "")

    def test_any_other_error_still_closes_the_server_and_propagates(self):
        """Only Ctrl-C is swallowed. A real failure must surface, and the
        socket must still be closed on the way out (serve's `finally`)."""
        self.install_stub(RuntimeError("boom"))
        with contextlib.redirect_stdout(self.out), contextlib.redirect_stderr(self.err):
            with self.assertRaises(RuntimeError):
                app.serve("test-rig", "127.0.0.1", 8080)
        self.assertEqual(self.record["closed"], 1)

    def test_server_gets_the_requested_address_and_a_session_for_the_profile(self):
        self.install_stub(KeyboardInterrupt())
        self.serve("marble", "127.0.0.1", 18080)
        self.assertEqual(self.record["constructed"], [(("127.0.0.1", 18080), app.Handler)])
        self.assertEqual(app.Handler.session.profile.key, "marble")

    def test_banner_is_on_stderr_before_the_server_blocks(self):
        """serve_forever never returns while the UI is up, so the banner has to
        be written before it - or the operator only sees it after Ctrl-C."""
        self.install_stub(KeyboardInterrupt())
        self.serve("marble", "127.0.0.1", 18080)
        banner = self.record["stderr_at_serve"]
        self.assertIsNotNone(banner)
        self.assertIn("profil=marble", banner)
        self.assertIn("http://127.0.0.1:18080/", banner)
        self.assertIn("mock adapter", banner)
        self.assertIn(NOT_CONNECTED, banner)
        self.assertEqual(self.out.getvalue(), "")

    def test_banner_claim_matches_the_adapter_actually_in_use(self):
        """build_session's comment invites swapping MockAdapter for
        LinuxCncAdapter. If that swap lands without touching serve's banner,
        the operator is told "not connected" while commands reach the machine.
        The banner may only say so while the session really holds a
        MockAdapter."""
        self.install_stub(KeyboardInterrupt())
        self.serve("test-rig", "127.0.0.1", 18080)
        banner = self.record["stderr_at_serve"] or ""
        if NOT_CONNECTED in banner or "mock adapter" in banner:
            self.assertIsInstance(app.Handler.session.adapter, MockAdapter)


# ---------------------------------------------------------------------------
# Citations
# ---------------------------------------------------------------------------


class TestCitationsBySymbol(unittest.TestCase):
    """Round 2 found line citations into production files pointing at
    unrelated code after an edit. In the server's files, production code is
    therefore cited by symbol; file:line stays for LinuxCNC under reference/,
    which does not move."""

    FILES = (
        os.path.join(UI_DIR, "bettercnc", "app.py"),
        os.path.join(UI_DIR, "bettercnc", "render.py"),
        os.path.join(UI_DIR, "tests", "test_app_cli.py"),
        os.path.join(UI_DIR, "tests", "test_server_and_iniload.py"),
    )

    def production_names(self):
        pkg = os.path.join(UI_DIR, "bettercnc")
        names = {n for n in os.listdir(pkg) if n.endswith(".py")}
        names.update(os.listdir(os.path.join(pkg, "static")))
        names.add("Makefile")
        self.assertIn("app.py", names)  # the listing found the real package
        return names

    def test_no_file_line_citation_into_production_code(self):
        names = "|".join(re.escape(n) for n in sorted(self.production_names()))
        # Not preceded by a word character or a dot, so a longer file name
        # that merely ends the same way ("myapp.py") does not match.
        cite = re.compile(r"(?<![\w.])(?:{}):\d".format(names))
        # The pattern is live, and the "file.py:<digit>" probes are built by
        # concatenation so that this test's own source does not match it.
        for hit in ("see ui/bettercnc/app.py" + ":12", "render.py" + ":3-5", "app.js" + ":10"):
            self.assertIsNotNone(cite.search(hit), hit)
        for miss in ("myapp.py" + ":12", "emcmodule.cc" + ":233", "app.py, Session.act"):
            self.assertIsNone(cite.search(miss), miss)
        found = []
        for path in self.FILES:
            with open(path, "r", encoding="utf-8") as fh:
                for lineno, line in enumerate(fh, 1):
                    if cite.search(line):
                        found.append("{}:{}: {}".format(
                            os.path.basename(path), lineno, line.strip()))
        self.assertEqual(found, [])


if __name__ == "__main__":
    unittest.main()
