"""Derive one LinuxCNC INI from another by changing a few keys, line for line.

Used by sim/make_sim_ini.py (the simulator's INI) and deploy/make_kiosk_ini.py
(the kiosk's INI), so that every value they do not change is the rig's own and
the copies cannot drift from it.

Each key to change must be found exactly once, in its section, or derive()
refuses: a renamed key must fail loudly, not leave the derived file running
the original value. Comments, blank lines and every other key are copied
unchanged, so line numbers in the derived file match the source's.
"""

import re
from typing import Dict, Tuple

_SECTION = re.compile(r"^\s*\[([^\]]+)\]")
_KEY = re.compile(r"^(\s*)([A-Za-z0-9_]+)(\s*=\s*)(.*?)\s*$")


def derive(text: str, replace: Dict[Tuple[str, str], str], source: str, append: str = "") -> str:
    """`text` with each (SECTION, KEY) in `replace` set to its new value, then
    `append` (extra sections) at the end. `source` names the file in the
    error message."""
    out = []
    section = None
    hits = {k: 0 for k in replace}
    for line in text.splitlines(keepends=True):
        m = _SECTION.match(line)
        if m:
            section = m.group(1).strip()
            out.append(line)
            continue
        k = _KEY.match(line)
        if k and (section, k.group(2)) in replace:
            key = (section, k.group(2))
            hits[key] += 1
            out.append("{}{}{}{}\n".format(k.group(1), k.group(2), k.group(3), replace[key]))
            continue
        out.append(line)
    bad = {"[{}]{}".format(*k): n for k, n in hits.items() if n != 1}
    if bad:
        raise SystemExit(
            "ini_derive: expected each key exactly once in {}, found {}".format(source, bad)
        )
    if append:
        if out and not out[-1].endswith("\n"):
            out.append("\n")
        out.append(append)
    return "".join(out)
