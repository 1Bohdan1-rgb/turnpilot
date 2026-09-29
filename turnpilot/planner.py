"""Pure planning logic for a turning process sheet.

This module knows nothing about Flask or the database. It works on small
dataclasses so it can be unit-tested in isolation and reused later
(e.g. when features are extracted automatically).
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field, replace

from .extraction_schema import coarse_pitch, hex_across_corners

G96_NOTE = "G96 constant surface speed, capped at max RPM"
G97_THREAD_NOTE = "G97 constant RPM — required for threading"
CHAMFER_NOTE = "incl. chamfer"
PARTING_CENTER_NOTE = "reduce feed ~50% for last 2 mm before center"
PARTING_BORE_NOTE = "reduce feed ~50% for last 2 mm before breakthrough into bore"
GRINDING_WARNING = "may require grinding — not guaranteed by turning"
THREAD_MAJOR_NOTE = "major diameter for thread"
# GOST 2789: Rz is roughly 4 x Ra over the usual turning range. An approximation, used only for the
# finishing feed; the drawing's Rz is kept as written everywhere else.
RZ_PER_RA = 4.0


def rz_to_ra(rz: float) -> float:
    """Approximate Ra (µm) for an Rz value: Ra ≈ Rz / 4 (GOST 2789)."""
    return round(rz / RZ_PER_RA, 3)
# Tapers and fillets are recognised on drawings but not planned automatically yet.
MANUAL_OPERATION_WARNING = "manual operation"
MANUAL_FEATURE_TYPES = ("taper", "fillet")
# A hex is turned to its diameter across corners, then its flats are milled with a driven tool.
HEX_CORNERS_NOTE = "diameter across corners of the hex"
NO_MILLING_TOOL = "manual operation: no driven tool in the turret, mill the hex on a milling machine"
MILLING_DATA_NOTE = "cutting data for milling are not calculated: set them for the tool"
# The OD under an external thread is turned slightly below nominal: d - 0.1 * pitch.
THREAD_MAJOR_REDUCTION = 0.1

# Finest tolerance / roughness that turning is expected to hold.
GRINDING_IT_GRADE = 5  # IT5 or finer
GRINDING_RA_UM = 0.4  # Ra <= 0.4 um

# ISO 286-1 standard tolerance IT5 in um: (upper bound of the diameter range in mm, IT5).
IT5_UM = (
    (3, 4), (6, 5), (10, 6), (18, 8), (30, 9), (50, 11), (80, 13),
    (120, 15), (180, 18), (250, 20), (315, 23), (400, 25), (500, 27),
)

# Stage order of the process sheet: face -> rough -> finish -> groove -> thread -> mill -> parting.
STAGE_ORDER = {"face": 0, "rough": 1, "finish": 2, "groove": 3, "thread": 4, "mill": 5, "parting": 6}

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
    start_diameter: float | None = None  # groove: diameter it is cut from; taper: diameter at its start
    tolerance: str | None = None
    radius: float | None = None  # fillet
    ra_from_rz: float | None = None  # the Rz written on the drawing when ra was converted from it
    location: str | None = None  # chamfer / thread: "external" / "internal"
    across_flats: float | None = None  # hex: size across flats S; diameter is across corners


def hex_corners(feature) -> float | None:
    """Diameter across corners of a hex feature: as given, or computed from the size across flats."""
    if feature.diameter is not None:
        return feature.diameter
    return hex_across_corners(feature.across_flats) if feature.across_flats else None


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


def iso_fit_grade(tolerance: str | None) -> int | None:
    """IT grade of an ISO fit tolerance: 'h6' -> 6, 'H7' -> 7, 'js5' -> 5. None otherwise."""
    if not tolerance:
        return None
    # The lookahead skips thread designations such as "M20x1.5".
    match = re.search(r"(?<![A-Za-z0-9.])([A-Za-z]{1,2})\s?(\d{1,2})(?![\d.xX×])", tolerance)
    if not match or int(match.group(2)) > 18:  # ISO 286 grades go up to IT18
        return None
    return int(match.group(2))


def tolerance_band_mm(tolerance: str | None) -> float | None:
    """Width of a numeric tolerance: '±0.01' -> 0.02, '+0.02/-0.01' -> 0.03, '0/-0.013' -> 0.013."""
    if not tolerance:
        return None
    text = tolerance.replace(",", ".").replace("−", "-")
    if "±" in text or "+/-" in text:
        match = re.search(r"\d*\.?\d+", text.split("±")[-1].split("+/-")[-1])
        return round(2 * float(match.group()), 6) if match else None
    values = [float(v.replace(" ", "")) for v in re.findall(r"[+-]?\s*\d*\.?\d+", text)]
    if len(values) < 2:
        return None
    return round(max(values) - min(values), 6)


def it5_mm(diameter: float) -> float | None:
    """IT5 tolerance for a nominal diameter (ISO 286-1), in mm. None above 500 mm."""
    for upper, um in IT5_UM:
        if diameter <= upper:
            return um / 1000
    return None


def needs_grinding(tolerance: str | None, diameter: float | None, ra: float | None) -> bool:
    """True when the tolerance is IT5 or finer, or Ra <= 0.4 um."""
    if ra is not None and ra <= GRINDING_RA_UM:
        return True
    grade = iso_fit_grade(tolerance)
    if grade is not None:
        return grade <= GRINDING_IT_GRADE
    band = tolerance_band_mm(tolerance)
    if band is not None and diameter:
        limit = it5_mm(diameter)
        return limit is not None and band <= limit + 1e-9
    return False


# --- general (unspecified) tolerances -------------------------------------------------------------
#
# A note such as "H14, h14, ±IT14/2" (GOST 25670 / ISO 2768 style) gives the tolerance of every size
# without its own: holes H14, shafts h14, everything else ±IT14/2.

HOLE_TYPES = ("bore",)
SHAFT_TYPES = ("od_turn", "taper", "groove", "hex")  # hex: its size across flats
OTHER_TYPES = ("fillet", "chamfer")  # threads have their own class, face/parting no diameter tolerance


def general_tolerance_grade(note: str | None) -> int | None:
    """IT grade of a general tolerance note: "H14, h14, ±IT14/2" -> 14. None if not recognised."""
    if not note:
        return None
    match = re.search(r"IT\s*(\d{1,2})", note) or re.search(r"(?<![A-Za-z])[Hh]\s?(\d{1,2})(?!\d)", note)
    if not match or not 1 <= int(match.group(1)) <= 18:
        return None
    return int(match.group(1))


def general_tolerance_for(feature_type: str, grade: int) -> str | None:
    """The tolerance a general note gives a feature: holes H<grade>, shafts h<grade>, the rest ±IT<grade>/2."""
    if feature_type in HOLE_TYPES:
        return f"H{grade}"
    if feature_type in SHAFT_TYPES:
        return f"h{grade}"
    if feature_type in OTHER_TYPES:
        return f"±IT{grade}/2"
    return None


@dataclass(frozen=True)
class BlankSuggestion:
    diameter: float | None
    length: float | None
    notes: tuple[str, ...] = ()


# --- geometry checks on data read from a drawing -------------------------------------------

GEOMETRY_TOLERANCE_MM = 0.2
# Sections that follow each other along the axis. Threads and chamfers lie on top of a section,
# bores inside the part, so they are not part of the sum.
AXIAL_SECTION_TYPES = ("od_turn", "taper", "groove", "fillet", "hex")


def thread_section(thread, features):
    """The od_turn section an external thread is cut on: same diameter, and of those the shortest one
    at least as long as the thread. A part can have several sections of the thread's diameter (a Ø10
    collar and an M10 thread), so the diameter alone is not enough. If none is long enough, the longest
    one (the geometry check then reports it)."""
    same = [f for f in features if f.type == "od_turn" and f.diameter and thread.diameter
            and math.isclose(f.diameter, thread.diameter)]
    if not same or thread.length is None:
        return same[0] if same else None
    long_enough = [f for f in same if f.length is not None and f.length >= thread.length - GEOMETRY_TOLERANCE_MM]
    if long_enough:
        return min(long_enough, key=lambda f: f.length)
    with_length = [f for f in same if f.length is not None]
    return max(with_length, key=lambda f: f.length) if with_length else same[0]


def axial_length(feature) -> float | None:
    """Length of a section along the axis. A fillet without a length takes its radius."""
    if feature.type == "fillet" and feature.length is None:
        return feature.radius
    return feature.length


def geometry_warnings(features, overall_length: float | None) -> list[str]:
    """Consistency checks on the features of one part (duck-typed: type, diameter, length, radius).

    - the axial sections add up to the overall length (±0.2 mm);
    - a thread is not longer than the section it is cut on.
    """
    warnings = []
    sections = [f for f in features if f.type in AXIAL_SECTION_TYPES]
    if overall_length is not None and sections:
        lengths = [axial_length(f) for f in sections]
        total = round(sum(v for v in lengths if v is not None), 3)
        if abs(total - overall_length) > GEOMETRY_TOLERANCE_MM:
            missing = sum(v is None for v in lengths)
            suffix = f" ({missing} section{'s' if missing > 1 else ''} without length)" if missing else ""
            warnings.append(f"section lengths sum to {total:g}, overall length is {overall_length:g}{suffix}")

    for thread in (f for f in features if f.type == "thread" and f.diameter and f.length
                   and getattr(f, "location", None) != "internal"):
        section = thread_section(thread, features)
        if section and section.length and thread.length > section.length + GEOMETRY_TOLERANCE_MM:
            warnings.append(
                f"thread Ø{thread.diameter:g} is {thread.length:g} long, longer than its section ({section.length:g})"
            )
    return warnings


def suggest_blank(
    features: list[FeatureSpec],
    overall_length: float | None,
    bar_diameters: tuple[float, ...],
    diameter_allowance: float,
    facing_allowance: float,
    parting_width: float,
) -> BlankSuggestion:
    """Suggest a bar blank for a part whose drawing does not state one.

    Diameter: the largest external diameter + allowance, rounded up to the next bar size.
    Length: overall length + facing allowance + parting tool width.
    """
    notes = []
    external = [
        f.diameter for f in features if f.type in ("od_turn", "thread", "chamfer", "parting", "taper") and f.diameter
    ]
    external += [hex_corners(f) for f in features if f.type == "hex" and hex_corners(f)]
    external += [f.start_diameter for f in features if f.type in ("groove", "taper") and f.start_diameter]

    diameter = None
    if external:
        needed = max(external) + diameter_allowance
        bigger = [d for d in sorted(bar_diameters) if d >= needed - 1e-9]
        if bigger:
            diameter = float(bigger[0])
        else:
            notes.append(f"No bar size in the list covers Ø{needed:g}.")
    else:
        notes.append("No external diameter to size the blank from.")

    base = overall_length
    if base is None:
        sections = [f.length for f in features if f.type == "od_turn" and f.length]
        if sections:
            base = sum(sections)
            notes.append("Overall length not on the drawing: sum of the OD sections used.")
    length = None
    if base is not None:
        length = float(math.ceil(round(base + facing_allowance + parting_width, 6)))
    else:
        notes.append("No length to size the blank from.")
    return BlankSuggestion(diameter, length, tuple(notes))


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
        # Only used when no finishing pass on the same diameter can take the chamfer. An internal
        # chamfer (bore / internal thread entrance) needs a boring tool, not an OD finishing tool.
        tool = "boring" if feature.location == "internal" else "turning_finish"
        return [Step(feature, tool, "finish", "finish")]
    if t == "groove":
        return [Step(feature, "grooving", "finish", "groove")]
    if t == "thread":
        if feature.location == "internal":
            # tap drill first, then a tap or an internal threading bar
            return [Step(feature, "drilling", "finish", "rough"), Step(feature, "internal_threading", "finish", "thread")]
        return [Step(feature, "threading", "finish", "thread")]
    if t == "parting":
        return [Step(feature, "parting", "finish", "parting")]
    if t == "hex":
        # turned round to the diameter across corners (see plan_job), then the flats are milled
        return [
            Step(feature, "turning_rough", "rough", "rough"),
            Step(feature, "turning_finish", "finish", "finish"),
            Step(feature, "milling", "finish", "mill"),
        ]
    if t in MANUAL_FEATURE_TYPES:
        return [Step(feature, "manual", "finish", "finish")]
    raise ValueError(f"Unknown feature type: {t}")


def order_steps(steps: list[Step]) -> list[Step]:
    """Sort steps by stage; features keep their input order within a stage."""
    return sorted(steps, key=lambda s: STAGE_ORDER[s.stage])


def _host_diameter(feature) -> float | None:
    """Diameter a chamfer can sit on: a hex is chamfered on its diameter across corners."""
    return hex_corners(feature) if feature.type == "hex" else feature.diameter


def match_chamfers(features: list[FeatureSpec]) -> dict[int, FeatureSpec]:
    """Map each chamfer (by id()) to the OD/bore feature whose finishing pass machines it.

    Chamfers without a feature of the same diameter are left out and get their own pass.
    """
    hosts = {}
    for chamfer in (f for f in features if f.type == "chamfer" and f.diameter is not None):
        # an internal chamfer belongs to a bore, an external one to an OD; unknown: either
        host_types = {"internal": ("bore",), "external": ("od_turn", "hex")}.get(
            chamfer.location, ("od_turn", "hex", "bore")
        )
        for host_type in host_types:
            host = next(
                (
                    f
                    for f in features
                    if f.type == host_type and _host_diameter(f) is not None
                    and math.isclose(_host_diameter(f), chamfer.diameter)
                ),
                None,
            )
            if host:
                hosts[id(chamfer)] = host
                break
    return hosts


# Tap drill diameters for ISO coarse threads (ISO 2306), mm; other sizes: nominal - pitch.
TAP_DRILL_MM = {
    3: 2.5, 4: 3.3, 5: 4.2, 6: 5.0, 8: 6.8, 10: 8.5, 12: 10.2, 14: 12.0, 16: 14.0, 18: 15.5, 20: 17.5,
    22: 19.5, 24: 21.0, 27: 24.0, 30: 26.5, 33: 29.5, 36: 32.0, 42: 37.5, 48: 43.0,
}
INTERNAL_THREAD_DEPTH_FACTOR = 0.541  # H1 of an internal metric thread: 0.541 * pitch
TAPPING_NOTE = "G84 rigid tapping, feed = pitch"
NO_INTERNAL_THREAD_TOOL = "manual operation: no tap or internal threading tool in the turret"
NO_DRILL = "manual operation: no drill in the turret"


def tap_drill_diameter(nominal: float, pitch: float) -> float:
    """Tap drill for an internal metric thread: the ISO 2306 table for coarse threads, else nominal - pitch."""
    for size, drill in TAP_DRILL_MM.items():
        if math.isclose(size, nominal) and math.isclose(pitch, coarse_pitch(size) or 0):
            return drill
    return round(nominal - pitch, 2)


def thread_major_diameter(nominal: float, pitch: float) -> float:
    """Turned diameter under an external thread: slightly below nominal, d - 0.1 * pitch."""
    return round(nominal - THREAD_MAJOR_REDUCTION * pitch, 3)


def match_thread_diameters(features: list[FeatureSpec]) -> dict[int, float]:
    """Map each od_turn feature (by id()) that carries an external thread to the thread pitch."""
    threads = [f for f in features if f.type == "thread" and f.diameter and f.pitch and f.location != "internal"]
    pitches = {}
    for thread in threads:
        section = thread_section(thread, features)
        if section is not None and id(section) not in pitches:
            pitches[id(section)] = thread.pitch
    return pitches


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


def _plan_internal_thread_step(op, step, job, turret, max_rpm) -> PlannedOperation:
    """Tap drill, then a tap or an internal threading bar; a manual operation without the tool."""
    feature = step.feature
    if not (feature.diameter and feature.pitch):
        op.warnings.append("Internal thread without diameter or pitch: cannot plan it.")
        return op
    if step.tool_type == "drilling":
        drill = tap_drill_diameter(feature.diameter, feature.pitch)
        op.notes.append(f"tap drill Ø{drill:g} for M{feature.diameter:g}×{feature.pitch:g}")
        op.ref_diameter, op.depth = drill, feature.length
        entry, _ = select_tool("drilling", job.iso_group, turret)
        if entry is None:
            op.warnings.append(NO_DRILL)
            return op
        op.tool_id, op.tool_name, op.turret_position = entry.tool.id, entry.tool.name, entry.position
        op.vc, op.f, _ = cutting_data(entry.tool, "finish")
        op.n, _ = spindle_speed(op.vc, drill, max_rpm)
        return op

    for tool_type in ("tapping", "threading_internal"):
        entry, _ = select_tool(tool_type, job.iso_group, turret)
        if entry:
            break
    op.tool_type = tool_type if entry else "tapping"
    op.f = feature.pitch
    op.ref_diameter = feature.diameter
    if entry is None:
        op.warnings.append(NO_INTERNAL_THREAD_TOOL)
        return op
    tool = entry.tool
    op.tool_id, op.tool_name, op.turret_position = tool.id, tool.name, entry.position
    op.vc = round((tool.vc_min + tool.vc_max) / 2, 1)
    op.n, _ = spindle_speed(op.vc, feature.diameter, max_rpm)
    if tool_type == "tapping":
        op.notes.append(TAPPING_NOTE)
    else:
        op.notes.insert(0, G97_THREAD_NOTE)
        op.depth = round(INTERNAL_THREAD_DEPTH_FACTOR * feature.pitch, 3)
        infeed, _method = thread_infeed(op.depth, tool.ap_max, tool.ap_min)
        op.passes = len(infeed)
        op.notes.append(f"radial infeed per pass: {', '.join(f'{d:g}' for d in infeed[:-1])} + spring pass")
    return op


def _plan_hex_milling(op: PlannedOperation, feature: FeatureSpec, job: JobSpec, turret) -> PlannedOperation:
    """Mill the flats of a hex with a driven tool; a manual operation without one."""
    size = f"S{feature.across_flats:g}" if feature.across_flats else "S not given"
    length = f", L{feature.length:g}" if feature.length else ""
    op.notes.append(f"mill hex {size} across flats{length}")
    op.depth = (
        round((hex_corners(feature) - feature.across_flats) / 2, 3)
        if feature.across_flats and hex_corners(feature) else None
    )
    entry, _ = select_tool("milling", job.iso_group, turret)
    if entry is None:
        op.warnings.append(NO_MILLING_TOOL)
        return op
    op.tool_id, op.tool_name, op.turret_position = entry.tool.id, entry.tool.name, entry.position
    op.notes.append("driven tool, C-axis: 6 flats at 60°")
    op.notes.append(MILLING_DATA_NOTE)
    return op


def _plan_step(step: Step, job: JobSpec, turret: list[TurretEntry], max_rpm: int) -> PlannedOperation:
    feature = step.feature
    op = PlannedOperation(
        feature_id=feature.id,
        feature_type=feature.type,
        tool_type=step.tool_type,
        mode=step.mode,
    )
    if step.mode == "finish" and needs_grinding(feature.tolerance, feature.diameter, feature.ra):
        op.warnings.append(GRINDING_WARNING)
    if step.mode == "finish" and feature.ra_from_rz is not None:
        op.warnings.append(
            f"Ra {feature.ra:g} µm assumed from Rz {feature.ra_from_rz:g} (Ra ≈ Rz/4, GOST 2789): check"
        )
    if step.tool_type == "manual":
        op.warnings.append(MANUAL_OPERATION_WARNING)
        return op

    if step.tool_type in ("drilling", "internal_threading"):
        return _plan_internal_thread_step(op, step, job, turret, max_rpm)

    if step.tool_type == "milling":
        return _plan_hex_milling(op, feature, job, turret)

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

    thread_pitches = match_thread_diameters(features)

    steps = [s for f in features if id(f) not in chamfer_hosts for s in feature_to_steps(f)]
    operations = []
    for step in order_steps(steps):
        original = step.feature
        pitch = thread_pitches.get(id(original))
        if pitch:
            # Rough and finish the OD under the thread to the reduced major diameter.
            major = thread_major_diameter(original.diameter, pitch)
            step = replace(step, feature=replace(original, diameter=major))
        elif original.type == "hex" and step.tool_type != "milling":
            step = replace(step, feature=replace(original, diameter=hex_corners(original)))
        op = _plan_step(step, job, turret, max_rpm)
        if pitch and step.mode == "finish":
            op.notes.append(f"{THREAD_MAJOR_NOTE}: Ø{major:g} (nominal Ø{original.diameter:g})")
        if step.mode == "finish" and id(original) in hosts_with_chamfer and step.tool_type != "milling":
            op.notes.append(CHAMFER_NOTE)
        if original.type == "hex" and step.tool_type == "turning_finish":
            op.notes.append(f"{HEX_CORNERS_NOTE} S{original.across_flats:g}" if original.across_flats
                            else HEX_CORNERS_NOTE)
        operations.append(op)
    for i, op in enumerate(operations, start=1):
        op.sequence = i
    return operations
