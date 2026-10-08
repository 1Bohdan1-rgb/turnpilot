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
    kind: str  # "int", "float", "bool" or "text"
    required: bool = False  # the planner cannot work without it (a value is kept, not cleared)
    help: str = ""


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
FIELDS_BY_NAME = {f.name: f for f in MACHINE_FIELDS}

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
