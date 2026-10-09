"""The part's outer profile from the part zero: Z0 on the free end face, X0 on the axis, Z negative towards the
chuck. Built from the confirmed rows of a DXF job (in their order along the axis), never from a drawing directly.

Two radii along z:
- turned(z): what the turning passes leave (finishing contour): a groove is bridged at the diameter it is cut
  from, a thread is turned to its reduced major diameter (d − 0.1·P, as the planner turns it), chamfers included;
- final(z): the smallest radius any tool may reach at z: a groove's bottom, a thread's root (its turned Ø − 2·h).
At a step both sides meet at one z: turned and final take the smaller radius there (a tool may touch the face).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..planner import METRIC_THREAD_DEPTH_FACTOR, THREAD_MAJOR_REDUCTION, hex_corners

SECTION_TYPES = ("od_turn", "hex", "taper", "arc", "groove")
EPS = 1e-6


@dataclass(frozen=True)
class FeatureData:
    id: int
    type: str
    diameter: float | None = None
    start_diameter: float | None = None
    length: float | None = None
    pitch: float | None = None
    radius: float | None = None
    face: str | None = None  # chamfer: "left" / "right" on the drawing
    location: str | None = None  # chamfer / thread: "external" / "internal"
    across_flats: float | None = None


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
    pitch: float | None = None  # an external thread on this section
    thread_feature_id: int | None = None
    groove_from: float | None = None  # groove: the Ø it is cut from
    fillet: bool = False  # a transition radius at one of its ends (not modelled)

    @property
    def turned_d(self) -> float:
        """The Ø the turning passes leave on a cylinder (a thread's reduced major Ø)."""
        if self.pitch:
            return round(self.d_free - THREAD_MAJOR_REDUCTION * self.pitch, 3)
        return self.d_free


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
            sections.append(Section(len(sections), f.type, f.id, f.length, d_left, d_right if d_right is not None
                                    else 0.0, groove_from=f.start_diameter if f.type == "groove" else None))
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
            sections[-1].fillet = True
            warnings.append(f"transition R{f.radius:g} (row {f.id}): not in the profile (cut as a sharp corner)"
                            if f.radius else f"transition (row {f.id}): not in the profile")
    if free_end == "right":
        sections.reverse()
        for s in sections:
            s.d_free, s.d_chuck = s.d_chuck, s.d_free
            s.chamfer_free, s.chamfer_chuck = s.chamfer_chuck, s.chamfer_free
    z = 0.0
    for i, s in enumerate(sections):
        s.index, s.z_free, s.z_chuck = i, z, round(z - s.length, 6)
        z = s.z_chuck
    profile = Profile(sections, round(-z, 6), warnings)
    profile.turned_points = _points(sections, final=False)
    profile.final_points = _points(sections, final=True)
    return profile


def _points(sections: list[Section], final: bool) -> list[tuple[float, float]]:
    points: list[tuple[float, float]] = []
    for s in sections:
        if s.kind == "groove":
            r0 = r1 = (s.d_free / 2) if final else (s.groove_from or s.d_free) / 2
        elif s.kind in ("taper", "arc"):
            r0, r1 = s.d_free / 2, s.d_chuck / 2
            if s.kind == "arc":  # not modelled: the larger end along it (no tool is sent below it)
                r0 = r1 = max(r0, r1)
        else:
            r0 = r1 = s.turned_d / 2
        c0, c1 = (s.chamfer_free, s.chamfer_chuck) if s.kind in ("od_turn", "hex") else (0.0, 0.0)
        own = []
        if c0:
            own += [(s.z_free, r0 - c0), (s.z_free - c0, r0)]
        else:
            own.append((s.z_free, r0))
        if c1:
            own += [(s.z_chuck + c1, r1), (s.z_chuck, r1 - c1)]
        else:
            own.append((s.z_chuck, r1))
        if final and s.pitch:  # the thread's minor radius, and below it where a chamfer goes lower
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
