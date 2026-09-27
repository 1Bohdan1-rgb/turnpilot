"""Experimental "dimensions first" reading mode.

In the default mode the model reads features with their lengths, which means it has to add up chain
and baseline dimensions itself. The eval on a real drawing showed that it reads the numbers well but
assembles the geometry poorly. In this mode the model only transcribes:

- the external profile as sections from left to right, without lengths; boundaries between sections
  are numbered 0 (left end face) .. N (right end face), section k lies between boundaries k-1 and k;
- overlays (threads, chamfers, bores) that refer to a section;
- every dimension as written: value, tolerance, kind and the two boundaries it connects.

The code then computes the boundary positions from the dimension graph and converts the result to
the normal DrawingData, so the review screen, the planner and the eval work unchanged.
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, PositiveFloat, PositiveInt

from .extraction_schema import PART_TYPES, DrawingData, ExtractedFeature, PartType

TOOL_NAME = "record_dimensions"
SECTION_TYPES = ("od_turn", "taper", "groove", "fillet")
OVERLAY_TYPES = ("thread", "chamfer", "bore")
DIMENSION_KINDS = ("diameter", "baseline", "chain", "overall")
LINEAR_KINDS = ("baseline", "chain", "overall")
CONFLICT_TOLERANCE_MM = 0.2


class Section(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["od_turn", "taper", "groove", "fillet"]
    diameter: PositiveFloat | None = None
    start_diameter: PositiveFloat | None = None
    tolerance: str | None = None
    ra: PositiveFloat | None = None
    radius: PositiveFloat | None = None
    confidence: float = Field(ge=0, le=1)


class Overlay(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["thread", "chamfer", "bore"]
    section: PositiveInt | None = None  # 1-based section number the overlay lies on
    diameter: PositiveFloat | None = None
    tolerance: str | None = None
    ra: PositiveFloat | None = None
    pitch: PositiveFloat | None = None
    size: PositiveFloat | None = None  # chamfer leg (1.5 for 1.5x45°)
    start: int | None = Field(default=None, ge=0)  # boundaries of its extent, when dimensioned
    end: int | None = Field(default=None, ge=0)
    confidence: float = Field(ge=0, le=1)


class Dimension(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    value: PositiveFloat
    tolerance: str | None = None
    kind: Literal["diameter", "baseline", "chain", "overall"]
    from_: int | None = Field(default=None, alias="from", ge=0)
    to: int | None = Field(default=None, ge=0)
    section: PositiveInt | None = None  # diameter dimensions: the section they belong to


class DimensionsData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    part_type: PartType
    material: str | None = None
    blank_diameter: PositiveFloat | None = None
    blank_length: PositiveFloat | None = None
    quantity: PositiveInt | None = None
    general_ra: PositiveFloat | None = None
    sections: list[Section]
    overlays: list[Overlay] = Field(default_factory=list)
    dimensions: list[Dimension] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


# --- solving the dimension graph ------------------------------------------------------------

@dataclass
class Solution:
    positions: list[float | None]  # x of each boundary, None if the dimensions do not fix it
    warnings: list[str] = field(default_factory=list)


def solve_boundaries(boundary_count: int, dimensions: list[Dimension]) -> Solution:
    """Boundary positions from linear dimensions, with boundary 0 at x = 0.

    Every linear dimension says x[hi] - x[lo] = value (dimensions are distances, so from/to may be
    given in either order). Positions are propagated through the graph; a dimension that disagrees
    with positions already fixed by others by more than 0.2 mm is reported as a conflict.
    """
    warnings = []
    edges = defaultdict(list)
    for d in dimensions:
        if d.kind not in LINEAR_KINDS:
            continue
        if d.from_ is None or d.to is None:
            warnings.append(f"dimension {d.value:g} ({d.kind}) has no boundaries and was not used")
            continue
        lo, hi = sorted((d.from_, d.to))
        if hi >= boundary_count or lo == hi:
            warnings.append(f"dimension {d.value:g} ({d.kind}) connects invalid boundaries {d.from_}–{d.to}")
            continue
        edges[lo].append((hi, d.value, d))
        edges[hi].append((lo, -d.value, d))

    positions: list[float | None] = [None] * boundary_count
    positions[0] = 0.0
    queue = deque([0])
    reported = set()
    while queue:
        node = queue.popleft()
        for other, delta, d in edges[node]:
            expected = round(positions[node] + delta, 6)
            if positions[other] is None:
                positions[other] = expected
                queue.append(other)
            elif abs(positions[other] - expected) > CONFLICT_TOLERANCE_MM and id(d) not in reported:
                reported.add(id(d))
                warnings.append(
                    f"dimension {d.value:g} ({d.kind}, boundaries {d.from_}–{d.to}) conflicts with the others "
                    f"by {abs(positions[other] - expected):g} mm"
                )
    return Solution(positions, warnings)


def _span(positions, start, end):
    if start is None or end is None or max(start, end) >= len(positions):
        return None
    a, b = positions[min(start, end)], positions[max(start, end)]
    if a is None or b is None:
        return None
    return round(b - a, 3)


def to_drawing_data(data: DimensionsData) -> DrawingData:
    """Turn sections + dimensions into the normal feature list, with lengths computed by the code."""
    sections = data.sections
    solution = solve_boundaries(len(sections) + 1, data.dimensions)
    x = solution.positions
    warnings = list(data.warnings) + solution.warnings

    # Diameter dimensions fill a section's diameter or tolerance the model left empty.
    sections = [s.model_copy() for s in sections]
    for d in (d for d in data.dimensions if d.kind == "diameter" and d.section):
        if d.section > len(sections):
            warnings.append(f"diameter dimension Ø{d.value:g} refers to missing section {d.section}")
            continue
        s = sections[d.section - 1]
        if s.diameter is None:
            s.diameter = d.value
        if s.tolerance is None and d.tolerance and s.diameter == d.value:
            s.tolerance = d.tolerance

    features = []
    section_lengths = []
    for number, s in enumerate(sections, start=1):
        length = _span(x, number - 1, number)
        if length is not None and length <= 0:
            warnings.append(f"section {number} ({s.type}) gets length {length:g} from the dimensions")
            length = None
        section_lengths.append(length)
        features.append(ExtractedFeature(
            type=s.type,
            diameter=None if s.type == "fillet" else s.diameter,
            start_diameter=s.start_diameter if s.type in ("groove", "taper") else None,
            # A fillet's axial extent is given by its radius, as in the features mode.
            length=None if s.type == "fillet" else length,
            tolerance=s.tolerance,
            ra=s.ra,
            radius=s.radius if s.type == "fillet" else None,
            confidence=s.confidence,
        ))

    for o in data.overlays:
        section = sections[o.section - 1] if o.section and o.section <= len(sections) else None
        diameter = o.diameter or (section.diameter if section else None)
        if o.type == "chamfer":
            length = o.size
        else:
            length = _span(x, o.start, o.end)
            if length is None and o.type == "thread" and o.section and o.section <= len(sections):
                length = section_lengths[o.section - 1]  # thread over the whole section
        features.append(ExtractedFeature(
            type=o.type,
            diameter=diameter,
            length=length,
            tolerance=o.tolerance,
            ra=o.ra,
            pitch=o.pitch if o.type == "thread" else None,
            confidence=o.confidence,
        ))

    overall = x[-1]
    if overall is None:
        overall = next((d.value for d in data.dimensions if d.kind == "overall"), None)
    return DrawingData(
        part_type=data.part_type,
        material=data.material,
        blank_diameter=data.blank_diameter,
        blank_length=data.blank_length,
        overall_length=overall,
        quantity=data.quantity,
        general_ra=data.general_ra,
        features=features,
        warnings=warnings,
    )


# --- tool schema and prompt -------------------------------------------------------------------

def _nullable(json_type: str, description: str) -> dict:
    return {"anyOf": [{"type": json_type}, {"type": "null"}], "description": description}


def _object(properties: dict, description: str | None = None) -> dict:
    schema = {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }
    if description:
        schema["description"] = description
    return schema


RECORD_DIMENSIONS_TOOL = {
    "name": TOOL_NAME,
    "description": (
        "Record the profile of the turned part and every dimension as written on the drawing. "
        "Do not compute lengths; use null for anything that is not on the drawing."
    ),
    "strict": True,
    "input_schema": _object({
        "part_type": {
            "type": "string",
            "enum": list(PART_TYPES),
            "description": "turned: a body of revolution made on a lathe. not_turned: clearly not a lathe part. "
                           "unclear: cannot tell.",
        },
        "material": _nullable("string", "Material exactly as written in the title block."),
        "blank_diameter": _nullable("number", "mm. Only if the drawing states the blank/stock size."),
        "blank_length": _nullable("number", "mm. Only if the drawing states the blank/stock size."),
        "quantity": _nullable("integer", "Quantity from the title block."),
        "general_ra": _nullable(
            "number", "µm. Ra of the roughness symbol without a leader in the top-right corner, else null."
        ),
        "sections": {
            "type": "array",
            "description": "External profile from the left end face to the right end face. Section k lies "
                           "between boundary k-1 and boundary k; boundary 0 is the left end face.",
            "items": _object({
                "type": {"type": "string", "enum": list(SECTION_TYPES)},
                "diameter": _nullable("number", "mm. Cylinder/groove bottom/taper end diameter; null for a fillet."),
                "start_diameter": _nullable("number", "mm. Taper: diameter at its start. Groove: the diameter "
                                                      "it is cut from. Else null."),
                "tolerance": _nullable("string", "Tolerance of this diameter (or radius) exactly as written."),
                "ra": _nullable("number", "Ra marked on this section itself, else null."),
                "radius": _nullable("number", "mm. Fillet radius (10 for R10), else null."),
                "confidence": {"type": "number", "description": "0..1"},
            }),
        },
        "overlays": {
            "type": "array",
            "description": "Features on top of or inside a section: threads, chamfers, bores.",
            "items": _object({
                "type": {"type": "string", "enum": list(OVERLAY_TYPES)},
                "section": _nullable("integer", "1-based number of the section it lies on (bore: null)."),
                "diameter": _nullable("number", "mm. Thread major diameter, bore diameter; chamfer: null."),
                "tolerance": _nullable("string", "Thread class (6g) or bore tolerance exactly as written."),
                "ra": _nullable("number", "Ra marked on it, else null."),
                "pitch": _nullable("number", "mm. Threads only."),
                "size": _nullable("number", "mm. Chamfer leg (1.5 for 1.5x45°), else null."),
                "start": _nullable("integer", "Boundary where it starts, if its length is dimensioned; "
                                              "for a THRU bore 0."),
                "end": _nullable("integer", "Boundary where it ends, if its length is dimensioned; "
                                            "for a THRU bore the last boundary."),
                "confidence": {"type": "number", "description": "0..1"},
            }),
        },
        "dimensions": {
            "type": "array",
            "description": "Every dimension on the drawing, exactly as written.",
            "items": _object({
                "value": {"type": "number", "description": "mm, as written (140.1 for 140,1)."},
                "tolerance": _nullable("string", "As written, e.g. 'js12', '±0.105'."),
                "kind": {
                    "type": "string",
                    "enum": list(DIMENSION_KINDS),
                    "description": "diameter: a Ø dimension. overall: the full part length. baseline: measured "
                                   "from a common datum face. chain: between two neighbouring boundaries.",
                },
                "from": _nullable("integer", "Boundary where one extension line starts (linear dimensions)."),
                "to": _nullable("integer", "Boundary where the other extension line starts (linear dimensions)."),
                "section": _nullable("integer", "Diameter dimensions: 1-based section number, else null."),
            }),
        },
        "warnings": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Short, one sentence each: anything unclear or contradictory.",
        },
    }),
}

SYSTEM_PROMPT = """\
You read engineering drawings of parts made on a CNC lathe and record them with the record_dimensions \
tool. Your job is to transcribe, not to calculate: the software computes all lengths from the \
dimensions you record.

Rules:
- Always answer by calling record_dimensions exactly once. All dimensions are in millimetres.
- Record only what is written on the drawing; use null for anything not visible. Never estimate, \
compute or guess a value.
- Sections: list the external profile from the left end face to the right end face. Every change of \
diameter or shape starts a new section: a cylinder is od_turn, a cone is taper, a recess is groove \
(a narrow step next to a thread, below its minor diameter, is a thread relief groove), a radius \
between two sections is fillet. Boundaries are numbered 0 (left end face) to N (right end face); \
section k lies between boundaries k-1 and k.
- Dimensions: record every dimension exactly as written, with its tolerance. For a length dimension, \
give the two boundaries its extension lines start from (from, to) and its kind: overall (the whole \
part), baseline (measured from a common datum face) or chain (between neighbouring boundaries). Do \
not add up or subtract dimensions yourself. For a diameter dimension use kind diameter and the \
section number.
- Overlays: threads (major diameter, pitch, thread class as tolerance), chamfers (size) and bores lie \
on a section; give start/end boundaries only if their length is dimensioned.
- material and quantity from the title block; blank size only if the drawing states it. A roughness \
symbol without a leader in the top-right corner is general_ra; a section's ra is only its own mark.
- Keep warnings short: one sentence each.
"""

USER_PROMPT = "Read this drawing and record its profile and dimensions with the record_dimensions tool."
