"""Machine profiles: test-rig and marble.

The UI is built for the MARBLE machine. The test rig is a subset of it. Anything
the test rig lacks is declared here as ABSENT with a reason, so the UI can render
it disabled rather than hiding it.

Every hardware fact carries a `source` string pointing at the repo file it came
from. Nothing in this module may assert a hardware number that is not traceable.
"""

from __future__ import annotations

from typing import Dict, List, NamedTuple, Optional, Tuple

from .capabilities import (
    CAPABILITY_CATALOGUE,
    Capability,
    CapabilityState,
    absent,
    present,
    todo,
)
from .iniload import test_rig_ini_path

# ---------------------------------------------------------------------------
# Board and platform facts
# ---------------------------------------------------------------------------


class BoardInfo(NamedTuple):
    """Verified from the board's own IDROM. Same for both machines - one board.

    docs/DURUM.md:46-56, docs/board-dumps/readhmid-10.10.10.10-2026-08-05.txt
    """

    name: str
    hal_prefix: str
    fpga: str
    io_pins: str
    clock: str
    link: str
    source: str


#: The prototype's board panel was factually correct and is carried over as-is.
#: docs/board-dumps/readhmid-10.10.10.10-2026-08-05.txt:12-19
ZHULONG_BOARD = BoardInfo(
    name="Zhulong V2.0 (Mesa 7i92)",
    hal_prefix="hm2_7i92.0",
    fpga="Spartan-6 XC6SLX9",
    io_pins="34 (2 × 17)",
    clock="ClockLow 100 MHz / ClockHigh 200 MHz",
    link="hm2_eth · 10.10.10.10",
    source="docs/board-dumps/readhmid-10.10.10.10-2026-08-05.txt:12-19",
)


class PlatformInfo(NamedTuple):
    """The host running LinuxCNC.

    `latency_us` is Optional and is None because it was never measured. The
    prototype invented 38 microseconds. See docs/05-pi-setup.md section b for the
    procedure that would produce a real figure.
    """

    kernel: str
    linuxcnc: str
    host: str
    nic: str
    servo_period_ms: float
    latency_us: Optional[float]
    latency_note: str
    source: str


#: docs/DURUM.md:20-27 - a Windows laptop booted from a LinuxCNC live USB.
#: amd64, NOT arm64: the prototype said arm64.
TEST_RIG_PLATFORM = PlatformInfo(
    kernel="6.12.95+deb13-rt-amd64 (PREEMPT_RT)",
    linuxcnc="2.9.10 uspace",
    host="Monster laptop · live USB, 10 GB persistence",
    nic="enp3s0 (Realtek RTL8125, 100 Mbps Full) · 10.10.10.1/24",
    servo_period_ms=1.0,
    latency_us=None,
    latency_note="latency-test hiç çalıştırılmadı",
    source="docs/DURUM.md:20-27",
)

#: The marble machine's controller host is not decided yet.
MARBLE_PLATFORM = PlatformInfo(
    kernel="TODO — karar verilmedi",
    linuxcnc="TODO",
    host="TODO — kalıcı kurulum planlanmadı",
    nic="TODO",
    servo_period_ms=1.0,
    latency_us=None,
    latency_note="ölçülmedi — makine kurulmadı",
    source="docs/MERMER-GECIS.md",
)


# ---------------------------------------------------------------------------
# Digital inputs
# ---------------------------------------------------------------------------


class DigitalInput(NamedTuple):
    """One physical input terminal.

    idle_high records the measured electrical behaviour: these inputs sit HIGH
    and go LOW when the switch closes, so HAL must consume `.in_not` rather than
    `.in`. docs/DURUM.md:71 -

        "Idle durumda hepsi `.in = TRUE`, basinca FALSE -> HAL'de `.in_not`"
    """

    pin: str            # gpio.020
    label: str          # Turkish UI label
    axis: str           # X / Y / Z / "" if not axis-related
    end: str            # "+" / "-" / "" - which end of travel
    idle_high: bool
    source: str

    @property
    def hal_pin(self) -> str:
        """The HAL pin a config should actually net.

        `.in_not` when the input idles high, because a limit switch that reads
        TRUE when nothing is touching it would put LinuxCNC permanently on its
        limit.
        """
        return "hm2_7i92.0.{}.{}".format(self.pin, "in_not" if self.idle_high else "in")


class MissingSwitch(NamedTuple):
    """A switch position that does NOT exist on this machine.

    Rendered explicitly. The test rig has no Z bottom switch, and pretending the
    axis is symmetric would imply crash protection that is not there.
    docs/DURUM.md:80 - "Z alt: switch yok"
    """

    axis: str
    end: str
    label: str
    consequence: str
    source: str


#: Hand-verified by triggering each switch and watching halmeter.
#: docs/DURUM.md:69-80
TEST_RIG_INPUTS: Tuple[DigitalInput, ...] = (
    DigitalInput("gpio.020", "X sağ uç", "X", "+", True, "docs/DURUM.md:75"),
    DigitalInput("gpio.021", "X sol uç", "X", "-", True, "docs/DURUM.md:76"),
    DigitalInput("gpio.022", "Y arka uç", "Y", "+", True, "docs/DURUM.md:77"),
    DigitalInput("gpio.023", "Y ön uç", "Y", "-", True, "docs/DURUM.md:78"),
    DigitalInput("gpio.024", "Z üst", "Z", "+", True, "docs/DURUM.md:79"),
)

TEST_RIG_MISSING_SWITCHES: Tuple[MissingSwitch, ...] = (
    MissingSwitch(
        axis="Z",
        end="-",
        label="Z alt",
        consequence=(
            "Z ekseninin alt ucunda switch YOK. Aşağı yönde donanımsal durdurma "
            "yok — tek koruma yazılım limiti, o da strok ölçülmediği için tanımsız."
        ),
        source="docs/DURUM.md:80",
    ),
)

#: Not yet surveyed. docs/MERMER-GECIS.md:94-110 sketches a plan for 14 inputs
#: but the machine inventory (docs/01-machine-survey.md) is still blank, so no
#: input map is asserted here.
MARBLE_INPUTS: Tuple[DigitalInput, ...] = ()

MARBLE_MISSING_SWITCHES: Tuple[MissingSwitch, ...] = ()


# ---------------------------------------------------------------------------
# Profile
# ---------------------------------------------------------------------------


class MachineProfile(NamedTuple):
    key: str
    label: str
    subtitle: str
    ini_path: Optional[str]
    capabilities: Dict[Capability, CapabilityState]
    inputs: Tuple[DigitalInput, ...]
    missing_switches: Tuple[MissingSwitch, ...]
    board: BoardInfo
    platform: PlatformInfo
    travel_measured: bool
    travel_note: str
    notes: Tuple[str, ...]

    def capability(self, key: Capability) -> CapabilityState:
        """Look up a capability. KeyError if undeclared - profiles are complete."""
        return self.capabilities[key]

    def has(self, key: Capability) -> bool:
        return self.capabilities[key].usable

    def inputs_for_axis(self, axis: str) -> List[DigitalInput]:
        return [i for i in self.inputs if i.axis == axis]

    def missing_for_axis(self, axis: str) -> List[MissingSwitch]:
        return [m for m in self.missing_switches if m.axis == axis]


_NO_ISOLATED_24V = (
    "İkinci izole 24V kaynak alınmadı (B7). Üretici ortak şaseyi yasaklıyor; "
    "bu kaynak olmadan NMOS çıkışlar ve 0-10V analog çıkış hiç çalışmaz."
)


def test_rig_profile() -> MachineProfile:
    """The wooden 3-axis router. Everything here is verified or explicitly absent."""
    caps: Dict[Capability, CapabilityState] = {
        Capability.SPINDLE: absent(
            "Bu makinede iğ mili yok — mil elle çalıştırılıyor, kontrolcüye bağlı değil.",
            "VFD bağlantısı + num_pwmgens=1 (IO 16) + CW çıkışı (IO 17) + izole 24V",
            "docs/MERMER-GECIS.md:34",
        ),
        Capability.SPINDLE_ANALOG_SPEED: absent(
            "0-10V hız referansı yok. Config'de num_pwmgens=0.",
            "PWM modülü aktif (num_pwmgens=1) + izole 24V kaynak",
            "docs/MERMER-GECIS.md:35",
        ),
        Capability.COOLANT_PUMP: absent(
            "Su pompası yok. Ahşap kesiyor, soğutma gerekmiyor.",
            "OUT1 (IO 18, NMOS 24V/50W) + izole 24V. " + _NO_ISOLATED_24V,
            "docs/MERMER-GECIS.md:36",
        ),
        Capability.FLOW_SENSOR: absent(
            "Su debi sensörü yok.",
            "Bir GPIO girişi + sensör (satın alınmadı)",
            "docs/MERMER-GECIS.md:37",
        ),
        Capability.FLOW_INTERLOCK: absent(
            "Su kilidi yok — kilitlenecek bir iğ mili de yok.",
            "Debi sensörü + iğ mili + HAL mantığı: debi yoksa mil dönmez",
            "docs/MERMER-GECIS.md:38",
        ),
        Capability.TOOL_PROBE: absent(
            "Dokunmatik prob yok. Probing ekranı bu makinede kullanılamaz.",
            "Bir GPIO girişi + prob. Prob bir HAL girişidir, takım tablosu kaydı değil.",
            "docs/MERMER-GECIS.md:39",
        ),
        Capability.TOOL_LENGTH_SENSOR: absent(
            "Takım boyu sensörü yok. Boy telafisi elle giriliyor.",
            "Bir GPIO girişi + sabit yüzey sensörü",
            "docs/MERMER-GECIS.md:39",
        ),
        Capability.ESTOP_INPUT: absent(
            "Donanımsal acil stop girişi YOK. 5 girişin 5'i limit/home switch'lerinde. "
            "Sadece GUI acil stop var; o hareketi durdurur ama hiçbir yerden güç kesmez.",
            "Bir GPIO girişi + kontaktör/güvenlik rölesi. "
            "num_encoders=0 sayesinde IO 25-33 boş, fiziksel olarak mümkün.",
            "docs/MERMER-GECIS.md:40, configs/test-rig/README.md",
        ),
        Capability.DOOR_SWITCH: absent(
            "Kapak yok — açık tezgah.",
            "Bir GPIO girişi + switch",
            "docs/MERMER-GECIS.md:109",
        ),
        Capability.FOURTH_AXIS: absent(
            "3 eksen. num_stepgens=3.",
            "num_stepgens=4 — stepgen.03 zaten IO 10/11'de hazır, firmware değişmiyor",
            "docs/MERMER-GECIS.md:41",
        ),
        Capability.SOFT_LIMITS: absent(
            "Strok ÖLÇÜLMEDİ. INI'deki değerler yer tutucu (ASSUMPTION) — "
            "gerçek çarpışma koruması yok.",
            "Her eksende uçtan uca jog + cetvelle ölçüm, sonra MIN_LIMIT/MAX_LIMIT",
            "configs/test-rig/test-rig.ini (ASSUMPTION blokları), docs/DURUM.md:139",
        ),
    }
    return MachineProfile(
        key="test-rig",
        label="Test tezgahı",
        subtitle="Ev yapımı ahşap 3 eksen router",
        ini_path=test_rig_ini_path(),
        capabilities=caps,
        inputs=TEST_RIG_INPUTS,
        missing_switches=TEST_RIG_MISSING_SWITCHES,
        board=ZHULONG_BOARD,
        platform=TEST_RIG_PLATFORM,
        travel_measured=False,
        travel_note=(
            "Strok ölçülmedi. GRBL'deki $130-132 = 200 mm fabrika varsayılanıydı, "
            "kalibre edilmiş değil."
        ),
        notes=(
            "Y ekseni DIR kablosunda aktif arıza — yön değişmiyor (docs/DURUM.md:12).",
            "Home ve limit aynı switch'i paylaşıyor → her eklemde HOME_IGNORE_LIMITS = YES.",
        ),
    )


def marble_profile() -> MachineProfile:
    """The marble machine. Almost entirely TODO - the inventory is still blank.

    This profile exists now, unfinished, on purpose: the UI renders its TODOs so
    that what has not been surveyed is visible rather than forgotten.
    """
    reason_unsurveyed = "Makine envanteri doldurulmadı (docs/01-machine-survey.md boş)."
    caps: Dict[Capability, CapabilityState] = {
        Capability.SPINDLE: todo(
            "İğ mili var ama VFD bağlantısı ve kontrol yöntemi belirlenmedi.",
            "VFD modeli + num_pwmgens=1 + CW çıkışı + izole 24V kaynak (B7)",
            "docs/MERMER-GECIS.md:34",
        ),
        Capability.SPINDLE_ANALOG_SPEED: todo(
            "0-10V hız kontrolü planlanıyor, donanım alınmadı.",
            "PWM modülü + izole 24V kaynak (B7 — alınmadı)",
            "docs/MERMER-GECIS.md:35",
        ),
        Capability.COOLANT_PUMP: todo(
            "Su pompası var, kontrolcüye nasıl bağlanacağı belirlenmedi.",
            "OUT1 (IO 18) + izole 24V kaynak (B7). Pompa 50 W üstündeyse röle gerekir.",
            "docs/MERMER-GECIS.md:36",
        ),
        Capability.FLOW_SENSOR: todo(
            "Debi sensörü satın alınmadı.",
            "Sensör + bir GPIO girişi (IO 25 planlandı — encoder klemensi, "
            "arkasındaki analog devre doğrulanmadı)",
            "docs/MERMER-GECIS.md:18, :112-114",
        ),
        Capability.FLOW_INTERLOCK: todo(
            "Su kilidi HAL mantığı yazılmadı. Elmas uç susuz kesimde saniyeler "
            "içinde gider — bu kilit pazarlık konusu değil.",
            "Debi sensörü + HAL mantığı: debi yoksa iğ mili dönmez",
            "docs/MERMER-GECIS.md:38, :120-122",
        ),
        Capability.TOOL_PROBE: todo(
            reason_unsurveyed + " Prob olup olmadığı bilinmiyor.",
            "Bir GPIO girişi + prob",
            "docs/01-machine-survey.md",
        ),
        Capability.TOOL_LENGTH_SENSOR: todo(
            "Takım boyu sensörü satın alınmadı. Elmas uçların boy telafisi için gerekli.",
            "Sensör + bir GPIO girişi",
            "docs/MERMER-GECIS.md:19",
        ),
        Capability.ESTOP_INPUT: todo(
            "Acil stop zinciri tasarlanmadı. Mermer makinesinde bu ZORUNLU — "
            "sürücü ve iğ mili gücünü donanımsal kesmeli.",
            "Kontaktör/güvenlik rölesi + bir GPIO girişi (IO 23 planlandı)",
            "docs/MERMER-GECIS.md:40, :120-122",
        ),
        Capability.DOOR_SWITCH: todo(
            reason_unsurveyed + " Kapak var mı bilinmiyor.",
            "Varsa bir GPIO girişi (IO 26 planlandı)",
            "docs/MERMER-GECIS.md:109",
        ),
        Capability.FOURTH_AXIS: todo(
            "Y ekseninde kaç motor olduğu bilinmiyor (B1). Çift motorlu portal "
            "çıkarsa 4 joint gerekir: trivkins coordinates=XYYZ.",
            "Panoyu açıp saymak. Çıkarsa num_stepgens=4 — stepgen.03 hazır.",
            "docs/OPEN-QUESTIONS.md B1, docs/MERMER-GECIS.md:75-79",
        ),
        Capability.SOFT_LIMITS: todo(
            "Strok ölçülmedi, makine kurulmadı.",
            "Eski Windows kontrolcüsünden steps/mm + komparatörle doğrulama, sonra ölçüm",
            "docs/MERMER-GECIS.md:81-90",
        ),
    }
    return MachineProfile(
        key="marble",
        label="Mermer makinesi",
        subtitle="Endüstriyel 3 eksen mermer CNC · Faz 2, beklemede",
        ini_path=None,
        capabilities=caps,
        inputs=MARBLE_INPUTS,
        missing_switches=MARBLE_MISSING_SWITCHES,
        board=ZHULONG_BOARD,
        platform=MARBLE_PLATFORM,
        travel_measured=False,
        travel_note="Makine kurulmadı, strok ölçülmedi.",
        notes=(
            "Config yazılmadı — configs/marble/ henüz yok.",
            "İkinci izole 24V kaynak alınmadı (B7): su pompası ve VFD onsuz çalışmaz.",
            "İki 24V klemensi aynı etiketli — yanlışına bağlanırsa Ethernet linki hiç kalkmaz.",
        ),
    )


PROFILES = {
    "test-rig": test_rig_profile,
    "marble": marble_profile,
}


def load_profile(key: str) -> MachineProfile:
    if key not in PROFILES:
        raise KeyError(
            "unknown machine profile {!r} - known: {}".format(
                key, ", ".join(sorted(PROFILES))
            )
        )
    return PROFILES[key]()


def validate_profile(profile: MachineProfile) -> None:
    """Every catalogued capability must be declared. Raises otherwise.

    This is the structural guarantee behind "never hidden": a capability the UI
    knows how to render but a profile forgot to mention is a hard error, not a
    silently missing panel.
    """
    declared = set(profile.capabilities)
    catalogued = {meta.key for meta in CAPABILITY_CATALOGUE}
    missing = catalogued - declared
    if missing:
        raise ValueError(
            "profile {!r} does not declare: {} - "
            "an undeclared capability would be silently hidden".format(
                profile.key, ", ".join(sorted(c.value for c in missing))
            )
        )
    extra = declared - catalogued
    if extra:
        raise ValueError(
            "profile {!r} declares unknown capabilities: {}".format(
                profile.key, ", ".join(sorted(str(c) for c in extra))
            )
        )
