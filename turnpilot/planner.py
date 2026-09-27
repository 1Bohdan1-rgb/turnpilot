"""Pure planning logic for a turning process sheet.

This module knows nothing about Flask or the database. It works on small
dataclasses so it can be unit-tested in isolation and reused later
(e.g. when features are extracted automatically).
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

G96_NOTE = "G96 constant surface speed, capped at max RPM"

# Stage order of the process sheet: face -> rough -> finish -> groove -> thread -> parting.
STAGE_ORDER = {"face": 0, "rough": 1, "finish": 2, "groove": 3, "thread": 4, "parting": 5}

# "Closer to the min/max of the range" is expressed as a position inside the range.
NEAR_MIN = 0.25
NEAR_MAX = 0.75

# Used when the nose radius cannot be read from the insert code.
DEFAULT_NOSE_RADIUS_MM = 0.4


@dataclass(frozen=True)
class ToolSpec:
    id: int | None
    name: str
    type: str
    iso_group: str  # one or more ISO letters the grade covers, e.g. "P" or "PMN"
    insert_code: str
    vc_min: float
    vc_max: float
    f_min: float
    f_max: float
    ap_min: float
    ap_max: float


@dataclass(frozen=True)
class TurretEntry:
    position: int
    tool: ToolSpec


@dataclass(frozen=True)
class FeatureSpec:
    id: int | None
    type: str
    diameter: float | None = None
    length: float | None = None
    ra: float | None = None
    pitch: float | None = None


@dataclass(frozen=True)
class JobSpec:
    iso_group: str
    blank_diameter: float
    blank_length: float
    features: tuple[FeatureSpec, ...]


@dataclass(frozen=True)
class Step:
    feature: FeatureSpec
    tool_type: str
    mode: str  # "rough" or "finish"
    stage: str  # key of STAGE_ORDER


@dataclass
class PlannedOperation:
    feature_id: int | None
    feature_type: str
    tool_type: str
    mode: str
    sequence: int = 0
    tool_id: int | None = None
    tool_name: str | None = None
    turret_position: int | None = None
    vc: float | None = None
    n: int | None = None
    f: float | None = None
    ap: float | None = None
    passes: int | None = None
    ref_diameter: float | None = None
    notes: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def spindle_speed(vc: float, diameter: float, max_rpm: int) -> tuple[int, bool]:
    """Return (n, limited): n = 1000 * Vc / (pi * D), capped at the machine max RPM."""
    if diameter <= 0:
        raise ValueError("Diameter must be positive")
    if vc <= 0:
        raise ValueError("Cutting speed must be positive")
    n = 1000 * vc / (math.pi * diameter)
    if n > max_rpm:
        return int(max_rpm), True
    return round(n), False


def _lerp(lo: float, hi: float, t: float) -> float:
    return lo + (hi - lo) * t


def nose_radius_from_insert(insert_code: str | None) -> float:
    """Read the nose radius (mm) from an ISO turning insert code, e.g. 'CNMG 120408' -> 0.8."""
    if insert_code:
        match = re.search(r"(\d{2})(\d{2})(\d{2})(?!\d)", insert_code)
        if match and int(match.group(3)) > 0:
            return int(match.group(3)) / 10
    return DEFAULT_NOSE_RADIUS_MM


def finish_feed_from_ra(ra: float, nose_radius: float, f_min: float, f_max: float) -> float:
    """Theoretical finishing feed for a target Ra (um): f = sqrt(Ra * 32 * r_eps / 1000).

    The result is clamped to the tool's f_min..f_max range.
    """
    f = math.sqrt(ra * 32 * nose_radius / 1000)
    return round(min(max(f, f_min), f_max), 3)


def rough_passes(blank_diameter: float, diameter: float, ap: float) -> int:
    """Number of roughing passes: ceil((blank_diameter - diameter) / 2 / ap)."""
    if ap <= 0:
        raise ValueError("Depth of cut must be positive")
    radial_stock = (blank_diameter - diameter) / 2
    if radial_stock <= 0:
        return 0
    return math.ceil(round(radial_stock / ap, 9))


def cutting_data(tool: ToolSpec, mode: str, ra: float | None = None) -> tuple[float, float, float]:
    """Pick (vc, f, ap) from the tool ranges.

    Rough: Vc near vc_min, f and ap near the top of their ranges.
    Finish: Vc near vc_max, ap = ap_min, f from Ra when given, otherwise near f_min.
    """
    if mode == "rough":
        vc = _lerp(tool.vc_min, tool.vc_max, NEAR_MIN)
        f = _lerp(tool.f_min, tool.f_max, NEAR_MAX)
        ap = _lerp(tool.ap_min, tool.ap_max, NEAR_MAX)
    elif mode == "finish":
        vc = _lerp(tool.vc_min, tool.vc_max, NEAR_MAX)
        ap = tool.ap_min
        if ra:
            f = finish_feed_from_ra(ra, nose_radius_from_insert(tool.insert_code), tool.f_min, tool.f_max)
        else:
            f = _lerp(tool.f_min, tool.f_max, NEAR_MIN)
    else:
        raise ValueError(f"Unknown mode: {mode}")
    return round(vc, 1), round(f, 3), round(ap, 2)


def feature_to_steps(feature: FeatureSpec) -> list[Step]:
    """Split a part feature into machining steps."""
    t = feature.type
    if t == "face":
        return [Step(feature, "facing", "rough", "face")]
    if t == "od_turn":
        return [
            Step(feature, "turning_rough", "rough", "rough"),
            Step(feature, "turning_finish", "finish", "finish"),
        ]
    if t == "bore":
        return [
            Step(feature, "boring", "rough", "rough"),
            Step(feature, "boring", "finish", "finish"),
        ]
    if t == "chamfer":
        return [Step(feature, "turning_finish", "finish", "finish")]
    if t == "groove":
        return [Step(feature, "grooving", "finish", "groove")]
    if t == "thread":
        return [Step(feature, "threading", "finish", "thread")]
    if t == "parting":
        return [Step(feature, "parting", "finish", "parting")]
    raise ValueError(f"Unknown feature type: {t}")


def order_steps(steps: list[Step]) -> list[Step]:
    """Sort steps by stage; features keep their input order within a stage."""
    return sorted(steps, key=lambda s: STAGE_ORDER[s.stage])


def select_tool(tool_type: str, iso_group: str, turret: list[TurretEntry]) -> tuple[TurretEntry | None, str | None]:
    """Choose a tool from the turret only. Returns (entry, None) or (None, warning)."""
    same_type = [e for e in turret if e.tool.type == tool_type]
    matching = [e for e in same_type if iso_group in e.tool.iso_group]
    if matching:
        return min(matching, key=lambda e: e.position), None
    if same_type:
        loaded = ", ".join(f"T{e.position} ({e.tool.iso_group})" for e in same_type)
        return None, (
            f"No {tool_type} tool for ISO {iso_group} in the turret "
            f"(loaded {tool_type}: {loaded}). Install a suitable tool."
        )
    return None, f"No {tool_type} tool in the turret. Install one to machine this feature."


def _reference_diameter(step: Step, job: JobSpec) -> float:
    """Diameter used for the spindle speed calculation."""
    if step.stage in ("face", "parting"):
        return job.blank_diameter
    if step.tool_type == "turning_rough":
        # Diameter before the first pass is the blank diameter.
        return job.blank_diameter
    return step.feature.diameter or job.blank_diameter


def _plan_step(step: Step, job: JobSpec, turret: list[TurretEntry], max_rpm: int) -> PlannedOperation:
    feature = step.feature
    op = PlannedOperation(
        feature_id=feature.id,
        feature_type=feature.type,
        tool_type=step.tool_type,
        mode=step.mode,
    )

    entry, warning = select_tool(step.tool_type, job.iso_group, turret)
    if entry is None:
        op.warnings.append(warning)
        return op

    tool = entry.tool
    op.tool_id = tool.id
    op.tool_name = tool.name
    op.turret_position = entry.position

    # The Ra feed formula only applies to nose-radius tools.
    uses_ra = step.tool_type in ("turning_finish", "boring")
    op.vc, op.f, op.ap = cutting_data(tool, step.mode, feature.ra if uses_ra else None)

    if step.tool_type == "threading":
        if feature.pitch:
            op.f = feature.pitch
        else:
            op.f = None
            op.warnings.append("Thread pitch is missing: feed equals pitch.")

    op.ref_diameter = _reference_diameter(step, job)
    op.n, limited = spindle_speed(op.vc, op.ref_diameter, max_rpm)
    if step.stage in ("face", "parting"):
        op.notes.append(G96_NOTE)
    elif limited:
        op.notes.append(f"n limited to machine max {max_rpm} rpm")

    if step.tool_type == "turning_rough" and feature.diameter is not None:
        op.passes = rough_passes(job.blank_diameter, feature.diameter, op.ap)
        if op.passes == 0:
            op.warnings.append("Feature diameter is not smaller than the blank: nothing to rough.")

    return op


def plan_job(job: JobSpec, turret: list[TurretEntry], max_rpm: int) -> list[PlannedOperation]:
    """Build the ordered list of proposed operations for a job."""
    steps = [s for feature in job.features for s in feature_to_steps(feature)]
    operations = [_plan_step(s, job, turret, max_rpm) for s in order_steps(steps)]
    for i, op in enumerate(operations, start=1):
        op.sequence = i
    return operations
