"""MDI: one gate for both adapters.

Pure G-code word parsing with no LinuxCNC dependency: mdi_refusal decides
whether an MDI line may be sent at all, and _needs_homing whether it moves the
machine. Both adapters import them from here (adapter.py re-exports them), so
the mock cannot drift from what the real adapter - and LinuxCNC - refuse.

Sources, relative to reference/linuxcnc/:

  interp_internal.cc  src/emc/rs274ngc/interp_internal.cc  (close_and_downcase,
                                                            enhance_block)
  interp_array.cc     src/emc/rs274ngc/interp_array.cc     (_gees: G modal groups)
  g-code.adoc         docs/src/gcode/g-code.adoc
  overview.adoc       docs/src/gcode/overview.adoc
  emctaskmain.cc      src/emc/task/emctaskmain.cc
  interp_read.cc      src/emc/rs274ngc/interp_read.cc      (read_operation_unary)
  rs274ngc_pre.cc     src/emc/rs274ngc/rs274ngc_pre.cc     (the INI the interpreter reads)
"""

from __future__ import annotations

import re
from typing import Any, FrozenSet, NamedTuple, Optional

#: Comments: "(...)" and everything after ";". A ";" inside parentheses is
#: part of that comment, as in close_and_downcase (interp_internal.cc:78-79):
#: the alternation tries "(" first at each position.
_COMMENT_RE = re.compile(r"\([^)]*\)|;.*$")
#: close_and_downcase drops blanks, tabs and CRs outside comments
#: (interp_internal.cc:91-92), so "X - 10" is "x-10" and "G 0" is "g0".
_BLANKS_RE = re.compile(r"[ \t\r]")
#: A named parameter, "#<name>". Replaced by a numbered one ("#0") so that
#: letters inside the name ("#<x1>") are never read as words.
_NAMED_PARAM_RE = re.compile(r"#<[^>]*>")
#: O-words, numbered or named ("O100 call", "o<sub> call").
_O_WORD_RE = re.compile(r"O[<\d\[#]")
#: The unary functions a word's value may start with (read_operation_unary,
#: interp_read.cc:1391-1480). Longer names first where one starts another.
_UNARY_NAMES = ("ACOS", "ASIN", "ATAN", "ABS", "COS", "EXISTS", "EXP", "FIX", "FUP", "LN",
                "ROUND", "SIN", "SQRT", "TAN")
#: A word's letter followed by a unary function's name and its bracket: the
#: interpreter reads the letter, then a value that starts with the function
#: (read_real_value -> read_unary, interp_read.cc:3189-3220). The name is
#: dropped and the letter kept, so "XSIN[30]" reads as "X[30]" - an axis
#: word - "ACOS[0.5]" as the A word "A[0.5]", and "GFUP[27.5]" as "G[27.5]",
#: a G code that cannot be known here (it is G28). A function inside brackets
#: ("X[SIN[30]]") goes with the bracket reduction.
_UNARY_RE = re.compile(r"([A-Z@^][-+]*)(?:{})(?=\[)".format("|".join(_UNARY_NAMES)))
#: A G word whose number is an expression or a parameter ("G[1]", "G#1"):
#: which G code it is cannot be known here.
_G_EXPRESSION_RE = re.compile(r"G[-+]*[\[#]")
#: An M word whose number is an expression ("M[98]", "M[6]"): read_m takes
#: one (read_integer_value, interp_read.cc:1119, :783-797), so which M code
#: it is cannot be known here either - as for "M#1" below.
_M_EXPRESSION_RE = re.compile(r"M[-+]*\[")
#: An innermost bracketed expression; reduced to a number repeatedly.
_INNER_BRACKET_RE = re.compile(r"\[[^\[\]]*\]")
#: A word: a letter (or "@" / "^", the polar words read by read_atsign and
#: read_carat, src/emc/rs274ngc/interp_read.cc:3339-3357) and its value. Any run of signs is
#: allowed, so a malformed "X--1" errs towards a word. "#" stands for a
#: parameter value; brackets are already reduced to "0" when this runs.
_WORD_RE = re.compile(r"([A-Z@^])[-+]*(\d+\.?\d*|\.\d+|#)")

#: G codes that move the machine by themselves: modal group 1 in _gees
#: (interp_array.cc:81-132) without G80, which cancels motion - G0-G3, G5,
#: G5.1, G5.2, G33, G33.1, G38.2-G38.5, the lathe cycles G70, G71, G71.1,
#: G71.2, G72, G72.1, G72.2 (g-code.adoc:1771, :1817; enhance_block lets them
#: run with no axis words, interp_internal.cc:190-196), the canned cycles G73,
#: G74 (left-hand tapping, g-code.adoc:1921; is_a_cycle, interp_internal.hh:
#: 837-839), G76 and G81-G89 - plus G5.3, which ends a NURBS block and
#: errs towards yes, and G28/G30, which move even with no axis words
#: (g-code.adoc:1004-1040).
_MOTION_G = frozenset(
    (0.0, 1.0, 2.0, 3.0, 5.0, 5.1, 5.2, 5.3, 28.0, 30.0, 33.0, 33.1,
     38.2, 38.3, 38.4, 38.5,
     70.0, 71.0, 71.1, 71.2, 72.0, 72.1, 72.2, 73.0, 74.0, 76.0)
    + tuple(float(n) for n in range(81, 90))
)
#: The group 0 codes that take axis words as data: G10, G52, G92
#: (mode_zero_covets_axes minus G28/G30, which move; interp_internal.cc:
#: 175-177, :198-203). On such a line no word is a move, polar and I/J/K
#: words included.
_ALL_WORDS_ARE_DATA_G = frozenset((10.0, 52.0, 92.0))
#: G43.1 takes axis words as tool offsets and does not move ("G43.1 does not
#: cause any motion", g-code.adoc:1517-1530; interp_internal.cc:209-211 skips
#: motion_to_be for it). G43.2 is NOT here: for G43.2 the same lines DO set
#: motion_to_be to the modal motion code, so "G43.2 X0.01" under a modal G1
#: is a move.
_AXIS_WORDS_ARE_DATA_G = frozenset((43.1,))
#: Axis words (enhance_block's axis_flag, interp_internal.cc:165-169) and the
#: polar words "@" (radius) and "^" (angle), which move under the modal
#: motion code like axis words (polar_flag, :170; overview.adoc:890-918).
_AXIS_LETTERS = frozenset("XYZABCUVW@^")
#: Arc words. A line of only I/J/K moves when G2/G3 is the modal motion code
#: (interp_internal.cc:212-215). The modal code is not visible here, so they
#: always count. R is counted too, by choice: enhance_block does not make an
#: R-only line a move, but this parse errs towards yes (and "M19 R90" is
#: therefore refused unhomed, too).
_ARC_LETTERS = frozenset("IJKR")


#: M98 calls a subprogram (Fanuc style, interp_o_word.cc:178, :208-218):
#: its body is not visible here, as an O-word call's is not.
_SUBPROGRAM_M = 98
#: M6, the tool change.
_TOOL_CHANGE_M = 6


class MdiContext(NamedTuple):
    """What the INI adds to the MDI gate (mdi_context_from_ini)."""

    #: M6 moves the machine: [EMCIO]TOOL_CHANGE_AT_G30 or
    #: TOOL_CHANGE_QUILL_UP, which the interpreter reads (rs274ngc_pre.cc:
    #: 869-870), or TOOL_CHANGE_POSITION, which task reads
    #: (taskclass.cc:463-480).
    tool_change_moves: bool = False
    #: The codes [RS274NGC]REMAP remaps ("M6", "G88.1", "T", ...): each runs
    #: code this parse cannot see (rs274ngc_pre.cc:1030-1035).
    remapped: FrozenSet[str] = frozenset()


def _ini_flag(value: Any) -> bool:
    """An [EMCIO] integer flag as IniFile::Find(int*) reads it: a number
    other than 0 is on. A value that is not a number errs towards on."""
    if value is None:
        return False
    try:
        return int(float(value.raw)) != 0
    except ValueError:
        return value.as_bool() is not False


def mdi_context_from_ini(ini: Any) -> MdiContext:
    """MdiContext from an iniload.IniFile; the empty context with none."""
    if ini is None:
        return MdiContext()
    moves = (_ini_flag(ini.get("EMCIO", "TOOL_CHANGE_AT_G30"))
             or _ini_flag(ini.get("EMCIO", "TOOL_CHANGE_QUILL_UP"))
             or ini.get("EMCIO", "TOOL_CHANGE_POSITION") is not None)
    remapped = set()
    for raw in ini.get_all("RS274NGC", "REMAP"):
        words = raw.split()
        if words:
            code = words[0].upper()
            # "M06" and "M6" are one code; "G88.1" keeps its fraction.
            m = re.match(r"^([GM])0*(\d+(?:\.\d+)?)$", code)
            remapped.add(m.group(1) + m.group(2) if m else code)
    return MdiContext(bool(moves), frozenset(remapped))


def _code(letter: str, value: str) -> str:
    """A word as REMAP names it: 'M6', 'G88.1'."""
    number = float(value)
    return letter + ("{:g}".format(number) if number != int(number) else str(int(number)))


def _needs_homing(command: str, context: MdiContext = MdiContext()) -> bool:
    """Whether an MDI line implies motion. Errs towards "yes".

    A small word parse, not an interpreter. Like close_and_downcase
    (interp_internal.cc:70-107) it drops comments, blanks and tabs first;
    then named parameters become "#0" and bracketed expressions count as
    numbers, and

      - an O-word (subroutine call or flow control) means motion, because its
        body is not visible here; so does a G word whose number is an
        expression or parameter;
      - any motion G code (_MOTION_G) means motion;
      - a G10/G52/G92 line is data, never motion;
      - axis or polar words mean motion unless G43.1 consumes them: a bare
        "X10" re-runs the modal motion code (overview.adoc:879-884), and
        "G53 G0 X0" is a move;
      - I/J/K/R words alone mean motion (see _ARC_LETTERS).

      - a value written as a unary function ("XSIN[30]") is that word's
        value; a G code so written ("GFUP[27.5]") cannot be known: motion;
      - M98 (a subprogram call) means motion; so does M6 when the INI makes
        a tool change move (context.tool_change_moves), and any code the
        INI remaps (context.remapped) - P0 cleanup; so does an M code held
        in a parameter or written as an expression ("M#1", "M[98]"), which
        cannot be known here (review of the P0 cleanup).

    G20/G21, G17-G19, G90/G91, G54-G59.3, G28.1/G30.1 without axis words,
    G4, M, S, F and T words alone are not motion. Not covered: user-defined
    M100-M199 programs (they run a program outside LinuxCNC, no motion of
    their own).
    """
    text = _BLANKS_RE.sub("", _COMMENT_RE.sub("", command.upper()))
    text = _NAMED_PARAM_RE.sub("#0", text)
    text = _UNARY_RE.sub(r"\1", text)
    if _O_WORD_RE.search(text):
        return True
    if _G_EXPRESSION_RE.search(text) or _M_EXPRESSION_RE.search(text):
        return True
    while "[" in text:
        reduced = _INNER_BRACKET_RE.sub("0", text)
        if reduced == text:   # unbalanced bracket: not parseable, so assume motion
            return True
        text = reduced
    g_codes = set()
    m_codes = set()
    axis_words = arc_words = False
    for letter, value in _WORD_RE.findall(text):
        if context.remapped and value != "#" and (
                _code(letter, value) in context.remapped or letter in context.remapped):
            return True
        if value == "#":
            if letter in "GM" or letter in context.remapped:
                return True       # which code a parameter holds is not known here
            if letter in _AXIS_LETTERS:
                axis_words = True
            elif letter in _ARC_LETTERS:
                arc_words = True
            continue
        if letter == "G":
            g_codes.add(round(float(value), 1))
        elif letter == "M":
            m_codes.add(round(float(value), 1))
        elif letter in _AXIS_LETTERS:
            axis_words = True
        elif letter in _ARC_LETTERS:
            arc_words = True
    if g_codes & _MOTION_G:
        return True
    if _SUBPROGRAM_M in m_codes:
        return True
    if context.tool_change_moves and _TOOL_CHANGE_M in m_codes:
        return True
    if g_codes & _ALL_WORDS_ARE_DATA_G:
        return False
    if axis_words:
        return not (g_codes & _AXIS_WORDS_ARE_DATA_G)
    return arc_words


#: mdi_refusal while something is paused.
MDI_WHILE_PAUSED = (
    "Duraklatılmış bir program ya da MDI satırı var — MDI kabul edilmedi. "
    "Önce «Devam» ya da «Durdur»."
)


#: mdi_refusal while a joint homes.
MDI_WHILE_HOMING = (
    "Referans alma sürüyor — MDI gönderilmez (MDI moduna geçiş referans almayı keser)."
)


def mdi_refusal(
    command: str,
    machine_on: bool,
    homed: bool,
    no_force_homing: bool,
    program_running: bool,
    paused: bool = False,
    homing: bool = False,
    context: MdiContext = MdiContext(),
) -> Optional[str]:
    """Why an MDI line must not be sent, or None. Shared by both adapters.

    `command` is already stripped. Before homing LinuxCNC refuses EVERY MDI
    command unless [TRAJ]NO_FORCE_HOMING is set (emctaskmain.cc:2208-2212).
    With it set, this UI still refuses lines that move (_needs_homing): an
    unhomed machine has no soft limits to stop the move.

    `paused`: a program or an MDI line is paused (stat.paused or the
    interpreter PAUSED). In MDI mode task takes the new line whatever the
    pause: it queues it behind the paused one, or issues it at once when
    nothing else is waiting (reference/linuxcnc/src/emc/task/
    emctaskmain.cc:1422-1439), and its moves join motion's paused queue.
    So «Devam», which says it resumes the paused line, would move this one
    too - motion the operator's resume did not name (final P0 review).

    `homing`: a joint homes. Any line is refused, a non-motion one too: the
    switch to MDI mode runs emcTaskAbort (emcTaskSetMode, emctask.cc:
    286-289), which can cancel the homing (P0 cleanup). Without
    [TRAJ]NO_FORCE_HOMING the "not homed" refusal below already covers it.

    `context`: what the INI adds to "does it move" (MdiContext).
    """
    if not command:
        return "Boş komut."
    if not machine_on:
        return "Makine devre dışı — MDI kabul edilmedi."
    if homing:
        return MDI_WHILE_HOMING
    if paused:
        return MDI_WHILE_PAUSED
    if program_running:
        return "Program çalışıyor — MDI kabul edilmedi."
    if not homed and not no_force_homing:
        return (
            "Referans alınmadı — LinuxCNC referanssız MDI kabul etmiyor "
            "([TRAJ]NO_FORCE_HOMING yok)."
        )
    if not homed and _needs_homing(command, context):
        return "Referans alınmadı — koordinatlı hareket kabul edilmiyor."
    return None
