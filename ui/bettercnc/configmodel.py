"""Axis and timing configuration, read from the machine's INI.

Nothing in this module hardcodes a hardware number. Every value comes out of the
INI file named by the profile, so there is exactly one source of truth and the
tests can compare the UI against the config rather than against a second copy.

The interesting part is TravelSpec, which refuses to display a number it knows
was not measured.
"""

from __future__ import annotations

from typing import Dict, List, NamedTuple, Optional

from .iniload import IniFile, load_ini
from .profiles import MachineProfile

#: What the UI shows in place of a value that was never measured.
UNMEASURED = "ölçülmedi"

#: What the UI shows for a machine that has not been surveyed at all.
NO_CONFIG = "config yok"


class TravelSpec(NamedTuple):
    """Soft-limit travel for one axis.

    `measured` is False when the INI marked these values with an ASSUMPTION
    comment. In that case `display` returns "olculmedi" and the numbers are NOT
    exposed, because rendering a placeholder as though it were a measurement is
    exactly how a fabricated default leaks into the operator's mental model.

    The provisional numbers are still kept in `min_limit`/`max_limit` because
    LinuxCNC is using them and a diagnostic screen may want to say so - but
    `display` will not show them and `has_crash_protection` is False.
    """

    min_limit: Optional[float]
    max_limit: Optional[float]
    measured: bool
    note: str

    @property
    def display(self) -> str:
        if not self.measured:
            return UNMEASURED
        if self.min_limit is None or self.max_limit is None:
            return UNMEASURED
        return "{:g} – {:g} mm".format(self.min_limit, self.max_limit)

    @property
    def has_crash_protection(self) -> bool:
        """Soft limits only protect you if they describe the real machine."""
        return self.measured and self.min_limit is not None


class AxisConfig(NamedTuple):
    """One axis, entirely INI-derived."""

    axis: str
    joint: int
    scale_steps_per_mm: float
    max_velocity_mm_s: float
    max_acceleration_mm_s2: float
    travel: TravelSpec
    steplen_ns: int
    stepspace_ns: int
    dirsetup_ns: int
    dirhold_ns: int
    home_sequence: int
    home_ignore_limits: bool
    home_search_vel: float

    # -- display strings, so the renderer never formats hardware numbers itself

    @property
    def scale_display(self) -> str:
        return "{:.3f} adım/mm".format(self.scale_steps_per_mm)

    @property
    def velocity_display(self) -> str:
        return "{:g} mm/s".format(self.max_velocity_mm_s)

    @property
    def acceleration_display(self) -> str:
        return "{:g} mm/s²".format(self.max_acceleration_mm_s2)

    @property
    def max_step_rate_hz(self) -> float:
        """Step frequency at max velocity - the number the TB6560 ceiling bites.

        docs/09-tb6560-drivers.md:77 puts that ceiling at 15 kHz.
        """
        return self.scale_steps_per_mm * self.max_velocity_mm_s


class TimingConfig(NamedTuple):
    """Step timing, shared across axes in our config."""

    steplen_ns: int
    stepspace_ns: int
    dirsetup_ns: int
    dirhold_ns: int

    def rows(self) -> List[Dict[str, str]]:
        return [
            {"label": "steplen", "value": "{:,} ns".format(self.steplen_ns).replace(",", " ")},
            {"label": "stepspace", "value": "{:,} ns".format(self.stepspace_ns).replace(",", " ")},
            {"label": "dirsetup", "value": "{:,} ns".format(self.dirsetup_ns).replace(",", " ")},
            {"label": "dirhold", "value": "{:,} ns".format(self.dirhold_ns).replace(",", " ")},
        ]


class MachineConfig(NamedTuple):
    """Everything the UI needs from the INI, or an explicit absence."""

    present: bool
    ini_path: Optional[str]
    axes: List[AxisConfig]
    timing: Optional[TimingConfig]
    kinematics: Optional[str]
    joints: Optional[int]
    units: Optional[str]
    board_ip: Optional[str]
    hal_prefix: Optional[str]
    hm2_config_string: Optional[str]
    servo_period_ms: Optional[float]
    #: Absent-config explanation, shown instead of numbers.
    absence_reason: str = ""

    def axis(self, name: str) -> Optional[AxisConfig]:
        for a in self.axes:
            if a.axis == name:
                return a
        return None

    @property
    def any_travel_measured(self) -> bool:
        return any(a.travel.measured for a in self.axes)

    @property
    def soft_limits_configured(self) -> bool:
        """True only if every axis has measured travel."""
        return bool(self.axes) and all(a.travel.has_crash_protection for a in self.axes)


#: Which INI joint maps to which axis letter, for trivkins coordinates=XYZ.
#: [TRAJ]COORDINATES gives the order; with trivkins joint n is the nth letter.
def _axis_order(ini: IniFile) -> List[str]:
    coords = ini.get("TRAJ", "COORDINATES")
    if coords is None:
        return ["X", "Y", "Z"]
    return [c for c in coords.raw.replace(" ", "").upper()]


def load_machine_config(profile: MachineProfile) -> MachineConfig:
    """Read the profile's INI. Returns an explicitly-absent config if there is none."""
    if profile.ini_path is None:
        return MachineConfig(
            present=False,
            ini_path=None,
            axes=[],
            timing=None,
            kinematics=None,
            joints=None,
            units=None,
            board_ip=None,
            hal_prefix=None,
            hm2_config_string=None,
            servo_period_ms=None,
            absence_reason=(
                "Bu makine için config yazılmadı. Envanter dolmadan yazılamaz "
                "(docs/01-machine-survey.md)."
            ),
        )

    ini = load_ini(profile.ini_path)
    axis_letters = _axis_order(ini)
    axes: List[AxisConfig] = []

    for jnum, letter in enumerate(axis_letters):
        sec = "JOINT_{}".format(jnum)
        if (sec, "SCALE") not in ini:
            continue
        min_v = ini.get(sec, "MIN_LIMIT")
        max_v = ini.get(sec, "MAX_LIMIT")
        # Either limit being marked provisional taints the pair: a half-measured
        # travel is not a measured travel.
        measured = bool(
            min_v is not None
            and max_v is not None
            and not min_v.provisional
            and not max_v.provisional
        )
        travel = TravelSpec(
            min_limit=min_v.as_float() if min_v else None,
            max_limit=max_v.as_float() if max_v else None,
            measured=measured,
            note=(
                (min_v.comment.splitlines()[0] if min_v and min_v.comment else "")
                if not measured
                else ""
            ),
        )
        axes.append(
            AxisConfig(
                axis=letter,
                joint=jnum,
                scale_steps_per_mm=ini.require(sec, "SCALE").as_float(),
                max_velocity_mm_s=ini.require(sec, "MAX_VELOCITY").as_float(),
                max_acceleration_mm_s2=ini.require(sec, "MAX_ACCELERATION").as_float(),
                travel=travel,
                steplen_ns=ini.require(sec, "STEPLEN").as_int(),
                stepspace_ns=ini.require(sec, "STEPSPACE").as_int(),
                dirsetup_ns=ini.require(sec, "DIRSETUP").as_int(),
                dirhold_ns=ini.require(sec, "DIRHOLD").as_int(),
                home_sequence=ini.require(sec, "HOME_SEQUENCE").as_int(),
                home_ignore_limits=(
                    ini.require(sec, "HOME_IGNORE_LIMITS").raw.strip().upper() == "YES"
                ),
                home_search_vel=ini.require(sec, "HOME_SEARCH_VEL").as_float(),
            )
        )

    timing = (
        TimingConfig(
            steplen_ns=axes[0].steplen_ns,
            stepspace_ns=axes[0].stepspace_ns,
            dirsetup_ns=axes[0].dirsetup_ns,
            dirhold_ns=axes[0].dirhold_ns,
        )
        if axes
        else None
    )

    servo = ini.get("EMCMOT", "SERVO_PERIOD")
    board = ini.get("HOSTMOT2", "BOARD")

    return MachineConfig(
        present=True,
        ini_path=profile.ini_path,
        axes=axes,
        timing=timing,
        kinematics=ini.get("KINS", "KINEMATICS").raw if ("KINS", "KINEMATICS") in ini else None,
        joints=ini.get("KINS", "JOINTS").as_int() if ("KINS", "JOINTS") in ini else None,
        units=ini.get("TRAJ", "LINEAR_UNITS").raw if ("TRAJ", "LINEAR_UNITS") in ini else None,
        board_ip=ini.get("HOSTMOT2", "BOARD_IP").raw if ("HOSTMOT2", "BOARD_IP") in ini else None,
        hal_prefix="hm2_{}.0".format(board.raw) if board else None,
        hm2_config_string=(
            ini.get("HOSTMOT2", "CONFIG").raw.strip('"')
            if ("HOSTMOT2", "CONFIG") in ini
            else None
        ),
        servo_period_ms=(servo.as_float() / 1e6) if servo else None,
    )


def joint_config_from_profile(profile: MachineProfile) -> Dict[int, Dict]:
    """Per-joint homing config for the adapter, keyed by joint number."""
    cfg = load_machine_config(profile)
    if not cfg.present:
        # No INI: assume nothing. Three axes with no homing config so the UI can
        # still render a gate rather than crashing.
        return {
            i: {
                "axis": a,
                "HOME_SEQUENCE": i,
                "HOME_IGNORE_LIMITS": False,
                "HOME_SEARCH_VEL": 0.0,
            }
            for i, a in enumerate(("X", "Y", "Z"))
        }
    return {
        a.joint: {
            "axis": a.axis,
            "HOME_SEQUENCE": a.home_sequence,
            "HOME_IGNORE_LIMITS": a.home_ignore_limits,
            "HOME_SEARCH_VEL": a.home_search_vel,
        }
        for a in cfg.axes
    }
