"""Where the centre of a turning insert's nose circle goes, for the simulation (gcode/simulate.py).

Coordinates are (z, r): r is the radius (X / 2). Without nose radius compensation the control puts the imaginary tip
on the programmed point, so the centre is the tip moved by rε against the tip direction T (Fanuc's numbers, seen
with Z to the right and X up: T3 = the tip below and towards the chuck, an outside turning tool; T2 = above and
towards the chuck, a boring bar; T1 / T4 the other corners; T0 / T9 the centre itself). T5 to T8 are not simulated.

With compensation (G41 left of the path, G42 right of it) the centre runs rε away from the programmed path:
- a straight move: shifted along its normal; an arc: the concentric arc rε larger or smaller;
- an outer corner: the centre goes round the corner point on an arc of rε; an inner corner: the two offset paths are
  cut at their intersection (no intersection: the nose does not fit, an interference);
- the start-up block (the move with G41 / G42) and the cancel block (G40) are of type A: the start-up runs from the
  centre as the tip put it to the point square to the next move's start; the cancel runs from the point square to
  the last move's end to the centre as the tip puts it at the cancel's end.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

TIP_FROM_CENTRE = {0: (0.0, 0.0), 9: (0.0, 0.0), 1: (1.0, 1.0), 2: (-1.0, 1.0), 3: (-1.0, -1.0), 4: (1.0, -1.0)}
ARC_STEP_DEG = 1.0
EPS = 1e-9


def centre_of_tip(z: float, r: float, radius: float, tip: int) -> tuple[float, float]:
    dz, dr = TIP_FROM_CENTRE[tip]
    return z - dz * radius, r - dr * radius


@dataclass(frozen=True)
class Move:
    """A programmed move from p0 to p1 (z, r); an arc has (centre z, centre r, radius, clockwise)."""
    line: int
    kind: str  # "rapid", "feed", "arc"
    p0: tuple[float, float]
    p1: tuple[float, float]
    arc: tuple[float, float, float, bool] | None = None

    @property
    def length(self) -> float:
        return math.hypot(self.p1[0] - self.p0[0], self.p1[1] - self.p0[1]) if self.arc is None else 1.0

    def _angles(self) -> tuple[float, float]:
        cz, cr, _, clockwise = self.arc
        t0 = math.atan2(self.p0[1] - cr, self.p0[0] - cz)
        t1 = math.atan2(self.p1[1] - cr, self.p1[0] - cz)
        sweep = (t1 - t0) % (2 * math.pi)
        if clockwise:
            sweep -= 2 * math.pi
        return t0, sweep

    def points(self) -> list[tuple[float, float]]:
        """The path's polyline (an arc in chords of 1°, its points on the circle)."""
        if self.arc is None:
            return [self.p0, self.p1]
        cz, cr, radius, _ = self.arc
        t0, sweep = self._angles()
        steps = max(2, math.ceil(abs(math.degrees(sweep)) / ARC_STEP_DEG))
        out = [self.p0]
        for k in range(1, steps):
            t = t0 + sweep * k / steps
            out.append((cz + radius * math.cos(t), cr + radius * math.sin(t)))
        return out + [self.p1]

    def tangent(self, at_end: bool) -> tuple[float, float]:
        if self.arc is None:
            dz, dr = self.p1[0] - self.p0[0], self.p1[1] - self.p0[1]
            length = math.hypot(dz, dr)
            return dz / length, dr / length
        cz, cr, _, clockwise = self.arc
        p = self.p1 if at_end else self.p0
        t = math.atan2(p[1] - cr, p[0] - cz)
        return (math.sin(t), -math.cos(t)) if clockwise else (-math.sin(t), math.cos(t))


def normal(tangent: tuple[float, float], side: str) -> tuple[float, float]:
    """The unit normal on the tool's side: G42 right of the direction of travel, G41 left."""
    tz, tr = tangent
    return (tr, -tz) if side == "G42" else (-tr, tz)


def _offset(move: Move, side: str, radius: float) -> tuple[list[tuple[float, float]] | None, str | None]:
    if move.arc is None:
        nz, nr = normal(move.tangent(False), side)
        return [(z + radius * nz, r + radius * nr) for z, r in move.points()], None
    cz, cr, rho, _ = move.arc
    nz, nr = normal(move.tangent(False), side)
    uz, ur = (move.p0[0] - cz) / rho, (move.p0[1] - cr) / rho
    sign = 1.0 if nz * uz + nr * ur > 0 else -1.0  # the tool outside the circle, or inside it
    new = rho + sign * radius
    if new <= 1e-6:
        return None, f"the arc R{rho:g} is smaller than the nose radius {radius:g} on the tool's side"
    return [(cz + (z - cz) * new / rho, cr + (r - cr) * new / rho) for z, r in move.points()], None


def _cross(a, b) -> float:
    return a[0] * b[1] - a[1] * b[0]


def _intersection(a0, a1, b0, b1):
    """(point, t along a, u along b) of two segments, or None."""
    d1 = (a1[0] - a0[0], a1[1] - a0[1])
    d2 = (b1[0] - b0[0], b1[1] - b0[1])
    den = _cross(d1, d2)
    if abs(den) < 1e-12:
        return None
    w = (b0[0] - a0[0], b0[1] - a0[1])
    t, u = _cross(w, d2) / den, _cross(w, d1) / den
    if -1e-9 <= t <= 1 + 1e-9 and -1e-9 <= u <= 1 + 1e-9:
        return (a0[0] + t * d1[0], a0[1] + t * d1[1]), t, u
    return None


def _trim(a: list, b: list):
    """Cut the end of polyline a and the start of polyline b at their intersection nearest the corner; None when
    they do not meet."""
    for i in range(len(a) - 2, -1, -1):
        for j in range(len(b) - 1):
            hit = _intersection(a[i], a[i + 1], b[j], b[j + 1])
            if hit is not None:
                point = hit[0]
                return a[:i + 1] + [point], [point] + b[j + 1:]
    return None


def _corner_arc(p, n_from, n_to, left_turn: bool, radius: float) -> list[tuple[float, float]]:
    """The centre round an outer corner p, from p + rε·n_from to p + rε·n_to (left turn: counter-clockwise)."""
    a0, a1 = math.atan2(n_from[1], n_from[0]), math.atan2(n_to[1], n_to[0])
    sweep = (a1 - a0) % (2 * math.pi)
    if not left_turn:
        sweep -= 2 * math.pi
    steps = max(1, math.ceil(abs(math.degrees(sweep)) / ARC_STEP_DEG))
    return [(p[0] + radius * math.cos(a0 + sweep * k / steps), p[1] + radius * math.sin(a0 + sweep * k / steps))
            for k in range(1, steps + 1)]


def compensated_paths(moves: list[Move], side: str, radius: float, tip: int, cancelled: bool):
    """The centre's path of each move of one compensated run: moves[0] is the start-up, moves[-1] the cancel (when
    cancelled). ({line: [(z, r), ...] from the centre's position at the move's start}, [(line, error)])."""
    errors: list[tuple[int, str]] = []
    paths: dict[int, list[tuple[float, float]]] = {}
    startup, rest = moves[0], moves[1:]
    cancel = rest.pop() if cancelled and rest else None
    interior = [m for m in rest if m.length > 1e-6]
    for m in rest:
        if m.length <= 1e-6:
            paths[m.line] = []  # no movement: the centre stays
    offsets = []
    for m in interior:
        points, why = _offset(m, side, radius)
        if points is None:
            errors.append((m.line, why + ": the nose does not fit (an interference)"))
            nz, nr = normal(m.tangent(False), side)
            points = [(z + radius * nz, r + radius * nr) for z, r in (m.p0, m.p1)]
        offsets.append(points)
    tails: list[list[tuple[float, float]]] = [[] for _ in interior]  # outer corner arcs after each move
    for k in range(len(interior) - 1):
        a, b = interior[k], interior[k + 1]
        ta, tb = a.tangent(True), b.tangent(False)
        turn = _cross(ta, tb)
        if abs(turn) < 1e-9 and ta[0] * tb[0] + ta[1] * tb[1] > 0:
            continue  # tangent: the offsets meet
        outer = (turn > 0) == (side == "G42") if abs(turn) >= 1e-9 else True
        if outer:
            tails[k] = _corner_arc(a.p1, normal(ta, side), normal(tb, side), side == "G42", radius)
            continue
        trimmed = _trim(offsets[k], offsets[k + 1])
        if trimmed is None:
            errors.append((b.line, f"the nose radius {radius:g} does not fit between this move and the one before "
                                   "(an interference: the control stops or cuts into the part)"))
            continue
        offsets[k], offsets[k + 1] = trimmed
    start = centre_of_tip(*startup.p0, radius, tip)
    if interior:
        paths[startup.line] = [start, offsets[0][0]]
    elif cancel is not None:
        n = normal(cancel.tangent(False), side) if cancel.length > 1e-6 else (0.0, 0.0)
        paths[startup.line] = [start, (startup.p1[0] + radius * n[0], startup.p1[1] + radius * n[1])]
    else:
        paths[startup.line] = [start, centre_of_tip(*startup.p1, radius, tip)]
    for m, points, tail in zip(interior, offsets, tails):
        paths[m.line] = points + tail
    if cancel is not None:
        here = paths[interior[-1].line][-1] if interior else paths[startup.line][-1]
        paths[cancel.line] = [here, centre_of_tip(*cancel.p1, radius, tip)]
    return paths, errors
