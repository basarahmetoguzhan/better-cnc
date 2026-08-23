"""Shared test fixtures.

Deliberately thin. Tests read the real config files rather than fixtures wherever
a hardware number is involved, because the point is to tie the UI to the config -
a fixture copy of SCALE would defeat that.
"""

from __future__ import annotations

import os
import re
import sys
from typing import Dict, List

HERE = os.path.dirname(os.path.abspath(__file__))
UI_DIR = os.path.dirname(HERE)
REPO_ROOT = os.path.dirname(UI_DIR)

if UI_DIR not in sys.path:
    sys.path.insert(0, UI_DIR)

from bettercnc import render as R              # noqa: E402
from bettercnc import viewmodel as VM          # noqa: E402
from bettercnc.adapter import MockAdapter      # noqa: E402
from bettercnc.configmodel import load_machine_config  # noqa: E402
from bettercnc.profiles import load_profile    # noqa: E402

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
