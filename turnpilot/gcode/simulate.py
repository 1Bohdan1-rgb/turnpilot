"""The simulation of a printed program: it reads the text (gcode/fanuc.py), not the generator's commands, and
checks what the machine would do. The tool is its imaginary tip point (grooving and parting inserts: their width);
the stock is a radius per z on a 0.01 mm grid, from the stock beyond Z0 to the jaws.

Errors (the program is not ready to run): a turning cut deeper than the tool's ap max, a rapid through material (both orders of the axes, as a Fanuc control
may move them separately), a cut below the finished profile, a tool point at the jaws, X below the axis (beyond the
facing overshoot), G96 while drilling, a speed above G50 or the machine's max, G96 without G50, a spindle direction
other than the operator's, G92 outside G97, n·P above the machine's threading limit, a tool change away from the
reference point, a feed with the spindle stopped or without F, a wrong program frame.
Warnings: parting to the axis in G96, n·P unknown, speeds below the min. Material left on the part makes the
program incomplete ("part not complete"), apart from the warnings.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from . import fanuc
from .profile import Profile

DZ = 0.01  # mm, the stock grid
TOL = 0.002  # mm
HOME = (1e6, 1e6)  # the reference point: far beyond the part in +X and +Z


@dataclass(frozen=True)
class ToolInfo:
    tool_type: str
    edge: float | None = None  # the insert's cutting edge length along Z for a radial feed (its ap_max)
    width: float | None = None  # grooving / parting insert width


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
    min_rpm: int | None = None
    max_thread_feed: float | None = None


@dataclass
class Segment:
    kind: str  # "rapid", "feed", "thread"
    x0: float
    z0: float
    x1: float
    z1: float
    line: int
    tool: int | None


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

    def cut(self, line: int, x: float, z: float, kind: str = "feed") -> None:
        x0, z0 = self.pos
        info = self.info
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
        self.r.segments.append(Segment(kind, x0, z0, x, z, line, self.tool))
        self.pos = (x, z)

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

    def check_bounds(self, line: int, x: float, z: float) -> None:
        lo, _ = self.footprint(z)
        if lo < self.z_jaws + self.d.chuck_safety - TOL:
            self.error(line, f"closer than {self.d.chuck_safety:g} mm to the jaws (Z{fanuc.number(lo)}, jaws at "
                             f"Z{fanuc.number(self.z_jaws)})")
        info = self.info
        if x < -TOL:
            allowed = info and info.tool_type == "facing" and x >= -2 * self.d.facing_overshoot - TOL
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
        seen_end = False
        for ln in parsed.lines:
            words, n = ln.words, ln.number
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
                if g in (0, 1, 92):
                    self.motion = g
            if "F" in words and self.motion in (1, None):
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
            elif self.motion == 92:
                self.thread(n, x, z, words.get("F"))
            else:
                self.error(n, "a move without G00 / G01 / G92")
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

    def finish(self) -> None:
        length = self.d.profile.length
        self.r.final_stock = list(self.stock)
        run = None
        for i, r in enumerate(self.stock):
            z = self.z(i)
            if z < -length + TOL:
                break
            target = 0.0 if z > TOL else self.final[i]
            excess = r - target
            if excess > 0.01:
                run = [z, z, excess] if run is None else [run[0], z, max(run[2], excess)]
            elif run is not None:
                self.r.leftover.append(tuple(run))
                run = None
        if run is not None:
            self.r.leftover.append(tuple(run))
        for z0, z1, excess in self.r.leftover:
            self.r.incomplete.append(f"PART NOT COMPLETE: material left from Z{fanuc.number(z0)} to "
                                     f"Z{fanuc.number(z1)} (up to {excess:.2f} mm per side): operations not in the "
                                     f"program, or manual")


def simulate(text: str, data: SimInput) -> SimResult:
    return _Sim(data).run(text)
