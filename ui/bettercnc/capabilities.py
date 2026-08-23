"""Machine capability model.

The central design rule of this package:

    A capability that a machine does NOT have must still be RENDERED, disabled,
    with the reason visible. It is never hidden.

The mechanism that guarantees this is structural rather than a matter of
discipline: the UI renders by iterating CAPABILITY_CATALOGUE, not by iterating
whatever a profile happens to declare. A profile that forgets a capability fails
validation (see profiles.validate_profile) rather than silently omitting a panel.

Rationale, from docs/MERMER-GECIS.md:43-45 --

    "Arayüz kuralı: bu modüller test tezgahında gizlenmeyecek, disabled
     görünecek. Sebebi ekranda yazacak ('bu makinede ig mili yok'), boylece
     mermer makinesine gecince neyin eksik oldugu kendiliginden ortaya cikar."

    ("UI rule: on the test rig these modules will not be hidden, they will
      appear disabled. The reason will be on screen, so that when we move to the
      marble machine what is missing surfaces by itself.")
"""

from __future__ import annotations

import enum
from typing import Dict, NamedTuple, Tuple


class Presence(str, enum.Enum):
    """Whether a machine has a capability.

    TODO is distinct from ABSENT on purpose. ABSENT means "we looked, it is not
    there". TODO means "we have not surveyed this machine yet" - which must never
    be rendered as if it were a definite answer either way.
    """

    PRESENT = "present"
    ABSENT = "absent"
    TODO = "todo"

    @property
    def usable(self) -> bool:
        """Only PRESENT capabilities may drive live controls."""
        return self is Presence.PRESENT


class Capability(str, enum.Enum):
    """Every capability the UI knows how to render.

    Adding a member here forces every profile to declare it (validate_profile),
    which is what stops a new capability from being quietly missing on a machine.
    """

    SPINDLE = "spindle"
    SPINDLE_ANALOG_SPEED = "spindle_analog_speed"
    COOLANT_PUMP = "coolant_pump"
    FLOW_SENSOR = "flow_sensor"
    FLOW_INTERLOCK = "flow_interlock"
    TOOL_PROBE = "tool_probe"
    TOOL_LENGTH_SENSOR = "tool_length_sensor"
    ESTOP_INPUT = "estop_input"
    DOOR_SWITCH = "door_switch"
    FOURTH_AXIS = "fourth_axis"
    SOFT_LIMITS = "soft_limits"


class CapabilityMeta(NamedTuple):
    """Static, machine-independent description of a capability."""

    key: Capability
    label: str          # Turkish UI label
    screen: str         # which screen owns its primary control
    blurb: str          # one line explaining what it is


CAPABILITY_CATALOGUE: Tuple[CapabilityMeta, ...] = (
    CapabilityMeta(
        Capability.SPINDLE, "Iğ mili", "kontrol",
        "Kesici mil motoru - VFD üzerinden aç/kapat ve yön.",
    ),
    CapabilityMeta(
        Capability.SPINDLE_ANALOG_SPEED, "İğ mili hız (0-10V)", "kontrol",
        "PWM modülünden türetilen 0-10 V analog hız referansı.",
    ),
    CapabilityMeta(
        Capability.COOLANT_PUMP, "Su pompası", "kontrol",
        "Kesme suyu pompası - NMOS çıkış üzerinden.",
    ),
    CapabilityMeta(
        Capability.FLOW_SENSOR, "Su debi sensörü", "makine",
        "Suyun gerçekten aktığını ölçer.",
    ),
    CapabilityMeta(
        Capability.FLOW_INTERLOCK, "Su kilidi", "makine",
        "Debi yoksa iğ milinin dönmesini engelleyen HAL mantığı.",
    ),
    CapabilityMeta(
        Capability.TOOL_PROBE, "Prob (kenar/merkez)", "probing",
        "Dokunmatik prob - HAL girişi, takım tablosu kaydı değil.",
    ),
    CapabilityMeta(
        Capability.TOOL_LENGTH_SENSOR, "Takım boyu sensörü", "takimlar",
        "Takım boyu telafisi için sabit yüzey sensörü.",
    ),
    CapabilityMeta(
        Capability.ESTOP_INPUT, "Donanımsal acil stop girişi", "kontrol",
        "Acil stop zincirinin durumunu LinuxCNC'ye bildiren giriş.",
    ),
    CapabilityMeta(
        Capability.DOOR_SWITCH, "Kapı switch'i", "makine",
        "Kapak açıkken hareketi kilitler.",
    ),
    CapabilityMeta(
        Capability.FOURTH_AXIS, "4. eksen / gantry Y", "makine",
        "İkinci Y motoru veya döner eksen.",
    ),
    CapabilityMeta(
        Capability.SOFT_LIMITS, "Yazılım limitleri", "makine",
        "Ölçülmüş stroka dayanan çarpışma koruması.",
    ),
)

CAPABILITY_META: Dict[Capability, CapabilityMeta] = {
    meta.key: meta for meta in CAPABILITY_CATALOGUE
}

assert len(CAPABILITY_CATALOGUE) == len(Capability), (
    "every Capability member needs a CAPABILITY_CATALOGUE entry"
)


class CapabilityState(NamedTuple):
    """A machine's declaration about one capability.

    reason and requirement are UI-visible strings, not developer comments. They
    are what the operator reads when a control is greyed out, which is the whole
    point of not hiding it.
    """

    presence: Presence
    reason: str = ""        # why it is absent / what is unknown
    requirement: str = ""   # what would be needed to enable it
    source: str = ""        # repo citation for the claim

    @property
    def usable(self) -> bool:
        return self.presence.usable

    @property
    def disabled(self) -> bool:
        """Rendered but not operable. True for both ABSENT and TODO."""
        return not self.presence.usable


def present(source: str) -> CapabilityState:
    return CapabilityState(Presence.PRESENT, source=source)


def absent(reason: str, requirement: str, source: str) -> CapabilityState:
    if not reason:
        raise ValueError("an absent capability must carry a UI-visible reason")
    if not requirement:
        raise ValueError(
            "an absent capability must say what enabling it requires - "
            "see docs/MERMER-GECIS.md section 2"
        )
    return CapabilityState(Presence.ABSENT, reason, requirement, source)


def todo(reason: str, requirement: str, source: str) -> CapabilityState:
    """Not yet surveyed. Must never render as a definite yes or no."""
    if not reason:
        raise ValueError("a TODO capability must say what is still unknown")
    return CapabilityState(Presence.TODO, reason, requirement, source)
