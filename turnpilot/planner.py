"""Pure planning logic for a turning process sheet.

This module knows nothing about Flask or the database. It works on small
dataclasses so it can be unit-tested in isolation and reused later
(e.g. when features are extracted automatically).
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field, replace

from .extraction_schema import WRENCH_SIZES_MM, coarse_pitch, hex_across_corners, hex_across_flats

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
# Tapers, fillets and arcs (formed sections) are recognised on drawings but not planned automatically yet.
MANUAL_OPERATION_WARNING = "manual operation"
MANUAL_FEATURE_TYPES = ("taper", "fillet", "arc")
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

# Stage order of the process sheet: face -> drill (centre, then drills) -> rough -> finish -> groove -> thread ->
# mill -> parting. Boring is in rough / finish, so always after the hole is drilled.
STAGE_ORDER = {"face": 0, "drill": 1, "rough": 2, "finish": 3, "groove": 4, "thread": 5, "mill": 6, "parting": 7}

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
    diameter: float | None = None  # drills: the hole they make


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
    start_diameter: float | None = None  # groove: diameter it is cut from; taper / arc: diameter at its start
    tolerance: str | None = None
    radius: float | None = None  # fillet, arc
    ra_from_rz: float | None = None  # the Rz written on the drawing when ra was converted from it
    location: str | None = None  # chamfer / thread: "external" / "internal"
    across_flats: float | None = None  # hex: size across flats S; diameter is across corners


def hex_corners(feature) -> float | None:
    """Diameter across corners of a hex feature: as given, or computed from the size across flats."""
    if feature.diameter is not None:
        return feature.diameter
    return hex_across_corners(feature.across_flats) if feature.across_flats else None


def hex_flats(feature) -> float | None:
    """Size across flats of a hex feature: as given, or computed from the diameter across corners."""
    if feature.across_flats is not None:
        return feature.across_flats
    return hex_across_flats(feature.diameter) if feature.diameter else None


@dataclass(frozen=True)
class JobSpec:
    iso_group: str
    blank_diameter: float  # round bar: Ø; hex bar: size across flats S
    blank_length: float
    features: tuple[FeatureSpec, ...]
    blank_shape: str = "round"  # "round" or "hex"
    # The features are listed in their order along the axis (a job from a DXF): each section is then roughed
    # from its neighbour towards the chuck, not from the bar (see rough_starts).
    axial_order: bool = False
    # The material's specific cutting force (kc = kc1 * hm^-mc) for the spindle power check, and its source.
    material_name: str | None = None
    kc1: float | None = None
    mc: float | None = None
    kc_source: str | None = None

    @property
    def stock_diameter(self) -> float:
        """Largest diameter of the bar the tools meet: a hex bar's diameter across corners."""
        return stock_diameter(self.blank_shape, self.blank_diameter)


@dataclass(frozen=True)
class PowerSpec:
    """The machine's spindle power and the share of it the drive delivers; None: not known."""
    power_kw: float | None
    efficiency: float | None


def stock_diameter(blank_shape: str, blank_size: float) -> float:
    """Diameter the lathe has to turn from: round bar Ø, or the diameter across corners of a hex bar S."""
    return hex_across_corners(blank_size) if blank_shape == "hex" else blank_size


# A hex whose size across flats is the hex bar's size needs no machining: the bar already has the flats.
HEX_BAR_MATCH_MM = 0.05
HEX_FROM_BAR_NOTE = "flats from the hex bar: not machined"


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


# --- roughing from the neighbouring section (features in their order along the axis) ---------------
#
# The profile is turned from the free end towards the chuck. The chuck holds the part at its largest
# diameter, so that section is roughed from the bar; every other section is roughed from the nearest
# section towards the chuck that the planner turns and that is at least as large (its pass has already
# brought the material there down to it). Grooves, tapers, arcs and fillets are not roughed by the planner:
# they are passed over. A section narrower than both neighbours is not guessed: it is flagged and roughed
# from the bar as before.

TURNED_SECTION_TYPES = ("od_turn", "hex")  # sections the planner roughs
PROFILE_SECTION_TYPES = ("od_turn", "hex", "taper", "arc")  # sections with a diameter at both ends
ROUGH_FROM_BAR_NOTE = "roughed from the bar Ø{d:g}"
ROUGH_FROM_NEIGHBOUR_NOTE = "roughed from Ø{d:g}, the neighbouring section towards the chuck"
NO_STOCK_AFTER_NEIGHBOUR_NOTE = "no roughing stock left after Ø{d:g} (the neighbouring section towards the chuck)"
PIT_WARNING = ("check: section narrower than both its neighbours (a wide groove?): how it is roughed is not "
               "determined, passes counted from the bar")
TWO_SIDES_NOTE = ("largest Ø between smaller sections: the part is machined from both sides of it "
                  "(re-chucking is not planned)")


@dataclass(frozen=True)
class RoughStart:
    diameter: float  # the diameter the section is roughed from
    from_bar: bool
    pit: bool = False


def _boundary_diameters(feature) -> tuple[float, float]:
    """(Ø at its left end, Ø at its right end); a taper / arc runs from start_diameter to diameter."""
    if feature.type in ("taper", "arc"):
        return feature.start_diameter or 0.0, feature.diameter or 0.0
    d = (hex_corners(feature) if feature.type == "hex" else feature.diameter) or 0.0
    return d, d


def rough_starts(features, turned_diameter, stock: float) -> tuple[dict[int, RoughStart], bool]:
    """The diameter each turned section (by id()) is roughed from, and whether the largest Ø is between smaller
    sections (the part is machined from both sides). `features` are in their order along the axis;
    turned_diameter(f) is the diameter a turned section is cut to (a thread's reduced major Ø, a hex's corners)."""
    profile = [f for f in features if f.type in PROFILE_SECTION_TYPES]
    turned = [k for k, f in enumerate(profile) if f.type in TURNED_SECTION_TYPES and turned_diameter(f)]
    if not turned:
        return {}, False
    # compared by the drawing's diameters; a start is the diameter the neighbour is actually cut to
    nominal = {k: _boundary_diameters(profile[k])[0] for k in turned}
    sizes = {k: turned_diameter(profile[k]) for k in turned}
    pits = set()
    for k in turned:
        if 0 < k < len(profile) - 1:
            left, right = _boundary_diameters(profile[k - 1])[1], _boundary_diameters(profile[k + 1])[0]
            if left > nominal[k] + 1e-9 and right > nominal[k] + 1e-9:
                pits.add(k)
    largest = max((nominal[k] for k in turned if k not in pits), default=None)
    roots = [k for k in turned if k not in pits and largest is not None and nominal[k] >= largest - 1e-9]
    starts = {}
    for k in turned:
        if k in pits:
            starts[id(profile[k])] = RoughStart(stock, from_bar=True, pit=True)
            continue
        if k in roots:
            starts[id(profile[k])] = RoughStart(stock, from_bar=True)
            continue
        root = min(roots, key=lambda r: abs(r - k))  # the chuck side: towards the nearest largest Ø
        step = 1 if root > k else -1
        j = k + step
        while not (j in sizes and j not in pits and nominal[j] >= nominal[k] - 1e-9):
            j += step
        starts[id(profile[k])] = RoughStart(sizes[j], from_bar=False)
    both_sides = bool(roots) and any(k < roots[0] for k in turned if k not in pits) and \
        any(k > roots[-1] for k in turned if k not in pits)
    return starts, both_sides


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


# ISO 286-1 standard tolerance IT11 in um: (upper bound of the size range in mm, IT11).
IT11_UM = ((3, 60), (6, 75), (10, 90), (18, 110), (30, 130), (50, 160), (80, 190), (120, 220), (180, 250), (250, 290))
# ISO 286-1 standard tolerance IT12 in um: (upper bound of the size range in mm, IT12).
IT12_UM = ((3, 100), (6, 120), (10, 150), (18, 180), (30, 210), (50, 250), (80, 300), (120, 350), (180, 400), (250, 460))
# Drawn (calibrated) hex bar is made to h11 across flats (GOST 8560 / EN 10278).
HEX_BAR_TOLERANCE = "h11"


def tighter_than_h11(tolerance: str | None, size: float | None) -> bool:
    """True when a tolerance on a size is tighter than IT11 (h9, ±0.02 on S11, ...)."""
    grade = iso_fit_grade(tolerance)
    if grade is not None:
        return grade < 11
    band = tolerance_band_mm(tolerance)
    if band is None or not size:
        return False
    it11 = next((um / 1000 for upper, um in IT11_UM if size <= upper), None)
    return it11 is not None and band < it11 - 1e-9


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
SHAFT_TYPES = ("od_turn", "taper", "groove", "hex", "arc")  # hex: its size across flats
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
    diameter: float | None  # round bar: Ø; hex bar: size across flats S
    length: float | None
    notes: tuple[str, ...] = ()
    shape: str = "round"


# --- geometry checks on data read from a drawing -------------------------------------------

GEOMETRY_TOLERANCE_MM = 0.2
# Sections that follow each other along the axis. Threads and chamfers lie on top of a section,
# bores inside the part, so they are not part of the sum.
AXIAL_SECTION_TYPES = ("od_turn", "taper", "groove", "fillet", "hex", "arc")


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
    - a thread is not longer than the section it is cut on;
    - a hex with both sizes: its diameter is not below its size across flats, nor well below S / cos 30°.
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
    for hex_ in (f for f in features if f.type == "hex"):
        warnings.extend(hex_size_warnings(hex_.diameter, getattr(hex_, "across_flats", None)))
        if hex_probably_s(hex_.diameter, getattr(hex_, "across_flats", None)):
            warnings.append(
                f"hex Ø{hex_.diameter:g}: probably S{hex_.diameter:g} on the chamfer circle "
                f"(Ø{hex_.diameter:g} is a wrench size, S{hex_across_flats(hex_.diameter):g} computed from it "
                f"is not): check"
            )
    return warnings


def _is_wrench_size(size: float, tolerance: float = 0.01) -> bool:
    return any(abs(size - s) <= tolerance for s in WRENCH_SIZES_MM)


def hex_probably_s(diameter: float | None, across_flats: float | None) -> bool:
    """True when a hex's Ø looks like its size across flats given on the chamfer circle.

    The Ø is a standard wrench size, the S computed from it (Ø · cos 30°) is not, and the S on record is
    that computed one (or missing): e.g. Ø13 read as across corners gives S11.26, where S13 is meant.
    """
    if not diameter:
        return False
    computed = hex_across_flats(diameter)
    if across_flats is not None and abs(across_flats - computed) > 0.05:
        return False  # an S of its own on the drawing: the Ø is not the only size
    return _is_wrench_size(diameter) and not _is_wrench_size(computed, tolerance=0.05)


# A hex given with both sizes: D need not be S / cos 30° (it may be the diameter turned before milling,
# with corners slightly rounded). ISO 4032 allows corners down to about 1.10 x S; below that the
# corners are visibly cut off.
HEX_MIN_CORNERS_PER_FLATS = 1.10


def hex_size_warnings(diameter: float | None, across_flats: float | None) -> list[str]:
    """Warnings for a hex with both its diameter and its size across flats on the drawing."""
    if not diameter or not across_flats:
        return []
    if diameter < across_flats:
        return [f"hex Ø{diameter:g} is smaller than its size across flats S{across_flats:g}: check"]
    if diameter < HEX_MIN_CORNERS_PER_FLATS * across_flats - 1e-9:
        return [
            f"hex Ø{diameter:g} is well below S{across_flats:g} / cos 30° = Ø{hex_across_corners(across_flats):g}: "
            f"corners cut off, check"
        ]
    return []


def suggest_blank(
    features: list[FeatureSpec],
    overall_length: float | None,
    bar_diameters: tuple[float, ...],
    diameter_allowance: float,
    facing_allowance: float,
    parting_width: float,
    hex_bar_sizes: tuple[float, ...] = (),
) -> BlankSuggestion:
    """Suggest a bar blank for a part whose drawing does not state one.

    Diameter: the largest external diameter + allowance, rounded up to the next bar size.
    Length: overall length + facing allowance + parting tool width.
    A part whose largest section is a hex takes a hex bar of that size across flats when one is in
    stock: the flats then need no machining.
    """
    notes = []
    external = [
        f.diameter for f in features
        if f.type in ("od_turn", "thread", "chamfer", "parting", "taper", "arc") and f.diameter
    ]
    external += [hex_corners(f) for f in features if f.type == "hex" and hex_corners(f)]
    external += [f.start_diameter for f in features if f.type in ("groove", "taper", "arc") and f.start_diameter]

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

    shape = "round"
    hexes = [f for f in features if f.type == "hex" and hex_corners(f)]
    if hexes:
        largest_hex = max(hexes, key=hex_corners)
        others = [
            f.diameter for f in features if f.type in ("od_turn", "thread", "parting", "taper", "arc") and f.diameter
        ] + [f.start_diameter for f in features if f.type in ("groove", "taper", "arc") and f.start_diameter]
        size = hex_flats(largest_hex)
        # every other diameter must fit inside the flats: between S and the corners it would stay
        # partly unturned on a hex bar
        if others and size and max(others) > size + 1e-9:
            notes.append(
                f"Ø{max(others):g} is larger than the hex size across flats S{size:g}: round bar, the hex is milled."
            )
        elif size and any(math.isclose(size, s, abs_tol=HEX_BAR_MATCH_MM) for s in hex_bar_sizes):
            shape, diameter = "hex", float(size)
            if tighter_than_h11(largest_hex.tolerance, size):
                notes.append(
                    f"Hex bar S{size:g}: tolerance {largest_hex.tolerance} is tighter than the bar's "
                    f"{HEX_BAR_TOLERANCE}, the flats have to be milled."
                )
            else:
                notes.append(f"Hex bar S{size:g}: the flats are not machined.")
        else:
            label = f"S{size:g}" if size else "of this size"
            notes.append(f"No hex bar {label} in stock: round bar, the hex is milled.")

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
    return BlankSuggestion(diameter, length, tuple(notes), shape)


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


def feature_to_steps(feature: FeatureSpec, hex_bar: float | None = None, hole: HolePlan | None = None) -> list[Step]:
    """Split a part feature into machining steps. hex_bar: size across flats of a hex bar blank. hole: how a bore
    or an internal thread is drilled (plan_holes); without it a bore is bored only, as from a drilled hole."""
    t = feature.type
    if t == "face":
        return [Step(feature, "facing", "rough", "face")]
    if t == "od_turn":
        return [
            Step(feature, "turning_rough", "rough", "rough"),
            Step(feature, "turning_finish", "finish", "finish"),
        ]
    if t == "bore":
        drilling = [Step(feature, "drilling", "finish", "drill")] if hole and hole.drilled_here else []
        if hole and hole.drill_only:
            return drilling
        return drilling + [
            Step(feature, "boring", "rough", "rough"),
            Step(feature, "boring", "finish", "finish"),
        ]
    if t == "chamfer":
        # Only used when no finishing pass on the same diameter can take the chamfer. An internal
        # chamfer (bore / internal thread entrance) needs a boring tool, not an OD finishing tool.
        tool = "boring" if feature.location == "internal" else "turning_finish"
        return [Step(feature, tool, "finish", "finish")]
    if t == "groove":
        if groove_needs_finish(feature):
            # plunges leaving an allowance, then a finishing pass over the bottom and the walls
            return [Step(feature, "grooving", "rough", "groove"), Step(feature, "grooving", "finish", "groove")]
        return [Step(feature, "grooving", "finish", "groove")]
    if t == "thread":
        if feature.location == "internal":
            # tap drill first (unless a hole drilled for another feature already makes it), then a tap or a bar
            drilling = [] if hole and not hole.drilled_here else [Step(feature, "drilling", "finish", "drill")]
            return drilling + [Step(feature, "internal_threading", "finish", "thread")]
        return [Step(feature, "threading", "finish", "thread")]
    if t == "parting":
        return [Step(feature, "parting", "finish", "parting")]
    if t == "hex" and hex_bar and hex_flats(feature) and math.isclose(
        hex_flats(feature), hex_bar, abs_tol=HEX_BAR_MATCH_MM
    ):
        if tighter_than_h11(feature.tolerance, hex_flats(feature)):
            return [Step(feature, "milling", "finish", "mill")]  # skim the bar's flats to the tolerance
        return [Step(feature, "hex_bar", "finish", "mill")]
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


# --- holes: centre drilling, then a drill chosen by its diameter, then boring ---------------------------------
#
# All holes are on the axis and drilled from one face: one centring starts them. A bore gets the largest drill
# that still leaves the boring tool's finishing allowance (its ap_min) on each side; a drill of the bore's own
# diameter makes it at once when the bore needs no finer tolerance or roughness than a drill gives. A tap drill
# has to be the exact size. A hole drilled for one feature that is at least as large and as deep as another's
# drill makes that one too.

# PLACEHOLDER: a drilled hole is about IT12 with Ra 6.3 or coarser; finer needs boring.
DRILL_IT_GRADE = 12
DRILL_RA_UM = 6.3
# PLACEHOLDER: deeper than this many drill diameters is drilled with chip removal (G83).
PECK_DEPTH_FACTOR = 3
DRILL_SIZE_TOL_MM = 0.01
TAP_DRILL_TOL_MM = 0.05
NO_CENTRE_DRILL = "no centre drill in the turret: centre the hole by hand"
NO_BORING_ALLOWANCE = "no boring tool in the turret: the boring allowance is unknown, no drill chosen"
NO_DRILL_UP_TO = "no drill up to Ø{d:g} in the turret (Ø{hole:g} less the boring allowance {a:g} mm/side): drill by hand"
NO_TAP_DRILL = "no Ø{d:g} drill in the turret (tap drill for M{nominal:g}×{pitch:g})"
DRILL_NOTE = "drill Ø{d:g}: the largest up to Ø{limit:g} (Ø{hole:g} less the boring tool's ap_min {a:g} mm/side)"
DRILL_ONLY_NOTE = "drill Ø{d:g} makes the hole: tolerance and roughness need no boring"
PECK_NOTE = "G83 peck drilling: depth {depth:g} > {k:g} × Ø{d:g}"
COVERED_NOTE = "drilled Ø{d:g} with feature {fid}'s hole"
FROM_SOLID_NOTE = "no drill chosen: bored from solid? Drill the hole first"


@dataclass(frozen=True)
class HolePlan:
    diameter: float | None  # the drilled diameter (None: no drill chosen)
    depth: float | None
    entry: TurretEntry | None = None  # the drill; None with a warning when there is none
    warning: str | None = None
    drill_only: bool = False  # the drill makes the bore, no boring
    drilled_here: bool = True  # False: another feature's hole makes this one (covered_by)
    covered_by: int | None = None  # that feature's id
    note: str | None = None


def drill_gives(tolerance: str | None, diameter: float, ra: float | None) -> bool:
    """A drilled hole is good enough: no tolerance finer than IT12 and no roughness finer than Ra 6.3."""
    if ra is not None and ra < DRILL_RA_UM - 1e-9:
        return False
    if not tolerance:
        return True
    grade = iso_fit_grade(tolerance)
    if grade is not None:
        return grade >= DRILL_IT_GRADE
    band = tolerance_band_mm(tolerance)
    single = re.fullmatch(r"\s*([+\-−])\s*(\d*[.,]?\d+)\s*", tolerance)
    if band is None and single:  # one deviation written ("+0.21" is 0 / +0.21): the band is its size
        band = float(single.group(2).replace(",", "."))
    it12 = next((um / 1000 for upper, um in IT12_UM if diameter <= upper), None)
    return band is not None and it12 is not None and band >= it12 - 1e-9


def _drills(iso_group, turret):
    return [e for e in turret if e.tool.type == "drilling" and iso_group in e.tool.iso_group and e.tool.diameter]


def plan_holes(features, iso_group: str, turret: list[TurretEntry]) -> dict[int, HolePlan]:
    """How each bore and internal thread (by id()) is drilled."""
    drills = _drills(iso_group, turret)
    boring, _ = select_tool("boring", iso_group, turret)
    plans = {}
    for f in features:
        if f.type == "bore" and f.diameter:
            exact = [e for e in drills if abs(e.tool.diameter - f.diameter) <= DRILL_SIZE_TOL_MM]
            if exact and drill_gives(f.tolerance, f.diameter, f.ra):
                plans[id(f)] = HolePlan(f.diameter, f.length, exact[0], drill_only=True,
                                        note=DRILL_ONLY_NOTE.format(d=f.diameter))
                continue
            if boring is None:
                plans[id(f)] = HolePlan(None, f.length, warning=NO_BORING_ALLOWANCE)
                continue
            allowance = boring.tool.ap_min
            limit = round(f.diameter - 2 * allowance, 3)
            fits = [e for e in drills if e.tool.diameter <= limit + 1e-9]
            if not fits:
                plans[id(f)] = HolePlan(None, f.length, warning=NO_DRILL_UP_TO.format(d=limit, hole=f.diameter,
                                                                                       a=allowance))
                continue
            entry = min(fits, key=lambda e: (-e.tool.diameter, e.position))
            plans[id(f)] = HolePlan(entry.tool.diameter, f.length, entry, note=DRILL_NOTE.format(
                d=entry.tool.diameter, limit=limit, hole=f.diameter, a=allowance))
        elif f.type == "thread" and f.location == "internal" and f.diameter and f.pitch:
            size = tap_drill_diameter(f.diameter, f.pitch)
            exact = [e for e in drills if abs(e.tool.diameter - size) <= TAP_DRILL_TOL_MM]
            if exact:
                plans[id(f)] = HolePlan(size, f.length, min(exact, key=lambda e: e.position))
            else:
                plans[id(f)] = HolePlan(size, f.length, warning=NO_DRILL if not drills else NO_TAP_DRILL.format(
                    d=size, nominal=f.diameter, pitch=f.pitch))
    # a hole drilled for one feature makes another's when it is at least as large and as deep
    by_id = {id(f): f for f in features}
    for key, plan in list(plans.items()):
        if plan.diameter is None or plan.depth is None or plan.drill_only:
            continue
        for other_key, other in plans.items():
            if other_key == key or other.diameter is None or other.depth is None or other.covered_by is not None:
                continue
            hole = by_id[key]
            fits_inside = hole.type != "bore" or other.diameter <= hole.diameter + 1e-9
            larger = other.diameter > plan.diameter + 1e-9 or (abs(other.diameter - plan.diameter) <= 1e-9
                                                               and other.depth > plan.depth + 1e-9)
            if fits_inside and larger and other.depth >= plan.depth - 1e-9 and other.diameter >= plan.diameter - 1e-9:
                plans[key] = replace(plan, diameter=other.diameter, entry=None, warning=None, drilled_here=False,
                                     covered_by=by_id[other_key].id,
                                     note=COVERED_NOTE.format(d=other.diameter, fid=by_id[other_key].id))
                break
    return plans


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


# --- grooves wider than the insert -----------------------------------------------------------------
#
# A groove wider than the insert is cut with several plunges that overlap (never edge to edge).
# PLACEHOLDER: the step between plunges is at most this share of the insert width, i.e. the plunges overlap
# by at least 20% of it (0.6 mm on a 3 mm insert). The tools carry no overlap of their own.
GROOVE_STEP_FACTOR = 0.8
GROOVE_WIDTH_TOL_MM = 0.01  # a groove this much wider than the insert is still one plunge
# PLACEHOLDER: plunging leaves about Ra 3.2; a groove with Ra this fine or finer gets a finishing pass over
# the bottom and both walls, after plunges that leave this allowance on each of them.
GROOVE_FINISH_RA = 1.6
GROOVE_FINISH_ALLOWANCE_MM = 0.2
GROOVE_FINISH_NOTE = "finish the bottom and both walls: {a:g} mm"
GROOVE_ALLOWANCE_NOTE = "leaves {a:g} mm on the walls and the bottom for finishing"
GROOVE_NO_ROOM_WARNING = ("Ra {ra:g} needs a finishing pass, but the groove ({width:g} mm) has no room for it with a "
                          "{insert:g} mm insert and {a:g} mm on each wall: finishing needs a narrower insert.")


def groove_needs_finish(feature) -> bool:
    return feature.type == "groove" and feature.ra is not None and feature.ra <= GROOVE_FINISH_RA + 1e-9


def _groove_has_room(width: float | None, insert_width: float | None) -> bool:
    """Plunges that leave the allowance on both walls still fit the insert (there is room to finish)."""
    if width is None or not insert_width:
        return True
    return width - 2 * GROOVE_FINISH_ALLOWANCE_MM >= insert_width - GROOVE_WIDTH_TOL_MM


def groove_plunges(width: float, insert_width: float) -> tuple[int, float]:
    """(plunges, step between them in mm) to cut a groove of `width` with an insert of `insert_width`.

    One plunge when the groove is the insert's width; otherwise the first and the last plunge touch the
    walls and the ones between are spread evenly, the step never above GROOVE_STEP_FACTOR * insert_width.
    """
    if width <= insert_width + GROOVE_WIDTH_TOL_MM:
        return 1, 0.0
    plunges = 1 + math.ceil(round((width - insert_width) / (GROOVE_STEP_FACTOR * insert_width), 9))
    return plunges, round((width - insert_width) / (plunges - 1), 3)


def select_grooving_tool(width: float | None, iso_group: str,
                         turret: list[TurretEntry]) -> tuple[TurretEntry | None, str | None]:
    """The grooving tool for a groove of `width`: of the inserts not wider than the groove, the widest (fewest
    plunges). A groove narrower than every insert gets no tool and a warning (not cut wider than drawn).
    Without a width, or when no insert width is known, the choice is select_tool's."""
    entry, warning = select_tool("grooving", iso_group, turret)
    if entry is None or width is None:
        return entry, warning
    sized = [e for e in turret if e.tool.type == "grooving" and iso_group in e.tool.iso_group and e.tool.insert_width]
    if not sized:
        return entry, None
    fits = [e for e in sized if e.tool.insert_width <= width + GROOVE_WIDTH_TOL_MM]
    if not fits:
        narrowest = min(e.tool.insert_width for e in sized)
        return None, (f"Groove {width:g} mm is narrower than the narrowest grooving insert in the turret "
                      f"({narrowest:g} mm): install a narrower insert.")
    return min(fits, key=lambda e: (-e.tool.insert_width, e.position)), None


def finish_allowance(iso_group: str, turret: list[TurretEntry]) -> tuple[float, bool]:
    """Radial allowance left for finishing: the finishing tool's ap. Returns (allowance, is_default)."""
    entry, _ = select_tool("turning_finish", iso_group, turret)
    if entry is None:
        return DEFAULT_FINISH_ALLOWANCE_MM, True
    return cutting_data(entry.tool, "finish")[2], False


def _reference_diameter(step: Step, job: JobSpec, start: RoughStart | None = None) -> float:
    """Diameter used for the spindle speed calculation."""
    if step.stage == "face":
        return job.stock_diameter
    if step.stage == "parting":
        return step.feature.diameter or job.stock_diameter
    if step.tool_type == "turning_rough":
        # Diameter before the first pass: the neighbouring section's when known, else the blank's (a hex bar:
        # across corners).
        return start.diameter if start else job.stock_diameter
    if step.tool_type == "grooving":
        # The groove starts on the larger diameter, not at the bottom.
        return step.feature.start_diameter or job.stock_diameter
    return step.feature.diameter or job.stock_diameter


# --- spindle power of roughing: Pc = Vc · ap · f · kc / 60000 kW ------------------------------------
# kc = kc1 · hm^-mc with hm = f · sin(κr); the tools carry no entering angle, so κr = 90° (hm = f). A 95°
# holder changes kc by under 0.5%; a 45° one would raise it.
POWER_OK_NOTE = "Pc {pc:.2f} kW ≤ {allowed:.2f} kW ({power:g} × {eff:g}); kc {kc:.0f} N/mm² ({source})"
POWER_REDUCED_NOTE = ("ap reduced for spindle power: Pc {pc0:.2f} > {allowed:.2f} kW ({power:g} × {eff:g}) at ap {ap0:g}; "
                      "now {passes} × ap {ap:g}, Pc {pc:.2f} kW; kc {kc:.0f} N/mm² ({source})")
POWER_AP_MIN_WARNING = ("spindle power is not enough even at the tool's ap_min {ap_min:g}: Pc {pc:.2f} > {allowed:.2f} kW; "
                        "reduce f or Vc (not changed automatically)")
NO_KC_WARNING = "no kc1 / mc for {material}: spindle power not checked (Machine page, Materials)"
NO_EFFICIENCY_WARNING = ("check the spindle power: Pc {pc:.2f} kW at ap {ap:g}, power {power:g} kW, but the drive "
                         "efficiency is not set on the Machine page")
NO_POWER_WARNING = "check the spindle power: Pc {pc:.2f} kW at ap {ap:g}, the machine power is not set"


def specific_cutting_force(kc1: float, mc: float, f: float) -> float:
    """kc in N/mm² for the feed f (mm/rev): kc1 · hm^-mc with hm = f (entering angle 90°)."""
    return kc1 * f ** -mc


def cutting_power(vc: float, ap: float, f: float, kc: float) -> float:
    """Cutting power in kW: Vc (m/min) · ap (mm) · f (mm/rev) · kc (N/mm²) / 60000."""
    return vc * ap * f * kc / 60000


def _check_rough_power(op, tool, feature, job, from_diameter, allowance, power: PowerSpec) -> None:
    """Fewer passes are not taken: above the available power the passes get thinner (more of them); Vc and f
    stay as chosen."""
    if job.kc1 is None or job.mc is None:
        op.warnings.append(NO_KC_WARNING.format(material=job.material_name or "this material"))
        return
    kc = specific_cutting_force(job.kc1, job.mc, op.f)
    vc = math.pi * op.ref_diameter * op.n / 1000  # actual: n may be capped at max RPM
    pc = cutting_power(vc, op.ap, op.f, kc)
    if power.power_kw is None:
        op.warnings.append(NO_POWER_WARNING.format(pc=pc, ap=op.ap))
        return
    if power.efficiency is None:
        op.warnings.append(NO_EFFICIENCY_WARNING.format(pc=pc, ap=op.ap, power=power.power_kw))
        return
    allowed = power.power_kw * power.efficiency
    source = job.kc_source or "source not given"
    if pc <= allowed + 1e-9:
        op.notes.append(POWER_OK_NOTE.format(pc=pc, allowed=allowed, power=power.power_kw, eff=power.efficiency,
                                             kc=kc, source=source))
        return
    ap_power = allowed * 60000 / (vc * op.f * kc)
    if ap_power < tool.ap_min:
        ap_power = tool.ap_min
        op.warnings.append(POWER_AP_MIN_WARNING.format(ap_min=tool.ap_min, pc=cutting_power(vc, tool.ap_min, op.f, kc),
                                                       allowed=allowed))
    ap0 = op.ap
    op.passes, op.ap = rough_passes(from_diameter, feature.diameter, min(tool.ap_max, ap_power), allowance)
    op.notes.append(POWER_REDUCED_NOTE.format(pc0=pc, allowed=allowed, power=power.power_kw, eff=power.efficiency,
                                              ap0=ap0, passes=op.passes, ap=op.ap,
                                              pc=cutting_power(vc, op.ap, op.f, kc), kc=kc, source=source))


def _plan_rough_turning(op: PlannedOperation, tool: ToolSpec, feature: FeatureSpec, job: JobSpec, turret,
                        start: RoughStart | None = None, power: PowerSpec | None = None) -> None:
    allowance, is_default = finish_allowance(job.iso_group, turret)
    from_diameter = start.diameter if start else job.stock_diameter
    if start:
        template = ROUGH_FROM_BAR_NOTE if start.from_bar else ROUGH_FROM_NEIGHBOUR_NOTE
        op.notes.append(template.format(d=from_diameter))
        if start.pit:
            op.warnings.append(PIT_WARNING)
    op.passes, op.ap = rough_passes(from_diameter, feature.diameter, tool.ap_max, allowance)
    if op.passes == 0:
        op.ap = None
        if start and not start.from_bar:
            # the neighbour's passes already took this section down: nothing is wrong
            op.notes[-1] = NO_STOCK_AFTER_NEIGHBOUR_NOTE.format(d=from_diameter)
        else:
            op.warnings.append("No roughing stock: feature diameter plus finishing allowance reaches the blank.")
        return
    op.notes.append(f"leaves {allowance:g} mm/side for finishing")
    if is_default:
        op.notes.append("no finishing tool in turret: default allowance used")
    if power is not None:  # None: not checked (a planner call without a machine)
        _check_rough_power(op, tool, feature, job, from_diameter, allowance, power)


def _plan_rough_boring(op, tool, feature, job, turret, hole: HolePlan) -> None:
    """Open the drilled hole to the bore, leaving the finishing pass (the boring tool's ap_min)."""
    if hole.diameter is None or feature.diameter is None:
        op.notes.append(FROM_SOLID_NOTE)
        return
    stock = (feature.diameter - hole.diameter) / 2 - tool.ap_min
    op.notes.append(f"bored from the drilled Ø{hole.diameter:g}, leaves {tool.ap_min:g} mm/side for finishing")
    if stock <= 1e-9:
        op.passes, op.ap = 0, None
        return
    op.passes = math.ceil(round(stock / tool.ap_max, 9))
    op.ap = round(stock / op.passes, 3)


def _plan_groove(op: PlannedOperation, tool: ToolSpec, feature: FeatureSpec, job: JobSpec,
                 leave_allowance: bool = False) -> None:
    """Plunges; with leave_allowance (a finishing pass follows) they stop short of the walls and the bottom."""
    op.ap = None
    op.insert_width = tool.insert_width
    if tool.insert_width is None:
        op.warnings.append("Insert width is not set for this grooving tool.")
    if feature.start_diameter is None:
        op.notes.append(f"start diameter not given: blank Ø{job.stock_diameter:g} used")
    allowance = GROOVE_FINISH_ALLOWANCE_MM if leave_allowance and _groove_has_room(
        feature.length, tool.insert_width) else 0.0
    if allowance:
        op.notes.append(GROOVE_ALLOWANCE_NOTE.format(a=allowance))
    if feature.diameter is not None:
        op.depth = round((op.ref_diameter - feature.diameter) / 2 - allowance, 3)
        if op.depth <= 0:
            op.warnings.append("Groove bottom diameter is not smaller than the start diameter.")
    if feature.length is None:
        op.notes.append("groove width not given: one plunge")
    elif tool.insert_width:
        op.passes, step = groove_plunges(feature.length - 2 * allowance, tool.insert_width)
        if op.passes > 1:
            op.notes.append(f"{op.passes} plunges, step {step:g} mm (overlap {round(tool.insert_width - step, 3):g} mm)")


def _plan_groove_finish(op: PlannedOperation, tool: ToolSpec, feature: FeatureSpec, job: JobSpec) -> None:
    """One pass down a wall, along the bottom and up the other wall, taking the allowance the plunges left."""
    op.ap = None
    op.insert_width = tool.insert_width
    if feature.diameter is not None:
        op.depth = round((op.ref_diameter - feature.diameter) / 2, 3)
    if _groove_has_room(feature.length, tool.insert_width):
        op.notes.append(GROOVE_FINISH_NOTE.format(a=GROOVE_FINISH_ALLOWANCE_MM))
    else:
        op.warnings.append(GROOVE_NO_ROOM_WARNING.format(ra=feature.ra, width=feature.length,
                                                         insert=tool.insert_width, a=GROOVE_FINISH_ALLOWANCE_MM))


# --- spindle speed when threading: the Z axis moves at n·P -------------------------------------------
THREAD_FEED_NOTE = "Z feed n·P {feed:g} mm/min"
THREAD_FEED_REDUCED_NOTE = "n reduced to {n} rpm: n·P {feed:g} ≤ {limit:g} mm/min (was {n0} rpm, n·P {feed0:g})"
THREAD_FEED_UNKNOWN_WARNING = ("check n·P = {feed:g} mm/min for your machine: no threading feed limit is set on "
                               "the Machine page")
THREAD_FEED_BELOW_PITCH_WARNING = ("the machine's threading feed limit {limit:g} mm/min is below one revolution "
                                   "per minute at pitch {pitch:g}: check the limit")


def limit_thread_speed(op: PlannedOperation, pitch: float | None, max_thread_feed: float | None) -> None:
    """Feed = pitch when threading, so the Z axis moves at n·P mm/min. Above the machine's limit n is reduced
    to fit it; without a known limit the operation asks for n·P to be checked (no limit is guessed)."""
    if not op.n or not pitch:
        return
    feed = round(op.n * pitch, 1)
    if max_thread_feed is None:
        op.warnings.append(THREAD_FEED_UNKNOWN_WARNING.format(feed=feed))
        return
    if feed <= max_thread_feed + 1e-9:
        op.notes.append(THREAD_FEED_NOTE.format(feed=feed))
        return
    n = math.floor(round(max_thread_feed / pitch, 9))
    if n < 1:
        op.warnings.append(THREAD_FEED_BELOW_PITCH_WARNING.format(limit=max_thread_feed, pitch=pitch))
        return
    op.notes.append(THREAD_FEED_REDUCED_NOTE.format(n=n, feed=round(n * pitch, 1), limit=max_thread_feed,
                                                    n0=op.n, feed0=feed))
    op.n = n


def _plan_thread(op: PlannedOperation, tool: ToolSpec, feature: FeatureSpec,
                 max_thread_feed: float | None = None) -> None:
    op.ap = None
    op.notes.insert(0, G97_THREAD_NOTE)
    if not feature.pitch:
        op.f = None
        op.warnings.append("Thread pitch is missing: feed equals pitch.")
        return
    op.f = feature.pitch
    limit_thread_speed(op, feature.pitch, max_thread_feed)
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


def _plan_centring(op, job, turret, max_rpm) -> PlannedOperation:
    """One centring for all the holes on the axis."""
    op.notes.append("centre the holes on the axis")
    entry, _ = select_tool("centre_drilling", job.iso_group, turret)
    if entry is None:
        op.warnings.append(NO_CENTRE_DRILL)
        return op
    op.tool_id, op.tool_name, op.turret_position = entry.tool.id, entry.tool.name, entry.position
    op.vc, op.f, _ = cutting_data(entry.tool, "finish")
    if entry.tool.diameter:
        op.ref_diameter = entry.tool.diameter
        op.n, _ = spindle_speed(op.vc, entry.tool.diameter, max_rpm)
    else:
        op.vc = op.f = None
        op.warnings.append("the centre drill has no diameter: set it in the tool library to get its speed")
    return op


def _plan_drilling(op, step, hole: HolePlan | None, max_rpm) -> PlannedOperation:
    """Drill a bore or a tap drill hole with the drill plan_holes chose; a manual operation without one."""
    feature = step.feature
    if feature.type == "thread":
        if not (feature.diameter and feature.pitch):
            op.warnings.append("Internal thread without diameter or pitch: cannot plan it.")
            return op
        op.notes.append(f"tap drill Ø{tap_drill_diameter(feature.diameter, feature.pitch):g} "
                        f"for M{feature.diameter:g}×{feature.pitch:g}")
    if hole is None:
        op.warnings.append(NO_DRILL)
        return op
    op.ref_diameter, op.depth = hole.diameter, hole.depth
    if hole.note:
        op.notes.append(hole.note)
    if hole.entry is None:
        op.warnings.append(hole.warning or NO_DRILL)
        return op
    tool = hole.entry.tool
    op.tool_id, op.tool_name, op.turret_position = tool.id, tool.name, hole.entry.position
    op.vc, op.f, _ = cutting_data(tool, "finish")
    op.n, _ = spindle_speed(op.vc, tool.diameter, max_rpm)
    if hole.depth and hole.depth > PECK_DEPTH_FACTOR * tool.diameter + 1e-9:
        op.notes.append(PECK_NOTE.format(depth=hole.depth, k=PECK_DEPTH_FACTOR, d=tool.diameter))
    return op


def _plan_internal_thread_step(op, step, job, turret, max_rpm, max_thread_feed=None) -> PlannedOperation:
    """A tap or an internal threading bar (the tap drill is a drilling step); a manual operation without one."""
    feature = step.feature
    if not (feature.diameter and feature.pitch):
        op.warnings.append("Internal thread without diameter or pitch: cannot plan it.")
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
    limit_thread_speed(op, feature.pitch, max_thread_feed)  # a tap (G84) and an internal threading bar alike
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
    flats = hex_flats(feature)
    size = f"S{flats:g}" if flats else "S not given"
    length = f", L{feature.length:g}" if feature.length else ""
    op.notes.append(f"mill hex {size} across flats{length}")
    from_bar = job.blank_shape == "hex" and flats and math.isclose(flats, job.blank_diameter, abs_tol=HEX_BAR_MATCH_MM)
    if from_bar:
        op.notes.append(
            f"flats of the hex bar ({HEX_BAR_TOLERANCE}) milled to {feature.tolerance}: skim, depth set by the bar"
        )
    else:
        op.depth = round((hex_corners(feature) - flats) / 2, 3) if flats and hex_corners(feature) else None
    entry, _ = select_tool("milling", job.iso_group, turret)
    if entry is None:
        op.warnings.append(NO_MILLING_TOOL)
        return op
    op.tool_id, op.tool_name, op.turret_position = entry.tool.id, entry.tool.name, entry.position
    op.notes.append("driven tool, C-axis: 6 flats at 60°")
    op.notes.append(MILLING_DATA_NOTE)
    return op


def _plan_step(step: Step, job: JobSpec, turret: list[TurretEntry], max_rpm: int,
               start: RoughStart | None = None, max_thread_feed: float | None = None,
               power: PowerSpec | None = None, hole: HolePlan | None = None) -> PlannedOperation:
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

    if step.tool_type == "centre_drilling":
        return _plan_centring(op, job, turret, max_rpm)
    if step.tool_type == "drilling":
        return _plan_drilling(op, step, hole, max_rpm)
    if step.tool_type == "internal_threading":
        return _plan_internal_thread_step(op, step, job, turret, max_rpm, max_thread_feed)

    if step.tool_type == "milling":
        return _plan_hex_milling(op, feature, job, turret)

    if step.tool_type == "hex_bar":
        op.notes.append(f"hex S{hex_flats(feature):g}: {HEX_FROM_BAR_NOTE}")
        return op

    if step.tool_type == "grooving":
        entry, warning = select_grooving_tool(feature.length, job.iso_group, turret)
    else:
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

    op.ref_diameter = _reference_diameter(step, job, start)
    op.n, limited = spindle_speed(op.vc, op.ref_diameter, max_rpm)
    if step.stage in ("face", "parting"):
        op.notes.append(G96_NOTE)
    elif limited:
        op.notes.append(f"n limited to machine max {max_rpm} rpm")

    if step.tool_type == "turning_rough" and feature.diameter is not None:
        _plan_rough_turning(op, tool, feature, job, turret, start, power)
    elif step.tool_type == "grooving" and step.mode == "finish" and groove_needs_finish(feature):
        _plan_groove_finish(op, tool, feature, job)
    elif step.tool_type == "grooving":
        _plan_groove(op, tool, feature, job, leave_allowance=step.mode == "rough")
    elif step.tool_type == "threading":
        _plan_thread(op, tool, feature, max_thread_feed)
    elif step.tool_type == "parting":
        _plan_parting(op, tool, feature, job)
    elif step.tool_type == "boring" and step.mode == "rough" and hole is not None:
        _plan_rough_boring(op, tool, feature, job, turret, hole)
    elif feature.type == "chamfer":
        op.notes.append("no finishing pass on this diameter: chamfer machined separately")

    return op


def plan_job(job: JobSpec, turret: list[TurretEntry], max_rpm: int,
             max_thread_feed: float | None = None, power: PowerSpec | None = None) -> list[PlannedOperation]:
    """Build the ordered list of proposed operations for a job. max_thread_feed: the machine's Z feed limit
    when threading (n·P, mm/min), None when not known. power: the machine's spindle power for the roughing
    check; None: not checked."""
    features = list(job.features)
    hex_bar = job.blank_diameter if job.blank_shape == "hex" else None
    holes = plan_holes(features, job.iso_group, turret)
    chamfer_hosts = match_chamfers(features)
    # a hex left as it comes from a hex bar, or a bore made by its drill, has no finish pass: its chamfer gets
    # its own
    chamfer_hosts = {
        chamfer: host for chamfer, host in chamfer_hosts.items()
        if not (host.type == "hex" and feature_to_steps(host, hex_bar)[0].tool_type in ("hex_bar", "milling"))
        and not (id(host) in holes and holes[id(host)].drill_only)
    }
    hosts_with_chamfer = {id(host) for host in chamfer_hosts.values()}

    thread_pitches = match_thread_diameters(features)

    def turned_diameter(f):
        """The diameter a section is cut to: a thread's reduced major Ø, a hex's corners."""
        if f.type == "hex":
            return hex_corners(f)
        pitch = thread_pitches.get(id(f))
        return thread_major_diameter(f.diameter, pitch) if pitch and f.diameter else f.diameter

    starts, both_sides = rough_starts(features, turned_diameter, job.stock_diameter) if job.axial_order else ({}, False)

    steps = [s for f in features if id(f) not in chamfer_hosts for s in feature_to_steps(f, hex_bar, holes.get(id(f)))]
    drilling = [s for s in steps if s.tool_type == "drilling"]
    if drilling:  # one centring for the holes on the axis, before the first drill; on the first hole's row
        first_hole = next(f for f in features if id(f) in holes)
        steps.insert(0, Step(first_hole, "centre_drilling", "finish", "drill"))

    def drill_rank(step):  # in the drilling stage: the centring, then the drills from the smallest
        if step.tool_type != "drilling":
            return 0.0
        plan = holes.get(id(step.feature))
        return plan.diameter if plan and plan.diameter else 0.0

    steps.sort(key=drill_rank)  # stable; order_steps then sorts by stage
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
        start = starts.get(id(original)) if step.tool_type == "turning_rough" else None
        op = _plan_step(step, job, turret, max_rpm, start, max_thread_feed, power, holes.get(id(original)))
        if start and start.from_bar and not start.pit and both_sides:
            op.notes.append(TWO_SIDES_NOTE)
        if pitch and step.mode == "finish":
            op.notes.append(f"{THREAD_MAJOR_NOTE}: Ø{major:g} (nominal Ø{original.diameter:g})")
        if step.mode == "finish" and id(original) in hosts_with_chamfer and step.tool_type != "milling":
            op.notes.append(CHAMFER_NOTE)
        if original.type == "hex" and step.tool_type == "turning_finish":
            op.notes.append(f"{HEX_CORNERS_NOTE} S{hex_flats(original):g}" if hex_flats(original)
                            else HEX_CORNERS_NOTE)
        operations.append(op)
    for i, op in enumerate(operations, start=1):
        op.sequence = i
    return operations
