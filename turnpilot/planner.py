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
G97_THREAD_NOTE = "G97 constant RPM — required for threading"
CHAMFER_NOTE = "incl. chamfer"
PARTING_CENTER_NOTE = "reduce feed ~50% for last 2 mm before center"
PARTING_BORE_NOTE = "reduce feed ~50% for last 2 mm before breakthrough into bore"

# Stage order of the process sheet: face -> rough -> finish -> groove -> thread -> parting.
STAGE_ORDER = {"face": 0, "rough": 1, "finish": 2, "groove": 3, "thread": 4, "parting": 5}

# "Closer to the min/max of the range" is expressed as a position inside the range.
NEAR_MIN = 0.25
NEAR_MAX = 0.75

# Used when the nose radius cannot be read from the insert code.
DEFAULT_NOSE_RADIUS_MM = 0.4

# PLACEHOLDER: radial finishing allowance used when no finishing tool is in the turret.
DEFAULT_FINISH_ALLOWANCE_MM = 0.5

# Full profile depth per side of an external metric thread: h = 0.613 * pitch.
METRIC_THREAD_DEPTH_FACTOR = 0.613
# First-pass factor of the modified radial infeed (constant chip area) method.
THREAD_FIRST_PASS_FACTOR = 0.3


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
    insert_width: float | None = None  # grooving / parting inserts


@dataclass(frozen=True)
class TurretEntry:
    position: int
    tool: ToolSpec


@dataclass(frozen=True)
class FeatureSpec:
    id: int | None
    type: str
    diameter: float | None = None  # groove: bottom diameter
    length: float | None = None
    ra: float | None = None
    pitch: float | None = None
    start_diameter: float | None = None  # groove: outer diameter the groove starts from


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
    insert_width: float | None = None
    depth: float | None = None  # per side: groove depth or thread profile depth h
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


def rough_passes(
    blank_diameter: float, diameter: float, ap_max: float, finish_allowance: float = 0.0
) -> tuple[int, float]:
    """Split the roughing stock into equal passes. Returns (passes, ap per pass).

    Radial stock = (blank_diameter - diameter) / 2 - finish_allowance,
    passes = ceil(stock / ap_max), ap = stock / passes.
    """
    if ap_max <= 0:
        raise ValueError("Depth of cut must be positive")
    stock = (blank_diameter - diameter) / 2 - finish_allowance
    if stock <= 1e-9:
        return 0, 0.0
    passes = math.ceil(round(stock / ap_max, 9))
    return passes, round(stock / passes, 3)


def thread_depth(pitch: float) -> float:
    """Full profile depth per side of an external metric thread: h = 0.613 * pitch."""
    return round(METRIC_THREAD_DEPTH_FACTOR * pitch, 3)


def _decreasing_infeed(h: float, n: int) -> list[float]:
    """Modified constant chip area method with n cutting passes.

    Cumulative depth after pass x: h * sqrt(0.3 / (n - 1)) for x = 1,
    h * sqrt((x - 1) / (n - 1)) for x >= 2. Increments decrease with every pass.
    """
    cumulative = [
        round(h * math.sqrt((THREAD_FIRST_PASS_FACTOR if x == 1 else x - 1) / (n - 1)), 3)
        for x in range(1, n + 1)
    ]
    return [round(b - a, 3) for a, b in zip([0.0] + cumulative, cumulative)]


def _equal_infeed(h: float, k: int) -> list[float]:
    """Split h into k equal passes; rounding leftovers go to the first passes (never the last)."""
    units = round(h * 1000)
    base, extra = divmod(units, k)
    return [(base + (1 if i < extra else 0)) / 1000 for i in range(k)]


def _non_increasing(values: list[float]) -> bool:
    return all(b <= a for a, b in zip(values, values[1:]))


def thread_infeed(h: float, ap_max: float, ap_min: float) -> tuple[list[float], str]:
    """Radial infeed per pass (mm per side), never increasing, plus a final 0.0 spring pass.

    Returns (infeed, method). Method "decreasing": modified constant chip area series with
    the fewest passes that keep the first pass within ap_max. If that series would need a
    pass thinner than ap_min, the whole depth is split evenly instead (method "equal"),
    using the fewest passes that keep each pass within ap_max.
    """
    if h <= 0 or ap_max <= 0:
        raise ValueError("Thread depth and ap_max must be positive")
    n = max(2, math.ceil(round(THREAD_FIRST_PASS_FACTOR * (h / ap_max) ** 2, 9)) + 1)
    cutting = _decreasing_infeed(h, n)
    method = "decreasing"
    if min(cutting) < ap_min or not _non_increasing(cutting):
        cutting = _equal_infeed(h, max(1, math.ceil(round(h / ap_max, 9))))
        method = "equal"
    return cutting + [0.0], method


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
        # Only used when no finishing pass on the same diameter can take the chamfer.
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


def match_chamfers(features: list[FeatureSpec]) -> dict[int, FeatureSpec]:
    """Map each chamfer (by id()) to the OD/bore feature whose finishing pass machines it.

    Chamfers without a feature of the same diameter are left out and get their own pass.
    """
    hosts = {}
    for chamfer in (f for f in features if f.type == "chamfer" and f.diameter is not None):
        for host_type in ("od_turn", "bore"):
            host = next(
                (
                    f
                    for f in features
                    if f.type == host_type and f.diameter is not None and math.isclose(f.diameter, chamfer.diameter)
                ),
                None,
            )
            if host:
                hosts[id(chamfer)] = host
                break
    return hosts


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


def finish_allowance(iso_group: str, turret: list[TurretEntry]) -> tuple[float, bool]:
    """Radial allowance left for finishing: the finishing tool's ap. Returns (allowance, is_default)."""
    entry, _ = select_tool("turning_finish", iso_group, turret)
    if entry is None:
        return DEFAULT_FINISH_ALLOWANCE_MM, True
    return cutting_data(entry.tool, "finish")[2], False


def _reference_diameter(step: Step, job: JobSpec) -> float:
    """Diameter used for the spindle speed calculation."""
    if step.stage == "face":
        return job.blank_diameter
    if step.stage == "parting":
        return step.feature.diameter or job.blank_diameter
    if step.tool_type == "turning_rough":
        # Diameter before the first pass is the blank diameter.
        return job.blank_diameter
    if step.tool_type == "grooving":
        # The groove starts on the larger diameter, not at the bottom.
        return step.feature.start_diameter or job.blank_diameter
    return step.feature.diameter or job.blank_diameter


def _plan_rough_turning(op: PlannedOperation, tool: ToolSpec, feature: FeatureSpec, job: JobSpec, turret) -> None:
    allowance, is_default = finish_allowance(job.iso_group, turret)
    op.passes, op.ap = rough_passes(job.blank_diameter, feature.diameter, tool.ap_max, allowance)
    if op.passes == 0:
        op.ap = None
        op.warnings.append("No roughing stock: feature diameter plus finishing allowance reaches the blank.")
        return
    op.notes.append(f"leaves {allowance:g} mm/side for finishing")
    if is_default:
        op.notes.append("no finishing tool in turret: default allowance used")


def _plan_groove(op: PlannedOperation, tool: ToolSpec, feature: FeatureSpec, job: JobSpec) -> None:
    op.ap = None
    op.insert_width = tool.insert_width
    if tool.insert_width is None:
        op.warnings.append("Insert width is not set for this grooving tool.")
    if feature.start_diameter is None:
        op.notes.append(f"start diameter not given: blank Ø{job.blank_diameter:g} used")
    if feature.diameter is not None:
        op.depth = round((op.ref_diameter - feature.diameter) / 2, 3)
        if op.depth <= 0:
            op.warnings.append("Groove bottom diameter is not smaller than the start diameter.")


def _plan_thread(op: PlannedOperation, tool: ToolSpec, feature: FeatureSpec) -> None:
    op.ap = None
    op.notes.insert(0, G97_THREAD_NOTE)
    if not feature.pitch:
        op.f = None
        op.warnings.append("Thread pitch is missing: feed equals pitch.")
        return
    op.f = feature.pitch
    op.depth = thread_depth(feature.pitch)
    infeed, method = thread_infeed(op.depth, tool.ap_max, tool.ap_min)
    op.passes = len(infeed)
    cutting = ", ".join(f"{d:g}" for d in infeed[:-1])
    op.notes.append(f"radial infeed per pass: {cutting} + spring pass")
    if method == "equal":
        op.notes.append("equal infeed: a decreasing series would need passes thinner than ap_min")
    if min(infeed[:-1]) < tool.ap_min:
        op.warnings.append("Thread infeed per pass is below the tool's ap_min: check the tool ap range.")


def _plan_parting(op: PlannedOperation, tool: ToolSpec, feature: FeatureSpec, job: JobSpec) -> None:
    op.ap = None
    op.insert_width = tool.insert_width
    if tool.insert_width is None:
        op.warnings.append("Insert width is not set for this parting tool.")
    outer = op.ref_diameter
    bores = [f.diameter for f in job.features if f.type == "bore" and f.diameter and f.diameter < outer]
    if bores:
        # Cut to the smallest bore so the part separates whatever bore step is at the cut.
        inner = min(bores)
        op.depth = round((outer - inner) / 2, 3)
        op.notes.append(f"parting to bore Ø{inner:g}")
        op.notes.append(PARTING_BORE_NOTE)
    else:
        op.depth = round(outer / 2, 3)
        op.notes.append(PARTING_CENTER_NOTE)


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

    op.ref_diameter = _reference_diameter(step, job)
    op.n, limited = spindle_speed(op.vc, op.ref_diameter, max_rpm)
    if step.stage in ("face", "parting"):
        op.notes.append(G96_NOTE)
    elif limited:
        op.notes.append(f"n limited to machine max {max_rpm} rpm")

    if step.tool_type == "turning_rough" and feature.diameter is not None:
        _plan_rough_turning(op, tool, feature, job, turret)
    elif step.tool_type == "grooving":
        _plan_groove(op, tool, feature, job)
    elif step.tool_type == "threading":
        _plan_thread(op, tool, feature)
    elif step.tool_type == "parting":
        _plan_parting(op, tool, feature, job)
    elif feature.type == "chamfer":
        op.notes.append("no finishing pass on this diameter: chamfer machined separately")

    return op


def plan_job(job: JobSpec, turret: list[TurretEntry], max_rpm: int) -> list[PlannedOperation]:
    """Build the ordered list of proposed operations for a job."""
    features = list(job.features)
    chamfer_hosts = match_chamfers(features)
    hosts_with_chamfer = {id(host) for host in chamfer_hosts.values()}

    steps = [s for f in features if id(f) not in chamfer_hosts for s in feature_to_steps(f)]
    operations = []
    for step in order_steps(steps):
        op = _plan_step(step, job, turret, max_rpm)
        if step.mode == "finish" and id(step.feature) in hosts_with_chamfer:
            op.notes.append(CHAMFER_NOTE)
        operations.append(op)
    for i, op in enumerate(operations, start=1):
        op.sequence = i
    return operations
