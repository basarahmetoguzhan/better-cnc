#!/usr/bin/env python3
"""SIMULATOR ONLY: the end-to-end harness of sim/e2e.

Drives the operator UI over HTTP, as the browser does, and checks LinuxCNC
itself - linuxcnc.stat() and halcmd - not the UI's picture of it. Runs INSIDE
the simulator container, one fresh LinuxCNC per test module:

    make sim-e2e                       every sim/e2e/test_*.py
    make sim-e2e E2E=test_baseline.py  one module

(sim/e2e/run.sh: `sim/sim.sh up` under its own name and port, then
`docker exec ... python3 -m unittest discover -s /repo/sim/e2e -p MODULE`,
then `sim/sim.sh down`.) Stdlib, the linuxcnc module and halcmd only;
imported on the Mac too (ui/tests/test_deploy.py checks its pure parts), so
nothing LinuxCNC is imported at module level and the syntax is Python 3.9's.

What it may and may not touch:

  - Commands reach LinuxCNC only through the UI: POST /api/action with
    Content-Type: application/json, as app.js sends it.
  - It never opens linuxcnc.command() or linuxcnc.error_channel(). emcError
    is a queue buffer (reference/linuxcnc/configs/common/linuxcnc.nml:9): a
    message read here would be gone for the UI's own drain
    (adapter.LinuxCncAdapter._drain_errors), which is under test.
  - It reads LinuxCNC with linuxcnc.stat() and `halcmd getp`.
  - It changes HAL only where the simulator stands in for the rig: the
    simulated switches' home-pos pins (place_switch, press,
    restore_switches) and the link stub's sim-link-down parameter.
  - /repo is mounted read-only: it writes no file.

How to assert, from the prototype runs of 2026-11-13 (three identical runs):

  - LinuxCNC's errors arrive later than the POST that caused them and are
    drained into the alarm log only when a request takes a snapshot; the
    message line is last-writer-wins (the error box, status.last_error,
    keeps the most specific error since plan WP04). Assert on the alarm
    log, polled with GET /api/state (wait_alarm), never on the POST's own
    answer.
  - stat.inpos stays True during a teleop jog (control.c's TELEOP branch
    never sets it, reference/linuxcnc/src/emc/motion/control.c:1398-1461;
    only free mode and coordinated mode do, :1205-1272 and :1392-1395). A
    move is over when stat.current_vel is 0 AND the target is reached
    (wait_position); current_vel is computed in every mode (:1946-1962).
  - But not from a live planner once motion is disabled: after a limit
    trip in the middle of a teleop jog, LinuxCNC sits in ESTOP_RESET with
    current_vel still at the jog's speed (seen: 3.000), because disabled
    motion zeroes only the joints' velocities (:1463-1477) while
    current_vel comes from the teleop planner (:1949-1951). Moving means
    ON and current_vel > 0 (moving()).
  - Container state persists for the module: positions, homed flags, motor
    offsets, moved switches. run.sh gives each module a fresh LinuxCNC;
    inside a module, tests clean up after themselves.
  - The kernel is not realtime: every timeout here is about twice what the
    simulator was measured to take (HOME_TIMEOUT_S: Home All took 14 s).

Simulated limit switches (sim/test-rig-sim.hal, SWITCHES): one
sim_home_switch per rig switch. Its home-pos pin is in MOTOR units, and its
SIGN picks the side: >= 0 a switch on the positive side, pressed at or above
it; < 0 on the negative side, pressed at or below it
(reference/linuxcnc/src/hal/components/sim_home_switch.comp:29-59). So a
switch moved across 0 does not move, it changes sides and reads the wrong
way: place_switch refuses that. Motor = machine + joint.N.motor-offset
(control.c:459-460, :2015-2016): 0 before homing, -9.9 / -9.9 / +4.9 after
it here. `halcmd sets` on a switch net fails ("already has writer(s)") and
setp on joint.N.*-lim-sw-in fails ("connected to a signal"): moving home-pos
is the way.

Also a command line, for sim/sim.sh press / release / switches:

    python3 sim/e2e/harness.py press X+|X-|Y+|Y-|Z+
    python3 sim/e2e/harness.py release [X+|X-|Y+|Y-|Z+ ...]
    python3 sim/e2e/harness.py switches
"""

from __future__ import annotations

import json
import math
import os
import re
import subprocess
import sys
import time
import unittest
import urllib.error
import urllib.request
from typing import Any, Callable, Dict, List, NamedTuple, Optional, Sequence, Tuple

E2E_DIR = os.path.dirname(os.path.abspath(__file__))
SIM_DIR = os.path.dirname(E2E_DIR)
REPO = os.path.dirname(SIM_DIR)
SIM_HAL = os.path.join(SIM_DIR, "test-rig-sim.hal")

#: The UI inside the container (sim/start.sh: --port 8080 on 0.0.0.0).
UI = os.environ.get("E2E_UI", "http://127.0.0.1:8080")

#: One HTTP request. An action holds the UI's session lock for its bounded
#: waits (app.Session.act); this only catches a UI that hangs.
HTTP_TIMEOUT_S = 15.0

#: One halcmd call.
HALCMD_TIMEOUT_S = 10.0

#: How often wait_until looks.
POLL_S = 0.05

#: Home All from a fresh start took 13.8-14.2 s in the simulator (switches
#: 10 / 10 / 5 mm from where the motors start; on the rig X alone can take
#: about 80 s).
HOME_TIMEOUT_S = 40.0

#: reset + ON took 0.4 s.
ON_TIMEOUT_S = 5.0

#: The software estop must stop a move within this, measured from the POST.
#: The prototype saw 0.34-0.41 s through `docker exec` (70-250 ms per probe);
#: from inside the container, where a probe costs nothing, 0.19 s.
ESTOP_STOP_S = 0.5

#: An alarm LinuxCNC raises after an action reaches the log within this.
ALARM_TIMEOUT_S = 3.0

#: The confirmation band's «Evet» counts app.CONFIRM_MIN_S (1 s) after its
#: question (the band's `ready`); wait_confirm_ready waits this long for it.
CONFIRM_READY_TIMEOUT_S = 3.0

AXES = "XYZ"
#: trivkins XYZ: joint N is axis N (configs/test-rig/test-rig.ini [KINS]).
JOINT_OF_AXIS = {"X": 0, "Y": 1, "Z": 2}

#: [JOINT_n]HOME: where Home All leaves the machine (test-rig.ini :454,
#: :497, :550; ui/tests/test_deploy.py ties these to the INI).
HOME_POSITION = (5.0, 5.0, -5.0)

#: [AXIS_*]MIN_LIMIT / MAX_LIMIT, machine coordinates; enforced only once
#: homed (test-rig.ini :576-595; tied to the INI by test_deploy.py).
SOFT_LIMITS = {"X": (3.0, 399.0), "Y": (3.0, 387.0), "Z": (-96.5, -3.0)}

#: A position counts as reached within this. Jogs and MDI moves end on
#: their targets to about 1e-10 mm in the simulator (no steps, feedback =
#: command).
POSITION_TOL = 0.001

# stat.task_state values, mirrored so this module imports without linuxcnc
# (reference/linuxcnc/src/emc/nml_intf/emc.hh:256-261, exported as
# linuxcnc.STATE_* by emcmodule.cc:2457-2460). Checked against the module at
# first use (_lc).
STATE_ESTOP, STATE_ESTOP_RESET, STATE_OFF, STATE_ON = 1, 2, 3, 4
TASK_STATE_NAMES = {1: "ESTOP", 2: "ESTOP_RESET", 3: "OFF", 4: "ON"}


# ---------------------------------------------------------------------------
# HTTP: the UI, as app.js drives it
# ---------------------------------------------------------------------------


def _json_request(req: urllib.request.Request) -> Dict[str, Any]:
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S) as resp:
        return json.loads(resp.read().decode("utf-8"))


def post(action: str, value: Any = "", screen: str = "kontrol") -> Dict[str, Any]:
    """POST /api/action as app.js's post() sends it; returns the answer
    ({"html", "screen"}). Do not assert on what it says: see the module
    docstring."""
    body = json.dumps({"action": action, "value": str(value), "screen": screen}).encode("utf-8")
    req = urllib.request.Request(
        UI + "/api/action", data=body, method="POST",
        headers={"Content-Type": "application/json"},
    )
    return _json_request(req)


def state(screen: str = "kontrol") -> Dict[str, Any]:
    """GET /api/state: the whole view model. Every call is a snapshot, so it
    also drains LinuxCNC's errors into the alarm log."""
    return _json_request(urllib.request.Request(UI + "/api/state?screen=" + screen))


def message() -> str:
    """The message line (status.message) as a fresh snapshot leaves it."""
    return str(state()["status"]["message"])


def ui_ready() -> bool:
    try:
        return bool(_json_request(urllib.request.Request(UI + "/healthz")).get("ok"))
    except (urllib.error.URLError, OSError, ValueError):
        return False


# ---------------------------------------------------------------------------
# LinuxCNC's own view
# ---------------------------------------------------------------------------

_LC = None


def _lc() -> Any:
    """The linuxcnc module, imported on first use (the Mac has none)."""
    global _LC
    if _LC is None:
        import linuxcnc  # only inside the container

        assert (linuxcnc.STATE_ESTOP, linuxcnc.STATE_ESTOP_RESET, linuxcnc.STATE_OFF,
                linuxcnc.STATE_ON) == (STATE_ESTOP, STATE_ESTOP_RESET, STATE_OFF, STATE_ON)
        _LC = linuxcnc
    return _LC


def stat() -> Any:
    """A freshly polled linuxcnc.stat(). A new object each call (about
    30 us), so a result kept for a before/after comparison does not change
    under the caller's feet as a re-polled one would."""
    s = _lc().stat()
    s.poll()
    return s


def position(s: Any = None) -> Tuple[float, float, float]:
    """Machine X, Y, Z: stat.actual_position (emcmodule.cc:738)."""
    s = stat() if s is None else s
    return tuple(float(v) for v in s.actual_position[:3])  # type: ignore[return-value]


def homed(s: Any = None) -> Tuple[bool, bool, bool]:
    s = stat() if s is None else s
    return tuple(bool(s.joint[j]["homed"]) for j in range(3))  # type: ignore[return-value]


def homing(s: Any = None) -> Tuple[bool, bool, bool]:
    s = stat() if s is None else s
    return tuple(bool(s.joint[j]["homing"]) for j in range(3))  # type: ignore[return-value]


def hard_limits(s: Any = None) -> Tuple[Tuple[bool, bool], ...]:
    """(min_hard_limit, max_hard_limit) per joint (emcmodule.cc:604-605):
    motion's own view of the switches, read every servo cycle."""
    s = stat() if s is None else s
    return tuple((bool(s.joint[j]["min_hard_limit"]), bool(s.joint[j]["max_hard_limit"]))
                 for j in range(3))


def task_state_name(s: Any = None) -> str:
    s = stat() if s is None else s
    return TASK_STATE_NAMES.get(int(s.task_state), str(s.task_state))


def describe(s: Any = None) -> str:
    """One line of LinuxCNC state, for assertion messages."""
    s = stat() if s is None else s
    return "task={} pos={} vel={:.3f} homed={} homing={} interp={} mode={} motion={}".format(
        task_state_name(s), tuple(round(v, 3) for v in position(s)), float(s.current_vel),
        [int(v) for v in homed(s)], [int(v) for v in homing(s)], s.interp_state,
        s.task_mode, s.motion_mode,
    )


# ---------------------------------------------------------------------------
# HAL
# ---------------------------------------------------------------------------


class HalcmdError(RuntimeError):
    pass


def halcmd(*args: Any) -> str:
    """Run halcmd; its stdout, stripped. Raises HalcmdError on a failure."""
    cmd = ["halcmd"] + [str(a) for a in args]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=HALCMD_TIMEOUT_S)
    if proc.returncode != 0:
        raise HalcmdError("{} -> {}: {}".format(
            " ".join(cmd), proc.returncode, (proc.stderr or proc.stdout).strip()))
    return proc.stdout.strip()


def getp(name: str) -> str:
    """`halcmd getp`: a bit reads TRUE / FALSE, a float "%.7g"
    (reference/linuxcnc/src/hal/utils/halcmd_commands.cc:2419-2428)."""
    return halcmd("getp", name)


def get_bit(name: str) -> bool:
    value = getp(name)
    if value not in ("TRUE", "FALSE"):
        raise HalcmdError("{} is not a bit: {!r}".format(name, value))
    return value == "TRUE"


def get_float(name: str) -> float:
    return float(getp(name))


def setp(name: str, value: Any) -> None:
    if isinstance(value, bool):
        value = "TRUE" if value else "FALSE"
    halcmd("setp", name, value)


# ---------------------------------------------------------------------------
# Waiting
# ---------------------------------------------------------------------------


def wait_until(
    fn: Callable[[], Any],
    timeout: float,
    what: str = "the condition",
    every: float = POLL_S,
    detail: Optional[Callable[[], str]] = None,
    start: Optional[float] = None,
) -> Any:
    """fn()'s first truthy value, looking every `every` s. After `timeout` s
    (counted from `start`, a time.monotonic() taken before the action, if
    given) an AssertionError naming `what` and, if given, detail()'s
    description of where things stand - a timeout here is a failed check,
    not an error."""
    deadline = (time.monotonic() if start is None else start) + timeout
    while True:
        value = fn()
        if value:
            return value
        if time.monotonic() >= deadline:
            raise AssertionError("timed out after {:g} s waiting for {}{}".format(
                timeout, what, " ({})".format(detail()) if detail else ""))
        time.sleep(every)


def wait_task_state(want: int, timeout: float = ON_TIMEOUT_S) -> Any:
    """Wait for stat.task_state == want; returns that stat."""
    def reached() -> Any:
        s = stat()
        return s if int(s.task_state) == want else None
    return wait_until(reached, timeout, "task_state {}".format(TASK_STATE_NAMES.get(want, want)),
                      detail=describe)


def near(a: Sequence[float], b: Sequence[float], tol: float = POSITION_TOL) -> bool:
    return all(abs(float(x) - float(y)) <= tol for x, y in zip(a, b))


def wait_position(
    target: Sequence[Optional[float]],
    timeout: float,
    tol: float = POSITION_TOL,
) -> Any:
    """Wait until the machine stands at `target` (X, Y, Z; None = any) with
    current_vel 0: the end of a jog or an MDI move (see the module
    docstring for why not stat.inpos). Returns that stat."""
    def done() -> Any:
        s = stat()
        pos = position(s)
        if float(s.current_vel) != 0.0:
            return None
        if all(t is None or abs(p - t) <= tol for p, t in zip(pos, target)):
            return s
        return None
    return wait_until(done, timeout, "position {}".format(tuple(target)), detail=describe)


def moving(s: Any = None) -> bool:
    """ON and current_vel > 0 (see the module docstring: out of ON a stale
    teleop velocity can remain)."""
    s = stat() if s is None else s
    return int(s.task_state) == STATE_ON and float(s.current_vel) > 0.0


def wait_still(timeout: float = 5.0) -> Any:
    """Wait until not moving() on two looks in a row; returns that stat."""
    looks = {"still": 0}

    def still() -> Any:
        s = stat()
        looks["still"] = 0 if moving(s) else looks["still"] + 1
        return s if looks["still"] >= 2 else None
    return wait_until(still, timeout, "the machine to stand still", detail=describe)


def wait_homing_over(timeout: float = HOME_TIMEOUT_S, settle_s: float = 0.5) -> Any:
    """Wait until no joint has been homing for `settle_s`: between two
    joints of the sequence one look can find none homing. Returns that
    stat. For a homing that may have been cut short; home_all() waits for
    a complete one."""
    quiet = {"since": None}

    def over() -> Any:
        s = stat()
        now = time.monotonic()
        if any(homing(s)):
            quiet["since"] = None
            return None
        if quiet["since"] is None:
            quiet["since"] = now
        return s if now - quiet["since"] >= settle_s else None
    return wait_until(over, timeout, "homing to be over", detail=describe)


def wait_homing_search(joint: int, travel: float = 0.5, timeout: float = HOME_TIMEOUT_S) -> Any:
    """Wait until `joint` is homing and has moved `travel` mm since its
    homing began: it is searching for its switch, well away from it (the
    sim's switches are 5-10 mm from where the search starts; Z searches at
    4 mm/s, X and Y at 5, test-rig.ini HOME_SEARCH_VEL). The safe moment to
    interrupt a homing: interrupted while on its switch, in the latch
    phase, a joint is left on a limit (control.c:807-826 trips once
    homing no longer ignores it) and only «Limitten kurtar» (plan WP05)
    takes it off. Returns that stat."""
    began = {"at": None}

    def searching() -> Any:
        s = stat()
        pos = float(s.joint[joint]["input"])
        if not homing(s)[joint]:
            began["at"] = None
            return None
        if began["at"] is None:
            began["at"] = pos
        return s if abs(pos - began["at"]) >= travel else None
    return wait_until(searching, timeout, "joint {} to search for its switch".format(joint),
                      every=0.01, detail=describe)


def move_time(distance: float, velocity: float) -> float:
    """A timeout for a move: twice its time at `velocity`, plus 2 s."""
    return 2.0 * abs(distance) / velocity + 2.0


def override_limits(s: Any = None) -> bool:
    """stat.joint[0]["override_limits"] (emcmodule.cc:606); every joint
    carries the same value (taskintf.cc:963)."""
    s = stat() if s is None else s
    return bool(s.joint[0]["override_limits"])


# ---------------------------------------------------------------------------
# The alarm log (Alarmlar), the place to look for LinuxCNC's errors
# ---------------------------------------------------------------------------


def alarm_events(vm: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """alarmlar.events, newest first (the adapter inserts at the front)."""
    vm = vm or state()
    return list(vm["alarmlar"]["events"])


def alarm_texts(vm: Optional[Dict[str, Any]] = None) -> List[str]:
    return [str(e.get("text") or "") for e in alarm_events(vm)]


def alarm_mark() -> int:
    """The log's length now; new_alarms(mark) is what came after. Breaks
    once the log is capped (none is today)."""
    return len(alarm_events())


def new_alarms(mark: int, vm: Optional[Dict[str, Any]] = None) -> List[str]:
    texts = alarm_texts(vm)
    return texts[:max(0, len(texts) - mark)]


def wait_alarm(text: str, mark: int = 0, timeout: float = ALARM_TIMEOUT_S) -> str:
    """The first alarm since `mark` containing `text`, polling
    GET /api/state - each poll drains LinuxCNC's error channel."""
    return wait_until(
        lambda: next((t for t in new_alarms(mark) if text in t), None),
        timeout, "an alarm containing {!r}".format(text),
        every=0.2, detail=lambda: "new alarms: {}".format(new_alarms(mark)),
    )


def norm(text: str) -> str:
    """Text for comparing: U+2212 minus as "-", U+2026 as "...", runs of
    whitespace as one space - the UI writes typographic signs, a test
    written by hand may not."""
    return " ".join(str(text).replace("\u2212", "-").replace("\u2026", "...").split())


def mdi_history(vm: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """kontrol.mdi.history, newest first (viewmodel.mdi_panel)."""
    vm = vm or state()
    return list(vm["kontrol"]["mdi"]["history"])


def mdi_entry(command: str, vm: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """The newest MDI history entry for `command`, None if there is none."""
    return next((e for e in mdi_history(vm) if e.get("command") == command), None)


class DroRow(NamedTuple):
    work: Optional[float]
    machine: Optional[float]


def dro(vm: Optional[Dict[str, Any]] = None) -> Dict[str, DroRow]:
    """The Kontrol DRO (kontrol.axes) by axis, as numbers: the work and
    machine positions as the UI prints them ("+10.000", 3 decimals), None
    for its "—"."""
    vm = vm or state()

    def number(text: Any) -> Optional[float]:
        text = norm(text)
        return None if text in ("", "—") else float(text)
    return {a["axis"]: DroRow(number(a["work"]), number(a["machine"]))
            for a in vm["kontrol"]["axes"]}


def motion(vm: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """The view model's motion section (viewmodel.motion_status): task,
    interpreter and motion modes, moving, paused, MDI running, program
    line, G92, the limit override, the hard-limit flags per joint and the
    commanded position per axis - raw values, None where not read."""
    vm = vm or state()
    return dict(vm["motion"])


# Readers of view-model fields later work adds (plan WP04, WP07, WP08). Each
# is the one place to change if that work names its field otherwise.


def last_error_text(vm: Optional[Dict[str, Any]] = None) -> str:
    """The sticky error box (WP04: status.last_error, a dict - id, time,
    Turkish text, LinuxCNC's raw text, source) as text: its string values
    joined; "" when there is none."""
    vm = vm or state()
    err = vm.get("status", {}).get("last_error")
    if not err:
        return ""
    if isinstance(err, dict):
        return " ".join(str(v) for v in err.values() if isinstance(v, str))
    return str(err)


def pending_confirm(vm: Optional[Dict[str, Any]] = None) -> Optional[str]:
    """The token of a confirmation the UI is asking for (WP07: a band with
    data-action=confirm, data-value=<token>), None if none."""
    vm = vm or state()
    confirm = vm.get("confirm")
    if isinstance(confirm, dict) and confirm.get("token"):
        return str(confirm["token"])
    return None


def wait_confirm_ready(token: str, timeout: float = CONFIRM_READY_TIMEOUT_S) -> None:
    """Wait until the question carrying `token` takes «Evet»: the band's
    `ready`, app.CONFIRM_MIN_S after it was asked. Until then an «Evet»
    sends nothing and the question stands (a double tap on the button that
    asked must not answer it)."""
    def ready() -> bool:
        confirm = state().get("confirm")
        return (isinstance(confirm, dict) and confirm.get("token") == token
                and bool(confirm.get("ready")))
    wait_until(ready, timeout, "«Evet» to count")


def confirm_if_asked() -> bool:
    """Answer a pending confirmation with «Evet» once it counts
    (wait_confirm_ready); whether there was one."""
    token = pending_confirm()
    if token is None:
        return False
    wait_confirm_ready(token)
    post("confirm", token)
    return True


def program_files(vm: Optional[Dict[str, Any]] = None) -> List[str]:
    """The names the Program screen lists (WP08: program.files, dicts with
    a "name"; plain strings are accepted too)."""
    vm = vm or state("program")
    names = []
    for item in vm["program"].get("files") or []:
        name = item.get("name") if isinstance(item, dict) else item
        if name:
            names.append(str(name))
    return names


# ---------------------------------------------------------------------------
# The machine, through the UI
# ---------------------------------------------------------------------------


def reset_on(timeout: float = ON_TIMEOUT_S) -> Any:
    """«Yazılım sıfırla» (software_reset) and wait for ON."""
    post("software_reset")
    return wait_task_state(STATE_ON, timeout)


def ensure_on() -> Any:
    s = stat()
    if int(s.task_state) == STATE_ON:
        return s
    return reset_on()


def home_all(timeout: float = HOME_TIMEOUT_S) -> List[int]:
    """Home All through the UI, confirming if the UI asks (confirm_if_asked),
    and wait for every joint homed. Returns the joints in the order their
    homed flags rose."""
    order: List[int] = []
    post("home_all")
    confirm_if_asked()

    def done() -> Any:
        s = stat()
        for j, is_homed in enumerate(homed(s)):
            if is_homed and j not in order:
                order.append(j)
        return s if all(homed(s)) and not any(homing(s)) else None
    wait_until(done, timeout, "Home All to finish", detail=describe)
    return order


def ensure_homed(timeout: float = HOME_TIMEOUT_S) -> Any:
    """ON and every joint homed, homing first if not. Returns the stat."""
    ensure_on()
    s = stat()
    if all(homed(s)) and not any(homing(s)):
        return s
    home_all(timeout)
    return wait_still()


def jog(axis: str, direction: int, step: Optional[float] = None) -> None:
    """A jog click: jog_step first when `step` is given, then "<axis>:<±1>"
    (the wire format app.py's Session.act takes)."""
    if step is not None:
        post("jog_step", "{:g}".format(step))
    post("jog", "{}:{}".format(axis, 1 if direction > 0 else -1))


def jog_and_wait(axis: str, direction: int, step: float, velocity: float = 3.0) -> Any:
    """jog() and wait for the target: the current position plus or minus
    `step` on that axis. velocity: [DISPLAY]DEFAULT_LINEAR_VELOCITY, the
    adapter's jog speed (test-rig.ini:59)."""
    i = AXES.index(axis)
    target: List[Optional[float]] = [None, None, None]
    target[i] = position()[i] + (step if direction > 0 else -step)
    jog(axis, direction, step)
    return wait_position(target, move_time(step, velocity))


def drive_into_switch(axis: str, direction: int, step: float = 1.0, max_steps: int = 6,
                      velocity: float = 3.0) -> Any:
    """Jog `axis` toward `direction` `step` mm at a time until LinuxCNC
    leaves ON - a limit trip ("joint N on limit switch error",
    reference/linuxcnc/src/emc/motion/control.c:807-826) - at most
    `max_steps` jogs. Returns the stat after the trip."""
    i = AXES.index(axis)
    for _ in range(max_steps):
        s = stat()
        if int(s.task_state) != STATE_ON:
            return s
        target = position(s)[i] + (step if direction > 0 else -step)
        jog(axis, direction, step)

        def done() -> Any:
            s = stat()
            if int(s.task_state) != STATE_ON:
                return s
            if float(s.current_vel) == 0.0 and abs(position(s)[i] - target) <= POSITION_TOL:
                return s
            return None
        s = wait_until(done, move_time(step, velocity),
                       "a jog {}{} or a limit trip".format(axis, "+" if direction > 0 else "-"),
                       detail=describe)
        if int(s.task_state) != STATE_ON:
            return s
    raise AssertionError("no limit trip after {} jogs of {:g} mm {}{} ({})".format(
        max_steps, step, axis, "+" if direction > 0 else "-", describe()))


def mdi(command: str) -> None:
    post("mdi", command)


def stop_motion(timeout: float = 5.0) -> Any:
    """Abort whatever moves (the Program screen's Durdur: program_stop sends
    abort, which also ends a jog or a homing) and wait until still. For
    clean-up; a test of stopping uses its own action."""
    post("program_stop", screen="program")
    return wait_still(timeout)


# ---------------------------------------------------------------------------
# Simulated limit switches
# ---------------------------------------------------------------------------


class Switch(NamedTuple):
    joint: int
    axis: str
    end: str        # "+" or "-": the end of travel it sits at
    gpio: str       # the rig input it stands for (configs/test-rig/test-rig.hal)
    label: str      # the UI's label for it (profiles.TEST_RIG_INPUTS)


#: sim_home_switch instance (without "sim-") -> the rig switch it stands for.
#: test_deploy.py ties every field to test-rig-sim.hal, test-rig.hal and the
#: profile.
SWITCHES: Dict[str, Switch] = {
    "x-min": Switch(0, "X", "-", "gpio.021", "X sol uç"),
    "x-max": Switch(0, "X", "+", "gpio.020", "X sağ uç"),
    "y-min": Switch(1, "Y", "-", "gpio.023", "Y ön uç"),
    "y-max": Switch(1, "Y", "+", "gpio.022", "Y arka uç"),
    "z-top": Switch(2, "Z", "+", "gpio.024", "Z üst"),
}

#: "X+" etc. -> instance. No "Z-": the rig has no Z bottom switch.
END_TO_SWITCH = {"{}{}".format(sw.axis, sw.end): name for name, sw in SWITCHES.items()}

#: How deep `press` puts the axis into the switch: the trip point this far
#: behind the current position. Chosen, not measured: the rig's switch
#: over-travel is unmeasured (plan, open items).
PRESS_DEPTH_MM = 0.5

_SETP_HOME_POS = re.compile(r"^\s*setp\s+sim-([a-z]+-[a-z]+)\.home-pos\s+(\S+)", re.M)


class SwitchError(Exception):
    """A switch move that cannot be done; the text is for the operator of
    sim/sim.sh (Turkish, as sim.sh speaks)."""


def default_switch_positions(text: Optional[str] = None) -> Dict[str, float]:
    """Each switch's home-pos as sim/test-rig-sim.hal sets it at start."""
    if text is None:
        with open(SIM_HAL, "r", encoding="utf-8") as fh:
            text = fh.read()
    found = {name: float(value) for name, value in _SETP_HOME_POS.findall(text)}
    missing = sorted(set(SWITCHES) - set(found))
    if missing:
        raise SwitchError("{} içinde home-pos yok: {}".format(SIM_HAL, ", ".join(missing)))
    return found


def switch_side(home_pos: float) -> int:
    """+1: a positive-side switch, pressed at or above home_pos; -1:
    negative side, pressed at or below it (sim_home_switch.comp:29-59)."""
    return 1 if home_pos >= 0 else -1


def check_placement(name: str, motor_pos: float, defaults: Dict[str, float]) -> float:
    """motor_pos if `name` may be put there, else SwitchError: a value that
    is not a finite number, or one on the other side of 0 than the rig's
    switch (the default), which would turn the switch around."""
    if name not in SWITCHES:
        raise SwitchError("bilinmeyen switch: {} (olanlar: {})".format(name, ", ".join(SWITCHES)))
    if not math.isfinite(motor_pos):
        raise SwitchError("{}: konum sayı değil: {!r}".format(name, motor_pos))
    side = switch_side(defaults[name])
    if switch_side(motor_pos) != side:
        raise SwitchError(
            "sim-{}.home-pos {:g} olamaz: {} switch'i motor 0'ın {} tarafında; işaret "
            "değişirse switch ters tarafa döner ve yanlış okunur "
            "(sim_home_switch.comp:29-59)".format(
                name, motor_pos, SWITCHES[name].label,
                "pozitif (>= 0)" if side > 0 else "negatif (< 0)"))
    return motor_pos


def press_position(name: str, cur: float, defaults: Dict[str, float],
                   depth: float = PRESS_DEPTH_MM) -> float:
    """Where to put `name` so it reads pressed with its motor at `cur`: the
    trip point `depth` behind cur, or less where 0 is closer (the switch
    must stay on its side, check_placement). SwitchError if the axis is on
    the other side of motor 0 itself: there the switch cannot be pressed
    without moving the axis."""
    sw = SWITCHES[name]
    side = switch_side(defaults[name])
    if side > 0:
        if cur < 0:
            raise SwitchError(
                "{}{} burada basılamaz: {} motoru {:.3f}, pozitif taraf switch'i home-pos "
                ">= 0 ister. Önce ekseni {}+ yönünde {:.1f} mm sürün (motor 0'ın "
                "üstüne).".format(sw.axis, sw.end, sw.axis, cur, sw.axis, -cur + 1.0))
        return max(0.0, cur - depth)
    if cur >= 0:
        raise SwitchError(
            "{}{} burada basılamaz: {} motoru {:.3f}, negatif taraf switch'i home-pos < 0 "
            "ister. Önce ekseni {}- yönünde {:.1f} mm sürün (motor 0'ın altına).".format(
                sw.axis, sw.end, sw.axis, cur, sw.axis, cur + 1.0))
    return min(cur + depth, cur / 2.0)


def switch_pressed(name: str) -> bool:
    """The simulated switch's output, sim-<name>.home-sw (what the rig's
    .in_not would read)."""
    return get_bit("sim-{}.home-sw".format(name))


def switch_motor_position(name: str) -> float:
    """The motor position the switch compares: sim-<name>.cur-pos."""
    return get_float("sim-{}.cur-pos".format(name))


def motor_offset(joint: int) -> float:
    return get_float("joint.{}.motor-offset".format(joint))


def place_switch(name: str, motor_pos: float) -> None:
    """Move switch `name` to `motor_pos` (motor units: machine +
    motor_offset(joint)). Refuses a move across 0 (check_placement)."""
    check_placement(name, motor_pos, default_switch_positions())
    setp("sim-{}.home-pos".format(name), repr(float(motor_pos)))


def place_switch_ahead(name: str, distance: float) -> float:
    """Put switch `name` `distance` mm beyond the axis's current position,
    toward the switch's own end, so the axis trips it after `distance` mm.
    Returns the motor position used."""
    defaults = default_switch_positions()
    cur = switch_motor_position(name)
    target = cur + switch_side(defaults[name]) * float(distance)
    check_placement(name, target, defaults)
    setp("sim-{}.home-pos".format(name), repr(target))
    return target


def press(end: str, depth: float = PRESS_DEPTH_MM, timeout: float = 0.5) -> str:
    """Make the switch at `end` ("X+", "X-", "Y+", "Y-", "Z+") read pressed
    where the axis stands now; it releases when the axis moves away by
    depth + the 0.1 mm hysteresis. Returns the instance name."""
    name = END_TO_SWITCH.get(end)
    if name is None:
        raise SwitchError("{}: yok. Olanlar: {} (Z'nin alt switch'i yok)".format(
            end, " ".join(sorted(END_TO_SWITCH))))
    defaults = default_switch_positions()
    target = press_position(name, switch_motor_position(name), defaults, depth)
    check_placement(name, target, defaults)
    setp("sim-{}.home-pos".format(name), repr(target))
    try:
        wait_until(lambda: switch_pressed(name), timeout, "sim-{}.home-sw".format(name))
    except AssertionError:
        raise SwitchError("{} basılmadı: sim-{}.home-pos {:g}, motor {:.4f}".format(
            SWITCHES[name].label, name, target, switch_motor_position(name))) from None
    return name


def restore_switches(names: Optional[Sequence[str]] = None) -> List[str]:
    """Put switches back where sim/test-rig-sim.hal has them (all, or
    `names`). Returns those still pressed afterwards: an axis really
    standing on its switch."""
    defaults = default_switch_positions()
    names = list(names) if names else list(SWITCHES)
    for name in names:
        setp("sim-{}.home-pos".format(name), repr(defaults[name]))
    time.sleep(0.01)   # ten servo periods: the component has run
    return [name for name in names if switch_pressed(name)]


#: How long one «Limitten kurtar» may take in the clean-up: its jog is at
#: most motionrules.RECOVERY_MAX_STEP_MM at the 3 mm/s jog speed.
RECOVER_TIMEOUT_S = move_time(10.0, 3.0)


def recover_through_the_ui(names: Sequence[str]) -> List[str]:
    """Press the UI's «Limitten kurtar» (plan WP05: override, ON, one jog
    off the switch) until none of `names` reads pressed, once per switch at
    most. A press that does not put LinuxCNC's limit override on was
    refused - say so on stderr and stop. Returns the switches it freed."""
    freed: List[str] = []
    for _ in range(len(names)):
        pressed = [n for n in names if switch_pressed(n)]
        if not pressed:
            break
        post("recover_limit")
        if not override_limits():
            sys.stderr.write("harness: «Limitten kurtar» refused: {}\n".format(message()))
            break
        try:
            wait_until(lambda: not override_limits() and not moving(), RECOVER_TIMEOUT_S,
                       "the recovery jog to end", detail=describe)
        except AssertionError as exc:
            sys.stderr.write("harness: {}\n".format(exc))
            break
        freed.extend(n for n in pressed if not switch_pressed(n))
    return freed


def rescue_from_switches() -> List[str]:
    """Free every axis that stands on one of its switches where the HAL
    file has them - left there by a homing cut short, say. First the UI's
    own way out, «Limitten kurtar» (recover_through_the_ui); what that does
    not free, the simulator's way: the switch moved 1 mm out from under the
    axis, the machine turned on, the axis jogged back inside the switch's
    place, the switch put back. Clean-up for the next test, never part of
    one; says which way on stderr. Returns the switches it freed."""
    defaults = default_switch_positions()
    pressed = [name for name in SWITCHES if switch_pressed(name)]
    if not pressed:
        return []
    by_ui = recover_through_the_ui(pressed)
    freed = []
    for name in pressed:
        if not switch_pressed(name):
            continue
        side = switch_side(defaults[name])
        place_switch(name, switch_motor_position(name) + side * 1.0)
        wait_until(lambda: not switch_pressed(name), 0.5, "sim-{}.home-sw to clear".format(name))
        freed.append(name)
    if freed:
        ensure_on()
        for name in freed:
            sw = SWITCHES[name]
            side = switch_side(defaults[name])
            inside = abs(switch_motor_position(name) - defaults[name]) + 1.0
            # 1 mm at a time: the UI refuses a longer unhomed step toward
            # the Z bottom, which has no switch (plan WP06), and 10 mm on
            # the other axes.
            while inside > 1e-9:
                step = min(inside, 1.0)
                jog_and_wait(sw.axis, -side, step)
                inside -= step
        still = restore_switches(freed)
        if still:
            raise AssertionError("still pressed after the rescue: {}".format(still))
    for how, names in (("the UI's «Limitten kurtar»", by_ui), ("the simulator's way", freed)):
        if names:
            sys.stderr.write("harness: freed from its switch for the next test ({}): {}\n".format(
                how, ", ".join(SWITCHES[n].label for n in names)))
    return by_ui + freed


def switches_moved() -> List[str]:
    """Switches not where the HAL file puts them."""
    defaults = default_switch_positions()
    return [name for name in SWITCHES
            if abs(get_float("sim-{}.home-pos".format(name)) - defaults[name]) > 1e-6]


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------


class E2ETestCase(unittest.TestCase):
    """Waits for the UI once per class. tearDown leaves the switches where
    the HAL file has them."""

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        wait_until(ui_ready, 30.0, "the UI's /healthz")

    def tearDown(self) -> None:
        if switches_moved():
            restore_switches()
        super().tearDown()


class Scenario(E2ETestCase):
    """Test methods that stand alone, though on one LinuxCNC. tearDown lets
    a homing finish, stops whatever else still moves, puts the switches
    back and turns the machine on, so the next test starts still, ON and
    with every switch free - homed or not, wherever the last one left the
    axes. A test that needs more says so in its setUp (ensure_homed, ...).

    The known gaps of later work are test methods marked
    @unittest.expectedFailure whose setUp arranges what works today: a
    failure there is an error, not an expected failure."""

    def tearDown(self) -> None:
        try:
            s = stat()
            if any(homing(s)):
                # Let it finish rather than abort it: aborted in a latch
                # phase it leaves a joint on its switch (wait_homing_search).
                wait_homing_over()
            elif moving(s):
                stop_motion()
            if switches_moved():
                restore_switches()
            rescue_from_switches()
            ensure_on()
        finally:
            super().tearDown()


class OrderedSteps(E2ETestCase):
    """The test_NN_* methods are the steps of ONE scenario on one LinuxCNC,
    in name order (unittest sorts by name), sharing its state on purpose:
    tearDown only puts the switches back. A step that fails or errors skips
    the steps after it, which would only fail on the state it left; the
    report names it."""

    _failed_step: Optional[str] = None

    def setUp(self) -> None:
        super().setUp()
        failed = type(self)._failed_step
        if failed:
            self.skipTest("an earlier step failed: {}".format(failed))

    def run(self, result: Any = None) -> Any:
        if result is None:
            return super().run(result)
        before = len(result.failures) + len(result.errors)
        out = super().run(result)
        if len(result.failures) + len(result.errors) > before:
            type(self)._failed_step = self._testMethodName
        return out


# ---------------------------------------------------------------------------
# Command line (sim/sim.sh press / release / switches)
# ---------------------------------------------------------------------------


def _describe_switch(name: str, defaults: Dict[str, float]) -> str:
    sw = SWITCHES[name]
    pos = get_float("sim-{}.home-pos".format(name))
    return "{:<10} {}{}  sim-{}.home-pos {:>9g} (başlangıç {:g})  motor {:>9.3f}  {}".format(
        sw.label, sw.axis, sw.end, name, pos, defaults[name], switch_motor_position(name),
        "BASILI" if switch_pressed(name) else "serbest")


def main(argv: Sequence[str]) -> int:
    usage = ("kullanım (sim/sim.sh ya da harness.py): press X+|X-|Y+|Y-|Z+ | "
             "release [UÇ ...] | switches\n")
    if not argv:
        sys.stderr.write(usage)
        return 2
    cmd, args = argv[0], list(argv[1:])
    try:
        if cmd == "press" and len(args) == 1:
            name = press(args[0])
            print("{} basılı (sim-{}.home-pos {}). Bırakmak için: sim/sim.sh release".format(
                SWITCHES[name].label, name, getp("sim-{}.home-pos".format(name))))
            return 0
        if cmd == "release":
            names = []
            for end in args:
                if end not in END_TO_SWITCH:
                    raise SwitchError("{}: yok. Olanlar: {}".format(end, " ".join(sorted(END_TO_SWITCH))))
                names.append(END_TO_SWITCH[end])
            still = restore_switches(names or None)
            if still:
                sys.stderr.write("yerine kondu ama hâlâ basılı (eksen switch'in üstünde): {}\n".format(
                    ", ".join(SWITCHES[n].label for n in still)))
                return 1
            print("switch'ler yerinde ve serbest: {}".format(
                ", ".join(SWITCHES[n].label for n in (names or SWITCHES))))
            return 0
        if cmd == "switches" and not args:
            defaults = default_switch_positions()
            for name in SWITCHES:
                print(_describe_switch(name, defaults))
            return 0
    except (SwitchError, HalcmdError) as exc:
        sys.stderr.write("{}\n".format(exc))
        return 1
    sys.stderr.write(usage)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
