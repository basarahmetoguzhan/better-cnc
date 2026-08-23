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
"""

from __future__ import annotations

import os
import re
from typing import Dict, List, NamedTuple, Optional, Tuple

SECTION_RE = re.compile(r"^\[([A-Za-z0-9_]+)\]$")
KEY_RE = re.compile(r"^([A-Za-z0-9_]+)\s*=\s*(.*?)\s*$")

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
    """
    values: Dict[Tuple[str, str], IniValue] = {}
    section = ""
    pending: List[str] = []
    governing = ""

    for line in text.splitlines():
        stripped = line.strip()

        if not stripped:
            # A blank line ends a comment block but does not clear the comment
            # currently governing subsequent keys.
            pending = []
            continue

        if stripped.startswith("#") or stripped.startswith(";"):
            pending.append(stripped.lstrip("#;").strip())
            continue

        m = SECTION_RE.match(stripped)
        if m:
            section = m.group(1)
            # A new section starts fresh: a comment from the previous section
            # must not govern keys here.
            governing = "\n".join(pending)
            pending = []
            continue

        m = KEY_RE.match(stripped)
        if m:
            if pending:
                governing = "\n".join(pending)
                pending = []
            key, raw = m.group(1), m.group(2)
            values[(section, key)] = IniValue(
                section=section,
                key=key,
                raw=raw,
                comment=governing,
                provisional=_is_provisional(governing),
            )
            continue

        # Anything else (continuation lines etc.) breaks the comment run.
        pending = []

    return IniFile(path, values)


def load_ini(path: str) -> IniFile:
    with open(path, "r", encoding="utf-8") as fh:
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
