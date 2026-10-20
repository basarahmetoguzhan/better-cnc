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
"""

from __future__ import annotations

import re
from typing import Optional

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
#: A G word whose number is an expression or a parameter ("G[1]", "G#1"):
#: which G code it is cannot be known here.
_G_EXPRESSION_RE = re.compile(r"G[-+]*[\[#]")
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


def _needs_homing(command: str) -> bool:
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

    G20/G21, G17-G19, G90/G91, G54-G59.3, G28.1/G30.1 without axis words,
    G4, M, S, F and T words alone are not motion. Not covered: M6 with
    [EMCIO]TOOL_CHANGE_AT_G30 moves the machine (g-code.adoc:1058-1059), and
    remapped or user-defined codes run code this parse cannot see.
    """
    text = _BLANKS_RE.sub("", _COMMENT_RE.sub("", command.upper()))
    text = _NAMED_PARAM_RE.sub("#0", text)
    if _O_WORD_RE.search(text):
        return True
    if _G_EXPRESSION_RE.search(text):
        return True
    while "[" in text:
        reduced = _INNER_BRACKET_RE.sub("0", text)
        if reduced == text:   # unbalanced bracket: not parseable, so assume motion
            return True
        text = reduced
    g_codes = set()
    axis_words = arc_words = False
    for letter, value in _WORD_RE.findall(text):
        if letter == "G":
            g_codes.add(round(float(value), 1))
        elif letter in _AXIS_LETTERS:
            axis_words = True
        elif letter in _ARC_LETTERS:
            arc_words = True
    if g_codes & _MOTION_G:
        return True
    if g_codes & _ALL_WORDS_ARE_DATA_G:
        return False
    if axis_words:
        return not (g_codes & _AXIS_WORDS_ARE_DATA_G)
    return arc_words


def mdi_refusal(
    command: str,
    machine_on: bool,
    homed: bool,
    no_force_homing: bool,
    program_running: bool,
) -> Optional[str]:
    """Why an MDI line must not be sent, or None. Shared by both adapters.

    `command` is already stripped. Before homing LinuxCNC refuses EVERY MDI
    command unless [TRAJ]NO_FORCE_HOMING is set (emctaskmain.cc:2208-2212).
    With it set, this UI still refuses lines that move (_needs_homing): an
    unhomed machine has no soft limits to stop the move.
    """
    if not command:
        return "Boş komut."
    if not machine_on:
        return "Makine devre dışı — MDI kabul edilmedi."
    if program_running:
        return "Program çalışıyor — MDI kabul edilmedi."
    if not homed and not no_force_homing:
        return (
            "Referans alınmadı — LinuxCNC referanssız MDI kabul etmiyor "
            "([TRAJ]NO_FORCE_HOMING yok)."
        )
    if not homed and _needs_homing(command):
        return "Referans alınmadı — koordinatlı hareket kabul edilmiyor."
    return None
