"""The part's outer profile from the part zero: Z0 on the free end face, X0 on the axis, Z negative towards the
chuck. Built from the confirmed rows of a DXF job (in their order along the axis), never from a drawing directly.

Each section is a list of segments from its free-end side to its chuck side: lines (cylinders, tapers, chamfers)
and arcs (arc sections and fillets, with their centre). Two radii along z come from them:
- turned(z): what the turning passes leave (finishing contour): a groove is bridged at the diameter it is cut
  from, a thread is turned to its reduced major diameter (d − 0.1·P, as the planner turns it);
- final(z): the smallest radius any tool may reach at z: a groove's bottom, a thread's root (its turned Ø − 2·h).
At a step both sides meet at one z: turned and final take the smaller radius there (a tool may touch the face).
An arc is used only when its shape (convex / concave) is known, the circle through its ends with its radius exists,
it does not turn back along the axis, and its radius is the one drawn (or the operator chose it); otherwise it is
not modelled: the larger end's radius along it, and the section is not programmed.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from ..planner import METRIC_THREAD_DEPTH_FACTOR, THREAD_MAJOR_REDUCTION, hex_corners

SECTION_TYPES = ("od_turn", "hex", "taper", "arc", "groove")
EPS = 1e-6
ARC_STEP_DEG = 1.0  # the polyline of an arc for r(z): a chord error below R·(1 − cos 0.5°) (R 20: 0.8 µm)
RADIUS_TOL = 0.01  # mm: a dimension and the drawn radius agree


@dataclass(frozen=True)
class FeatureData:
    id: int
    type: str
    diameter: float | None = None
    start_diameter: float | None = None
    length: float | None = None
    pitch: float | None = None
    radius: float | None = None
    face: str | None = None  # chamfer / fillet: "left" / "right" on the drawing
    location: str | None = None  # chamfer / thread: "external" / "internal"
    across_flats: float | None = None
    arc_convex: bool | None = None  # arc / fillet: bulging away from the axis
    drawn_radius: float | None = None  # arc from a DXF: the radius drawn (it may differ from the dimension)


@dataclass(frozen=True)
class Arc:
    cz: float
    cr: float
    radius: float
    convex: bool  # bulging away from the axis: the centre is on the axis side

    def r_at(self, z: float) -> float:
        root = math.sqrt(max(self.radius ** 2 - (z - self.cz) ** 2, 0.0))
        return self.cr + root if self.convex else self.cr - root


@dataclass(frozen=True)
class Segment:
    """From (z0, r0) to (z1, r1), z0 ≥ z1 (towards the chuck); a straight line, or an arc about its centre."""
    z0: float
    r0: float
    z1: float
    r1: float
    arc: Arc | None = None

    def points(self) -> list[tuple[float, float]]:
        """Its polyline (an arc in steps of ARC_STEP_DEG), both ends included."""
        if self.arc is None:
            return [(self.z0, self.r0), (self.z1, self.r1)]
        a = self.arc
        t0 = math.atan2(self.r0 - a.cr, self.z0 - a.cz)
        t1 = math.atan2(self.r1 - a.cr, self.z1 - a.cz)
        sweep = (t1 - t0 + math.pi) % (2 * math.pi) - math.pi  # the short way round
        n = max(2, math.ceil(abs(math.degrees(sweep)) / ARC_STEP_DEG))
        out = [(self.z0, self.r0)]
        for k in range(1, n):
            t = t0 + sweep * k / n
            out.append((a.cz + a.radius * math.cos(t), a.cr + a.radius * math.sin(t)))
        return out + [(self.z1, self.r1)]


def arc_through(p0, p1, radius, convex) -> tuple[Arc | None, str | None]:
    """The arc from p0 to p1 (z, r) with this radius, bulging away from the axis (convex) or into it; (None, why)
    when there is no such circle or the arc turns back along the axis (its z not monotonic)."""
    (z0, r0), (z1, r1) = p0, p1
    dz, dr = z1 - z0, r1 - r0
    chord = math.hypot(dz, dr)
    if chord < EPS:
        return None, "its ends coincide"
    half = chord / 2
    if half > radius + 1e-6:
        return None, f"no circle R{radius:g} through its ends (the chord is {chord:.3f} mm)"
    d = math.sqrt(max(radius ** 2 - half ** 2, 0.0))
    nz, nr = -dr / chord, dz / chord  # a normal of the chord
    if (nr < 0) != convex:  # convex: the centre on the axis side (lower r); concave: away from it
        nz, nr = -nz, -nr
    arc = Arc((z0 + z1) / 2 + d * nz, (r0 + r1) / 2 + d * nr, radius, convex)
    # the arc must not turn back along the axis: no point of it beyond its ends in z
    t0, t1 = math.atan2(r0 - arc.cr, z0 - arc.cz), math.atan2(r1 - arc.cr, z1 - arc.cz)
    sweep = (t1 - t0 + math.pi) % (2 * math.pi) - math.pi
    for extreme in (0.0, math.pi, -math.pi):
        inside = (extreme - t0) % (2 * math.pi) if sweep > 0 else (t0 - extreme) % (2 * math.pi)
        if 1e-6 < inside < abs(sweep) - 1e-6:
            return None, "the arc turns back along the axis"
    return arc, None


@dataclass
class Section:
    index: int  # from the free end
    kind: str
    feature_id: int
    length: float
    d_free: float  # Ø at its free-end side (the drawing's Ø; a taper / arc: its end there)
    d_chuck: float  # Ø at its chuck side
    z_free: float = 0.0
    z_chuck: float = 0.0
    chamfer_free: float = 0.0  # 45° chamfer leg on the free-end side
    chamfer_chuck: float = 0.0
    fillet_free: tuple | None = None  # (radius, convex) at the free-end side
    fillet_chuck: tuple | None = None
    pitch: float | None = None  # an external thread on this section
    thread_feature_id: int | None = None
    groove_from: float | None = None  # groove: the Ø it is cut from
    radius: float | None = None  # arc: its radius
    convex: bool | None = None  # arc: its shape
    arc: Arc | None = None  # arc: the circle, when it can be programmed
    arc_problem: str | None = None  # arc: why it cannot
    fillet: bool = False  # a transition radius not modelled

    @property
    def turned_d(self) -> float:
        """The Ø the turning passes leave on a cylinder (a thread's reduced major Ø)."""
        if self.pitch:
            return round(self.d_free - THREAD_MAJOR_REDUCTION * self.pitch, 3)
        return self.d_free

    @property
    def modelled(self) -> bool:
        return self.kind != "arc" or self.arc is not None

    def segments(self, final: bool = False) -> list[Segment]:
        """Free-end side to chuck side. final: a groove at its bottom (a thread's root is clamped by the caller)."""
        zf, zc = self.z_free, self.z_chuck
        if self.kind == "groove":
            r = self.d_free / 2 if final else (self.groove_from or self.d_free) / 2
            return [Segment(zf, r, zc, r)]
        if self.kind == "taper":
            return [Segment(zf, self.d_free / 2, zc, self.d_chuck / 2)]
        if self.kind == "arc":
            if self.arc is None:  # not modelled: the larger end along it (no tool is sent below it)
                r = max(self.d_free, self.d_chuck) / 2
                return [Segment(zf, r, zc, r)]
            return [Segment(zf, self.d_free / 2, zc, self.d_chuck / 2, self.arc)]
        r = self.turned_d / 2
        out, z_start, z_end = [], zf, zc
        if self.chamfer_free:
            out.append(Segment(zf, r - self.chamfer_free, zf - self.chamfer_free, r))
            z_start = zf - self.chamfer_free
        elif self.fillet_free:
            radius, convex = self.fillet_free
            centre = Arc(zf - radius, r - radius if convex else r + radius, radius, convex)
            out.append(Segment(zf, r - radius if convex else r + radius, zf - radius, r, centre))
            z_start = zf - radius
        tail = []
        if self.chamfer_chuck:
            tail.append(Segment(zc + self.chamfer_chuck, r, zc, r - self.chamfer_chuck))
            z_end = zc + self.chamfer_chuck
        elif self.fillet_chuck:
            radius, convex = self.fillet_chuck
            centre = Arc(zc + radius, r - radius if convex else r + radius, radius, convex)
            tail.append(Segment(zc + radius, r, zc, r - radius if convex else r + radius, centre))
            z_end = zc + radius
        if not out:
            out.append(Segment(zf, r, z_end, r) if z_end < zf - EPS else Segment(zf, r, zf, r))
        elif z_end < z_start - EPS:
            out.append(Segment(z_start, r, z_end, r))
        return [s for s in out + tail if abs(s.z0 - s.z1) > EPS or abs(s.r0 - s.r1) > EPS] or [Segment(zf, r, zc, r)]


@dataclass
class Profile:
    sections: list[Section]
    length: float
    warnings: list[str] = field(default_factory=list)
    turned_points: list[tuple[float, float]] = field(default_factory=list)  # (z, r), z falling
    final_points: list[tuple[float, float]] = field(default_factory=list)

    def section_of(self, feature_id: int) -> Section | None:
        return next((s for s in self.sections if feature_id in (s.feature_id, s.thread_feature_id)), None)

    def turned(self, z: float) -> float:
        return _radius_at(self.turned_points, z)

    def final(self, z: float) -> float:
        return _radius_at(self.final_points, z)

    @property
    def max_radius(self) -> float:
        return max(r for _, r in self.turned_points)


def _radius_at(points, z: float) -> float:
    """The radius of a polyline at z; at a step (two points at one z) the smaller one; 0 outside the part."""
    if not points or z > points[0][0] + EPS or z < points[-1][0] - EPS:
        return 0.0
    found = []
    for (z0, r0), (z1, r1) in zip(points, points[1:]):
        if abs(z0 - z1) < EPS:
            if abs(z - z0) < EPS:
                found += [r0, r1]
            continue
        if z1 - EPS <= z <= z0 + EPS:
            found.append(r0 + (r1 - r0) * (z - z0) / (z1 - z0))
    return min(found) if found else 0.0


def build(features: list[FeatureData], free_end: str) -> Profile:
    """The profile from the rows in their drawing order (left to right) and the free end ("left" / "right")."""
    sections: list[Section] = []
    warnings = []
    pending_fillets = []  # (section, side on the drawing, radius, convex)
    for f in features:
        if f.type in SECTION_TYPES:
            d_left = f.start_diameter if f.type in ("taper", "arc") else (
                hex_corners(f) if f.type == "hex" else f.diameter)
            d_right = f.diameter if f.type != "hex" else hex_corners(f)
            if f.type == "groove":
                d_left = d_right = f.diameter
            if not f.length or not d_left or (d_right is None and f.type != "arc"):
                warnings.append(f"{f.type} (row {f.id}): no length or diameter: the profile stops before it")
                break
            section = Section(len(sections), f.type, f.id, f.length, d_left, d_right if d_right is not None else 0.0,
                              groove_from=f.start_diameter if f.type == "groove" else None)
            if f.type == "arc":
                section.radius, section.convex = f.radius, f.arc_convex
                if f.drawn_radius is not None and f.radius is not None \
                        and abs(f.drawn_radius - f.radius) > RADIUS_TOL:
                    section.arc_problem = (f"drawn R{f.drawn_radius:g} ≠ dimensioned R{f.radius:g}: choose the "
                                           "radius on the job page")
            sections.append(section)
        elif f.type == "thread" and (f.location or "external") == "external" and sections:
            host = sections[-1]
            if host.kind == "od_turn" and f.diameter and abs(host.d_free - f.diameter) < 0.01:
                host.pitch, host.thread_feature_id = f.pitch, f.id
        elif f.type == "chamfer" and (f.location or "external") == "external" and sections and f.length:
            host = sections[-1]
            if f.face == "left":
                host.chamfer_free = f.length  # mapped below when the free end is the right one
            elif f.face == "right":
                host.chamfer_chuck = f.length
            else:
                warnings.append(f"chamfer {f.length:g} (row {f.id}) without its face: not in the profile")
        elif f.type == "fillet" and sections:
            host = sections[-1]
            if f.radius and f.face in ("left", "right") and f.arc_convex is not None \
                    and host.kind in ("od_turn", "hex"):
                pending_fillets.append((host, f.face, f.radius, f.arc_convex))
            else:
                host.fillet = True
                warnings.append(f"transition R{f.radius:g} (row {f.id}): its side or shape is not known, not in the "
                                "profile (cut as a sharp corner)" if f.radius
                                else f"transition (row {f.id}): not in the profile")
    for host, side, radius, convex in pending_fillets:
        if side == "left":
            host.fillet_free = (radius, convex)  # mapped below when the free end is the right one
        else:
            host.fillet_chuck = (radius, convex)
    if free_end == "right":
        sections.reverse()
        for s in sections:
            s.d_free, s.d_chuck = s.d_chuck, s.d_free
            s.chamfer_free, s.chamfer_chuck = s.chamfer_chuck, s.chamfer_free
            s.fillet_free, s.fillet_chuck = s.fillet_chuck, s.fillet_free
    z = 0.0
    for i, s in enumerate(sections):
        s.index, s.z_free, s.z_chuck = i, z, round(z - s.length, 6)
        z = s.z_chuck
    for s in sections:
        if s.kind != "arc":
            continue
        if s.arc_problem is None and (s.radius is None or s.convex is None):
            s.arc_problem = "its radius or its shape (convex / concave) is not known"
        if s.arc_problem is None:
            s.arc, s.arc_problem = arc_through((s.z_free, s.d_free / 2), (s.z_chuck, s.d_chuck / 2), s.radius,
                                               s.convex)
        if s.arc_problem:
            warnings.append(f"arc R{s.radius:g} (row {s.feature_id}): {s.arc_problem}: not programmed"
                            if s.radius else f"arc (row {s.feature_id}): {s.arc_problem}: not programmed")
    for i, s in enumerate(sections):  # a groove is bridged at the smaller of its neighbours as they are turned
        if s.kind == "groove":
            sides = [_turned_end(sections[i - 1], chuck_side=True)] if i > 0 else []
            sides += [_turned_end(sections[i + 1], chuck_side=False)] if i + 1 < len(sections) else []
            s.groove_from = min([d for d in [s.groove_from] + sides if d] or [s.d_free])
    profile = Profile(sections, round(-z, 6), warnings)
    profile.turned_points = _points(sections, final=False)
    profile.final_points = _points(sections, final=True)
    return profile


def _turned_end(section: Section, chuck_side: bool) -> float | None:
    """The Ø a neighbour is turned to at the end next to a groove (a thread: its reduced major Ø)."""
    if section.kind in ("od_turn", "hex"):
        return section.turned_d
    if section.kind == "taper" or (section.kind == "arc" and section.arc is not None):
        return section.d_chuck if chuck_side else section.d_free
    return None


def _points(sections: list[Section], final: bool) -> list[tuple[float, float]]:
    points: list[tuple[float, float]] = []
    for s in sections:
        own: list[tuple[float, float]] = []
        for segment in s.segments(final):
            pts = segment.points()
            own += pts if not own else pts[1:] if own[-1] == pts[0] else pts
        if final and s.pitch:  # the thread's root, and below it where a chamfer goes lower
            own = _clamp(own, s.turned_d / 2 - METRIC_THREAD_DEPTH_FACTOR * s.pitch)
        points += own
    return points


def _clamp(points, top: float) -> list[tuple[float, float]]:
    """The polyline cut off at radius top (the lower of the two along it), with the crossing points added."""
    out = []
    for (z0, r0), (z1, r1) in zip(points, points[1:]):
        out.append((z0, min(r0, top)))
        if (r0 - top) * (r1 - top) < 0:
            out.append((z0 + (z1 - z0) * (top - r0) / (r1 - r0), top))
    out.append((points[-1][0], min(points[-1][1], top)))
    return out
