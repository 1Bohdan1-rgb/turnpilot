"""The inner profile (bores) of a part read from a DXF, taken from the frozen reader's result without changing it.

dxf_reader (frozen by the tag dxf-blind-freeze) keeps the contour pieces of the upper half that lie under the outer
envelope as `part.inner_prims`, and marks the diameter dimensions that stand on them "inner". Here they become rows:
- a bore wall: a horizontal piece off the axis. A chain of walls starts at an end face (directly, or at the foot of a
  45° chamfer at that face) and goes inwards, each next wall narrower and starting where the one before ends;
- its bottom: a line from the wall's end down to the axis is a drill point (its cone angle kept for information), a
  vertical face down to the axis a flat bottom, a narrower wall a step (a flat ring); a wall that reaches the other
  end face makes a through bore (its bottom "through"; the frozen reader finds no part whose contour a through bore
  splits into two halves, so this is seen only in the operator's rows for now);
- a 45° diagonal up to CHAMFER_MAX_LEG at the mouth: an internal chamfer.
Each bore row records where it is (the face it opens at, its start from that face, its bottom), so the program knows
it. Dimensions: an inner diameter is bound to the wall at its x whose radius equals its value (the geometry, as the
reader measured it); a length to the wall whose ends (or the face and its end) it spans. Numbers come from the
dimension texts, else from the geometry with a note.

IN-SAMPLE: written while looking at Завіса 36 (the bushing's blind Ø22+0,21 × 32 with a drill point, the pin's
Ø10+0,15 × 5 with a flat bottom); no blind check of the inner profile has been made. Every bore row carries a note
saying so. Internal grooves, threads, tapers and arcs are not read (warnings).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from .dxf_input import _mm, parse_chamfer, parse_size
from .dxf_reader import BIND_TOL, CHAMFER_MAX_LEG, GEOM_TOL, DxfReading, Part, upper_verticals
from .extraction_schema import ExtractedFeature

IN_SAMPLE_NOTE = ("inner profile read from the DXF in-sample (written on Завіса 36): no blind check yet; check the "
                  "bore on the drawing")
INNER_WARNING = ("dimension {text} is on the inner profile, which is not read from the DXF: "
                 "add the bore by hand")  # dxf_input's warning, as it writes it


@dataclass
class Wall:
    x0: float
    x1: float
    r: float


@dataclass
class Bore:
    wall: Wall
    open_end: str  # "left" / "right": the face it opens at (a through bore: the face its start is measured from)
    start: float  # mm from the open face to where this bore starts
    length: float  # mm along the axis (the first one from the face, chamfer included)
    bottom: str | None  # "flat" / "drill_point" / "through" / None (not found)
    cone_angle: float | None = None
    chamfer: float | None = None  # a 45° chamfer at the mouth (first bore only)
    notes: list = field(default_factory=list)


def _same(a, b, tol=BIND_TOL):
    return abs(a - b) <= tol


def _walls(part: Part) -> list[Wall]:
    """The horizontal inner pieces off the axis, contiguous ones of one radius joined."""
    pieces = sorted((Wall(p.x0, p.x1, p.r0) for p in part.inner_prims
                     if p.kind == "line" and abs(p.r1 - p.r0) <= GEOM_TOL and p.r0 > GEOM_TOL),
                    key=lambda w: (w.r, w.x0))
    out: list[Wall] = []
    for w in pieces:
        if out and abs(out[-1].r - w.r) <= GEOM_TOL and w.x0 <= out[-1].x1 + GEOM_TOL:
            out[-1].x1 = max(out[-1].x1, w.x1)
        else:
            out.append(Wall(w.x0, w.x1, w.r))
    return out


def _diagonals(part: Part):
    return [p for p in part.inner_prims if p.kind == "line" and abs(p.r1 - p.r0) > GEOM_TOL]


def _mouth_chamfer(part: Part, wall: Wall, face_x: float) -> float | None:
    """The leg of a 45° chamfer from the face down to the wall's end next to it, or None."""
    end = wall.x1 if face_x > wall.x0 else wall.x0
    for p in _diagonals(part):
        ends = [(p.x0, p.r0), (p.x1, p.r1)]
        at_wall = [e for e in ends if _same(e[0], end, GEOM_TOL) and _same(e[1], wall.r, GEOM_TOL)]
        at_face = [e for e in ends if _same(e[0], face_x, GEOM_TOL)]
        if at_wall and at_face:
            leg = abs(face_x - end)
            if abs(abs(at_face[0][1] - wall.r) - leg) <= GEOM_TOL and leg <= CHAMFER_MAX_LEG + GEOM_TOL:
                return leg
    return None


def _bottom(part: Part, wall: Wall, inner_x: float, walls: list[Wall]):
    """(kind, cone angle, next wall) at a wall's inner end."""
    for p in _diagonals(part):
        ends = [(p.x0, p.r0), (p.x1, p.r1)]
        if any(_same(x, inner_x, GEOM_TOL) and _same(r, wall.r, GEOM_TOL) for x, r in ends) \
                and any(r <= GEOM_TOL for _, r in ends):
            dx = abs(p.x1 - p.x0)
            return "drill_point", round(2 * math.degrees(math.atan2(wall.r, dx)), 1), None
    narrower = [w for w in walls if w is not wall and w.r < wall.r - GEOM_TOL
                and (_same(w.x0, inner_x, GEOM_TOL) or _same(w.x1, inner_x, GEOM_TOL))]
    if narrower:
        return "flat", None, max(narrower, key=lambda w: w.r)
    for x, lo, hi in upper_verticals(part):
        if _same(x, inner_x, GEOM_TOL) and lo <= GEOM_TOL and hi >= wall.r - GEOM_TOL:
            return "flat", None, None
    return None, None, None


def bores(part: Part) -> tuple[list[Bore], list[str]]:
    """The part's bores, from each end face inwards, and what was seen but not read."""
    walls = _walls(part)
    out: list[Bore] = []
    used: set[int] = set()
    warnings = []
    for face_x, side in ((part.x_max, "right"), (part.x_min, "left")):
        for wall in walls:
            if id(wall) in used:
                continue
            near = wall.x1 if side == "right" else wall.x0
            chamfer = _mouth_chamfer(part, wall, face_x)
            if not (_same(near, face_x, GEOM_TOL) or chamfer):
                continue
            depth, start, current, first = 0.0, 0.0, wall, True
            while current is not None:
                used.add(id(current))
                far = current.x0 if side == "right" else current.x1
                through = _same(far, part.x_min if side == "right" else part.x_max, GEOM_TOL)
                length = abs(face_x - far) - start
                bore = Bore(current, side, round(start, 6), round(length, 6), "through" if through else None,
                            chamfer=chamfer if first else None)
                out.append(bore)
                if through:
                    break
                kind, angle, nxt = _bottom(part, current, far, walls)
                bore.bottom, bore.cone_angle = kind, angle
                if kind is None:
                    bore.notes.append("the bore's bottom was not recognised (no drill point, flat face or step): check")
                start, current, first = start + length, nxt, False
    for wall in walls:
        if id(wall) not in used:
            warnings.append(f"an inner wall Ø{_mm(2 * wall.r):g} at x {wall.x0 - part.x_min:g}..{wall.x1 - part.x_min:g} "
                            "is not reached from an end face (an internal groove?): not read, check")
    bore_ends = {round(x, 3) for b in out for x in (b.wall.x0, b.wall.x1)}
    for p in _diagonals(part):
        if p.r0 > GEOM_TOL and p.r1 > GEOM_TOL and not any(b.chamfer for b in out
                                                           if round(p.x0, 3) in bore_ends or round(p.x1, 3) in bore_ends):
            warnings.append(f"an inner taper or chamfer at x {p.x0 - part.x_min:g}..{p.x1 - part.x_min:g}: not read, check")
    for p in part.inner_prims:
        if p.kind == "arc":
            warnings.append(f"an inner arc at x {p.x0 - part.x_min:g}..{p.x1 - part.x_min:g}: not read, check")
    return out, warnings


@dataclass
class InnerRow:
    feature: dict
    dims: list = field(default_factory=list)
    notes: list = field(default_factory=list)
    inner: dict | None = None  # the bore's position, for the job


def inner_rows(reading: DxfReading, part: Part) -> tuple[list[InnerRow], list[str], set[str]]:
    """(rows, warnings, texts of the inner dimensions bound to a row)."""
    found, warnings = bores(part)
    dims = reading.dims_of(part)
    inner_dims = [d for d in dims if d.is_inner]
    lengths = [d for d in dims if d.target and d.target[0] == "x"]
    chamfer_dims = [d for d in dims if d.kind == "chamfer"]
    bound: set[str] = set()
    rows: list[InnerRow] = []
    for bore in found:
        wall = bore.wall
        geometry = _mm(2 * wall.r)
        row = InnerRow({"type": "bore", "diameter": geometry, "length": _mm(bore.length)})
        row.notes.append(IN_SAMPLE_NOTE)
        mine = [d for d in inner_dims if d.kind == "diameter" and d.value is not None
                and wall.x0 - BIND_TOL <= d.points[0][0] <= wall.x1 + BIND_TOL and _same(d.value / 2, wall.r)]
        if mine:
            number, tolerance = parse_size(mine[0].text)
            if number is not None:
                row.feature["diameter"] = number
            row.feature["tolerance"] = tolerance
            for d in mine:
                row.dims.append(d.text)
                row.notes.extend(d.flags)
                bound.add(d.text)
        else:
            row.notes.append(f"Ø{geometry:g} from the geometry: no diameter dimension on it")
        face_x = part.x_max if bore.open_end == "right" else part.x_min
        far = wall.x0 if face_x > wall.x0 else wall.x1
        near = face_x if bore.start == 0 else (wall.x1 if face_x > wall.x0 else wall.x0)
        spans = [d for d in lengths if _same(min(d.target[1], d.target[2]), min(near, far))
                 and _same(max(d.target[1], d.target[2]), max(near, far))]
        if spans:
            row.dims.append(spans[0].text)
            row.feature["length_derived"] = False
        else:
            row.feature["length_derived"] = True
            row.notes.append(f"length {bore.length:g} from the geometry: no dimension spans this bore")
        row.notes.extend(bore.notes)
        if bore.bottom == "drill_point":
            row.notes.append(f"bottom: a drill point drawn at {bore.cone_angle:g}° (the drill's own point angle "
                             "makes it): check")
        row.inner = {"open_end": bore.open_end, "from": _mm(bore.start), "bottom": bore.bottom,
                     "cone_angle": bore.cone_angle}
        rows.append(row)
        if bore.chamfer:
            chamfer = InnerRow({"type": "chamfer", "diameter": row.feature["diameter"], "length": _mm(bore.chamfer),
                                "location": "internal", "face": "right" if face_x == part.x_max else "left"})
            at = [d for d in chamfer_dims if any(_same(p[0], face_x) for p in d.points)
                  and any(abs(p[1] - part.axis_y) < wall.r + bore.chamfer + BIND_TOL for p in d.points)]
            for d in at:
                if parse_chamfer(d.text) is not None and d.target is None:
                    chamfer.feature["length"] = parse_chamfer(d.text)
                    chamfer.dims.append(d.text)
            if not chamfer.dims:
                chamfer.notes.append(f"chamfer {bore.chamfer:g}×45° from the geometry: not dimensioned")
            chamfer.notes.append(IN_SAMPLE_NOTE)
            rows.append(chamfer)
    for d in inner_dims:
        if d.text not in bound:
            warnings.append(f"dimension {d.text} is on the inner profile but on no bore read: check it on the drawing")
    return rows, warnings, bound


def add_inner(reading: DxfReading, results) -> None:
    """Append each part's bore rows to its DrawingData and binding report (dxf_input.part_results' output, changed in
    place): the inner dimensions they take no longer warn "add the bore by hand"."""
    for part, result in zip(reading.parts, results):
        rows, warnings, bound = inner_rows(reading, part)
        if not rows and not warnings:
            continue
        taken = {INNER_WARNING.format(text=t) for t in bound}
        result.data.warnings = [w for w in result.data.warnings if w not in taken] + warnings
        result.data.features += [ExtractedFeature(**r.feature) for r in rows]
        for d in result.report["dims"]:
            if d["inner"] and d["text"] in bound:
                d["inner_row"] = True
        result.report["rows"] += [{"section": "inner", "dims": r.dims, "notes": r.notes, "inner": r.inner}
                                  for r in rows]
