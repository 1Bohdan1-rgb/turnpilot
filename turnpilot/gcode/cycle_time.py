"""The cycle time of a program, estimated from its text as the control would run it.

- G01 / G02 / G03: time = ∫ dl / (f · n), f in mm/rev (G99); with G96, n = min(1000·Vc / (π·D), G50 S) at the
  diameter along the move (the move is cut into steps of at most STEP_MM); with G97, n = S.
- G92: each pass is a rapid in X, the thread along Z at n · P, a rapid out in X and a rapid back in Z.
- G00: the axes move at their own rapid rates (Fanuc's non-interpolated rapids): max(|ΔX/2| / vX, |ΔZ| / vZ).
- G04 P: the dwell; every T word: the machine's tool change time.
Not counted: acceleration of the axes and the spindle, M01, loading the part or feeding the bar, and the moves to
and from the reference point (G28 and the first approach after it: the reference point's position is not known;
they are taken as part of the tool change time).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from . import fanuc

STEP_MM = 0.1  # the step along a cutting move for G96 (n changes with the diameter)


@dataclass
class OperationTime:
    number: int | None  # the block's N (the operation's sequence); None: before the first block
    title: str = ""
    cut_s: float = 0.0  # G01 / G02 / G03
    thread_s: float = 0.0  # G92 along Z
    rapid_s: float | None = 0.0  # None when the rapid rates are not known
    rapid_x_mm: float = 0.0  # radial (per side) and axial rapid distance, shown when the rates are not known
    rapid_z_mm: float = 0.0
    dwell_s: float = 0.0
    tool_changes: int = 0
    tool_change_s: float | None = 0.0  # None when the tool change time is not known

    @property
    def total_s(self) -> float:
        return self.cut_s + self.thread_s + (self.rapid_s or 0.0) + self.dwell_s + (self.tool_change_s or 0.0)


@dataclass
class CycleTime:
    operations: list[OperationTime] = field(default_factory=list)
    rapid_known: bool = True
    tool_change_known: bool = True
    errors: list[tuple[int, str]] = field(default_factory=list)  # the text could not be read

    @property
    def total_s(self) -> float:
        return sum(op.total_s for op in self.operations)

    @property
    def complete(self) -> bool:
        """Every part of the time is counted (the rapid rates and the tool change time are known)."""
        return self.rapid_known and self.tool_change_known


class _Run:
    def __init__(self, rapid_x, rapid_z, tool_change_s):
        self.vx = rapid_x * 1000 / 60 if rapid_x else None  # mm/s
        self.vz = rapid_z * 1000 / 60 if rapid_z else None
        self.change = tool_change_s
        self.result = CycleTime(rapid_known=bool(self.vx and self.vz), tool_change_known=tool_change_s is not None)
        self.op = OperationTime(None, "program start")
        self.result.operations.append(self.op)
        self.x = self.z = None  # diameter, Z; None: at the reference point (not known)
        self.css = self.rpm = self.limit = None
        self.feed = None
        self.motion = None
        self.tool = None

    def n_at(self, d: float) -> float | None:
        if self.rpm:
            return self.rpm
        if self.css:
            n = 1000 * self.css / (math.pi * abs(d)) if abs(d) > 1e-9 else math.inf
            return min(n, self.limit) if self.limit else n
        return None

    def feed_time(self, points) -> float:
        """Time along a polyline of (x, z) points at the modal feed per revolution."""
        if not self.feed:
            return 0.0
        t = 0.0
        for (x0, z0), (x1, z1) in zip(points, points[1:]):
            length = math.hypot((x1 - x0) / 2, z1 - z0)
            steps = max(1, math.ceil(length / STEP_MM))
            for i in range(steps):
                d = x0 + (x1 - x0) * (i + 0.5) / steps
                n = self.n_at(d)
                if n:
                    t += (length / steps) / (self.feed * n / 60)
        return t

    def rapid(self, x, z) -> None:
        if self.x is not None and self.z is not None:
            dx, dz = abs(x - self.x) / 2, abs(z - self.z)
            self.op.rapid_x_mm += dx
            self.op.rapid_z_mm += dz
            if self.vx and self.vz:
                self.op.rapid_s += max(dx / self.vx, dz / self.vz)
        self.x, self.z = x, z

    def arc_points(self, x, z, words):
        """The minor arc from the current point to (x, z) about the centre I / K (radius, incremental)."""
        cr, cz = self.x / 2 + words.get("I", 0.0), self.z + words.get("K", 0.0)
        r = math.hypot(self.x / 2 - cr, self.z - cz)
        a0 = math.atan2(self.x / 2 - cr, self.z - cz)
        a1 = math.atan2(x / 2 - cr, z - cz)
        sweep = (a1 - a0 + math.pi) % (2 * math.pi) - math.pi
        steps = max(1, math.ceil(abs(sweep) * r / STEP_MM))
        return [(2 * (cr + r * math.sin(a0 + sweep * i / steps)), cz + r * math.cos(a0 + sweep * i / steps))
                for i in range(steps + 1)]

    def line(self, ln) -> None:
        words = ln.words
        if "N" in words:
            self.op = OperationTime(words["N"], ln.comment)
            self.result.operations.append(self.op)
        if 4 in ln.g:
            self.op.dwell_s += words.get("P", 0) / 1000
            return
        if 28 in ln.g:
            self.x = self.z = None
            return
        if "T" in words and words["T"] // 100 == self.tool:
            return  # the same tool called again: no indexing, no time
        if "T" in words:
            self.tool = words["T"] // 100
            self.op.tool_changes += 1
            self.op.tool_change_s = None if self.change is None else self.op.tool_changes * self.change
            self.css = self.rpm = self.limit = None
            return
        if 50 in ln.g:
            self.limit = words.get("S")
            return
        if 96 in ln.g:
            self.css, self.rpm = words.get("S"), None
        if 97 in ln.g:
            self.rpm, self.css = words.get("S"), None
        for g in ln.g:
            if g in (0, 1, 2, 3, 92):
                self.motion = g
        if "F" in words and self.motion in (1, 2, 3, None):
            self.feed = words["F"]
        if "X" not in words and "Z" not in words:
            return
        x, z = words.get("X", self.x), words.get("Z", self.z)
        if self.motion == 0 or x is None or z is None or self.x is None or self.z is None:
            self.rapid(x, z) if self.motion == 0 else None
            self.x, self.z = x, z
            return
        if self.motion == 1:
            self.op.cut_s += self.feed_time([(self.x, self.z), (x, z)])
        elif self.motion in (2, 3):
            self.op.cut_s += self.feed_time(self.arc_points(x, z, words))
        elif self.motion == 92:
            x0, z0 = self.x, self.z
            pitch = words.get("F")
            self.rapid(x, z0)
            if pitch and self.rpm:
                self.op.thread_s += abs(z - z0) / (self.rpm * pitch / 60)
            self.x, self.z = x, z
            self.rapid(x0, z)
            self.rapid(x0, z0)
            return
        self.x, self.z = x, z

    def finish(self) -> CycleTime:
        for op in self.result.operations:
            if not self.result.rapid_known:
                op.rapid_s = None
        self.result.operations = [op for op in self.result.operations if op.number is not None or op.total_s > 0]
        return self.result


def estimate(text: str, rapid_x_m_min: float | None, rapid_z_m_min: float | None,
             tool_change_s: float | None) -> CycleTime:
    """The program's time by operation (block N). Rapids without both rates and tool changes without their time
    are left out of the total (CycleTime.complete is then False)."""
    parsed = fanuc.parse(text)
    run = _Run(rapid_x_m_min, rapid_z_m_min, tool_change_s)
    if not parsed.ok:
        run.result.errors = parsed.errors
        return run.result
    for ln in parsed.lines:
        run.line(ln)
    return run.finish()
