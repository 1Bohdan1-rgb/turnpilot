"""The outer profile of a turned part from a KOMPAS DXF, and every DIMENSION bound to its section(s).

A DXF keeps what a PDF loses: each dimension is a DIMENSION entity with its anchor points (defpoints),
and the contour, the centre lines and the thin lines are told apart by their linetypes. So the binding of
a number to a section comes from the geometry of the file, with no model involved and no API call.

Steps:
  1. entities and their line roles (KOMPAS linetypes K5LT_BASIC / K5LT_THIN / K5LT_AXLED);
  2. axes and parts: one part per horizontal centre line, made of the contour components symmetric about
     it (a sheet may hold several parts);
  3. the outer profile: sections run face / step to face / step; a short 45° diagonal is a chamfer of the
     flat it touches, a transition arc is a fillet at a boundary, other arcs are sections of type "arc",
     a cylinder narrower than both neighbours is a groove;
  4. dimensions: the value is computed from the defpoints (KOMPAS writes −1 as the measurement), the text
     comes from the dimension's block; "geometry ≠ text" is flagged;
  5. binding: lengths by the x of their ends, diameters / radii / chamfers to sections; dimensions of the
     inner profile are listed apart ("inner"), not bound.

Only KOMPAS DXF files have been tried. Ported unchanged from the prototype that was measured on three
KOMPAS files (two in-sample, one partly seen); see CLAUDE.md.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

import ezdxf

GEOM_TOL = 0.01  # mm: endpoints that coincide
BIND_TOL = 0.05  # mm: a defpoint on a boundary, a diameter equal to a section's
CHAMFER_MAX_LEG = 3.0  # mm: a 45° diagonal with a leg up to this next to a flat is a chamfer, not a taper
TANGENT_TOL_DEG = 1.0  # an arc end whose tangent is within this of horizontal / vertical
AXIS_OVERHANG = 5.0  # mm: a part's contour may stick out of its centre line by this much at either end
MIN_MIRRORED_SHARE = 0.8  # share of a contour component's entities that must be symmetric about the axis

CONTOUR, THIN, AXIS = "K5LT_BASIC", "K5LT_THIN", "K5LT_AXLED"
# KOMPAS writes special signs as private-use codes; AutoCAD as %% codes.
SPECIAL = {"": "Ø", "": "×", "": "°", "": "±",
           "%%c": "Ø", "%%C": "Ø", "%%d": "°", "%%p": "±"}
# Threads are written with a Cyrillic М; other look-alike capitals for safety.
CYRILLIC = str.maketrans("МРСНОАВЕКТХ", "MPCHOABEKTX")


class DxfReadError(Exception):
    """The file cannot be turned into a profile; the message says why, for the machinist."""


# --- 1. entities ------------------------------------------------------------------------------------------

def linetype(doc, e):
    name = e.dxf.get("linetype", "BYLAYER")
    if name.upper() == "BYLAYER":
        name = doc.layers.get(e.dxf.layer).dxf.linetype
    return name.upper()


def load(path):
    try:
        doc = ezdxf.readfile(path)
    except (OSError, ezdxf.DXFError) as exc:
        raise DxfReadError(f"The DXF file cannot be read: {exc}") from None
    contour, thin, axes, dims = [], [], [], []
    for e in doc.modelspace():
        kind = e.dxftype()
        if kind == "DIMENSION":
            dims.append(e)
        elif kind in ("LINE", "ARC"):
            role = linetype(doc, e)
            if role == AXIS and kind == "LINE":
                axes.append(e)
            elif role == CONTOUR:
                contour.append(e)
            elif role == THIN:
                thin.append(e)
    return doc, contour, thin, axes, dims


def endpoints(e):
    if e.dxftype() == "LINE":
        return (e.dxf.start.x, e.dxf.start.y), (e.dxf.end.x, e.dxf.end.y)
    c, r = e.dxf.center, e.dxf.radius
    a0, a1 = math.radians(e.dxf.start_angle), math.radians(e.dxf.end_angle)
    return (c.x + r * math.cos(a0), c.y + r * math.sin(a0)), (c.x + r * math.cos(a1), c.y + r * math.sin(a1))


# --- 2. axes and parts ------------------------------------------------------------------------------------

def on_line(p, e):
    """p lies on the LINE e (a T-junction: a groove bottom meeting a step in its middle)."""
    if e.dxftype() != "LINE":
        return False
    (x0, y0), (x1, y1) = endpoints(e)
    length = math.hypot(x1 - x0, y1 - y0)
    if length <= GEOM_TOL:
        return False
    cross = abs((x1 - x0) * (p[1] - y0) - (y1 - y0) * (p[0] - x0)) / length
    along = ((p[0] - x0) * (x1 - x0) + (p[1] - y0) * (y1 - y0)) / length
    return cross <= GEOM_TOL and -GEOM_TOL <= along <= length + GEOM_TOL


def components(entities):
    """Groups of contour entities connected by coinciding endpoints or T-junctions."""
    parent = list(range(len(entities)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    ends = [endpoints(e) for e in entities]
    for i in range(len(entities)):
        for j in range(i + 1, len(entities)):
            if any(math.dist(p, q) <= GEOM_TOL for p in ends[i] for q in ends[j]) or \
                    any(on_line(p, entities[j]) for p in ends[i]) or any(on_line(q, entities[i]) for q in ends[j]):
                parent[find(i)] = find(j)
    by_root = {}
    for i, e in enumerate(entities):
        by_root.setdefault(find(i), []).append(e)
    return list(by_root.values())


def mirrored_share(group, axis_y):
    count = 0
    for e in group:
        (x0, y0), (x1, y1) = endpoints(e)
        if abs(y0 - axis_y) <= GEOM_TOL and abs(y1 - axis_y) <= GEOM_TOL:
            count += 1
            continue
        mirror = any(p is not e and all(
            any(abs(a[0] - b[0]) <= GEOM_TOL and abs((a[1] - axis_y) + (b[1] - axis_y)) <= GEOM_TOL for b in endpoints(p))
            for a in ((x0, y0), (x1, y1))) for p in group)
        count += mirror or (min(y0, y1) < axis_y - GEOM_TOL < axis_y + GEOM_TOL < max(y0, y1))
    return count / len(group)


@dataclass
class Part:
    axis_y: float
    x_min: float
    x_max: float
    entities: list
    sections: list = field(default_factory=list)
    boundaries: list = field(default_factory=list)
    fillets: list = field(default_factory=list)  # (x of the boundary, radius, arc prim)
    inner_prims: list = field(default_factory=list)


def find_parts(contour, axes):
    """A part: the contour components that lie on one horizontal centre line and are symmetric about it."""
    horizontal = [a for a in axes if abs(a.dxf.start.y - a.dxf.end.y) <= GEOM_TOL]
    parts = []
    groups = components(contour)
    for axis in horizontal:
        ay = axis.dxf.start.y
        ax0, ax1 = sorted((axis.dxf.start.x, axis.dxf.end.x))
        mine = []
        for g in groups:
            xs = [p[0] for e in g for p in endpoints(e)]
            ys = [p[1] for e in g for p in endpoints(e)]
            if min(ys) < ay < max(ys) and ax0 - AXIS_OVERHANG <= min(xs) and max(xs) <= ax1 + AXIS_OVERHANG \
                    and mirrored_share(g, ay) >= MIN_MIRRORED_SHARE:
                mine += g
        if mine:
            xs = [p[0] for e in mine for p in endpoints(e)]
            parts.append(Part(ay, min(xs), max(xs), mine))
    used = {id(e) for p in parts for e in p.entities}
    return parts, [e for e in contour if id(e) not in used]


# --- 3. outer profile -------------------------------------------------------------------------------------

@dataclass
class Prim:
    """A contour primitive of the upper half: x along the axis, r = distance from the axis."""
    kind: str  # "line" or "arc"
    x0: float
    r0: float
    x1: float
    r1: float
    arc: tuple | None = None  # (cx, cr, radius) in part coordinates


def radius_at(p: Prim, x):
    if p.kind == "line":
        return p.r0 if abs(p.x1 - p.x0) <= GEOM_TOL else p.r0 + (p.r1 - p.r0) * (x - p.x0) / (p.x1 - p.x0)
    cx, cr, rad = p.arc
    root = math.sqrt(max(rad * rad - (x - cx) ** 2, 0.0))
    # the branch of the circle the arc lies on (above or below its centre)
    return cr + root if (p.r0 + p.r1) / 2 >= cr else cr - root


def upper_prims(part):
    prims = []
    for e in part.entities:
        (x0, y0), (x1, y1) = endpoints(e)
        r0, r1 = y0 - part.axis_y, y1 - part.axis_y
        if r0 < -GEOM_TOL or r1 < -GEOM_TOL:
            continue  # the lower half mirrors the upper one
        if x0 > x1:
            x0, r0, x1, r1 = x1, r1, x0, r0
        if e.dxftype() == "LINE":
            if abs(x1 - x0) > GEOM_TOL:  # verticals are faces, steps, edge lines
                prims.append(Prim("line", x0, r0, x1, r1))
        else:
            c = e.dxf.center
            prims.append(Prim("arc", x0, r0, x1, r1, (c.x, c.y - part.axis_y, e.dxf.radius)))
    return prims


def is_transition(p: Prim, verticals):
    """A transition arc (fillet) joins two sections: tangent to a horizontal flat at one end, and at the other
    end tangent to a vertical face / step that the contour really has there (a vertical line through that
    end, off the axis). An arc ending on the axis (a spherical end) joins nothing: it is a section."""
    if p.kind != "arc":
        return False
    cx, cr, _ = p.arc

    def tangent(x, r):  # angle of the tangent at (x, r): the radius direction turned by 90°
        angle = math.degrees(math.atan2(r - cr, x - cx)) % 180
        return "horizontal" if min(abs(angle - 90), abs(angle - 270)) <= TANGENT_TOL_DEG else \
            "vertical" if min(angle, abs(angle - 180)) <= TANGENT_TOL_DEG else "other"

    ends = {tangent(p.x0, p.r0): (p.x0, p.r0), tangent(p.x1, p.r1): (p.x1, p.r1)}
    if set(ends) != {"horizontal", "vertical"}:
        return False
    x, r = ends["vertical"]
    return r > GEOM_TOL and any(abs(vx - x) <= GEOM_TOL and lo - GEOM_TOL <= r <= hi + GEOM_TOL
                                for vx, lo, hi in verticals)


@dataclass
class Section:
    index: int
    kind: str  # od_turn, groove, taper, arc, thread (thread is set from a dimension)
    x0: float
    x1: float
    prim: Prim
    chamfers: list = field(default_factory=list)  # (side, leg, x of the face)
    fillets: list = field(default_factory=list)  # (side, radius)

    @property
    def length(self):
        return self.x1 - self.x0

    def r_at(self, x):
        return radius_at(self.prim, min(max(x, self.prim.x0), self.prim.x1))

    def size(self):
        if self.kind == "arc":
            return f"R{self.prim.arc[2]:g}"
        a, b = self.r_at(self.x0), self.r_at(self.x1)
        return f"Ø{2 * a:g}" if abs(a - b) <= GEOM_TOL else f"Ø{2 * a:g}→Ø{2 * b:g}"


def upper_verticals(part):
    """(x, r_low, r_high) of the vertical contour lines of the upper half: faces, steps."""
    out = []
    for e in part.entities:
        if e.dxftype() != "LINE":
            continue
        (x0, y0), (x1, y1) = endpoints(e)
        if abs(x1 - x0) <= GEOM_TOL:
            r0, r1 = sorted((y0 - part.axis_y, y1 - part.axis_y))
            if r1 > GEOM_TOL:
                out.append((x0, max(r0, 0.0), r1))
    return out


def outer_profile(part):
    prims = upper_prims(part)
    verticals = upper_verticals(part)
    xs = sorted({round(v, 4) for p in prims for v in (p.x0, p.x1)})
    pieces = []  # (x0, x1, prim) of the outer envelope
    for a, b in zip(xs, xs[1:]):
        if b - a <= GEOM_TOL:
            continue
        mid = (a + b) / 2
        covering = [p for p in prims if p.x0 - GEOM_TOL <= mid <= p.x1 + GEOM_TOL]
        if not covering:
            raise DxfReadError(f"The outer contour has a gap at x {a:.3f}..{b:.3f}: no profile can be built.")
        top = max(covering, key=lambda p: radius_at(p, mid))
        if pieces and pieces[-1][2] is top:
            pieces[-1] = (pieces[-1][0], b, top)
        else:
            pieces.append((a, b, top))
    part.inner_prims = [p for p in prims if all(p is not top for _, _, top in pieces)]
    sections, extras = [], []  # extras: chamfers and transition arcs, to join a flat
    for a, b, p in pieces:
        if p.kind == "arc":
            if is_transition(p, verticals):
                extras.append(("fillet", a, b, p))
            else:
                sections.append(Section(0, "arc", a, b, p))
            continue
        r_a, r_b = radius_at(p, a), radius_at(p, b)
        dx, dr = b - a, abs(r_b - r_a)
        if dr <= GEOM_TOL:
            sections.append(Section(0, "od_turn", a, b, p))
        elif abs(dx - dr) <= GEOM_TOL and dx <= CHAMFER_MAX_LEG:
            extras.append(("chamfer", a, b, p))
        else:
            sections.append(Section(0, "taper", a, b, p))
    for kind, a, b, p in extras:
        r_a, r_b = radius_at(p, a), radius_at(p, b)
        high_x, high_r = (a, r_a) if r_a > r_b else (b, r_b)
        low_x = b if high_x == a else a
        host = next((s for s in sections if s.kind == "od_turn" and abs(s.r_at(s.x0) - high_r) <= GEOM_TOL
                     and (abs(s.x1 - high_x) <= GEOM_TOL or abs(s.x0 - high_x) <= GEOM_TOL)), None)
        if host is None:
            sections.append(Section(0, "arc" if kind == "fillet" else "taper", a, b, p))
            continue
        side = "right" if abs(host.x1 - high_x) <= GEOM_TOL else "left"
        if side == "right":
            host.x1 = low_x
        else:
            host.x0 = low_x
        if kind == "chamfer":
            host.chamfers.append((side, round(b - a, 3), low_x))
        else:
            host.fillets.append((side, p.arc[2]))
            part.fillets.append((low_x, p.arc[2], p))
    sections.sort(key=lambda s: s.x0)
    for i, s in enumerate(sections):  # a cylinder narrower than both neighbours is a groove
        if s.kind == "od_turn" and 0 < i < len(sections) - 1:
            left, right = sections[i - 1], sections[i + 1]
            if left.r_at(left.x1) > s.r_at(s.x0) + GEOM_TOL and right.r_at(right.x0) > s.r_at(s.x1) + GEOM_TOL:
                s.kind = "groove"
    for i, s in enumerate(sections, start=1):
        s.index = i
    gaps = [(a.x1, b.x0) for a, b in zip(sections, sections[1:]) if abs(a.x1 - b.x0) > GEOM_TOL]
    if gaps:
        raise DxfReadError(f"The sections do not join up (gaps at {gaps}): no profile can be built.")
    part.sections = sections
    part.boundaries = [sections[0].x0] + [s.x1 for s in sections]


# --- 4. dimensions ----------------------------------------------------------------------------------------

def clean(text):
    for code, char in SPECIAL.items():
        text = text.replace(code, char)
    text = re.sub(r"\\[A-Za-z][^;\\{}]*;", "", text)
    text = re.sub(r"\\[PNnLlOoKk~]", " ", text)
    return text.replace("{", "").replace("}", "").strip()


def dim_text(doc, dim):
    name = dim.dxf.get("geometry")
    texts = []
    if name and name in doc.blocks:
        texts = [e.dxf.text if e.dxftype() == "TEXT" else e.text for e in doc.blocks[name] if e.dxftype() in ("TEXT", "MTEXT")]
    text = "".join(clean(t) for t in texts if t.strip()) or clean(dim.dxf.get("text", ""))
    return text.translate(CYRILLIC)


@dataclass
class Dim:
    kind: str  # length, diameter, radius, chamfer, other
    text: str
    value: float | None
    text_number: float | None
    points: list
    flags: list = field(default_factory=list)
    part: Part | None = None
    binding: str | None = None  # what it is bound to, in words, or why it is not
    target: tuple | None = None  # ("x", a, b), ("section", x0, x1), ("fillet_at", x), ("chamfer_at", x)

    @property
    def is_inner(self):
        return self.binding == "inner"


def read_dims(doc, dims):
    out = []
    for d in dims:
        kind_code = d.dxf.dimtype & 7
        text = dim_text(doc, d)
        number = re.search(r"\d+(?:[.,]\d+)?", re.sub(r"^\s*[ØRMS]?\s*", "", text))
        number = float(number.group().replace(",", ".")) if number else None
        if kind_code == 0:
            p2, p3 = d.dxf.defpoint2, d.dxf.defpoint3
            angle = d.dxf.get("angle", 0.0)
            a = math.radians(angle)
            value = abs((p3.x - p2.x) * math.cos(a) + (p3.y - p2.y) * math.sin(a))
            kind = "length" if abs(angle) <= 0.01 or abs(angle - 180) <= 0.01 else \
                "diameter" if abs(abs(angle) - 90) <= 0.01 else "other"
            if "×45" in text and kind == "length":
                kind = "chamfer"
            points = [(p2.x, p2.y), (p3.x, p3.y)]
        elif kind_code == 4:
            c, p = d.dxf.defpoint, d.dxf.defpoint4
            value, kind, points = math.hypot(p.x - c.x, p.y - c.y), "radius", [(c.x, c.y), (p.x, p.y)]
        else:
            value, kind, points = None, "other", []
        dim = Dim(kind, text, value, number, points)
        if value is not None and number is not None and abs(value - number) > GEOM_TOL:
            dim.flags.append(f"geometry {value:g} ≠ text {number:g}")
        out.append(dim)
    return out


# --- 5. binding -------------------------------------------------------------------------------------------

def owner(parts, dim):
    """The part whose x range covers the defpoints; of several, the one whose axis is nearest. (A defpoint
    need not lie on the contour: KOMPAS starts a baseline dimension on the previous dimension line.)"""
    if not dim.points:
        return None
    if dim.kind == "radius":
        cx, cy = dim.points[1]  # the point on the arc
        near = [p for p in parts if p.x_min - BIND_TOL <= cx <= p.x_max + BIND_TOL]
        return min(near, key=lambda p: abs(cy - p.axis_y), default=None)
    xs = [p[0] for p in dim.points]
    near = [p for p in parts if all(p.x_min - BIND_TOL <= x <= p.x_max + BIND_TOL for x in xs)]
    return min(near, key=lambda p: sum(abs(y - p.axis_y) for _, y in dim.points), default=None)


def where(part, x):
    """'boundary k' if x is on a section boundary, else 'inside section k'."""
    for k, bx in enumerate(part.boundaries):
        if abs(bx - x) <= BIND_TOL:
            return f"boundary {k}"
    s = next((s for s in part.sections if s.x0 < x < s.x1), None)
    return f"inside section {s.index}" if s else "outside the part"


def bind(part, dim):
    """(target, binding in words); target is None when the dimension is not bound."""
    if dim.kind == "length":
        a, b = sorted(x for x, _ in dim.points)
        ends = [where(part, a), where(part, b)]
        if "outside the part" in ends:
            return None, "an end is outside the part"
        return ("x", a, b), f"x {a:g} → {b:g} ({ends[0]} → {ends[1]})"
    if dim.kind == "chamfer":
        xs = [x for x, _ in dim.points]
        for s in part.sections:
            for side, leg, face_x in s.chamfers:
                lo, hi = sorted((face_x, face_x + (-leg if side == "right" else leg)))
                if all(lo - BIND_TOL <= x <= hi + BIND_TOL for x in xs):
                    return ("chamfer_at", face_x), f"chamfer of section {s.index} at x {face_x:g}"
        return None, "no chamfer under its points"
    if dim.kind == "diameter":
        x = dim.points[0][0]
        want = (dim.value or 0) / 2
        at_x = [s for s in part.sections if s.x0 - BIND_TOL <= x <= s.x1 + BIND_TOL]
        exact = [s for s in at_x if abs(s.r_at(x) - want) <= BIND_TOL]
        inside = [s for s in exact if s.x0 + BIND_TOL < x < s.x1 - BIND_TOL]
        chosen = inside or exact
        if len(chosen) > 1:
            # on a boundary between two sections: the one whose radius at x is closest to the dimension;
            # still ambiguous if they are equally close
            chosen.sort(key=lambda s: abs(s.r_at(x) - want))
            if abs(abs(chosen[0].r_at(x) - want) - abs(chosen[1].r_at(x) - want)) <= GEOM_TOL:
                # equally close: a constant-diameter section (a cylinder) is what a single Ø describes;
                # for an arc or a taper it is only an end point
                cylinders = [s for s in chosen if s.kind in ("od_turn", "groove", "thread")]
                if len(cylinders) != 1:
                    return None, "AMBIGUOUS sections " + ", ".join(str(s.index) for s in chosen)
                chosen = cylinders
            chosen = chosen[:1]
        if not chosen:
            inner = [p for p in part.inner_prims if p.x0 - BIND_TOL <= x <= p.x1 + BIND_TOL
                     and abs(radius_at(p, x) - want) <= BIND_TOL]
            if inner:
                return None, "inner"
            if not at_x:
                return None, f"no section at x {x:g}"
            # the dimension does not lie on the drawn contour: bind to the outer section at x, flagged
            s = min(at_x, key=lambda s: abs(s.r_at(x) - want))
            dim.flags.append(f"Ø{2 * want:g} ≠ contour Ø{2 * s.r_at(x):g} at x {x:g}")
            chosen = [s]
        s = chosen[0]
        if dim.text.startswith("M") and s.kind == "od_turn":
            s.kind = "thread"
        return ("section", s.x0, s.x1), f"section {s.index}"
    if dim.kind == "radius":
        px, py = dim.points[1]
        r_point = py - part.axis_y
        for fx, rad, p in part.fillets:
            if p.x0 - BIND_TOL <= px <= p.x1 + BIND_TOL and abs(radius_at(p, px) - r_point) <= BIND_TOL:
                if abs(rad - dim.value) > BIND_TOL:
                    dim.flags.append(f"R{dim.value:g} ≠ drawn R{rad:g}")
                return ("fillet_at", fx), f"transition at x {fx:g}"
        for s in part.sections:
            if s.kind == "arc" and s.x0 - BIND_TOL <= px <= s.x1 + BIND_TOL and abs(s.r_at(px) - r_point) <= BIND_TOL:
                if abs(s.prim.arc[2] - dim.value) > BIND_TOL:
                    dim.flags.append(f"R{dim.value:g} ≠ drawn arc R{s.prim.arc[2]:g}")
                return ("section", s.x0, s.x1), f"section {s.index} (arc)"
        return None, "its point is on no arc"
    return None, f"kind {dim.kind} not handled"


# --- reading a file ---------------------------------------------------------------------------------------

@dataclass
class DxfReading:
    parts: list  # Part, in the order of the centre lines in the file
    dims: list  # Dim, in file order; dim.part is None when no part covers it
    unassigned: list = field(default_factory=list)  # contour entities that belong to no part

    def dims_of(self, part):
        return [d for d in self.dims if d.part is part]


def read_dxf(path) -> DxfReading:
    """Read a KOMPAS DXF: its parts with their outer profiles, and every dimension bound where possible.

    Raises DxfReadError when there is no profile to build (not a DXF, no centre line, no symmetric contour,
    a gap in the contour).
    """
    doc, contour, _thin, axes, raw_dims = load(path)
    if not contour:
        raise DxfReadError(f"No contour lines ({CONTOUR}) in the file. Only KOMPAS-3D DXF files are supported.")
    if not axes:
        raise DxfReadError(f"No centre line ({AXIS}) in the file: the axis of the part cannot be found.")
    parts, unassigned = find_parts(contour, axes)
    if not parts:
        raise DxfReadError("No turned part found: no contour is symmetric about a horizontal centre line.")
    for part in parts:
        outer_profile(part)
    dims = read_dims(doc, raw_dims)
    for dim in dims:
        dim.part = owner(parts, dim)
        if dim.part is None:
            dim.binding = "no part"
            continue
        dim.target, dim.binding = bind(dim.part, dim)
    return DxfReading(parts, dims, unassigned)
