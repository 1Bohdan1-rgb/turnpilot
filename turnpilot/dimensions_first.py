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


AMBIGUOUS_BINDING_PREFIX = "check: dimension"


def _quality(boundary_count, dimensions):
    """(conflicts, sections with zero or negative length) of a dimension system."""
    solution = solve_boundaries(boundary_count, dimensions)
    conflicts = sum("conflicts with the others" in w for w in solution.warnings)
    x = solution.positions
    bad = sum(1 for a, b in zip(x, x[1:]) if a is not None and b is not None and b - a <= 0)
    return conflicts, bad


def ambiguous_face_bindings(data: DimensionsData) -> list[tuple[str, int]]:
    """Face dimensions whose binding next to a groove cannot be told apart by the other dimensions.

    Candidate: a length dimension from an end face of the part (boundary 0 or N, a datum) that spans
    exactly one section, not a groove, whose inner boundary borders a groove. Alternative reading:
    the same dimension with its inner boundary moved across the groove, so that it covers the groove
    too. If the alternative gives no more conflicts than the recorded reading and no section of zero
    or negative length, both readings fit: the machinist has to check the extension lines.

    Only a warning: lengths are never changed. Returns (message, 1-based section number) pairs.
    """
    count = len(data.sections) + 1  # boundaries 0..N
    last = count - 1
    grooves = {k for k, s in enumerate(data.sections, start=1) if s.type == "groove"}  # section k: k-1..k
    base = _quality(count, data.dimensions)
    findings = []
    for index, d in enumerate(data.dimensions):
        if d.kind not in LINEAR_KINDS or d.from_ is None or d.to is None:
            continue
        a, b = sorted((d.from_, d.to))
        if b - a != 1 or b in grooves or b > last:  # exactly one section, not the groove itself
            continue
        if a == 0 and b + 1 in grooves:  # from the left face; a groove right of the inner boundary
            alternative = (0, b + 1)
        elif b == last and a in grooves:  # from the right face; a groove left of the inner boundary
            alternative = (a - 1, last)
        else:
            continue
        dims = [x.model_copy() for x in data.dimensions]
        dims[index].from_, dims[index].to = alternative
        conflicts, bad = _quality(count, dims)
        if conflicts <= base[0] and bad == 0:
            findings.append((
                f"{AMBIGUOUS_BINDING_PREFIX} {d.value:g} (boundaries {a}–{b}) may also span the groove "
                f"({alternative[0]}–{alternative[1]}); both readings fit the other dimensions, so check its "
                f"extension lines on the drawing",
                b,  # the section between boundaries a and b
            ))
    return findings


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

    # Pairs of boundaries that one linear dimension connects directly.
    direct = {
        tuple(sorted((d.from_, d.to))) for d in data.dimensions
        if d.kind in LINEAR_KINDS and d.from_ is not None and d.to is not None
    }

    ambiguous = ambiguous_face_bindings(data)
    warnings += [message for message, _ in ambiguous]
    ambiguous_sections = {number for _, number in ambiguous}

    features = []
    section_lengths = []
    section_derived = []
    for number, s in enumerate(sections, start=1):
        length = _span(x, number - 1, number)
        if length is not None and length <= 0:
            warnings.append(f"section {number} ({s.type}) gets length {length:g} from the dimensions")
            length = None
        derived = length is not None and (number - 1, number) not in direct
        section_lengths.append(length)
        section_derived.append(derived)
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
            length_derived=derived and s.type != "fillet",
            length_ambiguous=number in ambiguous_sections and length is not None,
        ))

    for o in data.overlays:
        section = sections[o.section - 1] if o.section and o.section <= len(sections) else None
        diameter = o.diameter or (section.diameter if section else None)
        derived = False
        ambiguous_length = False
        if o.type == "chamfer":
            length = o.size
        else:
            length = _span(x, o.start, o.end)
            if length is not None and o.start is not None and o.end is not None:
                derived = tuple(sorted((o.start, o.end))) not in direct
            if length is None and o.type == "thread" and o.section and o.section <= len(sections):
                length = section_lengths[o.section - 1]  # thread over the whole section
                derived = section_derived[o.section - 1]
                ambiguous_length = o.section in ambiguous_sections and length is not None
        features.append(ExtractedFeature(
            type=o.type,
            diameter=diameter,
            length=length,
            tolerance=o.tolerance,
            ra=o.ra,
            pitch=o.pitch if o.type == "thread" else None,
            confidence=o.confidence,
            length_derived=derived,
            length_ambiguous=ambiguous_length,
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
#
# Strict tool schemas allow only a limited number of nullable (union-typed) parameters, and this
# schema has many optional values. So nothing here is nullable; "not on the drawing" is encoded as
#   0   for numbers that cannot physically be 0 (diameter, length, Ra, pitch, radius, chamfer size,
#       quantity) and for 1-based section numbers,
#   ""  for text (a tolerance stays one string as written, so "0/-0.021" is never lost),
#   -1  for boundary numbers (0 is a real boundary, the left end face).
# from_wire() turns these back into None before validation.

ZERO_MEANS_NONE = {
    "blank_diameter", "blank_length", "quantity", "general_ra",
    "diameter", "start_diameter", "ra", "radius", "pitch", "size", "section",
}
MINUS_ONE_MEANS_NONE = {"from", "to", "start", "end"}
EMPTY_MEANS_NONE = {"material", "tolerance"}


def _decode(key, value):
    if key in ZERO_MEANS_NONE and value == 0:
        return None
    if key in MINUS_ONE_MEANS_NONE and value == -1:
        return None
    if key in EMPTY_MEANS_NONE and isinstance(value, str) and not value.strip():
        return None
    return value


def from_wire(tool_input: dict) -> dict:
    """Replace the "not on the drawing" markers (0 / "" / -1) of the tool input with None."""
    data = {k: _decode(k, v) for k, v in tool_input.items() if k not in ("sections", "overlays", "dimensions")}
    for key in ("sections", "overlays", "dimensions"):
        data[key] = [{k: _decode(k, v) for k, v in item.items()} for item in tool_input.get(key, [])]
    return data


def parse_tool_input(tool_input: dict) -> DrawingData:
    """record_dimensions tool input -> the usual DrawingData, lengths computed by the code."""
    return to_drawing_data(DimensionsData.model_validate(from_wire(tool_input)))


def _number(description: str) -> dict:
    return {"type": "number", "description": description}


def _integer(description: str) -> dict:
    return {"type": "integer", "description": description}


def _text(description: str) -> dict:
    return {"type": "string", "description": description}


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
        "Record the profile of the turned part and every dimension as written on the drawing. Do not "
        "compute lengths. A value that is not on the drawing is 0 for numbers, \"\" for text and -1 for "
        "boundary numbers."
    ),
    "strict": True,
    "input_schema": _object({
        "part_type": {
            "type": "string",
            "enum": list(PART_TYPES),
            "description": "turned: a body of revolution made on a lathe. not_turned: clearly not a lathe part. "
                           "unclear: cannot tell.",
        },
        "material": _text('Material exactly as written in the title block; "" if none.'),
        "blank_diameter": _number("mm. Only if the drawing states the blank/stock size; else 0."),
        "blank_length": _number("mm. Only if the drawing states the blank/stock size; else 0."),
        "quantity": _integer("Quantity from the title block; 0 if none."),
        "general_ra": _number("µm. Ra of the roughness symbol without a leader in the top-right corner; else 0."),
        "sections": {
            "type": "array",
            "description": "External profile from the left end face to the right end face. Section k lies "
                           "between boundary k-1 and boundary k; boundary 0 is the left end face.",
            "items": _object({
                "type": {"type": "string", "enum": list(SECTION_TYPES)},
                "diameter": _number("mm. Cylinder / groove bottom / taper end diameter; 0 for a fillet."),
                "start_diameter": _number("mm. Taper: diameter at its start. Groove: the diameter it is cut "
                                          "from. Else 0."),
                "tolerance": _text('Tolerance of this diameter (or radius) exactly as written, e.g. "±0.05", '
                                   '"0/-0.021", "h7"; "" if none.'),
                "ra": _number("µm. Ra marked on this section itself; else 0."),
                "radius": _number("mm. Fillet radius (10 for R10); else 0."),
                "confidence": _number("0..1"),
            }),
        },
        "overlays": {
            "type": "array",
            "description": "Features on top of or inside a section: threads, chamfers, bores.",
            "items": _object({
                "type": {"type": "string", "enum": list(OVERLAY_TYPES)},
                "section": _integer("1-based number of the section it lies on; 0 for a bore."),
                "diameter": _number("mm. Thread major diameter or bore diameter; 0 for a chamfer."),
                "tolerance": _text('Thread class ("6g") or bore tolerance exactly as written; "" if none.'),
                "ra": _number("µm. Ra marked on it; else 0."),
                "pitch": _number("mm. Threads only; else 0."),
                "size": _number("mm. Chamfer leg (1.5 for 1.5x45°); else 0."),
                "start": _integer("Boundary where it starts if its length is dimensioned (a THRU bore: 0); else -1."),
                "end": _integer("Boundary where it ends if its length is dimensioned (a THRU bore: the last "
                                "boundary); else -1."),
                "confidence": _number("0..1"),
            }),
        },
        "dimensions": {
            "type": "array",
            "description": "Every dimension on the drawing, exactly as written.",
            "items": _object({
                "value": _number("mm, as written (140.1 for 140,1)."),
                "tolerance": _text('As written, e.g. "js12", "±0.105", "0/-0.021"; "" if none.'),
                "kind": {
                    "type": "string",
                    "enum": list(DIMENSION_KINDS),
                    "description": "diameter: a Ø dimension. overall: the full part length. baseline: measured "
                                   "from a common datum face. chain: between two neighbouring boundaries.",
                },
                "from": _integer("Linear dimensions: boundary where one extension line starts; else -1."),
                "to": _integer("Linear dimensions: boundary where the other extension line starts; else -1."),
                "section": _integer("Diameter dimensions: 1-based section number; else 0."),
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
- Record only what is written on the drawing. Never estimate, compute or guess a value. A value that \
is not on the drawing is recorded as 0 for numbers, "" for text and -1 for boundary numbers.
- Sections: list the external profile from the left end face to the right end face. Every change of \
diameter or shape starts a new section: a cylinder is od_turn, a cone is taper, a recess is groove \
(a narrow step next to a thread, below its minor diameter, is a thread relief groove), a radius \
between two sections is fillet. Boundaries are numbered 0 (left end face) to N (right end face); \
section k lies between boundaries k-1 and k.
- Dimensions: record every dimension exactly as written, with its tolerance as one string. For a \
length dimension, give the two boundaries its extension lines start from (from, to) and its kind: \
overall (the whole part), baseline (measured from a common datum face) or chain (between neighbouring \
boundaries). Do not add up or subtract dimensions yourself. For a diameter dimension use kind \
diameter and the section number.
- A baseline dimension runs from its datum (the base face) to a boundary and may span several \
sections, including grooves. Find both boundaries of every length dimension by following its extension \
lines to the part, not by the section the number happens to be written above.
- A diameter dimensioned at the end of a taper is the diameter of that end of the taper (the taper's \
diameter or start_diameter), not a separate cylinder. Add a cylinder only where the drawing shows one.
- Overlays: threads (major diameter, pitch, thread class as tolerance), chamfers (size) and bores lie \
on a section; give start/end boundaries only if their length is dimensioned.
- A bore marked THRU (through) runs the full part: record it with start 0 and end the last boundary. \
That is reading the drawing, not a guess. A bore with neither THRU nor a length dimension gets start \
and end -1.
- material and quantity from the title block; blank size only if the drawing states it. A roughness \
symbol without a leader in the top-right corner is general_ra; a section's ra is only its own mark.
- Tolerances and Ra belong to the section or overlay they are written on; do not copy a value to \
another one. A roughness (Ra) symbol on a leader line belongs to the surface the leader's arrow \
touches (for example the bore wall), not to the nearest dimension or the side of the part where the \
symbol is placed.
- Keep warnings short: one sentence each.
"""

USER_PROMPT = "Read this drawing and record its profile and dimensions with the record_dimensions tool."
