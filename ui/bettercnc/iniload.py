"""Read a LinuxCNC INI file, keeping track of which values are PROVISIONAL.

Why this exists rather than configparser: we need one thing configparser throws
away - the comments. configs/test-rig/test-rig.ini deliberately marks every value
that was guessed rather than measured:

    # --- Soft limits: PROVISIONAL, MUST BE MEASURED ------------------------
    # ASSUMPTION: 150 mm of usable X travel.
    #   NOT derived from the machine. GRBL's $130 = 200 mm is an untouched
    #   factory default ...
    MIN_LIMIT = 0.0
    MAX_LIMIT = 150.0

LinuxCNC needs *a* number there, so a number exists. But the UI must not show it
as if it were measured. Parsing the ASSUMPTION marker is what lets the UI say
"olculmedi" ("not measured") while still being driven by the real config file.

The useful property: when travel is finally measured and the ASSUMPTION comment
is deleted from the INI, the UI starts showing the numbers with no code change.

Line structure follows LinuxCNC's own reader, IniFile::Find in
reference/linuxcnc/src/libnml/inifile/inifile.cc (cited below as inifile.cc):

  * only "\n" ends a line (fgets, :273), and a trailing backslash joins the next
    line on (:295-327);
  * a line starting with "[" starts a new section whatever its name holds
    (:261 matches "[NAME]" as a prefix, :337-340 ends a section at any "[");
  * "#" and ";" lines are comments (SkipWhite, :556-558);
  * the FIRST occurrence wins. The section search stops at the first line
    starting "[SECTION]" (:261-267) and the key search ends at the next "["
    (:337-340), so the keys of a repeated [SECTION] are never read, even ones
    the first occurrence lacks. Inside the section the first line that
    starts with the key followed by a blank or "=" is the match (:343-354,
    num = 1);
  * the value is what follows the first "=" on that line (AfterEqual,
    :508-538), trailing blanks dropped (:365-370). A matching line with no
    value ("KEY =", or "KEY 1 2" with no "=") makes the key not found
    (:359-363), and a later line for the same key does not help.

Known divergences, none of which the repo's INIs trigger:

  * LinuxCNC's section search (:230-268) does not join backslash lines, its key
    search does. A "\" line directly above a "[SECTION]" header is therefore
    read two ways by LinuxCNC; here it is joined, like the key search does.
  * More than MAX_EXTEND_LINES (20, :27) continuation lines make LinuxCNC refuse
    the key (:309-315). They are joined here.
"""

from __future__ import annotations

import os
import re
from typing import Dict, List, NamedTuple, Optional, Set, Tuple

#: A line IniFile::Find takes as the key's line: the key, then a blank or "="
#: (inifile.cc:343-352). The value is found separately (_after_equal).
TAG_RE = re.compile(r"([A-Za-z0-9_]+)[ \t\r=]")

#: What sscanf("%i") accepts at the start of a value: leading whitespace, a
#: sign, then hex (0x..), octal (0..) or decimal digits. IniFile::Find(int*)
#: tries this before the string table (inifile.cc:122-137).
_SCANF_INT_RE = re.compile(r"[ \t\n\v\f\r]*([+-]?)(0[xX][0-9A-Fa-f]+|0[0-7]*|[1-9][0-9]*)")

#: EmcIniFile::boolMap, src/emc/ini/emcIniFile.cc:50-58. Compared with
#: strcasecmp, i.e. ASCII case-insensitively (inifile.cc:130).
_BOOL_WORDS = {"TRUE": True, "YES": True, "1": True, "FALSE": False, "NO": False, "0": False}

#: A comment block containing any of these marks every value it governs as
#: provisional - present in the file, but not established from the machine.
PROVISIONAL_MARKERS = ("ASSUMPTION:", "MUST BE MEASURED", "PROVISIONAL")


class IniValue(NamedTuple):
    """One INI key, with the provenance we can recover from the file."""

    section: str
    key: str
    raw: str
    comment: str
    provisional: bool

    def as_float(self) -> float:
        return float(self.raw)

    def as_int(self) -> int:
        return int(float(self.raw))

    def as_bool(self) -> Optional[bool]:
        """The value as LinuxCNC reads a boolean, or None if it cannot.

        EmcIniFile::Find(bool*) (src/emc/ini/emcIniFile.cc:60-71) goes through
        IniFile::Find(int*, boolMap) (inifile.cc:107-141): an integer prefix
        first, any non-zero value meaning true ("2", "0x10", "-1"); then
        TRUE/YES/1 or FALSE/NO/0, case-insensitively. Anything else is a
        conversion error (inifile.cc:139). The joint loader turns that error
        into a failed joint init (src/emc/ini/inijoint.cc:102, :236-239), so
        such a value is unknown, never a silent False.

        An empty value is None too, though parse_ini_text never stores one:
        LinuxCNC treats "KEY =" as absent (see parse_ini_text).
        """
        m = _SCANF_INT_RE.match(self.raw)
        if m:
            digits = m.group(2)
            if digits[:2] in ("0x", "0X"):
                number = int(digits, 16)
            elif digits.startswith("0"):
                number = int(digits, 8)
            else:
                number = int(digits)
            return number != 0
        if not self.raw.isascii():
            return None
        return _BOOL_WORDS.get(self.raw.upper())


class IniFile:
    """Parsed INI with provenance. Read-only."""

    def __init__(self, path: str, values: Dict[Tuple[str, str], IniValue]):
        self.path = path
        self._values = values

    # -- lookup ---------------------------------------------------------------

    def get(self, section: str, key: str) -> Optional[IniValue]:
        return self._values.get((section, key))

    def require(self, section: str, key: str) -> IniValue:
        v = self.get(section, key)
        if v is None:
            raise KeyError(
                "[{}]{} missing from {} - the UI refuses to invent a default "
                "for a hardware value".format(section, key, self.path)
            )
        return v

    def sections(self) -> List[str]:
        out: List[str] = []
        for sec, _ in self._values:
            if sec not in out:
                out.append(sec)
        return out

    def keys(self, section: str) -> List[str]:
        return [k for (s, k) in self._values if s == section]

    def __contains__(self, item: Tuple[str, str]) -> bool:
        return item in self._values

    def __len__(self) -> int:
        return len(self._values)


def _logical_lines(text: str) -> List[str]:
    """Physical lines joined the way IniFile::Find joins them.

    A line whose last character before its "\n" is a backslash loses the
    backslash and has the next line appended verbatim (inifile.cc:295-323), so
    'A = "x \\' + 'y"' reads as 'A = "x y"'. Only "\n" ends a line: a "\r"
    before it (a DOS line ending) means the backslash is not last, and the line
    is not continued, as in LinuxCNC.

    A continuation still open at the end of the file is dropped: LinuxCNC hits
    end of file while extending and reports the key as not found
    (inifile.cc:273-277). A final line with no "\n" after it is taken as it is.
    """
    physical = text.split("\n")
    ends_with_newline = physical[-1] == ""
    if ends_with_newline:
        physical.pop()  # what follows the last "\n" is not a line (fgets: EOF)
    out: List[str] = []
    joined = ""
    extending = False
    for i, line in enumerate(physical):
        terminated = ends_with_newline or i < len(physical) - 1
        if terminated and line.endswith("\\"):
            joined += line[:-1]
            extending = True
            continue
        out.append(joined + line if extending else line)
        joined = ""
        extending = False
    return out


def parse_ini_text(text: str, path: str = "<string>") -> IniFile:
    """Parse INI text, attaching the nearest preceding comment block to each key.

    A comment block governs every key that follows it until the next comment
    block. That matters because the file writes one ASSUMPTION block above a run
    of keys:

        # ASSUMPTION: 150 mm of usable X travel.
        MIN_LIMIT = 0.0
        MAX_LIMIT = 150.0

    Attaching comments only to the immediately-next key would mark MIN_LIMIT
    provisional and miss MAX_LIMIT, which is exactly the sort of half-applied
    rule that leaks a fabricated number into the UI.

    The rule is fail-safe in one direction: a line the loader cannot parse
    does not end a pending comment block. Only a blank line, a section header
    or a key with a value does. So an ASSUMPTION block, a stray line and then
    a key still taints that key.

    Repeats resolve as LinuxCNC resolves them: the first [SECTION] and the
    first line for a key win (module docstring).
    """
    values: Dict[Tuple[str, str], IniValue] = {}
    #: Keys LinuxCNC has already matched once, with or without a value: a
    #: later line for the same key is never reached (inifile.cc:354).
    matched: Set[Tuple[str, str]] = set()
    #: Section names already started: LinuxCNC never reads past the first
    #: "[NAME]" (inifile.cc:261-267).
    started: Set[str] = set()
    #: None while inside a "[" line LinuxCNC cannot find by name (no "]"), or
    #: inside a repeat of a section that already appeared.
    section: Optional[str] = ""
    pending: List[str] = []
    governing = ""

    for line in _logical_lines(text):
        stripped = line.strip()

        if not stripped:
            # A blank line ends a comment block but does not clear the comment
            # currently governing subsequent keys.
            pending = []
            continue

        if stripped.startswith("#") or stripped.startswith(";"):
            pending.append(stripped.lstrip("#;").strip())
            continue

        if stripped.startswith("["):
            # Any "[" line ends the previous section (inifile.cc:337-340). Its
            # name is what LinuxCNC's "[%s]" prefix match can find
            # (inifile.cc:227, :261): the text up to the first "]". Without a
            # "]" no name matches it, and its keys are unreachable.
            close = stripped.find("]")
            section = stripped[1:close] if close != -1 else None
            if section is not None:
                if section in started:
                    # A repeat: Find stops at the first "[NAME]" line
                    # (inifile.cc:261-267) and its key search ends at the
                    # next "[" (:337-340), so these keys are never read.
                    section = None
                else:
                    started.add(section)
            # A new section starts fresh: a comment from the previous section
            # must not govern keys here. A block directly above the header
            # does: it is the header's own comment.
            governing = "\n".join(pending)
            pending = []
            continue

        # Matched after SkipWhite's own blanks (inifile.cc:560-561), so a
        # DOS "KEY\r" line is still a key line, as it is for LinuxCNC.
        head = line.lstrip(" \t\r\n")
        m = TAG_RE.match(head)
        if m:
            key = m.group(1)
            raw = _after_equal(head[m.end(1):])
            # A key line with no value ("KEY =", or upstream's
            # "TOOL_CHANGE_POSITION -18 -12 0") is not found by LinuxCNC
            # (inifile.cc:359-363). Like any other unparsed line it leaves
            # `pending` alone: dropping it would let the key below read as
            # measured although an ASSUMPTION block sits right above it.
            if raw is not None and pending:
                governing = "\n".join(pending)
                pending = []
            if section is None or (section, key) in matched:
                continue
            # The first line for the key decides, with or without a value.
            matched.add((section, key))
            if raw is not None:
                values[(section, key)] = IniValue(
                    section=section,
                    key=key,
                    raw=raw,
                    comment=governing,
                    provisional=_is_provisional(governing),
                )
            continue

        # Anything else is ignored and leaves `pending` alone, as above.

    return IniFile(path, values)


def _after_equal(rest: str) -> Optional[str]:
    """The value IniFile::Find returns for a key line, or None if it has none.

    AfterEqual (inifile.cc:508-538): the text after the first "=", from its
    first character that is not a blank, or NULL when there is no "=" or
    nothing but blanks after it. Find then drops trailing blanks
    (:365-370).
    """
    eq = rest.find("=")
    if eq == -1:
        return None
    value = rest[eq + 1:].lstrip(" \t\r\n").rstrip(" \t\r")
    return value or None


def load_ini(path: str) -> IniFile:
    # newline="" keeps "\r" so a DOS line ending reaches _logical_lines as
    # LinuxCNC's fgets sees it, instead of being translated to "\n".
    with open(path, "r", encoding="utf-8", newline="") as fh:
        return parse_ini_text(fh.read(), path)


def _is_provisional(comment: str) -> bool:
    up = comment.upper()
    return any(marker.upper() in up for marker in PROVISIONAL_MARKERS)


# -- convenience ------------------------------------------------------------

def repo_root() -> str:
    """Repo root, derived from this file's location.

    ui/bettercnc/iniload.py -> ui/bettercnc -> ui -> repo root
    """
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.dirname(os.path.dirname(here))


def test_rig_ini_path() -> str:
    return os.path.join(repo_root(), "configs", "test-rig", "test-rig.ini")
