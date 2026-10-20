"""Server routes, session wiring, and the INI loader's edge cases."""

from __future__ import annotations

import ast
import http.client
import inspect
import json
import math
import re
import socket
import textwrap
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer

from helpers import ALL_SCREENS

from bettercnc import render as R
from bettercnc import viewmodel as VM
from bettercnc.adapter import LinuxCncAdapter, MockAdapter
from bettercnc.app import (
    MAX_ACTION_BODY,
    REQUEST_TIMEOUT_S,
    Handler,
    Session,
    build_session,
)
from bettercnc.configmodel import load_machine_config
from bettercnc.iniload import parse_ini_text
from bettercnc.profiles import load_profile
from bettercnc.state import DrivePower

#: Bound for every wait on another thread in this file. Only reached when the
#: behaviour under test is broken; a passing run never waits on it.
WAIT_S = 5.0


class TestSession(unittest.TestCase):
    def test_build_session_validates_the_profile(self):
        sess = build_session("test-rig")
        self.assertEqual(sess.profile.key, "test-rig")
        self.assertTrue(sess.config.present)

    def test_marble_session_builds_without_a_config(self):
        sess = build_session("marble")
        self.assertFalse(sess.config.present)

    def test_unknown_profile_raises(self):
        with self.assertRaises(KeyError):
            build_session("nope")

    def test_view_renders_every_screen(self):
        sess = build_session("test-rig")
        for screen in ("kontrol", "program", "probing", "takimlar", "alarmlar", "makine"):
            with self.subTest(screen=screen):
                self.assertEqual(sess.view(screen)["screen"], screen)

    def test_unknown_action_is_ignored_not_raised(self):
        """A stale browser must not be able to 500 the controller UI."""
        sess = build_session("test-rig")
        sess.act("definitely-not-an-action", "x")
        sess.act("", "")
        self.assertIsNotNone(sess.view("kontrol"))

    def test_malformed_jog_value_is_ignored(self):
        sess = build_session("test-rig")
        for bad in ("", "X", "X:", ":1", "Q:1", "X:notanint"):
            with self.subTest(value=bad):
                sess.act("jog", bad)
        self.assertIsNotNone(sess.view("kontrol"))

    def test_malformed_numeric_actions_are_ignored(self):
        sess = build_session("test-rig")
        sess.act("jog_step", "abc")
        sess.act("feed", "abc")
        self.assertIsNotNone(sess.view("kontrol"))

    def test_actions_change_state(self):
        sess = build_session("test-rig")
        self.assertTrue(sess.view("kontrol")["kontrol"]["estop"]["software_disabled"])
        sess.act("software_reset", "")
        self.assertFalse(sess.view("kontrol")["kontrol"]["estop"]["software_disabled"])
        sess.act("home_all", "")
        self.assertTrue(sess.view("kontrol")["kontrol"]["homing"]["all_homed"])

    def test_jog_direction_must_be_exactly_plus_or_minus_one(self):
        """The jog pad sends "<axis>:1" or "<axis>:-1" (render.py render_jog).
        Any other number used to reach MockAdapter.jog, which moved that many
        steps while reporting one ("X:5"), or reported a move it did not make
        ("X:0" -> "Jog X-1 mm")."""
        sess = build_session("test-rig")
        sess.act("software_reset", "")  # enabled, so a jog would really move
        before = sess.adapter.snapshot()
        for bad in ("X:5", "X:0", "X:-2", "X:+10", "X:1.0", "X:2-1"):
            with self.subTest(value=bad):
                sess.act("jog", bad)
                after = sess.adapter.snapshot()
                self.assertEqual(after.positions, before.positions)
                self.assertEqual(after.message, before.message)
        # Control: the same session does jog for the two valid values, by
        # one step each way, so the checks above are not vacuous.
        step = before.jog_step_mm
        x0 = [p.machine for p in before.positions if p.axis == "X"][0]
        sess.act("jog", "X:1")
        x1 = [p.machine for p in sess.adapter.snapshot().positions if p.axis == "X"][0]
        self.assertAlmostEqual(x1 - x0, step)
        sess.act("jog", "X:-1")
        x2 = [p.machine for p in sess.adapter.snapshot().positions if p.axis == "X"][0]
        self.assertAlmostEqual(x2, x0)

    def test_jog_reaches_the_adapter_only_as_axis_and_unit_direction(self):
        calls = []

        class Recorder(object):
            def jog(self, axis, direction):
                calls.append((axis, direction))

        sess = _session_with(Recorder())
        for value in ("X:1", "Y:-1", "Z:+1", "X: -1 ", "X:5", "X:", ":1", "X", "X:-0"):
            sess.act("jog", value)
        self.assertEqual(calls, [("X", 1), ("Y", -1), ("Z", 1), ("X", -1)])
        for _, direction in calls:
            self.assertIs(type(direction), int)

    def test_adapter_key_error_for_an_unknown_axis_is_contained(self):
        """LinuxCncAdapter.jog raises KeyError for an axis the profile lacks
        (adapter.py _joint_for_axis); a stale page must not turn that into a
        dropped connection."""

        class NoSuchAxis(object):
            def jog(self, axis, direction):
                raise KeyError(axis)

        _session_with(NoSuchAxis()).act("jog", "Q:1")


def _session_with(adapter, profile_key="test-rig"):
    profile = load_profile(profile_key)
    return Session(adapter, profile, load_machine_config(profile))


class _LockProbe(object):
    """Adapter stand-in that records, for each call, whether Session._lock was
    held at that moment. Single-threaded, so `locked()` can only mean the
    Session took it for this call."""

    def __init__(self):
        self.session = None
        self.calls = []

    def _record(self, name):
        self.calls.append((name, self.session._lock.locked()))

    def software_estop(self):
        self._record("software_estop")

    def software_estop_reset(self):
        self._record("software_estop_reset")

    def home_all(self):
        self._record("home_all")

    def jog(self, axis, direction):
        self._record("jog")

    def send_mdi(self, command):
        self._record("send_mdi")

    def program_start(self):
        self._record("program_start")

    def program_stop(self):
        self._record("program_stop")

    def set_jog_step(self, mm):
        self._record("set_jog_step")

    def set_feed_override(self, pct):
        self._record("set_feed_override")

    def set_wcs(self, wcs):
        self._record("set_wcs")


class _BlockingJog(object):
    """jog() parks on an Event, standing in for a LinuxCncAdapter call stuck
    in command.wait_complete() while Session._lock is held. Event.wait
    releases the GIL; the real wait_complete does not (see Session.act), so
    this models the lock queue only - which is what Session controls."""

    def __init__(self):
        self.events = []  # list.append is atomic under the GIL
        self.jog_entered = threading.Event()
        self.release_jog = threading.Event()

    def jog(self, axis, direction):
        self.events.append("jog:start")
        self.jog_entered.set()
        self.release_jog.wait(WAIT_S)
        self.events.append("jog:end")

    def software_estop(self):
        self.events.append("estop")


class TestSessionLocking(unittest.TestCase):
    """Session.act serialises adapter calls under one lock - except the
    software estop. On the test rig the GUI button is the only estop
    (configs/test-rig/test-rig.hal, "ESTOP CHAIN - GUI ONLY. THERE IS NO
    HARDWARE ESTOP INPUT."), so it must not queue behind an action that is
    blocked inside the adapter."""

    #: Every action Session.act dispatches under the lock, a value it accepts,
    #: and the adapter method it must reach.
    LOCKED = (
        ("software_reset", "", "software_estop_reset"),
        ("home_all", "", "home_all"),
        ("jog", "X:1", "jog"),
        ("mdi", "G0 X1", "send_mdi"),
        ("mdi_preset", "G0 X0 Y0", "send_mdi"),
        ("program_start", "", "program_start"),
        ("program_stop", "", "program_stop"),
        ("jog_step", "0.1", "set_jog_step"),
        ("feed", "100", "set_feed_override"),
        ("wcs", "G55", "set_wcs"),
    )

    def test_software_estop_does_not_wait_behind_a_blocked_jog(self):
        adapter = _BlockingJog()
        sess = _session_with(adapter)
        jog = threading.Thread(target=sess.act, args=("jog", "X:1"), daemon=True)
        self.addCleanup(jog.join, WAIT_S)  # cleanups run last-in first-out:
        self.addCleanup(adapter.release_jog.set)  # release, then join
        jog.start()
        self.assertTrue(adapter.jog_entered.wait(WAIT_S), "the jog never reached the adapter")
        self.assertTrue(sess._lock.locked(), "the blocked jog must hold the session lock")

        # From this thread. Under the lock this would return only after the
        # jog's bounded wait ran out, with "jog:end" recorded before "estop".
        sess.act("software_estop", "")

        self.assertFalse(adapter.release_jog.is_set())
        self.assertEqual(adapter.events, ["jog:start", "estop"])
        self.assertTrue(sess._lock.locked(), "the estop must not have disturbed the jog's lock")

        adapter.release_jog.set()
        jog.join(WAIT_S)
        self.assertFalse(jog.is_alive())
        self.assertEqual(adapter.events, ["jog:start", "estop", "jog:end"])
        self.assertFalse(sess._lock.locked())

    def test_software_estop_is_called_without_the_lock(self):
        probe = _LockProbe()
        probe.session = _session_with(probe)
        probe.session.act("software_estop", "")
        self.assertEqual(probe.calls, [("software_estop", False)])

    def test_every_other_action_runs_under_the_lock(self):
        for action, value, method in self.LOCKED:
            with self.subTest(action=action):
                probe = _LockProbe()
                probe.session = _session_with(probe)
                probe.session.act(action, value)
                self.assertEqual(probe.calls, [(method, True)])
                self.assertFalse(probe.session._lock.locked())  # released after

    def test_the_action_list_above_is_every_action_session_act_knows(self):
        """Read the action names out of Session.act's source, so an action
        added later cannot skip the lock check by being missing from LOCKED."""
        tree = ast.parse(textwrap.dedent(inspect.getsource(Session.act)))
        names = set()
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Compare) and isinstance(node.left, ast.Name)):
                continue
            if node.left.id != "action":
                continue
            for comp in node.comparators:
                if isinstance(comp, ast.Constant) and isinstance(comp.value, str):
                    names.add(comp.value)
                elif isinstance(comp, (ast.Tuple, ast.List, ast.Set)):
                    names.update(
                        e.value for e in comp.elts
                        if isinstance(e, ast.Constant) and isinstance(e.value, str)
                    )
        self.assertIn("software_estop", names)  # the parse found something real
        self.assertEqual(names, {a for a, _, _ in self.LOCKED} | {"software_estop"})


class TestSessionActNotes(unittest.TestCase):
    """Session.act's docstring cites code by symbol rather than by line, so it
    cannot drift onto unrelated lines. These fail when a cited symbol goes
    away or a note stops being true."""

    def doc(self):
        return " ".join(Session.act.__doc__.split())

    def test_the_bound_it_names_exists_and_is_shorter_than_the_binding_default(self):
        from bettercnc import adapter as A

        self.assertIn("adapter.WAIT_TIMEOUT_S", self.doc())
        # The binding's own default is EMC_COMMAND_TIMEOUT = 5 s
        # (emcmodule.cc:209); a "bound" at or above it bounds nothing.
        self.assertGreater(A.WAIT_TIMEOUT_S, 0)
        self.assertLess(A.WAIT_TIMEOUT_S, 5.0)

    def test_load_program_exists_on_both_adapters_but_no_action_calls_it(self):
        """When load_program gets an action this fails, and the "Not wired
        yet" note has to go with it."""
        for cls in (MockAdapter, LinuxCncAdapter):
            self.assertTrue(callable(getattr(cls, "load_program", None)), cls.__name__)
        tree = ast.parse(textwrap.dedent(inspect.getsource(Session.act)))
        called = {
            n.func.attr for n in ast.walk(tree)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        }
        self.assertIn("home_all", called)  # the walk does see adapter calls
        self.assertNotIn("load_program", called)
        self.assertIn("Not wired yet: load_program", self.doc())
        profile = load_profile("test-rig")
        program = VM.program_screen(
            profile, load_machine_config(profile), MockAdapter(profile).snapshot()
        )
        self.assertEqual(program["files"], [])


class _FixedSnapshot(object):
    """Adapter stand-in whose snapshot() is a given MachineState."""

    def __init__(self, state):
        self.state = state

    def snapshot(self):
        return self.state


class TestStatusUnknownDro(unittest.TestCase):
    """The Konum card while LinuxCNC's status is unknown
    (MachineState.status_known False): viewmodel.control_screen hands
    render.render_dro "—" for the positions and None for `homed`. The homed
    column used to read None as False and print "ref yok", a homing reading
    nobody took. The state is hand-built with positions kept, so the rows are
    rendered at all; LinuxCncAdapter drops them when the status is lost."""

    DRO_HOME = re.compile(r'<span class="dro-home" style="color:([^"]*)">([^<]*)</span>')
    DRO_NUMBERS = re.compile(r'<span class="dro-(?:work|mach)">([^<]*)</span>')

    def body(self, status_known):
        profile = load_profile("test-rig")
        state = MockAdapter(profile).snapshot()._replace(status_known=status_known)
        self.assertTrue(state.positions)  # rows to check
        self.assertFalse(state.homing.all_homed)  # the mock starts unhomed
        return R.render_body(_session_with(_FixedSnapshot(state)).view("kontrol"))

    def test_unknown_status_shows_no_homing_reading(self):
        body = self.body(status_known=False)
        homes = self.DRO_HOME.findall(body)
        self.assertEqual(len(homes), 3, body)
        self.assertEqual({text for _, text in homes}, {"—"})
        self.assertEqual({colour for colour, _ in homes}, {R.MUTED})
        self.assertNotIn("ref yok", body)
        self.assertEqual(set(self.DRO_NUMBERS.findall(body)), {"—"})

    def test_known_status_still_shows_ref_yok(self):
        """Control: the same unhomed state with a readable status."""
        homes = self.DRO_HOME.findall(self.body(status_known=True))
        self.assertEqual(homes, [(R.WARN, "ref yok")] * 3)

    def test_homed_axis_reads_ref(self):
        profile = load_profile("test-rig")
        adapter = MockAdapter(profile)
        adapter.software_estop_reset()
        adapter.home_all()
        state = adapter.snapshot()
        self.assertTrue(state.homing.all_homed)
        body = R.render_body(_session_with(_FixedSnapshot(state)).view("kontrol"))
        self.assertEqual(self.DRO_HOME.findall(body), [(R.OK, "ref")] * 3)


class TestHttpRoutes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Handler.session = build_session("test-rig")
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(timeout=5)

    def url(self, path: str) -> str:
        return "http://127.0.0.1:{}{}".format(self.port, path)

    def get(self, path: str):
        with urllib.request.urlopen(self.url(path), timeout=5) as r:
            return r.status, r.read().decode("utf-8")

    def post(self, path: str, payload: dict):
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            self.url(path), data=data, headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read().decode("utf-8"))

    def test_index_serves_a_full_page(self):
        status, body = self.get("/")
        self.assertEqual(status, 200)
        self.assertIn("<!doctype html>", body)
        self.assertIn("hm2_7i92.0", body)
        self.assertIn("--acc:", body)  # inlined stylesheet

    def test_index_includes_the_client_script(self):
        _, body = self.get("/")
        self.assertIn("api/action", body)

    def test_healthz(self):
        status, body = self.get("/healthz")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["ok"], True)

    def test_api_render_returns_html(self):
        status, body = self.get("/api/render?screen=makine")
        payload = json.loads(body)
        self.assertEqual(status, 200)
        self.assertEqual(payload["screen"], "makine")
        self.assertIn("833.300", payload["html"])

    def test_api_render_rejects_an_unknown_screen(self):
        _, body = self.get("/api/render?screen=../../etc/passwd")
        self.assertEqual(json.loads(body)["screen"], "kontrol")

    def test_api_state_returns_the_view_model(self):
        _, body = self.get("/api/state?screen=kontrol")
        vm = json.loads(body)
        self.assertIn("kontrol", vm)
        self.assertIn("link", vm)

    def test_unknown_path_404s(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.get("/nope")
        self.assertEqual(ctx.exception.code, 404)

    def test_post_to_wrong_path_404s(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.post("/api/nope", {})
        self.assertEqual(ctx.exception.code, 404)

    def test_action_round_trip(self):
        status, payload = self.post(
            "/api/action", {"action": "software_reset", "value": "", "screen": "kontrol"}
        )
        self.assertEqual(status, 200)
        self.assertIn("html", payload)

    def test_malformed_json_does_not_crash(self):
        req = urllib.request.Request(
            self.url("/api/action"),
            data=b"{not json",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=5) as r:
            self.assertEqual(r.status, 200)

    def raw_post(self, content_length=None, body=b""):
        """POST /api/action with full control over Content-Length. The client
        timeout bounds a server that waits for bytes that never come."""
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=WAIT_S)
        try:
            conn.putrequest("POST", "/api/action")
            conn.putheader("Content-Type", "application/json")
            if content_length is not None:
                conn.putheader("Content-Length", content_length)
            conn.endheaders(body or None)
            resp = conn.getresponse()
            return resp.status, resp.read()
        finally:
            conn.close()

    def test_json_that_is_not_an_object_is_no_action(self):
        """Valid JSON that is not an object used to raise AttributeError in
        do_POST (payload.get): no response, a traceback on the controller
        console. It is now handled like malformed JSON - no action, re-render."""
        for body in (b"[]", b"null", b'"home_all"', b"1", b'["home_all"]', b"true"):
            with self.subTest(body=body):
                status, raw = self.raw_post(str(len(body)), body)
                self.assertEqual(status, 200)
                payload = json.loads(raw.decode("utf-8"))
                self.assertEqual(payload["screen"], "kontrol")
                self.assertIn("html", payload)

    def test_bad_content_length_is_400_without_reading_the_body(self):
        """Before: "abc" and "-5" raised (no response at all) and "-1" made
        rfile.read() wait for end-of-stream, i.e. until the client gave up.
        No body is sent here, so a server that tried to read would hit the
        client timeout instead of answering."""
        for bad in ("abc", "-1", "-5", "1.5", "0x10"):
            with self.subTest(content_length=bad):
                status, raw = self.raw_post(bad)
                self.assertEqual(status, 400)
                self.assertIn(b"Content-Length", raw)

    def test_oversized_body_is_refused_before_it_is_read(self):
        status, _ = self.raw_post(str(MAX_ACTION_BODY + 1))
        self.assertEqual(status, 413)

    def test_body_of_exactly_the_limit_is_accepted(self):
        head = b'{"action": "", "value": "'
        tail = b'"}'
        body = head + b"x" * (MAX_ACTION_BODY - len(head) - len(tail)) + tail
        self.assertEqual(len(body), MAX_ACTION_BODY)
        status, raw = self.raw_post(str(len(body)), body)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(raw.decode("utf-8"))["screen"], "kontrol")

    def test_garbage_requests_change_nothing_and_the_server_keeps_answering(self):
        before = Handler.session.adapter.snapshot()
        self.raw_post("abc")
        self.raw_post(str(MAX_ACTION_BODY + 1))
        self.raw_post("2", b"[]")
        after = Handler.session.adapter.snapshot()
        self.assertEqual(after.estop, before.estop)
        self.assertEqual(after.homing, before.homing)
        self.assertEqual(after.mdi_history, before.mdi_history)
        status, body = self.get("/healthz")
        self.assertEqual(status, 200)
        self.assertIs(json.loads(body)["ok"], True)

    def test_no_route_can_clear_a_hardware_estop(self):
        Handler.session.adapter.hardware_chain_open()
        for action in ("software_reset", "home_all", "program_start", "wcs"):
            with self.subTest(action=action):
                self.post("/api/action", {"action": action, "value": "G55"})
                self.assertIs(
                    Handler.session.adapter.snapshot().estop.drive_power,
                    DrivePower.CUT,
                )

    # -- screen name ---------------------------------------------------------

    def test_action_screen_is_validated_like_get(self):
        """POST /api/action falls back to "kontrol" for a screen not in
        VM.SCREENS, exactly as GET /api/render does. Round 2's mutant AP6
        dropped the POST check and no test noticed: VM.build then echoes the
        name into the JSON "screen", the nav and <main data-screen>."""
        bogus = ("nope", "Makine", " kontrol", "../../etc/passwd", '"><b>x</b>', "")
        for name in bogus + tuple(ALL_SCREENS):
            expected = name if name in ALL_SCREENS else "kontrol"
            with self.subTest(screen=name):
                _, posted = self.post(
                    "/api/action", {"action": "", "value": "", "screen": name}
                )
                query = urllib.parse.urlencode({"screen": name})
                _, raw = self.get("/api/render?" + query)
                for route, payload in (("POST", posted), ("GET", json.loads(raw))):
                    self.assertEqual(payload["screen"], expected, route)
                    self.assertIn(
                        '<main class="main" data-screen="{}">'.format(expected),
                        payload["html"],
                        route,
                    )
                    self.assertIn(
                        'class="nav-item active" data-action="screen" '
                        'data-value="{}"'.format(expected),
                        payload["html"],
                        route,
                    )

    def test_action_screen_that_is_not_a_string_is_kontrol(self):
        for odd in (5, None, True, ["makine"], {"screen": "makine"}):
            with self.subTest(screen=odd):
                _, payload = self.post("/api/action", {"action": "", "screen": odd})
                self.assertEqual(payload["screen"], "kontrol")

    # -- socket timeout ------------------------------------------------------

    def exchange(self, data):
        """Send `data` on a fresh connection, send nothing more, and return
        every byte the server writes before it closes the connection. The
        client timeout (WAIT_S) bounds the wait, so a server that never
        closes fails the test instead of hanging it."""
        sock = socket.create_connection(("127.0.0.1", self.port), timeout=WAIT_S)
        try:
            sock.sendall(data)
            received = []
            while True:
                try:
                    chunk = sock.recv(65536)
                except ConnectionResetError:
                    break  # closed with a reset rather than a FIN: still closed
                except socket.timeout:
                    self.fail("the server held the connection for {} s".format(WAIT_S))
                if not chunk:
                    break
                received.append(chunk)
            return b"".join(received)
        finally:
            sock.close()

    @staticmethod
    def action_request(content_length, body):
        return (
            "POST /api/action HTTP/1.0\r\n"
            "Content-Type: application/json\r\n"
            "Content-Length: {}\r\n\r\n".format(content_length)
        ).encode("ascii") + body

    def serve_with(self, handler_class):
        """Serve the rest of this test with `handler_class` on the same
        server; finish_request reads RequestHandlerClass per connection."""
        self.addCleanup(setattr, self.httpd, "RequestHandlerClass", Handler)
        self.httpd.RequestHandlerClass = handler_class

    def test_every_request_socket_gets_the_request_timeout(self):
        """Handler.timeout is what socketserver puts on the accepted socket
        (StreamRequestHandler.setup). Checked on a real request through a
        subclass that only records it, because the stall test below sets its
        own timeout and so cannot see Handler's going missing."""
        # Finite and positive is the point: a stalled client is let go in
        # seconds. The exact 10 s is argued at REQUEST_TIMEOUT_S.
        self.assertTrue(math.isfinite(REQUEST_TIMEOUT_S))
        self.assertGreater(REQUEST_TIMEOUT_S, 0)
        self.assertLessEqual(REQUEST_TIMEOUT_S, 60)
        applied = []

        class Recorder(Handler):
            def setup(self):
                Handler.setup(self)
                applied.append(self.connection.gettimeout())

        self.serve_with(Recorder)
        status, _ = self.get("/healthz")
        self.assertEqual(status, 200)
        self.assertEqual(applied, [REQUEST_TIMEOUT_S])

    def test_a_stalled_request_is_closed_once_the_timeout_runs_out(self):
        """A client that stops sending mid-request is cut off when the
        handler's timeout runs out: no response, and the action it was
        carrying is not applied. Before, it parked its handler thread until it
        hung up. The server's timeout is shrunk to 0.2 s here; the client's
        WAIT_S bounds the test if the server never closes."""
        sess = build_session("test-rig")
        sess.act("software_reset", "")  # ON, so a home_all that got in would home
        self.addCleanup(setattr, Handler, "session", Handler.session)
        Handler.session = sess
        logged = []

        class ShortTimeout(Handler):
            timeout = 0.2

            def log_error(self, fmt, *args):
                logged.append(fmt % args)

        self.serve_with(ShortTimeout)
        body = b'{"action": "home_all", "value": "", "screen": "kontrol"}'
        stalls = (
            ("request line, then nothing", b"POST /api/action HTTP/1.0\r\n"),
            # Valid JSON on its own, but 10 bytes short of what was declared.
            ("headers, then a short body", self.action_request(len(body) + 10, body)),
        )
        for label, data in stalls:
            with self.subTest(stall=label):
                del logged[:]
                self.assertEqual(self.exchange(data), b"")
                # handle_one_request logs this just before it closes, so the
                # close came from the timeout and not from an exception.
                self.assertEqual(len(logged), 1, logged)
                self.assertIn("timed out", logged[0])
                self.assertFalse(sess.view("kontrol")["kontrol"]["homing"]["all_homed"])

        # Control, on the normal handler: the same body with its true length
        # is answered and does home, so the checks above failed for the stall.
        self.httpd.RequestHandlerClass = Handler
        reply = self.exchange(self.action_request(len(body), body))
        self.assertTrue(reply.startswith(b"HTTP/1.0 200 "), reply[:60])
        self.assertTrue(sess.view("kontrol")["kontrol"]["homing"]["all_homed"])


class TestIniLoaderEdgeCases(unittest.TestCase):
    def test_empty_text(self):
        ini = parse_ini_text("")
        self.assertEqual(len(ini), 0)

    def test_comment_only(self):
        self.assertEqual(len(parse_ini_text("# nothing here\n")), 0)

    def test_semicolon_comments(self):
        ini = parse_ini_text("[S]\n; ASSUMPTION: guessed\nK = 1\n")
        self.assertTrue(ini.require("S", "K").provisional)

    def test_blank_line_ends_a_comment_block(self):
        ini = parse_ini_text("[S]\n# ASSUMPTION: x\n\nK = 1\n")
        # the blank line clears the pending block, so K inherits nothing new
        self.assertFalse(ini.require("S", "K").provisional)

    def test_section_change_resets_governing_comment(self):
        ini = parse_ini_text(
            "[A]\n# ASSUMPTION: guessed\nK = 1\n[B]\nK = 2\n"
        )
        self.assertTrue(ini.require("A", "K").provisional)
        self.assertFalse(ini.require("B", "K").provisional)

    def test_require_raises_with_a_useful_message(self):
        ini = parse_ini_text("[S]\nK = 1\n")
        with self.assertRaises(KeyError) as ctx:
            ini.require("S", "MISSING")
        self.assertIn("refuses to invent", str(ctx.exception))

    def test_get_returns_none_for_missing(self):
        self.assertIsNone(parse_ini_text("[S]\nK=1\n").get("S", "X"))

    def test_contains(self):
        ini = parse_ini_text("[S]\nK=1\n")
        self.assertIn(("S", "K"), ini)
        self.assertNotIn(("S", "Z"), ini)

    def test_value_conversions(self):
        ini = parse_ini_text("[S]\nF = 1.5\nI = 42.0\n")
        self.assertAlmostEqual(ini.require("S", "F").as_float(), 1.5)
        self.assertEqual(ini.require("S", "I").as_int(), 42)

    def test_sections_and_keys(self):
        ini = parse_ini_text("[A]\nX=1\nY=2\n[B]\nZ=3\n")
        self.assertEqual(ini.sections(), ["A", "B"])
        self.assertEqual(ini.keys("A"), ["X", "Y"])

    def test_quoted_values_are_kept_raw(self):
        ini = parse_ini_text('[S]\nC = "a b c"\n')
        self.assertEqual(ini.require("S", "C").raw, '"a b c"')

    def test_all_provisional_markers_are_recognised(self):
        from bettercnc.iniload import PROVISIONAL_MARKERS

        for marker in PROVISIONAL_MARKERS:
            with self.subTest(marker=marker):
                ini = parse_ini_text("[S]\n# {} something\nK = 1\n".format(marker))
                self.assertTrue(ini.require("S", "K").provisional)

    def test_marker_matching_is_case_insensitive(self):
        ini = parse_ini_text("[S]\n# assumption: lowercase\nK = 1\n")
        self.assertTrue(ini.require("S", "K").provisional)


if __name__ == "__main__":
    unittest.main(verbosity=2)
