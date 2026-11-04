"""Shared test fixtures.

Deliberately thin. Tests read the real config files rather than fixtures wherever
a hardware number is involved, because the point is to tie the UI to the config -
a fixture copy of SCALE would defeat that.
"""

from __future__ import annotations

import os
import re
import sys
import tempfile
import unittest
from typing import Callable, Dict, List, Mapping, Optional, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
UI_DIR = os.path.dirname(HERE)
REPO_ROOT = os.path.dirname(UI_DIR)

if UI_DIR not in sys.path:
    sys.path.insert(0, UI_DIR)

from bettercnc import render as R              # noqa: E402
from bettercnc import viewmodel as VM          # noqa: E402
from bettercnc.adapter import MockAdapter      # noqa: E402
from bettercnc.configmodel import load_machine_config  # noqa: E402
from bettercnc.iniload import PROVISIONAL_MARKERS  # noqa: E402
from bettercnc.profiles import load_profile    # noqa: E402
from bettercnc.profiles import test_rig_profile as _build_test_rig_profile  # noqa: E402

TEST_RIG_INI = os.path.join(REPO_ROOT, "configs", "test-rig", "test-rig.ini")
ALL_SCREENS = [s[0] for s in VM.SCREENS]


def session(profile_key: str = "test-rig"):
    """(profile, config, adapter) for a fresh machine."""
    profile = load_profile(profile_key)
    config = load_machine_config(profile)
    adapter = MockAdapter(profile)
    return profile, config, adapter


def view(profile_key: str = "test-rig", screen: str = "kontrol", adapter=None):
    profile = load_profile(profile_key)
    config = load_machine_config(profile)
    adapter = adapter if adapter is not None else MockAdapter(profile)
    return VM.build(profile, config, adapter.snapshot(), screen=screen)


def html(profile_key: str = "test-rig", screen: str = "kontrol", adapter=None) -> str:
    return R.render_body(view(profile_key, screen, adapter))


def all_html(profile_key: str = "test-rig", adapter=None) -> str:
    """Every screen concatenated - for 'this string must never appear' checks."""
    return "\n".join(html(profile_key, s, adapter) for s in ALL_SCREENS)


def ini_raw_values(path: str = TEST_RIG_INI) -> Dict[str, str]:
    """Flat {"SECTION.KEY": raw} read straight off disk with no project code.

    Independent of bettercnc.iniload on purpose: if both the UI and the test used
    the same parser, a parser bug would pass unnoticed.
    """
    out: Dict[str, str] = {}
    section = ""
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            t = line.strip()
            if not t or t.startswith("#"):
                continue
            m = re.match(r"^\[([A-Za-z0-9_]+)\]$", t)
            if m:
                section = m.group(1)
                continue
            m = re.match(r"^([A-Za-z0-9_]+)\s*=\s*(.*?)\s*$", t)
            if m:
                out["{}.{}".format(section, m.group(1))] = m.group(2)
    return out


def tags_with_attr(markup: str, attr: str) -> List[str]:
    """Values of `attr` across the markup, in document order."""
    return re.findall(r'{}="([^"]*)"'.format(re.escape(attr)), markup)


_SECTION_RE = re.compile(r"^\[([A-Za-z0-9_]+)\]$")
_KEY_RE = re.compile(r"^([A-Za-z0-9_]+)\s*=")


def strip_markers(text: str) -> str:
    """Delete every provisional marker word (iniload.PROVISIONAL_MARKERS):
    the "travel was measured" edit the iniload module docstring describes.
    Key lines are otherwise left alone."""
    for marker in PROVISIONAL_MARKERS:
        text = re.sub(re.escape(marker), "", text, flags=re.IGNORECASE)
    return text


def rig_profile(
    testcase: unittest.TestCase,
    edits: Optional[Mapping[Tuple[str, str], Optional[str]]] = None,
    insert: Optional[Mapping[Tuple[str, str], str]] = None,
    transform: Optional[Callable[[str], str]] = None,
):
    """The test-rig profile built on a temp copy of test-rig.ini.

    `edits` maps (SECTION, KEY) to a new value, or to None to delete the key.
    The copy is edited line by line, tracking the section, so [DISPLAY] and
    [TRAJ] keys of the same name are told apart. Every edit must hit a key
    that exists, so a renamed key fails here rather than silently testing
    the original file.

    `insert` adds (SECTION, KEY) = value right after the FIRST [SECTION]
    header. LinuxCNC reads only the first occurrence of a section
    (inifile.cc:261-267, :337-340), so a key appended in a second [TRAJ]
    would be one LinuxCNC never sees. The header must exist, and the key must
    not (use `edits` for that).

    `transform` runs last, on the whole text (e.g. strip_markers).

    Built with profiles.test_rig_profile(path), not load_profile()._replace:
    the profile's travel facts are read from its INI, so from this one.
    """
    edits = dict(edits or {})
    insert = dict(insert or {})
    hit = set()
    headers = set()
    present = set()
    out: List[str] = []
    section = ""
    with open(TEST_RIG_INI, "r", encoding="utf-8") as fh:
        for line in fh:
            stripped = line.strip()
            m = _SECTION_RE.match(stripped)
            if m:
                section = m.group(1)
                out.append(line)
                if section not in headers:
                    headers.add(section)
                    for (sec, key), value in insert.items():
                        if sec == section:
                            out.append("{} = {}\n".format(key, value))
                continue
            m = _KEY_RE.match(stripped)
            if m:
                present.add((section, m.group(1)))
                if (section, m.group(1)) in edits:
                    hit.add((section, m.group(1)))
                    value = edits[(section, m.group(1))]
                    if value is None:
                        continue
                    line = "{} = {}\n".format(m.group(1), value)
            out.append(line)
    testcase.assertEqual(set(edits) - hit, set(), "edit for a key test-rig.ini lacks")
    testcase.assertEqual(
        {sec for sec, _ in insert} - headers, set(), "insert into a section test-rig.ini lacks"
    )
    testcase.assertEqual(
        set(insert) & present, set(), "insert of a key test-rig.ini already has; use edits"
    )
    text = "".join(out)
    if transform is not None:
        text = transform(text)
    tmp = tempfile.NamedTemporaryFile("w", suffix=".ini", delete=False, encoding="utf-8")
    tmp.write(text)
    tmp.close()
    testcase.addCleanup(os.unlink, tmp.name)
    return _build_test_rig_profile(tmp.name)
