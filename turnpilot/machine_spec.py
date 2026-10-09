"""The machine's data the planner uses: one list of fields for the Machine page, the passport reading and its
review screen. Every value carries its source (MachineSpecSource): the passport (page, quote), the operator, or
"not in passport". No value is guessed: an unknown field stays empty.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MachineField:
    name: str  # Machine column
    label: str
    unit: str  # the unit the value is stored in ("" for a flag or a text)
    kind: str  # "int", "float", "bool", "text" or "choice"
    required: bool = False  # the planner cannot work without it (a value is kept, not cleared)
    help: str = ""
    options: tuple = ()  # "choice": the allowed values


MACHINE_FIELDS = (
    MachineField("max_rpm", "Max spindle speed", "rpm", "int", True, "caps n"),
    MachineField("min_rpm", "Min spindle speed", "rpm", "int", help="n below it gets a warning"),
    MachineField("power_kw", "Spindle power S1 (continuous)", "kW", "float", True, "spindle power check"),
    MachineField("power_s6_kw", "Spindle power S6 (short time)", "kW", "float",
                 help="shown only: the power check takes S1"),
    MachineField("drive_efficiency", "Drive efficiency (0–1)", "", "float",
                 help="power check: power × efficiency at the spindle"),
    MachineField("max_diameter", "Max turning diameter", "mm", "float", True, "a larger blank is refused"),
    MachineField("max_turning_length", "Max turning length", "mm", "float", help="a longer blank gets a warning"),
    MachineField("max_bar_diameter", "Max bar through the spindle", "mm", "float",
                 help="a thicker round bar gets a warning"),
    MachineField("turret_positions", "Turret positions", "", "int", True, "positions on this page"),
    MachineField("max_z_feed", "Max Z feed", "mm/min", "float",
                 help="shown only: not the threading limit unless the operator sets that below"),
    MachineField("max_thread_feed", "Max Z feed when threading (n·P)", "mm/min", "float",
                 help="thread operations keep n·P within it; without it they ask to check n·P"),
    MachineField("coolant", "Coolant", "", "bool", help="catalogue Vc are given with coolant"),
    MachineField("coolant_pressure_bar", "Coolant pressure", "bar", "float"),
    MachineField("live_tooling", "Driven (live) tools", "", "bool", help="milling the flats of a hex"),
    MachineField("c_axis", "C axis", "", "bool", help="milling the flats of a hex"),
    MachineField("control", "CNC control", "", "text", help="shown on the process sheet"),
)

# Values a G-code program needs that no catalogue or passport gives: the operator enters them (no default; a
# program is not built while one it needs is empty). Not part of the passport reading.
GROOVE_REFERENCE_OPTIONS = ("toward Z0", "toward chuck")
PROGRAMMING_FIELDS = (
    MachineField("spindle_right_hand", "Spindle direction for a right-hand tool", "", "choice",
                 help="M03 or M04, as the tools are mounted on this machine", options=("M03", "M04")),
    MachineField("clearance_x", "Approach clearance in X (per side)", "mm", "float",
                 help="rapids stop this far above the material"),
    MachineField("clearance_z", "Approach clearance in Z", "mm", "float",
                 help="rapids stop this far in front of the face"),
    MachineField("retract_mm", "Retract after a pass (per side)", "mm", "float",
                 help="lift off the cut surface before the rapid back"),
    MachineField("chuck_safety_mm", "Safety distance to the chuck jaws", "mm", "float",
                 help="no tool point closer to the jaws"),
    MachineField("thread_run_in_mm", "Thread run-in (start before the thread)", "mm", "float",
                 help="the axis accelerates over it; rule of thumb ≥ 3·P, check your control"),
    MachineField("peck_depth_mm", "Drilling peck depth", "mm", "float", help="the drill backs out after each peck"),
    MachineField("facing_overshoot_mm", "Facing past the axis (per side)", "mm", "float",
                 help="no centre pip left; about the nose radius"),
    MachineField("groove_reference", "Grooving / parting insert: touched-off corner", "", "choice",
                 help="the corner the Z offset is set to", options=GROOVE_REFERENCE_OPTIONS),
    MachineField("parting_overshoot_mm", "Parting past the axis (per side)", "mm", "float",
                 help="the part comes off cleanly"),
    MachineField("groove_dwell_s", "Dwell at a groove's bottom", "s", "float",
                 help="optional: empty, no dwell (G04)"),
)
FIELDS_BY_NAME = {f.name: f for f in MACHINE_FIELDS + PROGRAMMING_FIELDS}

SOURCE_PASSPORT = "passport"
SOURCE_OPERATOR = "operator"
SOURCE_NOT_IN_PASSPORT = "not in passport"


def parse_value(field: MachineField, raw):
    """A value from a form or a reading: None when empty; ValueError when it is not of the field's kind."""
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None
    if field.kind == "bool":
        if isinstance(raw, bool):
            return raw
        text = str(raw).strip().lower()
        if text in ("yes", "true", "1", "так", "є"):
            return True
        if text in ("no", "false", "0", "ні", "немає"):
            return False
        raise ValueError(f"{field.label}: yes or no")
    if field.kind == "text":
        return str(raw).strip()[:100]
    if field.kind == "choice":
        if str(raw).strip() not in field.options:
            raise ValueError(f"{field.label}: one of {', '.join(field.options)}")
        return str(raw).strip()
    number = float(str(raw).replace(",", ".")) if not isinstance(raw, (int, float)) else float(raw)
    if number <= 0:
        raise ValueError(f"{field.label} must be positive")
    if field.kind == "int":
        if number != int(number):
            raise ValueError(f"{field.label} must be a whole number")
        return int(number)
    return number


def format_value(field: MachineField, value) -> str:
    if value is None:
        return ""
    if field.kind == "bool":
        return "yes" if value else "no"
    if field.kind == "float":
        return f"{value:g}"
    return str(value)
