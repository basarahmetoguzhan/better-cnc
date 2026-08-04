#!/usr/bin/env python3
"""grbl2ini.py - convert a GRBL 1.1 ``$$`` settings dump into draft LinuxCNC INI fragments.

Reads a raw ``$$`` dump (stdin or a file argument) and emits ``[JOINT_n]`` and
``[AXIS_x]`` fragments to stdout, with the arithmetic shown in a comment on every
converted line.

DESIGN RULE, enforced throughout: this tool NEVER emits a value it did not derive from
the input. Anything it cannot derive becomes an explicit ``TODO`` placeholder and is
listed in the warning block at the top of the output. A plausible-looking default
presented as if it were the machine's real value is worse than no value at all, because
it cannot be distinguished from a measured one later.

The conversions, and why they are what they are (see docs/06-grbl-to-linuxcnc.md for
the full citations):

    $0   step pulse   us      -> STEPLEN            x1000   (ns)
    $24  homing feed  mm/min  -> HOME_LATCH_VEL     /60     (mm/s)  [note: feed->latch]
    $25  homing seek  mm/min  -> HOME_SEARCH_VEL    /60     (mm/s)  [note: seek->search]
    $10x steps/mm            -> SCALE              unchanged magnitude
    $11x max rate    mm/min  -> MAX_VELOCITY       /60     (mm/s)
    $12x acceleration mm/s^2 -> MAX_ACCELERATION    unchanged  <-- do NOT convert
    $13x max travel  mm      -> MIN_LIMIT/MAX_LIMIT  sign depends on homing end

Pure standard library. No dependencies.

Usage:
    grbl2ini.py [dump.txt] [--joints N]
    cat dump.txt | grbl2ini.py
"""

import argparse
import re
import sys

# --- GRBL setting numbers we understand -------------------------------------------------
#
# Per-axis settings are keyed by GRBL's axis index (0=X, 1=Y, 2=Z, and by extension
# 3=A, 4=B, 5=C in forks that support them).

STEPS_PER_MM_BASE = 100      # $100, $101, $102, ...
MAX_RATE_BASE = 110          # $110, ...   mm/min
ACCEL_BASE = 120             # $120, ...   mm/sec^2
MAX_TRAVEL_BASE = 130        # $130, ...   mm

STEP_PULSE = 0               # $0    microseconds
HOMING_FEED = 24             # $24   mm/min  (slow latch pass)
HOMING_SEEK = 25             # $25   mm/min  (fast search pass)
HOMING_PULLOFF = 27          # $27   mm
SOFT_LIMITS = 20             # $20   bool
HARD_LIMITS = 21             # $21   bool
HOMING_ENABLE = 22           # $22   bool
DIR_INVERT_MASK = 3          # $3    bitmask
LIMIT_INVERT = 5             # $5    bool

# GRBL axis index -> LinuxCNC axis letter. trivkins with COORDINATES=XYZ makes
# JOINT_n correspond to the n'th letter here.
AXIS_LETTERS = ["X", "Y", "Z", "A", "B", "C"]

# Matches "$100=250.000", tolerating leading/trailing whitespace and a trailing
# comment in either "(...)" or ";..." / "#..." form.
SETTING_RE = re.compile(
    r"""^\s*
        \$(?P<num>\d+)          # $100
        \s*=\s*
        (?P<val>[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?)   # 250.000
        \s*
        (?:\(.*\)|[;#].*)?      # optional trailing comment
        \s*$
    """,
    re.VERBOSE,
)


class Dump:
    """A parsed GRBL settings dump."""

    def __init__(self):
        self.settings = {}       # int -> float
        self.malformed = []      # (lineno, text) for lines that looked like settings but weren't
        self.ignored = 0         # count of blank/banner/ok lines skipped

    def get(self, num):
        """Return the value of $num, or None if absent."""
        return self.settings.get(num)

    def has(self, num):
        return num in self.settings


def parse_dump(text):
    """Parse a raw $$ dump. Never raises on bad input."""
    dump = Dump()
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        # Banner, "ok", "[VER:...]", "[OPT:...]", error lines - not settings, not errors.
        if not line.startswith("$"):
            dump.ignored += 1
            continue
        m = SETTING_RE.match(line)
        if m is None:
            # Looked like a setting ($...) but did not parse. Record it; do not guess.
            dump.malformed.append((lineno, raw.rstrip()))
            continue
        try:
            dump.settings[int(m.group("num"))] = float(m.group("val"))
        except ValueError:                                   # pragma: no cover - regex guards this
            dump.malformed.append((lineno, raw.rstrip()))
    return dump


# --- conversions ------------------------------------------------------------------------
#
# Each returns (value, comment) or (None, reason) so the caller can emit a TODO.

def conv_per_minute_to_per_second(dump, num, label):
    """mm/min -> mm/sec. The /60 conversion. Trap 1 and Trap 2."""
    v = dump.get(num)
    if v is None:
        return None, "${} ({}) not present in dump".format(num, label)
    return v / 60.0, "from ${}={:g} mm/min / 60".format(num, v)


def conv_microseconds_to_nanoseconds(dump, num, label):
    """us -> ns. The x1000 conversion. Trap 3."""
    v = dump.get(num)
    if v is None:
        return None, "${} ({}) not present in dump".format(num, label)
    return v * 1000.0, "from ${}={:g} us x 1000".format(num, v)


def conv_passthrough(dump, num, label, units):
    """Value carries across unchanged. Trap 4 - do NOT convert acceleration."""
    v = dump.get(num)
    if v is None:
        return None, "${} ({}) not present in dump".format(num, label)
    return v, "from ${}={:g} {} (unchanged)".format(num, v, units)


def fmt(value):
    """Format a number without gratuitous trailing zeros, keeping useful precision."""
    if value == int(value):
        return str(int(value))
    return "{:.6g}".format(value)


# --- output helpers ---------------------------------------------------------------------

class Emitter:
    def __init__(self):
        self.lines = []
        self.todos = []      # human-readable list of things a person must supply

    def out(self, text=""):
        self.lines.append(text)

    def kv(self, key, value, comment=None, indent=0):
        pad = " " * indent
        line = "{}{} = {}".format(pad, key, value)
        if comment:
            line = "{:<38} # {}".format(line, comment)
        self.lines.append(line)

    def todo(self, key, why, indent=0):
        """Emit a TODO placeholder and register it in the warning block."""
        pad = " " * indent
        self.lines.append("{}# TODO {} = ?   ({})".format(pad, key, why))
        self.todos.append((key, why))

    def render(self):
        return "\n".join(self.lines) + "\n"


def emit_warning_block(em, dump, joints):
    em.out("# " + "=" * 76)
    em.out("# DRAFT INI FRAGMENTS - generated by scripts/grbl2ini.py")
    em.out("# " + "=" * 76)
    em.out("#")
    em.out("# This is NOT a working configuration. It is a set of fragments derived")
    em.out("# strictly from a GRBL $$ dump. Every value below traces to a $ setting;")
    em.out("# anything that could not be derived appears as an explicit TODO.")
    em.out("#")
    em.out("# This tool never substitutes a default for a missing measurement.")
    em.out("#")
    em.out("# See docs/06-grbl-to-linuxcnc.md for the conversion reference and citations.")
    em.out("#")

    # Report what we read.
    em.out("# ---- Input summary " + "-" * 57)
    em.out("# settings parsed : {}".format(len(dump.settings)))
    em.out("# joints requested: {}".format(joints))
    if dump.ignored:
        em.out("# non-setting lines ignored (banner/ok/$I): {}".format(dump.ignored))
    if dump.malformed:
        em.out("#")
        em.out("# !! {} MALFORMED LINE(S) - these were NOT parsed:".format(len(dump.malformed)))
        for lineno, text in dump.malformed:
            em.out("#      line {}: {}".format(lineno, text))
        em.out("#    Check them by hand. A setting you meant to provide may be missing.")
    em.out("#")

    # Flag the informational settings that change how the config must be built.
    em.out("# ---- Machine facts from the dump " + "-" * 44)
    for num, label in ((SOFT_LIMITS, "soft limits ($20)"),
                       (HARD_LIMITS, "hard limits ($21)"),
                       (HOMING_ENABLE, "homing cycle ($22)")):
        v = dump.get(num)
        if v is None:
            em.out("# {:<22}: NOT IN DUMP - cannot tell".format(label))
        else:
            em.out("# {:<22}: {} ({})".format(
                label, "ENABLED" if v else "DISABLED", fmt(v)))
    for num, label in ((DIR_INVERT_MASK, "dir invert mask ($3)"),
                       (LIMIT_INVERT, "limit pins invert ($5)")):
        v = dump.get(num)
        if v is not None:
            em.out("# {:<22}: {} - not translatable on paper, see below".format(label, fmt(v)))
    if dump.get(HARD_LIMITS) == 0 and dump.get(HOMING_ENABLE) == 0:
        em.out("#")
        em.out("#   NOTE: both hard limits and homing were DISABLED on this machine.")
        em.out("#   The switches may never have been wired or may not work. Verify")
        em.out("#   each switch electrically before relying on it.")
    em.out("#")


def emit_todo_summary(em):
    """Re-list every TODO at the end, so nothing is lost in the body."""
    em.out("")
    em.out("# " + "=" * 76)
    em.out("# MUST BE FILLED IN BY HAND - {} item(s)".format(len(em.todos)))
    em.out("# " + "=" * 76)
    em.out("#")
    if not em.todos:
        em.out("# (none)")
        return
    seen = set()
    for key, why in em.todos:
        if key in seen:
            continue
        seen.add(key)
        em.out("#   {:<24} {}".format(key, why))
    em.out("#")
    em.out("# Where these come from:")
    em.out("#   STEPSPACE / DIRSETUP / DIRHOLD  -> the stepper driver's datasheet.")
    em.out("#       GRBL has no equivalent settings at all. Do not guess: these")
    em.out("#       protect the driver.")
    em.out("#   FERROR / MIN_FERROR             -> your tolerance for following error.")
    em.out("#       GRBL is open-loop and has no such concept.")
    em.out("#   HOME_*_VEL signs                -> determined empirically. Jog the axis")
    em.out("#       and watch which way it moves. $23 is a mask over GRBL's internal")
    em.out("#       ordering and cannot be decoded on paper.")
    em.out("#   HOME_SEQUENCE                   -> your homing order. Required if the")
    em.out("#       switches share one input pin.")
    em.out("#   HOME / HOME_OFFSET              -> where you want the origin.")
    em.out("#   MIN_LIMIT / MAX_LIMIT signs     -> depends which end you home to.")
    em.out("#   STEPGEN_MAX_VEL / _MAX_ACC      -> 1-10% above the joint limits.")


def emit_joint(em, dump, jnum):
    """Emit one [JOINT_n] fragment."""
    letter = AXIS_LETTERS[jnum] if jnum < len(AXIS_LETTERS) else "?"
    em.out("")
    em.out("# " + "-" * 76)
    em.out("[JOINT_{}]   # GRBL axis {}".format(jnum, letter))
    em.out("# " + "-" * 76)
    em.kv("TYPE", "LINEAR")

    # --- SCALE, from $10x. Magnitude only; sign is a human decision (Trap 5).
    v, why = conv_passthrough(dump, STEPS_PER_MM_BASE + jnum, "steps/mm", "steps/mm")
    if v is None:
        em.todo("SCALE", why)
    else:
        em.kv("SCALE", fmt(v), why)
        mask = dump.get(DIR_INVERT_MASK)
        if mask:
            em.out("#   $3={:g} is set - this axis may need SCALE negated.".format(mask))
            em.out("#   The mask is not decodable on paper. Jog and check, then flip the")
            em.out("#   sign here if the axis runs backwards.")

    # --- MAX_VELOCITY, from $11x. The /60 conversion (Trap 1).
    v, why = conv_per_minute_to_per_second(dump, MAX_RATE_BASE + jnum, "max rate")
    if v is None:
        em.todo("MAX_VELOCITY", why)
        vel = None
    else:
        em.kv("MAX_VELOCITY", fmt(v), why)
        vel = v

    # --- MAX_ACCELERATION, from $12x. Unchanged (Trap 4).
    v, why = conv_passthrough(dump, ACCEL_BASE + jnum, "acceleration", "mm/sec^2")
    if v is None:
        em.todo("MAX_ACCELERATION", why)
        acc = None
    else:
        em.kv("MAX_ACCELERATION", fmt(v), why)
        acc = v

    # --- Stepgen headroom. Derived arithmetic, but the *policy* (how much headroom)
    #     is a human choice, so present it as a suggestion in a comment, not a value.
    em.out("#")
    if vel is not None:
        em.todo("STEPGEN_MAX_VEL",
                "1-10% above MAX_VELOCITY; e.g. {}".format(fmt(vel * 1.1)))
    else:
        em.todo("STEPGEN_MAX_VEL", "needs MAX_VELOCITY first")
    if acc is not None:
        em.todo("STEPGEN_MAX_ACC",
                "1-10% above MAX_ACCELERATION; e.g. {}".format(fmt(acc * 1.1)))
    else:
        em.todo("STEPGEN_MAX_ACC", "needs MAX_ACCELERATION first")

    # --- Following error. No GRBL source whatsoever.
    em.out("#")
    em.todo("FERROR", "no GRBL equivalent - GRBL is open-loop")
    em.todo("MIN_FERROR", "no GRBL equivalent - GRBL is open-loop")

    # --- Soft limits, from $13x. Magnitude known, placement is not.
    em.out("#")
    travel = dump.get(MAX_TRAVEL_BASE + jnum)
    if travel is None:
        em.todo("MIN_LIMIT", "${} (max travel) not present in dump".format(MAX_TRAVEL_BASE + jnum))
        em.todo("MAX_LIMIT", "${} (max travel) not present in dump".format(MAX_TRAVEL_BASE + jnum))
    else:
        em.out("#   ${}={:g} mm of travel. Which coordinates that maps to depends on".format(
            MAX_TRAVEL_BASE + jnum, travel))
        em.out("#   which end you home to and what HOME_OFFSET you pick:")
        em.out("#     home at minimum end -> MIN_LIMIT = 0        MAX_LIMIT = {}".format(fmt(travel)))
        em.out("#     home at maximum end -> MIN_LIMIT = {:<8} MAX_LIMIT = 0".format(fmt(-travel)))
        em.todo("MIN_LIMIT", "pick per homing end; travel is {} mm".format(fmt(travel)))
        em.todo("MAX_LIMIT", "pick per homing end; travel is {} mm".format(fmt(travel)))

    # --- Stepgen timing. Only STEPLEN has a GRBL source.
    em.out("#")
    em.out("#   Stepgen timing (nanoseconds). Only STEPLEN comes from GRBL.")
    v, why = conv_microseconds_to_nanoseconds(dump, STEP_PULSE, "step pulse")
    if v is None:
        em.todo("STEPLEN", why + " - get from the driver datasheet")
    else:
        em.kv("STEPLEN", fmt(v), why)
    em.todo("STEPSPACE", "no GRBL equivalent - from the driver datasheet")
    em.todo("DIRSETUP", "no GRBL equivalent - from the driver datasheet")
    em.todo("DIRHOLD", "no GRBL equivalent - from the driver datasheet")

    # --- Homing. Signs and sequence are human decisions.
    em.out("#")
    em.out("#   Homing. Magnitudes from GRBL; SIGNS must be determined empirically.")
    v, why = conv_per_minute_to_per_second(dump, HOMING_SEEK, "homing seek")
    if v is None:
        em.todo("HOME_SEARCH_VEL", why)
    else:
        em.out("#   HOME_SEARCH_VEL magnitude = {}   ({})".format(fmt(v), why))
        em.todo("HOME_SEARCH_VEL", "magnitude {}, sign unknown".format(fmt(v)))

    v, why = conv_per_minute_to_per_second(dump, HOMING_FEED, "homing feed")
    if v is None:
        em.todo("HOME_LATCH_VEL", why)
    else:
        em.out("#   HOME_LATCH_VEL  magnitude = {}   ({})".format(fmt(v), why))
        em.todo("HOME_LATCH_VEL", "magnitude {}, sign unknown".format(fmt(v)))

    pulloff = dump.get(HOMING_PULLOFF)
    if pulloff is not None:
        em.out("#   $27={:g} mm pull-off. NOT the same as HOME_OFFSET - $27 is a".format(pulloff))
        em.out("#   retreat distance, HOME_OFFSET defines a coordinate. Set deliberately.")
    em.todo("HOME", "where the joint should end up after homing")
    em.todo("HOME_OFFSET", "coordinate assigned at the switch trip point")
    em.todo("HOME_SEQUENCE", "homing order; required if switches share one input")


def emit_axis(em, dump, jnum):
    """Emit one [AXIS_x] fragment. With trivkins this mirrors the joint."""
    if jnum >= len(AXIS_LETTERS):
        return
    letter = AXIS_LETTERS[jnum]
    em.out("")
    em.out("# " + "-" * 76)
    em.out("[AXIS_{}]   # mirrors JOINT_{} under trivkins".format(letter, jnum))
    em.out("# " + "-" * 76)

    v, why = conv_per_minute_to_per_second(dump, MAX_RATE_BASE + jnum, "max rate")
    if v is None:
        em.todo("[AXIS_{}]MAX_VELOCITY".format(letter), why)
    else:
        em.kv("MAX_VELOCITY", fmt(v), why)

    v, why = conv_passthrough(dump, ACCEL_BASE + jnum, "acceleration", "mm/sec^2")
    if v is None:
        em.todo("[AXIS_{}]MAX_ACCELERATION".format(letter), why)
    else:
        em.kv("MAX_ACCELERATION", fmt(v), why)

    travel = dump.get(MAX_TRAVEL_BASE + jnum)
    if travel is None:
        em.todo("[AXIS_{}]MIN_LIMIT".format(letter), "no $13x in dump")
        em.todo("[AXIS_{}]MAX_LIMIT".format(letter), "no $13x in dump")
    else:
        em.out("#   Must match [JOINT_{}] limits once you have chosen them.".format(jnum))
        em.todo("[AXIS_{}]MIN_LIMIT".format(letter),
                "match JOINT_{}; travel {} mm".format(jnum, fmt(travel)))
        em.todo("[AXIS_{}]MAX_LIMIT".format(letter),
                "match JOINT_{}; travel {} mm".format(jnum, fmt(travel)))


def convert(text, joints=3):
    """Convert a raw dump into INI fragment text. Returns a string."""
    dump = parse_dump(text)
    em = Emitter()
    emit_warning_block(em, dump, joints)
    for j in range(joints):
        emit_joint(em, dump, j)
    for j in range(joints):
        emit_axis(em, dump, j)
    emit_todo_summary(em)
    return em.render()


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Convert a GRBL 1.1 $$ dump into draft LinuxCNC INI fragments.",
        epilog="Emits TODO placeholders for anything it cannot derive from the input. "
               "It never invents values.",
    )
    ap.add_argument("dump", nargs="?", default="-",
                    help="file containing the $$ dump, or - for stdin (default)")
    ap.add_argument("--joints", type=int, default=3, metavar="N",
                    help="number of joints to emit (default: 3)")
    args = ap.parse_args(argv)

    if args.joints < 1:
        ap.error("--joints must be at least 1")
    if args.joints > len(AXIS_LETTERS):
        ap.error("--joints greater than {} is not supported (axis letters run out)".format(
            len(AXIS_LETTERS)))

    if args.dump == "-":
        text = sys.stdin.read()
    else:
        try:
            with open(args.dump, "r", encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except OSError as exc:
            sys.stderr.write("error: cannot read {}: {}\n".format(args.dump, exc))
            return 2

    if not text.strip():
        sys.stderr.write("error: input is empty - no $$ dump to convert\n")
        return 2

    parsed = parse_dump(text)
    if not parsed.settings:
        sys.stderr.write(
            "error: no GRBL settings found in input.\n"
            "       Expected lines like '$100=250.000'.\n"
            "       If the machine returned nothing for $$, the firmware is probably\n"
            "       not GRBL - see docs/06-grbl-to-linuxcnc.md.\n")
        return 1

    sys.stdout.write(convert(text, joints=args.joints))
    return 0


if __name__ == "__main__":
    sys.exit(main())
