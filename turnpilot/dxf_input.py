"""A part read from a DXF (dxf_reader) as the DrawingData the review screen and the planner work with.

Each section of the outer profile becomes a feature row; its chamfers and transition radii become chamfer
and fillet rows next to it. The numbers come from the dimension texts where a dimension is bound to the
row (that is what the drawing says), otherwise from the geometry, and then the row carries a note for the
machinist ("check"). Lengths are never corrected: a section's length is the distance between its
boundaries in the drawing, and a note says when no single dimension gives it.

Besides the DrawingData, every part has a binding report (JSON) for the review screen: per row the section
and the dimensions behind it with their notes, and the list of all dimensions with what they are bound to.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .dxf_reader import BIND_TOL, DxfReading, Part, Section
from .extraction_schema import DrawingData, ExtractedFeature, normalize_tolerance

NUMBER = r"\d+(?:[.,]\d+)?"
THREAD_TEXT = re.compile(rf"^M\s*({NUMBER})(?:\s*×\s*({NUMBER}))?(?:\s*-\s*(\S+))?")
SIZE_TEXT = re.compile(rf"^[ØR]?\s*({NUMBER})\s*(.*)$")
CHAMFER_TEXT = re.compile(rf"^({NUMBER})\s*×\s*45")


def _num(text):
    return float(text.replace(",", "."))


def _mm(value):
    """A geometry value in mm, rounded: drawings are made to a thousandth at best (Ø13.0001 is Ø13)."""
    return round(value, 3)


def parse_size(text):
    """(number, tolerance) of a diameter or radius text: "Ø22+0,21" -> (22, "+0.21"), "Ø40h12" -> (40, "h12")."""
    match = SIZE_TEXT.match(text.strip())
    if not match:
        return None, None
    return _num(match.group(1)), normalize_tolerance(match.group(2).strip(" ()"))


def parse_thread(text):
    """(diameter, pitch, class) of "M22×1,5-6g"; pitch and class may be None."""
    match = THREAD_TEXT.match(text.strip())
    if not match:
        return None, None, None
    return _num(match.group(1)), _num(match.group(2)) if match.group(2) else None, match.group(3)


def parse_chamfer(text):
    match = CHAMFER_TEXT.match(text.strip())
    return _num(match.group(1)) if match else None


@dataclass
class PartResult:
    data: DrawingData
    report: dict  # JSON-able: {"label", "rows": [...], "dims": [...], "overall_length_source"}


@dataclass
class _Row:
    feature: dict
    section: int | None
    dims: list = field(default_factory=list)  # texts of the dimensions behind the row
    notes: list = field(default_factory=list)  # for the machinist: values not given directly by a dimension


def _same(a, b):
    return abs(a - b) <= BIND_TOL


def _section_of(part, target):
    if target and target[0] == "section":
        return next((s for s in part.sections if _same(s.x0, target[1]) and _same(s.x1, target[2])), None)
    return None


def _length_note(part, section, length_dims):
    """None when one dimension spans the section, else a note on where its length comes from."""
    spans = [(d.target[1], d.target[2]) for d in length_dims]
    if any(_same(a, section.x0) and _same(b, section.x1) for a, b in spans):
        return None
    ends = [x for span in spans for x in span]
    loose = [x for x in (section.x0, section.x1) if not any(_same(x, e) for e in ends)]
    if loose:
        where = " and ".join(f"x {x - part.x_min:g}" for x in loose)
        return f"length from the geometry: no dimension ends at its boundary ({where} from the left face)"
    return "length computed from other dimensions (no dimension spans this section alone)"


def _end_diameters(section, dims, row, elsewhere):
    """Diameters at the start and the end of a taper / arc: a dimension at an end gives its text. An end that
    meets a dimensioned cylinder of the same diameter (a taper from Ø30 next to the Ø30 section) needs none."""
    start, end = _mm(2 * section.r_at(section.x0)), _mm(2 * section.r_at(section.x1))
    for dim in dims:
        number, _tolerance = parse_size(dim.text)
        x = dim.points[0][0]
        if number is None:
            continue
        if _same(x, section.x0):
            start = number
        elif _same(x, section.x1):
            end = number
        else:
            row.notes.append(f"{dim.text} at x {x - section.x0:g} from the section start (not at an end): check")
        row.dims.append(dim.text)
        row.notes.extend(dim.flags)
    for label, value, x in (("start", start, section.x0), ("end", end, section.x1)):
        if value > 0 and value not in elsewhere and                 not any(parse_size(d.text)[0] is not None and _same(d.points[0][0], x) for d in dims):
            row.notes.append(f"Ø at the {label} ({value:g}) from the geometry: not dimensioned")
    # an arc that ends on the axis (a spherical end) has no diameter there
    return start or None, end or None


def _section_rows(part, section: Section, by_section, chamfer_dims, fillet_dims, length_dims, elsewhere):
    dims = by_section.get(section.index, [])
    diameter_dims = [d for d in dims if d.kind == "diameter"]
    row = _Row({"type": section.kind}, section.index)
    feature = row.feature
    if section.kind in ("od_turn", "groove", "thread"):
        geometry = _mm(2 * section.r_at(section.x0))
        feature["diameter"] = geometry
        if diameter_dims:
            first = diameter_dims[0]
            if section.kind == "thread":
                number, pitch, thread_class = parse_thread(first.text)
                feature.update(pitch=pitch, tolerance=thread_class, location="external")
            else:
                number, tolerance = parse_size(first.text)
                feature["tolerance"] = tolerance
            if number is not None:
                feature["diameter"] = number
            for dim in diameter_dims:
                row.dims.append(dim.text)
                row.notes.extend(dim.flags)
            others = {parse_size(d.text)[0] for d in diameter_dims[1:]} - {number}
            if others:
                row.notes.append(f"several diameters bound here: {', '.join(d.text for d in diameter_dims)}: check")
        elif geometry in elsewhere and section.kind != "thread":
            # a drawing dimensions a repeated diameter once: take its text, and say where it stands
            index, dim = elsewhere[geometry]
            number, tolerance = parse_size(dim.text)
            feature.update(diameter=number, tolerance=tolerance)
            row.notes.append(f"{dim.text} is dimensioned on section {index} (same diameter): check")
        else:
            row.notes.append(f"Ø{geometry:g} from the geometry: no diameter dimension")
    else:  # taper, arc
        start, end = _end_diameters(section, diameter_dims, row, elsewhere)
        feature.update(start_diameter=start, diameter=end)
        if section.kind == "arc":
            radius_dims = [d for d in dims if d.kind == "radius"]
            feature["radius"] = _mm(section.prim.arc[2])
            if radius_dims:
                number, _ = parse_size(radius_dims[0].text)
                feature["radius"] = number if number is not None else feature["radius"]
                for dim in radius_dims:
                    row.dims.append(dim.text)
                    row.notes.extend(dim.flags)
            else:
                row.notes.append(f"R{feature['radius']:g} from the geometry: no radius dimension")
    if section.kind == "groove":
        index = part.sections.index(section)
        neighbours = [part.sections[index - 1].r_at(section.x0), part.sections[index + 1].r_at(section.x1)]
        feature["start_diameter"] = _mm(2 * min(neighbours))
    feature["length"] = _mm(section.length)
    note = _length_note(part, section, length_dims)
    feature["length_derived"] = note is not None
    if note:
        row.notes.append(note)
    rows = [row]
    if section.kind == "thread":
        # the section under an external thread is turned first (to the reduced major Ø by the planner), so it
        # is an od_turn row as well, as the model records it
        turned = _Row({"type": "od_turn", "diameter": feature["diameter"], "length": feature["length"],
                       "length_derived": feature["length_derived"]}, section.index, list(row.dims), list(row.notes))
        rows.insert(0, turned)

    for side, leg, face_x in section.chamfers:
        # a chamfer's host is always a cylinder (dxf_reader), so the row has its diameter
        chamfer = _Row({"type": "chamfer", "diameter": feature["diameter"], "length": leg, "location": "external",
                        "face": side}, section.index)
        bound = [d for d in chamfer_dims if _same(d.target[1], face_x)]
        for dim in bound:
            number = parse_chamfer(dim.text)
            if number is not None:
                chamfer.feature["length"] = number
            chamfer.dims.append(dim.text)
            chamfer.notes.extend(dim.flags)
        if not bound:
            chamfer.notes.append(f"chamfer {leg:g}×45° from the geometry: not dimensioned")
        rows.append(chamfer)

    for side, radius in section.fillets:
        face_x = section.x1 if side == "right" else section.x0
        fillet = _Row({"type": "fillet", "radius": _mm(radius)}, section.index)
        bound = [d for d in fillet_dims if _same(d.target[1], face_x)]
        for dim in bound:
            number, _ = parse_size(dim.text)
            if number is not None:
                fillet.feature["radius"] = number
            fillet.dims.append(dim.text)
            fillet.notes.extend(dim.flags)
        if not bound:
            fillet.notes.append(f"R{radius:g} from the geometry: not dimensioned")
        rows.append(fillet)
    return rows


def _label(number, count, part, overall):
    biggest = max(2 * s.r_at(x) for s in part.sections for x in (s.x0, s.x1))
    prefix = f"Part {number} of {count}" if count > 1 else "Part"
    return f"{prefix}: Ø{_mm(biggest):g} × {overall:g}, {len(part.sections)} sections"


def part_result(reading: DxfReading, part: Part, number: int = 1) -> PartResult:
    dims = reading.dims_of(part)
    by_section = {}
    for dim in dims:
        section = _section_of(part, dim.target)
        if section is not None:
            by_section.setdefault(section.index, []).append(dim)
    length_dims = [d for d in dims if d.target and d.target[0] == "x"]
    chamfer_dims = [d for d in dims if d.target and d.target[0] == "chamfer_at"]
    fillet_dims = [d for d in dims if d.target and d.target[0] == "fillet_at"]

    # geometry diameter of a cylinder -> (section, its diameter dimension), for diameters dimensioned once
    elsewhere = {}
    for section in part.sections:
        bound = [d for d in by_section.get(section.index, []) if d.kind == "diameter"]
        if section.kind in ("od_turn", "groove") and bound and parse_size(bound[0].text)[0] is not None:
            elsewhere.setdefault(_mm(2 * section.r_at(section.x0)), (section.index, bound[0]))
    rows = []
    for section in part.sections:
        rows += _section_rows(part, section, by_section, chamfer_dims, fillet_dims, length_dims, elsewhere)

    x0, x1 = part.sections[0].x0, part.sections[-1].x1
    overall_dims = [d for d in length_dims if _same(d.target[1], x0) and _same(d.target[2], x1)]
    warnings = []
    if overall_dims:
        overall = _num(re.search(NUMBER, overall_dims[0].text).group()) if re.search(NUMBER, overall_dims[0].text) \
            else _mm(x1 - x0)
        overall_source = overall_dims[0].text
    else:
        overall, overall_source = _mm(x1 - x0), None
        warnings.append(f"overall length {overall:g} from the geometry: not dimensioned, check")
    for dim in dims:
        if dim.target is None and not dim.is_inner:
            warnings.append(f"dimension {dim.text} not bound ({dim.binding}): check it on the drawing")
        elif dim.is_inner:
            warnings.append(f"dimension {dim.text} is on the inner profile, which is not read from the DXF: "
                            f"add the bore by hand")

    data = DrawingData(
        part_type="turned",
        overall_length=overall,
        features=[ExtractedFeature(**r.feature) for r in rows],
        warnings=warnings,
    )
    row_of = {}
    for i, r in enumerate(rows):
        for text in r.dims:
            row_of.setdefault(text, i)
    count = len(reading.parts)
    report = {
        "label": _label(number, count, part, overall),
        "part": number,
        "parts": count,
        "overall_length_source": overall_source,
        "rows": [{"section": r.section, "dims": r.dims, "notes": r.notes} for r in rows],
        "dims": [
            {
                "text": d.text,
                "kind": d.kind,
                "binding": d.binding,
                "bound": d.target is not None,
                "inner": d.is_inner,
                "flags": list(d.flags),
            }
            for d in dims
        ],
    }
    return PartResult(data, report)


def part_results(reading: DxfReading) -> list[PartResult]:
    return [part_result(reading, part, n) for n, part in enumerate(reading.parts, start=1)]
