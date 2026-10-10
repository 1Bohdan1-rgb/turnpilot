"""The simulation of a printed program: it reads the text (gcode/fanuc.py), not the generator's commands, and
checks what the machine would do. The stock is a radius per z on a 0.01 mm grid, from the stock beyond Z0 to the
jaws. A turning or facing insert whose nose radius rε and tip direction T are known is its nose circle (gcode/nose.py:
the tip moved by T without compensation, the offset path with G41 / G42); the other tools are their imaginary tip
point (grooving and parting inserts: their width).

Errors (the program is not ready to run): a turning cut deeper than the tool's ap max, a turning cut down towards
the chuck steeper than the insert's max in-copying angle (or with that angle not known), a rapid through material (both orders of the axes, as a Fanuc control
may move them separately), a cut below the finished profile, a tool point at the jaws, X below the axis (beyond the
facing overshoot), G96 while drilling, a speed above G50 or the machine's max, G96 without G50, a spindle direction
other than the operator's, G92 outside G97, n·P above the machine's threading limit, a tool change away from the
reference point, a feed with the spindle stopped or without F, a wrong program frame.
Nose radius compensation errors: G41 / G42 or G40 on an arc or without a move, the side changed without G40, a tool
change, G28, G92 or the program's end under compensation, two blocks without a move under it, a tool without rε and
T, an arc or a corner the nose does not fit (an interference), the nose centre below the axis under compensation;
a tip direction the simulation does not know (T5 to T8).
Warnings: parting to the axis in G96, n·P unknown, speeds below the min, an inner corner left with the nose radius.
Material left on the part makes the program incomplete ("part not complete"), apart from the warnings.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from . import fanuc
from .nose import TIP_FROM_CENTRE, Move, centre_of_tip, compensated_paths
from .profile import Profile

DZ = 0.01  # mm, the stock grid
TOL = 0.002  # mm
HOME = (1e6, 1e6)  # the reference point: far beyond the part in +X and +Z
NOSE_TYPES = ("facing", "turning_rough", "turning_finish")  # simulated as a nose circle when rε and T are known


@dataclass(frozen=True)
class ToolInfo:
    tool_type: str
    edge: float | None = None  # the insert's cutting edge length along Z for a radial feed (its ap_max)
    width: float | None = None  # grooving / parting insert width
    max_ramp: float | None = None  # turning: the max in-copying angle (degrees) going down towards the chuck
    nose_radius: float | None = None  # turning: rε
    tip_direction: int | None = None  # turning: the imaginary tip's number T (0-9)


@dataclass(frozen=True)
class SimInput:
    profile: Profile
    stock_radius: float
    face_stock: float
    stickout: float
    chuck_safety: float
    max_rpm: int
    spindle: str  # the operator's direction for a right-hand tool
    tools: dict  # turret position -> ToolInfo
    groove_reference: str | None = None
    facing_overshoot: float = 0.0
    parting_overshoot: float = 0.0
    min_rpm: int | None = None
    max_thread_feed: float | None = None


@dataclass
class Segment:
    kind: str  # "rapid", "feed", "thread", "arc"
    x0: float
    z0: float
    x1: float
    z1: float
    line: int
    tool: int | None
    arc: tuple | None = None  # (centre z, centre radius, radius, clockwise) of an "arc"


@dataclass
class SimResult:
    segments: list[Segment] = field(default_factory=list)
    errors: list[tuple[int, str]] = field(default_factory=list)  # (line, message)
    warnings: list[tuple[int, str]] = field(default_factory=list)
    leftover: list[tuple[float, float, float]] = field(default_factory=list)  # (z from, z to, mm per side)
    incomplete: list[str] = field(default_factory=list)  # the part is not finished by this program: why
    final_stock: list[float] = field(default_factory=list)
    z_top: float = 0.0

    @property
    def ok(self) -> bool:
        return not self.errors

    @property
    def complete(self) -> bool:
        """The program machines the whole outer profile (no material left on the part)."""
        return not self.incomplete


class _Sim:
    def __init__(self, data: SimInput):
        self.d = data
        self.z_top = data.face_stock
        self.z_jaws = -data.stickout
        count = int(round((self.z_top - self.z_jaws) / DZ)) + 1
        self.stock = [data.stock_radius] * count
        self.final = [data.profile.final(self.z(i)) for i in range(count)]
        self.hole = 0.0  # the drilled depth on the axis (z of the bottom)
        self.r = SimResult(z_top=self.z_top)
        self.pos = HOME
        self.tool: int | None = None
        self.homed_x = self.homed_z = False
        self.motion = None
        self.css = None  # G96 S
        self.rpm = None  # G97 S
        self.limit = None  # G50 S of the current tool
        self.spindle_on = None  # "M03" / "M04"
        self.feed = None
        self.reported = set()
        self.comp_paths: dict[int, list] = {}  # line -> the nose centre's path under compensation
        self.comp_lines: set[int] = set()  # lines whose moves are compensated (start-up and cancel included)
        self.nose_used: set[float] = set()  # rε of the nose circles that cut

    # grid
    def z(self, i: int) -> float:
        return self.z_top - i * DZ

    def index(self, z: float) -> int | None:
        i = int(round((self.z_top - z) / DZ))
        return i if 0 <= i < len(self.stock) else None

    def indices(self, z0: float, z1: float) -> range:
        lo, hi = min(z0, z1), max(z1, z0)
        first = max(0, int(math.ceil((self.z_top - hi) / DZ - 1e-9)))
        last = min(len(self.stock) - 1, int(math.floor((self.z_top - lo) / DZ + 1e-9)))
        return range(first, last + 1)

    def error(self, line: int, message: str) -> None:
        if (line, message) not in self.reported:
            self.reported.add((line, message))
            self.r.errors.append((line, message))

    def warning(self, line: int, message: str) -> None:
        if (line, message) not in self.reported:
            self.reported.add((line, message))
            self.r.warnings.append((line, message))

    @property
    def info(self) -> ToolInfo | None:
        return self.d.tools.get(self.tool)

    @property
    def nose(self) -> tuple[float, int] | None:
        """(rε, T) when the current tool is simulated as its nose circle."""
        info = self.info
        if info and info.tool_type in NOSE_TYPES and info.nose_radius and info.tip_direction in TIP_FROM_CENTRE:
            return info.nose_radius, info.tip_direction
        return None

    def footprint(self, z: float) -> tuple[float, float]:
        """The z range the tool cuts at its programmed point (an insert's width for grooving / parting)."""
        info = self.info
        if info and info.tool_type in ("grooving", "parting") and info.width:
            if self.d.groove_reference == "toward chuck":
                return z, z + info.width
            return z - info.width, z
        return z, z

    # moves
    def _path_points(self, x0, z0, x1, z1):
        """(z, x) samples of a straight move: every grid z it crosses, or its two ends for a radial move."""
        if abs(z1 - z0) < 1e-9:
            return [(z0, x0), (z0, x1)]
        out = []
        for i in self.indices(z0, z1):
            z = self.z(i)
            t = (z - z0) / (z1 - z0)
            out.append((z, x0 + (x1 - x0) * t))
        return out

    def _stock_above(self, z: float, r: float, radial_to: float | None = None) -> bool:
        """Material at z above r (a radial move: between r and radial_to)."""
        lo, hi = self.footprint(z)
        for i in self.indices(lo + TOL, hi - TOL) if hi - lo > 2 * TOL else [self.index(z)]:
            if i is None:
                continue
            if self.stock[i] > r + TOL:
                return True
        return False

    def _rapid_clear(self, x0, z0, x1, z1) -> bool:
        """A straight rapid from (x0, z0) to (x1, z1) stays out of the material (the drill in its own hole)."""
        drilling = self.info and self.info.tool_type == "drilling"
        if abs(z1 - z0) < 1e-9:  # radial: the lower end matters
            r = min(x0, x1) / 2
            if drilling and abs(r) < TOL and z0 >= self.hole - TOL:
                return True
            return z0 > self.z_top + TOL or not self._stock_above(z0, max(r, 0.0))
        for z, x in self._path_points(x0, z0, x1, z1):
            r = x / 2
            if drilling and abs(r) < TOL and z >= self.hole - TOL:
                continue
            if self._stock_above(z, max(r, 0.0)):
                return False
        return True

    def rapid(self, line: int, x: float, z: float) -> None:
        if self.nose:
            self._nose_rapid(line, x, z)
            return
        x0, z0 = self.pos
        legs = [((x0, z0), (x, z))]
        if abs(x - x0) > 1e-9 and abs(z - z0) > 1e-9:  # the axes may move one after the other
            legs = [((x0, z0), (x, z0), (x, z)), ((x0, z0), (x0, z), (x, z)), ((x0, z0), (x, z))]
        else:
            legs = [((x0, z0), (x, z))]
        for path in legs:
            for (ax, az), (bx, bz) in zip(path, path[1:]):
                ax, bx = min(ax, 2e5), min(bx, 2e5)
                az, bz = min(az, self.z_top + 50), min(bz, self.z_top + 50)
                if not self._rapid_clear(ax, az, bx, bz):
                    self.error(line, "G00 through material" + (" (if the axes move one after the other)"
                                                               if len(path) == 3 else ""))
        self.r.segments.append(Segment("rapid", min(x0, 2e5), min(z0, self.z_top + 50), x, z, line, self.tool))
        self.check_bounds(line, x, z)
        self.pos = (x, z)

    ARC_RADIUS_TOL = 0.002  # mm: the end of an arc off its circle by more than this is an error (a Fanuc alarm too)

    def arc_centre(self, x0, z0, x, z, words: dict, clockwise: bool):
        """(centre z, centre r, radius, None) of G02 / G03 from I (radius) / K, or from R (the short arc); (None, None,
        None, why) when there is none. An end off the circle is reported as its own error by the caller."""
        r0, r1 = x0 / 2, x / 2
        if "I" in words or "K" in words:
            cr, cz = r0 + words.get("I", 0.0), z0 + words.get("K", 0.0)
            return cz, cr, math.hypot(r0 - cr, z0 - cz), None
        if "R" in words:
            radius = words["R"]
            half = math.hypot(r1 - r0, z - z0) / 2
            if half > radius + self.ARC_RADIUS_TOL:
                return None, None, None, f"no arc R{radius:g} between its ends"
            d = math.sqrt(max(radius ** 2 - half ** 2, 0.0))
            mz, mr = (z0 + z) / 2, (r0 + r1) / 2
            nz, nr = -(r1 - r0) / (2 * half), (z - z0) / (2 * half)  # a normal of the chord (left of its direction)
            sign = -1 if clockwise else 1  # the short arc's centre: right of the chord for G02, left for G03
            return mz + sign * d * nz, mr + sign * d * nr, radius, None
        return None, None, None, "an arc without I / K or R"

    def arc(self, line: int, x: float, z: float, words: dict, clockwise: bool) -> None:
        """G02 / G03: cut in chords of 1°."""
        x0, z0 = self.pos
        r0, r1 = x0 / 2, x / 2
        cz, cr, radius, why = self.arc_centre(x0, z0, x, z, words, clockwise)
        if why:
            self.error(line, why)
            self.pos = (x, z)
            return
        end = math.hypot(r1 - cr, z - cz)
        if ("I" in words or "K" in words) and abs(end - radius) > self.ARC_RADIUS_TOL:
            self.error(line, f"the arc's end is off its circle: R{radius:.3f} at the start, R{end:.3f} at the end")
        t0, t1 = math.atan2(r0 - cr, z0 - cz), math.atan2(r1 - cr, z - cz)
        sweep = (t1 - t0) % (2 * math.pi)
        if clockwise:
            sweep -= 2 * math.pi
        steps = max(2, math.ceil(abs(math.degrees(sweep))))
        tips = [(z0, r0)]
        for k in range(1, steps + 1):
            t = t0 + sweep * k / steps
            tips.append((cz + radius * math.cos(t), cr + radius * math.sin(t)) if k < steps else (z, r1))
        if self.nose:
            self._nose_cut(line, x, z, tips, "arc")
        else:
            for zz, rr in tips[1:]:
                self.cut(line, 2 * rr, zz, record=False)
        self.r.segments.append(Segment("arc", x0, z0, x, z, line, self.tool, (cz, cr, radius, clockwise)))

    def cut(self, line: int, x: float, z: float, kind: str = "feed", record: bool = True) -> None:
        x0, z0 = self.pos
        info = self.info
        if self.nose and kind == "feed" and record:
            self._nose_cut(line, x, z, [(z0, x0 / 2), (z, x / 2)], kind)
            return
        if self.spindle_on is None:
            self.error(line, "a cutting move with the spindle stopped")
        if kind == "feed" and self.feed is None:
            self.error(line, "G01 without a feed (F)")
        if x0 >= HOME[0] or z0 >= HOME[1]:
            self.error(line, "a cutting move straight from the reference point")
        self.check_bounds(line, x, z)
        drilling = info and info.tool_type == "drilling"
        if drilling:
            if abs(x) > TOL or abs(x0) > TOL:
                self.error(line, "the drill is off the axis")
            self.hole = min(self.hole, z)
        else:
            points = self._path_points(x0, z0, x, z)
            excess = []  # (z, material above the tip) along a move towards the chuck
            for k, (zz, xx) in enumerate(points):
                r = max(xx, 0.0) / 2
                i = self.index(zz)
                if z < z0 - 1e-9 and i is not None and k < len(points) - 1:
                    excess.append((zz, self.stock[i] - r))
                self._remove(line, zz, r, radial=abs(x - x0) > 1e-9)
            self._check_depth(line, excess, x0, z0, x, z)
            self._check_ramp(line, excess, x0, z0, x, z)
        if record:
            self.r.segments.append(Segment(kind, x0, z0, x, z, line, self.tool))
        self.pos = (x, z)

    # the nose circle --------------------------------------------------------------------------------------
    def _centre_path(self, line: int, tips: list[tuple[float, float]]) -> list[tuple[float, float]]:
        """The nose centre's polyline (z, r) for a move: the compensated path, else the tip's points moved by T."""
        if line in self.comp_paths:
            return self.comp_paths[line]
        radius, tip = self.nose
        return [centre_of_tip(z, r, radius, tip) for z, r in tips]

    def _samples(self, path: list[tuple[float, float]], dense: bool = True) -> list[tuple[float, float]]:
        """The centre along its path: every DZ of its length (a cut: no scallops between the circles), or (a rapid's
        clearance) at every grid z it crosses and at the ends of each straight piece."""
        out = []
        for (za, ra), (zb, rb) in zip(path, path[1:]):
            out.append((za, ra))
            if dense:
                steps = min(100000, math.ceil(math.hypot(zb - za, rb - ra) / DZ))
                out += [(za + (zb - za) * k / steps, ra + (rb - ra) * k / steps) for k in range(1, steps)]
            elif abs(zb - za) > 1e-9:
                for i in self.indices(za, zb):
                    zz = self.z(i)
                    out.append((zz, ra + (rb - ra) * (zz - za) / (zb - za)))
        if path:
            out.append(path[-1])
        return out

    def _disc(self, cz: float, cr: float, radius: float):
        """(cell, the circle's lowest radius in it) over the cells the circle covers."""
        for i in self.indices(cz - radius, cz + radius):
            dz = self.z(i) - cz
            yield i, cr - math.sqrt(max(radius * radius - dz * dz, 0.0))

    def _nose_bounds(self, line: int, path: list[tuple[float, float]], radius: float) -> None:
        lo = min(z for z, _ in path) - radius
        if lo < self.z_jaws + self.d.chuck_safety - TOL:
            self.error(line, f"closer than {self.d.chuck_safety:g} mm to the jaws (Z{fanuc.number(lo)}, jaws at "
                             f"Z{fanuc.number(self.z_jaws)})")
        low = min(r for _, r in path)
        if line in self.comp_lines and low < -TOL:
            self.error(line, f"the nose centre goes below the axis (X{fanuc.number(2 * low)}) under nose radius "
                             "compensation")

    def _nose_rapid(self, line: int, x: float, z: float) -> None:
        radius, _ = self.nose
        x0, z0 = self.pos
        x0, z0, xc, zc = min(x0, 2e5), min(z0, self.z_top + 50), min(x, 2e5), min(z, self.z_top + 50)
        path = self._centre_path(line, [(z0, x0 / 2), (zc, xc / 2)])
        paths = [path]
        if line not in self.comp_paths and abs(xc - x0) > 1e-9 and abs(zc - z0) > 1e-9:
            (za, ra), (zb, rb) = path[0], path[-1]
            paths = [[(za, ra), (za, rb), (zb, rb)], [(za, ra), (zb, ra), (zb, rb)], path]
        for legs in paths:
            for zz, rr in self._samples(legs, dense=False):
                if any(self.stock[i] > low + TOL for i, low in self._disc(zz, rr, radius)):
                    self.error(line, "G00 through material" + (" (if the axes move one after the other)"
                                                               if len(legs) == 3 else ""))
                    break
        if path:
            self._nose_bounds(line, path, radius)
        self.check_bounds(line, x, z, jaws=False)
        self.r.segments.append(Segment("rapid", x0, z0, x, z, line, self.tool))
        self.pos = (x, z)

    def _nose_cut(self, line: int, x: float, z: float, tips: list[tuple[float, float]], kind: str) -> None:
        """A feed or an arc with the nose circle: it takes the material inside the circle along the centre's path;
        inside the finished profile it is an error. The depth and the ramp are checked at the circle's lowest point
        against the stock as it was before the move."""
        radius, _ = self.nose
        x0, z0 = self.pos
        info = self.info
        if self.spindle_on is None:
            self.error(line, "a cutting move with the spindle stopped")
        if kind == "feed" and self.feed is None:
            self.error(line, "G01 without a feed (F)")
        if x0 >= HOME[0] or z0 >= HOME[1]:
            self.error(line, "a cutting move straight from the reference point")
        path = self._centre_path(line, tips)
        if path:
            self._nose_bounds(line, path, radius)
        self.check_bounds(line, x, z, jaws=False)
        self.nose_used.add(radius)
        samples = self._samples(path)
        before = list(self.stock)
        excess = []
        if z < z0 - 1e-9:
            for zz, rr in samples[:-1]:
                i = self.index(zz)
                if i is not None:
                    excess.append((zz, before[i] - (rr - radius)))
        length = self.d.profile.length
        edge = info.edge if abs(x - x0) > 1e-9 and info.edge else 0.0
        gouged: dict[int, float] = {}
        for zz, rr in samples:
            for i, low in self._disc(zz, rr, radius):
                low = max(low, 0.0)
                if self.stock[i] > low:
                    self.stock[i] = low
                if low < self.final[i] - TOL and low < gouged.get(i, math.inf):
                    gouged[i] = low  # the deepest per cell, reported after the move (one message per cell)
            if edge:  # the insert's edge beside the nose, as for the tip point
                for i in self.indices(zz, zz + edge):
                    self.stock[i] = min(self.stock[i], max(rr - radius, 0.0))
        for i, low in sorted(gouged.items()):
            if -length + TOL < self.z(i) < -TOL:
                self.error(line, f"cuts below the finished profile at Z{fanuc.number(self.z(i))} "
                                 f"(D{fanuc.number(2 * low)} < D{fanuc.number(2 * self.final[i])})")
        self._check_depth(line, excess, x0, z0, x, z)
        self._check_ramp(line, excess, x0, z0, x, z)
        if kind == "feed":
            self.r.segments.append(Segment(kind, x0, z0, x, z, line, self.tool))
        self.pos = (x, z)

    def _plan_compensation(self, lines) -> None:
        """A pass over the program before it runs: the moves under G41 / G42 grouped from the start-up to the cancel,
        each group's nose centre path (gcode/nose.py), and the compensation's own errors."""
        pos, motion, comp, tool, idle = HOME, None, None, None, 0
        run: list[Move] = []
        run_tool = None

        def close(cancelled: bool) -> None:
            info = self.d.tools.get(run_tool)
            self.comp_lines.update(m.line for m in run)
            if not run or not (info and info.tool_type in NOSE_TYPES and info.nose_radius
                               and info.tip_direction in TIP_FROM_CENTRE):
                return
            paths, errors = compensated_paths(run, comp, info.nose_radius, info.tip_direction, cancelled)
            self.comp_paths.update(paths)
            for line, message in errors:
                self.error(line, message)

        for ln in lines:
            words, n = ln.words, ln.number
            side = "G41" if 41 in ln.g else "G42" if 42 in ln.g else None
            cancel = 40 in ln.g
            moving = "X" in words or "Z" in words
            if 28 in ln.g:
                if comp:
                    self.error(n, "G28 under nose radius compensation: G40 first")
                pos = (HOME[0] if "U" in words else pos[0], HOME[1] if "W" in words else pos[1])
                continue
            if "T" in words:
                if comp:
                    self.error(n, "a tool change under nose radius compensation: G40 first")
                tool = words["T"] // 100
                continue
            for g in ln.g:
                if g in (0, 1, 2, 3, 92):
                    motion = g
            if 30 in ln.m and comp:
                self.error(n, "the program ends under nose radius compensation: G40 first")
            if not moving:
                if comp and (ln.g or ln.m or words):
                    idle += 1
                    if idle >= 2:
                        self.error(n, "two blocks without a move under nose radius compensation: the control "
                                      "cannot see the next move (it may cut into the part)")
                if side or (cancel and comp):
                    self.error(n, f"{side or 'G40'} without a move: write it on the G00 / G01 move "
                                  + ("onto" if side else "off") + " the contour")
                continue
            idle = 0
            x, z = words.get("X", pos[0]), words.get("Z", pos[1])
            p0, p1 = (min(pos[1], self.z_top + 50), min(pos[0], 2e5) / 2), (z, x / 2)
            arc = None
            if motion in (2, 3) and pos[0] < HOME[0] and pos[1] < HOME[1]:
                cz, cr, radius, why = self.arc_centre(pos[0], pos[1], x, z, words, motion == 2)
                arc = (cz, cr, radius, motion == 2) if why is None else None
            move = Move(n, "arc" if arc else "rapid" if motion == 0 else "feed", p0, p1, arc)
            if side and comp is None:
                if motion not in (0, 1):
                    self.error(n, f"{side} on G0{motion}: the start-up needs a G00 / G01 move")
                info = self.d.tools.get(tool)
                if not (info and info.tool_type in NOSE_TYPES and info.nose_radius
                        and info.tip_direction is not None):
                    self.error(n, f"{side} with a tool that has no nose radius and tip direction T, or is not a "
                                  "turning tool")
                comp, run, run_tool = side, [move], tool
            elif side and comp is not None and side != comp:
                self.error(n, f"{side} while {comp} is on: the side changes only after G40")
                run.append(move)
            elif cancel and comp:
                if motion not in (0, 1):
                    self.error(n, f"G40 on G0{motion}: the cancel needs a G00 / G01 move")
                run.append(move)
                close(True)
                comp, run = None, []
            elif comp:
                if motion == 92:
                    self.error(n, "G92 under nose radius compensation: G40 first")
                run.append(move)
            pos = (x, z)
        if comp:
            self.error(lines[-1].number if lines else 1, "nose radius compensation is not cancelled (G40)")
            close(False)

    def _check_ramp(self, line, excess, x0, z0, x, z) -> None:
        """A turning insert cutting down towards the chuck steeper than its holder allows (the catalogue's max
        in-copying angle): an error, also when the angle is not known."""
        info = self.info
        if not info or info.tool_type not in ("turning_rough", "turning_finish", "facing"):
            return
        if not (z < z0 - 1e-9 and x < x0 - 1e-9) or not any(e > TOL for _, e in excess):
            return
        angle = math.degrees(math.atan2((x0 - x) / 2, z0 - z))
        if info.max_ramp is None:
            self.error(line, f"goes down at {angle:.0f}° towards the chuck in material: the tool's max in-copying "
                             "angle is not set")
        elif angle > info.max_ramp + 0.5:
            self.error(line, f"goes down at {angle:.0f}° towards the chuck, above the tool's max in-copying angle "
                             f"{info.max_ramp:g}°")

    def _check_depth(self, line, excess, x0, z0, x, z) -> None:
        """A turning insert cutting deeper than its ap max (the catalogue's) along more than ap max of its way: an
        error. Shorter runs are a face's allowance taken across (an axial cut); a 45° chamfer up to 3 mm cut in one
        pass is a warning (the chip is a triangle)."""
        info = self.info
        if not info or info.tool_type not in ("turning_rough", "turning_finish") or not info.edge:
            return
        limit, depth, run = info.edge + 0.01, 0.0, []
        for zz, e in excess + [(None, 0.0)]:
            if e > limit:
                run.append((zz, e))
                continue
            if run and abs(run[0][0] - run[-1][0]) + DZ > info.edge:
                depth = max(depth, max(e for _, e in run))
            run = []
        if not depth:
            return
        chamfer = abs(abs(x - x0) / 2 - abs(z - z0)) < 0.01 and abs(z - z0) <= 3 + 1e-9
        message = (f"cuts {depth:.2f} mm deep (per side), above the tool's ap max {info.edge:g}")
        if chamfer:
            self.warning(line, message + ": a chamfer in one pass, check")
        else:
            self.error(line, message)

    def _remove(self, line: int, z: float, r: float, radial: bool) -> None:
        """The tool at (z, r) takes the material above r over its footprint; a turning insert moving radially
        also takes it along its edge ahead of the tip. Below the finished profile (inside the part): an error."""
        lo, hi = self.footprint(z)
        info = self.info
        wide = hi - lo > 2 * TOL
        cells = list(self.indices(lo, hi)) if wide else [i for i in [self.index(z)] if i is not None]
        if radial and info and info.tool_type in ("facing", "turning_rough", "turning_finish") and info.edge:
            cells += [i for i in self.indices(z, z + info.edge) if i not in cells]
        length = self.d.profile.length
        for i in cells:
            zi = self.z(i)
            if self.stock[i] > r:
                self.stock[i] = r
            on_tool = (lo + TOL < zi < hi - TOL) if wide else abs(zi - z) < DZ / 2
            if on_tool and -length + TOL < zi < -TOL and r < self.final[i] - TOL:
                self.error(line, f"cuts below the finished profile at Z{fanuc.number(zi)} "
                                 f"(D{fanuc.number(2 * r)} < D{fanuc.number(2 * self.final[i])})")

    def check_bounds(self, line: int, x: float, z: float, jaws: bool = True) -> None:
        lo, _ = self.footprint(z)
        if jaws and lo < self.z_jaws + self.d.chuck_safety - TOL:
            self.error(line, f"closer than {self.d.chuck_safety:g} mm to the jaws (Z{fanuc.number(lo)}, jaws at "
                             f"Z{fanuc.number(self.z_jaws)})")
        info = self.info
        if x < -TOL:
            allowed = info and (info.tool_type == "facing" and x >= -2 * self.d.facing_overshoot - TOL
                                or info.tool_type == "parting" and x >= -2 * self.d.parting_overshoot - TOL)
            if not allowed:
                self.error(line, f"X{fanuc.number(x)} below the axis")

    # the spindle
    def speed_checks(self, line: int) -> None:
        info = self.info
        if self.css is not None and info and info.tool_type == "drilling":
            self.error(line, "G96 while drilling on the axis: the spindle runs up to G50 (use G97)")
        if self.rpm is not None and self.rpm > (self.limit or self.d.max_rpm):
            self.error(line, f"S{self.rpm} above the limit {self.limit or self.d.max_rpm}")
        if self.rpm is not None and self.d.min_rpm and self.rpm < self.d.min_rpm:
            self.warning(line, f"S{self.rpm} below the machine's min {self.d.min_rpm}")

    def run(self, text: str) -> SimResult:
        parsed = fanuc.parse(text)
        self.r.errors += parsed.errors
        if not parsed.ok:
            return self.r
        self._plan_compensation(parsed.lines)
        seen_end = False
        for ln in parsed.lines:
            words, n = ln.words, ln.number
            if 4 in ln.g:  # a dwell: no motion
                continue
            if 28 in ln.g:
                if "U" in words:
                    self.homed_x = True
                    self.pos = (HOME[0], self.pos[1])
                if "W" in words:
                    self.homed_z = True
                    self.pos = (self.pos[0], HOME[1])
                continue
            if "T" in words:
                if not (self.homed_x and self.homed_z) or self.pos != HOME:
                    self.error(n, "tool change away from the reference point (G28 U0. then G28 W0. first)")
                self.tool = words["T"] // 100
                if self.tool not in self.d.tools:
                    self.error(n, f"T{words['T']:04d}: no such tool in the turret")
                info = self.info
                if info and info.tool_type in NOSE_TYPES and info.nose_radius and info.tip_direction is not None \
                        and info.tip_direction not in TIP_FROM_CENTRE:
                    self.error(n, f"tip direction T{info.tip_direction}: only T0 to T4 and T9 are simulated, the nose "
                                  "of this tool is not checked")
                self.limit = self.css = self.rpm = None
                continue
            if 50 in ln.g:
                self.limit = words.get("S")
                if self.limit and self.limit > self.d.max_rpm:
                    self.error(n, f"G50 S{self.limit} above the machine's max {self.d.max_rpm}")
                continue
            if 96 in ln.g:
                if self.limit is None:
                    self.error(n, "G96 without G50 (no spindle limit)")
                self.css, self.rpm = words.get("S"), None
            if 97 in ln.g:
                self.rpm, self.css = words.get("S"), None
            for m in ln.m:
                if m in (3, 4):
                    direction = f"M0{m}"
                    if direction != self.d.spindle:
                        self.error(n, f"{direction}: the operator's direction for a right-hand tool is "
                                      f"{self.d.spindle}")
                    self.spindle_on = direction
                elif m == 5:
                    self.spindle_on = None
                elif m == 30:
                    seen_end = True
            if 96 in ln.g or 97 in ln.g:
                self.speed_checks(n)
            for g in ln.g:
                if g in (0, 1, 2, 3, 92):
                    self.motion = g
            if "F" in words and self.motion in (1, 2, 3, None):
                self.feed = words["F"]
            if "X" not in words and "Z" not in words:
                continue
            x = words.get("X", self.pos[0])
            z = words.get("Z", self.pos[1])
            if x >= HOME[0] or z >= HOME[1]:
                self.error(n, "a move with one axis still at the reference point")
                x, z = min(x, 2e5), min(z, self.z_top + 50)
            if self.motion == 0:
                self.rapid(n, x, z)
            elif self.motion == 1:
                self.cut(n, x, z)
            elif self.motion in (2, 3):
                self.arc(n, x, z, words, clockwise=self.motion == 2)
            elif self.motion == 92:
                self.thread(n, x, z, words.get("F"))
            else:
                self.error(n, "a move without G00 / G01 / G02 / G03 / G92")
            if self.motion in (1, 92) and self.info and self.info.tool_type == "parting" and x <= TOL \
                    and self.css is not None:
                self.warning(n, f"parting to the axis in G96: the spindle runs up to G50 S{self.limit}; the "
                                "operator may switch to G97")
        if not seen_end:
            self.error(len(text.splitlines()), "no M30 at the end")
        self.finish()
        return self.r

    def thread(self, line: int, x: float, z: float, pitch: float | None) -> None:
        if self.rpm is None:
            self.error(line, "G92 outside G97 (a thread needs a fixed spindle speed)")
        if pitch is None:
            self.error(line, "G92 without the pitch (F)")
        elif self.rpm and self.d.max_thread_feed and self.rpm * pitch > self.d.max_thread_feed + 1e-6:
            self.error(line, f"n·P {self.rpm * pitch:g} mm/min above the machine's threading limit "
                             f"{self.d.max_thread_feed:g}")
        elif self.rpm and self.d.max_thread_feed is None:
            self.warning(line, f"n·P {self.rpm * pitch:g} mm/min: no threading limit for this machine, check it")
        x0, z0 = self.pos  # the cycle's start point
        self.rapid(line, x, z0)
        self.cut(line, x, z, "thread")
        self.rapid(line, x0, z)
        self.rapid(line, x0, z0)

    CORNER_DEG = 2.0  # a turn of the finished profile above this is a corner (an arc's polyline turns 1° a point)

    def _inner_corners(self) -> list[tuple[float, float]]:
        """The finished profile's inner corners (z, r) along the part: a nose circle cannot reach into them."""
        points = []
        for p in self.d.profile.final_points:
            if not points or abs(p[0] - points[-1][0]) > 1e-9 or abs(p[1] - points[-1][1]) > 1e-9:
                points.append(p)
        out = []
        for a, v, b in zip(points, points[1:], points[2:]):
            t_in, t_out = (v[0] - a[0], v[1] - a[1]), (b[0] - v[0], b[1] - v[1])
            turn = math.degrees(math.atan2(t_in[0] * t_out[1] - t_in[1] * t_out[0],
                                           t_in[0] * t_out[0] + t_in[1] * t_out[1]))
            if turn < -self.CORNER_DEG and -self.d.profile.length < v[0] < 0:
                out.append(v)
        return out

    def finish(self) -> None:
        length = self.d.profile.length
        self.r.final_stock = list(self.stock)
        # the material a nose circle leaves in an inner corner (within rε·√2 of it, at most rε thick): a warning,
        # not a part left unfinished
        nose = min(self.nose_used) if self.nose_used else None
        corners = self._inner_corners() if nose else []
        fillets: dict[tuple[float, float], float] = {}
        run = None
        for i, r in enumerate(self.stock):
            z = self.z(i)
            if z < -length + TOL:
                break
            target = 0.0 if z > TOL else self.final[i]
            excess = r - target
            corner = next((v for v in corners if math.hypot(z - v[0], r - v[1]) <= nose * math.sqrt(2) + 0.02),
                          None) if corners and 0.01 < excess <= nose + 0.01 else None
            if corner is not None:
                fillets[corner] = max(fillets.get(corner, 0.0), excess)
            if excess > 0.01 and corner is None:
                run = [z, z, excess] if run is None else [run[0], z, max(run[2], excess)]
            elif run is not None:
                self.r.leftover.append(tuple(run))
                run = None
        if run is not None:
            self.r.leftover.append(tuple(run))
        for (z, r), excess in sorted(fillets.items(), reverse=True):
            self.warning(0, f"the inner corner at Z{fanuc.number(z)} D{fanuc.number(2 * r)} keeps the nose radius "
                            f"R{nose:g} (up to {excess:.2f} mm per side): the drawn corner is sharp, check")
        for z0, z1, excess in self.r.leftover:
            self.r.incomplete.append(f"PART NOT COMPLETE: material left from Z{fanuc.number(z0)} to "
                                     f"Z{fanuc.number(z1)} (up to {excess:.2f} mm per side): operations not in the "
                                     f"program, or manual")


def simulate(text: str, data: SimInput) -> SimResult:
    return _Sim(data).run(text)
