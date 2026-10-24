"""A program as a neutral list of commands, built from the approved operations and the profile from the part zero.

Coordinates: X is a diameter, Z is negative towards the chuck. The tool point is the insert's imaginary tip. The
finishing contour is written with nose radius compensation (G42, the contour as drawn) when the finishing tool's
rε and tip direction T are both known; the operator enters them on the control's offset page. Every number comes from the operation (confirmed catalogue
rows or the operator), the machine (passport or operator) or the operator's programming values; nothing is
guessed. A dialect prints the commands as the text of one control (gcode/fanuc.py).

Motions are explicit (G00 / G01 and G92 per thread pass), no canned cycles: the simulation reads exactly the
moves the machine makes, and canned cycles differ between controls.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from .profile import EPS, Arc, Profile, Section, Segment

# --- commands -----------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Rapid:
    x: float | None = None
    z: float | None = None
    comp: str | None = None  # "G42" (start-up: the move onto the contour) / "G40" (cancel); None: unchanged


@dataclass(frozen=True)
class Feed:
    x: float | None = None
    z: float | None = None
    f: float | None = None  # mm/rev; None: the modal feed
    comp: str | None = None  # as Rapid.comp


@dataclass(frozen=True)
class ArcMove:
    """A circular feed to (x, z) about the centre at (i, k) from the start: I along X in radius, K along Z (Fanuc).
    clockwise: G02, else G03, as seen with Z to the right and X up (the tool's side)."""
    x: float
    z: float
    i: float
    k: float
    clockwise: bool
    f: float | None = None


def arc_direction(z0: float, r0: float, z1: float, r1: float, cz: float, cr: float) -> bool:
    """True (clockwise, G02) or False (G03) for the short arc from (z0, r0) to (z1, r1) about (cz, cr), seen with Z to
    the right and the radius up."""
    cross = (z0 - cz) * (r1 - cr) - (r0 - cr) * (z1 - cz)
    return cross < 0


@dataclass(frozen=True)
class ThreadPass:  # one pass of a single thread cycle (G92): X the pass diameter, Z the thread end, F the pitch
    x: float
    z: float
    pitch: float


@dataclass(frozen=True)
class Spindle:
    mode: str  # "css" (G96, value = Vc m/min) or "rpm" (G97, value = n)
    value: int
    direction: str | None = None  # "M03" / "M04"; None: keep turning as it is
    limit: int | None = None  # G50 S: the max spindle speed (written before G96)


@dataclass(frozen=True)
class Dwell:
    seconds: float


@dataclass(frozen=True)
class Coolant:
    on: bool


@dataclass(frozen=True)
class Comment:
    text: str


@dataclass(frozen=True)
class Home:  # to the reference point: X first, then Z
    pass


@dataclass(frozen=True)
class ToolCall:
    position: int


@dataclass(frozen=True)
class OptionalStop:
    pass


@dataclass
class Block:
    number: int  # N number (the tool block's restart point)
    title: str
    tool_position: int
    tool_name: str
    op_ids: list[int]
    tool_type: str
    commands: list = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)  # "check" items, for the operator
    notes: list[str] = field(default_factory=list)  # information


@dataclass
class Program:
    number: int
    job_title: str
    header: list[str]  # comment lines for the top
    blocks: list[Block] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)  # the whole program
    skipped: dict[int, str] = field(default_factory=dict)  # operation id -> why it is not in the program
    notes: list[str] = field(default_factory=list)  # information for the whole program
    spindle_off_at_end: bool = True
    compensation: list[tuple[int, str, float, int]] = field(default_factory=list)  # (position, tool, rε, T) with G42
    inner_compensation: list[tuple[int, str, float, int]] = field(default_factory=list)  # boring bars with G41
    boring_spindle: list[tuple[int, str, str]] = field(default_factory=list)  # (position, bar, M03 / M04) as set


# --- inputs ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class MachineData:
    max_rpm: int
    spindle: str  # direction for a right-hand tool, "M03" / "M04"
    clearance_x: float  # per side
    clearance_z: float
    retract: float  # per side
    chuck_safety: float
    coolant: bool | None = None
    thread_run_in: float | None = None
    peck_depth: float | None = None
    facing_overshoot: float | None = None  # per side
    groove_reference: str | None = None  # "toward Z0" / "toward chuck"
    parting_overshoot: float | None = None  # per side
    groove_dwell: float | None = None  # seconds at a groove's bottom; None: no dwell
    min_rpm: int | None = None
    max_thread_feed: float | None = None
    control: str | None = None


@dataclass(frozen=True)
class JobData:
    id: int
    name: str
    material: str
    blank_diameter: float  # the stock's largest Ø (a hex bar: across corners)
    blank_label: str
    stickout: float
    face_stock: float | None
    free_end: str


@dataclass(frozen=True)
class OpData:
    id: int
    sequence: int
    tool_type: str
    mode: str
    feature_id: int
    feature_type: str
    position: int
    tool_name: str
    insert_code: str | None
    vc: float | None
    n: int | None
    f: float | None
    ap: float | None
    passes: int | None
    depth: float | None
    insert_width: float | None
    ref_diameter: float | None
    ap_min: float | None = None
    ap_max: float | None = None
    tool_diameter: float | None = None
    max_ramp_angle: float | None = None  # turning: the insert's max in-copying angle, degrees
    nose_radius: float | None = None  # turning: rε (the operator's, else the insert code's); None: not known
    tip_direction: int | None = None  # turning: the imaginary tip's number T (0-9); None: not known
    pitch: float | None = None  # threading: the feature's pitch
    point_angle: float | None = None  # drills: the point angle, degrees; None: not known
    max_depth: float | None = None  # drills: the deepest hole they reach
    depth_mode: str | None = None  # a bore's drill: "full_diameter" / "tip", the operator's decision
    depth_decided: str | None = None  # who and when ("Bohdan 2026-10-16")
    warnings: tuple = ()  # the planner's warnings on the operation
    dmin: float | None = None  # boring: the smallest bore the holder goes into
    spindle_direction: str | None = None  # boring: the spindle direction the operator set for the bar
    entering_angle: float | None = None  # boring: κr of the insert in its holder


# A thread's run-in is at least 3 × P (the axis reaches the thread's feed before the thread): rule of thumb, the
# operator's; not checked against a catalogue page (placeholder table: open).
THREAD_RUN_IN_PITCHES = 3


# A rapid keeps at least this far (per side, and along Z before a face) from the material the roughing already
# written leaves; closer, the tool feeds. The operator's rule (2026-10-17); placeholder table: open.
RAPID_STOCK_MARGIN_MM = 1.0
DRILLED_TOL_MM = 0.001  # a hole this close to a radius counts as it: the program writes to 0.001 mm
STOCK_STEP_MM = 0.05  # the step of the stock look-up along Z


def _r3(value: float) -> float:
    return round(value + 0.0, 3)


class _Builder:
    def __init__(self, job: JobData, machine: MachineData, profile: Profile, ops: list[OpData]):
        self.job, self.m, self.p = job, machine, profile
        self.ops = ops
        self.stock_r = job.blank_diameter / 2
        self.x_safe = _r3(job.blank_diameter + 2 * machine.clearance_x)
        self.z_safe = _r3((job.face_stock or 0.0) + machine.clearance_z)
        self.finished: set[int] = set()  # sections whose finishing pass is written
        self.roughed: list[tuple[float, float, float]] = []  # (z from, z to, radius) of the passes written
        self.covered: list[str] = []  # operations whose material earlier passes already took
        self.descents: list[tuple] = []  # contour segments the finishing tool may not cut (too steep down)
        self.back_chamfer_done = False
        self.comp = False  # the finishing contour being written is nose radius compensated (G42)
        self.compensated: dict[int, tuple[str, float, int]] = {}  # position -> (tool, rε, T) used with G42
        self.allowance = max((op.ap or 0.0 for op in ops if op.tool_type == "turning_finish"), default=0.0)
        self.holes: list[tuple[float, float, float, float]] = []  # (z_a, r_a, z_b, r_b), z_a > z_b: the hole so far
        self.compensated_inner: dict[int, tuple[str, float, int]] = {}  # position -> (tool, rε, T) used with G41
        self.boring_spindle: dict[int, tuple[str, str]] = {}  # position -> (bar, M03 / M04)

    # spindle and coolant
    def _start(self, block: Block, op: OpData, mode: str = "css") -> None:
        value = round(op.vc) if mode == "css" else op.n
        block.commands.append(Spindle(mode, int(value), self.m.spindle, self.m.max_rpm))
        block.commands.append(Rapid(self.x_safe, self.z_safe))
        if self.m.coolant:
            block.commands.append(Coolant(True))

    def _end(self, block: Block) -> None:
        if _position(block.commands)[0] != self.x_safe:
            block.commands.append(Rapid(x=self.x_safe))
        if self.m.coolant:
            block.commands.append(Coolant(False))

    def block(self, op: OpData, title: str, op_ids=None) -> Block:
        b = Block(op.sequence, title, op.position, op.tool_name, op_ids or [op.id], op.tool_type)
        for o in self.ops:  # the planner's warnings of the operations in the block (the operator approved them)
            if o.id in b.op_ids:
                b.warnings += [w for w in o.warnings if w not in b.warnings]
        return b

    # facing ------------------------------------------------------------------------------------------
    def facing(self, op: OpData) -> Block | str:
        stock = self.job.face_stock
        if stock is None:
            return "no stock beyond Z0 given: not generated"
        b = self.block(op, f"FACE Z0, {stock:g} MM STOCK")
        passes = max(1, math.ceil(round(stock / op.ap, 9))) if op.ap and stock > EPS else 1
        step = stock / passes
        self._start(b, op)
        x_end = _r3(-2 * self.m.facing_overshoot)
        for i in range(1, passes + 1):
            z = _r3(stock - i * step)
            b.commands += [Rapid(z=z), Feed(x=x_end, f=op.f), Feed(z=_r3(z + self.m.retract)), Rapid(x=self.x_safe)]
        b.commands.append(Rapid(z=self.z_safe))
        self._end(b)
        return b

    # roughing ----------------------------------------------------------------------------------------
    def _scan(self, z_from: float, toward_chuck: bool, level: float) -> float | None:
        """The first z from z_from (not at it) where the turned profile rises above level; None: none up to the end."""
        points = self.p.turned_points if toward_chuck else list(reversed(self.p.turned_points))
        for (z0, r0), (z1, r1) in zip(points, points[1:]):
            ahead = (lambda z: z < z_from - EPS) if toward_chuck else (lambda z: z > z_from + EPS)
            if not ahead(z1) and not (abs(z0 - z1) < EPS and ahead(z0)):
                continue
            if abs(z0 - z1) < EPS:  # a step
                if max(r0, r1) > level + EPS:
                    return z0
                continue
            # the part of the segment ahead of z_from
            za = z0 if ahead(z0) else z_from
            ra = r0 + (r1 - r0) * (za - z0) / (z1 - z0)
            if ra > level + EPS:
                return za
            if r1 > level + EPS:
                return za + (z1 - za) * (level - ra) / (r1 - ra)
        return None

    def rough(self, op: OpData, section: Section) -> Block | str:
        if not op.passes:
            return None  # no stock left: nothing to write
        if op.ref_diameter is None or op.ap is None:
            return "no start diameter or depth of cut: not generated"
        if section.kind == "arc" and section.arc is None:
            return f"the arc is not programmed: {section.arc_problem}"
        if section.kind == "arc" and not section.arc.convex and op.max_ramp_angle is not None:
            return self._copy_rough(op, section)
        name = _section_name(section)
        own = [p for segment in section.segments() for p in segment.points()]  # its own, not a neighbour's step
        target = min(r for _, r in own) if section.kind in ("taper", "arc") else section.turned_d / 2
        allowance = max(0.0, op.ref_diameter / 2 - op.passes * op.ap - target)
        if abs(allowance - self.allowance) < 0.01:  # the planner's ap is rounded: the finishing tool's ap exactly
            allowance = self.allowance
        start, n_passes = op.ref_diameter / 2, op.passes
        b = self.block(op, f"ROUGH {name} {op.passes} X AP {op.ap:g} FROM D{op.ref_diameter:g}")
        step = (start - target - allowance) / n_passes  # equal passes down to target + allowance
        # the scans start where the section is lowest (a taper: its smaller end)
        z_mid = (section.z_free + section.z_chuck) / 2
        if section.kind in ("taper", "arc"):  # where it is lowest
            z_mid = min(own, key=lambda p: (p[1], -p[0]))[0]
        passes, in_air = [], 0
        written = len(self.roughed)
        for i in range(1, n_passes + 1):
            radius = start - i * step
            level = radius - allowance
            if self._scan(z_mid, False, level) is not None:
                del self.roughed[written:]  # nothing of this operation is written
                return ("a larger diameter between this section and the free end: roughing it needs plunging, "
                        "not generated")
            shoulder = self._scan(z_mid, True, level)
            z_end = -self.p.length if shoulder is None else min(shoulder + allowance, section.z_free)
            if z_end > -EPS:
                continue  # a taper's step that does not reach the part
            if self._roughed_to(z_end, radius):
                continue  # an earlier pass already took the material down to this radius over the span
            rapid_to = self._air_until(radius, z_end)
            if rapid_to is None:
                in_air += 1
                continue  # the passes before took the material of the whole pass: left out
            passes.append((radius, _r3(z_end), _r3(rapid_to) if rapid_to < -EPS else None))
            self.roughed.append((0.0, _r3(z_end), radius))  # the next pass sees what this one leaves
        profile_pass = section.kind == "taper" and section.d_free < section.d_chuck and allowance >= 0
        arc_pass = None
        if section.kind == "arc":
            arc_pass, why = self._arc_profile_pass(section, allowance, op)
            if arc_pass is None:
                del self.roughed[written:]
                return why
        if not passes and not profile_pass and not arc_pass:
            self.covered.append(f"operation {op.id} (rough {name}): the passes before it already took the "
                                f"material down to its Ø")
            return b  # no commands: left out of the program
        if in_air:
            b.notes.append(f"{in_air} of the {n_passes} passes left out: the passes before took that material "
                           f"(the process sheet starts from D{op.ref_diameter:g})")
        if any(rapid_to is not None for _, _, rapid_to in passes):
            b.notes.append(f"no material within {RAPID_STOCK_MARGIN_MM:g} mm on part of a pass: a rapid there")
        self._start(b, op)
        for radius, z_end, rapid_to in passes:
            b.commands.append(Rapid(x=_r3(2 * radius)))
            if rapid_to is not None:  # no material within the margin up to there: a rapid along Z
                b.commands.append(Rapid(z=rapid_to))
            b.commands += [Feed(z=z_end, f=op.f), Feed(x=_r3(2 * (radius + self.m.retract))), Rapid(z=self.z_safe)]
        if profile_pass:  # along the taper's line, the allowance above it: the steps' corners off
            r0, r1 = section.d_free / 2 + allowance, section.d_chuck / 2 + allowance
            self._approach(b, r0, section.z_free + self.m.clearance_z, op.f)
            b.commands += [Feed(z=_r3(section.z_free), f=op.f), Feed(x=_r3(2 * r1), z=_r3(section.z_chuck)),
                           Feed(x=_r3(2 * (r1 + self.m.retract))), Rapid(z=self.z_safe)]
            b.notes.append(f"taper: steps, then one pass along its line {allowance:g} mm above it")
        if arc_pass:
            (z_in, r_in), rest = arc_pass
            self._approach(b, r_in, z_in, op.f)
            b.commands += rest
            b.notes.append(f"arc: steps, then one pass along it {allowance:g} mm above it")
        self._end(b)
        if allowance:
            b.notes.append(f"leaves {allowance:g} mm per side for finishing")
        return b

    def _copy_rough(self, op: OpData, section: Section) -> Block | str | None:
        """A concave arc roughed by copying, without compensation: the passes are worked out for the nose centre on
        arcs concentric to the drawn one (its radius less the pass's distance and rε), from the stock level the
        roughing already written leaves over the arc down to the finishing allowance, in equal steps of at most ap;
        the program follows the imaginary tip, the centre moved by T. Each pass goes in above the material, down at
        its free-side end, along its arc and up at its chuck-side end. The tool must be allowed to go down as
        steeply as a pass starts (its RMPX); without rε and T the passes cannot be placed: not generated."""
        from .nose import TIP_FROM_CENTRE

        a = section.arc
        rn, tip = op.nose_radius, op.tip_direction
        if not rn or tip not in TIP_FROM_CENTRE:
            return ("copy roughing needs the tool's nose radius and tip direction T (the passes are placed for the "
                    "nose centre): not generated")
        allowance = self.allowance
        # the level the roughing passes already written leave over the whole arc, else the planner's start Ø
        over = [r for _, z_to, r in self.roughed if z_to <= section.z_chuck + allowance + EPS]
        r_start = min(over) if over else op.ref_diameter / 2
        depth = r_start - (a.cr - a.radius) - allowance
        if depth <= EPS:
            return None
        passes = max(1, math.ceil(round(depth / op.ap, 9))) if op.ap else max(1, op.passes)
        step = depth / passes
        b = self.block(op, f"COPY ROUGH ARC R{a.radius:g} {passes} X AP {step:.3g} FROM D{_r3(2 * r_start):g}")
        x_above = _r3(2 * self._above(0.0, section.z_chuck))
        dz, dr = TIP_FROM_CENTRE[tip]
        commands, steepest = [], 0.0
        for k in range(1, passes + 1):
            rho = a.radius - (allowance + (passes - k) * step) - rn  # the centre's circle, first pass farthest
            h = a.cr - (r_start + rn)  # the centre where the nose's bottom is at the stock level
            if rho <= h + EPS:
                continue  # it does not reach below the stock level
            w = math.sqrt(rho * rho - h * h)
            # the whole nose stays over the arc's section at its chuck side (the neighbour there rises: a face)
            za, zb = min(a.cz + w, section.z_free), max(a.cz - w, section.z_chuck + rn)
            if zb >= za - EPS:
                continue
            ra = a.cr - math.sqrt(max(rho * rho - (za - a.cz) ** 2, 0.0))
            rb = a.cr - math.sqrt(max(rho * rho - (zb - a.cz) ** 2, 0.0))
            steepest = max(steepest, math.degrees(math.atan2(abs(za - a.cz), a.cr - ra)))
            # the imaginary tip: the centre moved by T
            z0, r0, z1, r1 = _r3(za + dz * rn), _r3(ra + dr * rn), _r3(zb + dz * rn), _r3(rb + dr * rn)
            cz, cr = a.cz + dz * rn, a.cr + dr * rn
            commands += [Rapid(z=z0), Feed(x=_r3(2 * r0), f=op.f),
                         ArcMove(x=_r3(2 * r1), z=z1, i=_r3(cr - r0), k=_r3(cz - z0),
                                 clockwise=arc_direction(z0, r0, z1, r1, cz, cr)),
                         Feed(x=x_above), Rapid(z=self.z_safe)]
        if steepest > op.max_ramp_angle + 0.5:
            return (f"copy roughing goes down at {steepest:.0f}° at its passes' start, above the tool's RMPX "
                    f"{op.max_ramp_angle:g}°: not generated")
        if not commands:
            return None
        self._start(b, op)
        b.commands += [Rapid(x=x_above)] + commands
        self._end(b)
        b.notes.append(f"concave arc: {passes} passes, the nose centre on arcs concentric to it (rε {rn:g}, T{tip}), "
                       f"leaves {allowance:g} mm for finishing")
        return b

    def _copies(self, op: OpData) -> bool:
        """A concave arc roughed by copying: after the other roughing, which leaves the level it starts from."""
        section = self.p.section_of(op.feature_id)
        return bool(section and section.kind == "arc" and section.arc is not None and not section.arc.convex
                    and op.max_ramp_angle is not None)

    def _arc_profile_pass(self, section: Section, allowance: float, op: OpData):
        """The roughing tool along the arc, the allowance outside it (the same centre, R ± allowance); (commands,
        None) or (None, why) when the tool may not follow it down towards the chuck."""
        a = section.arc
        radius = a.radius + allowance if a.convex else a.radius - allowance
        if radius <= EPS:
            return None, "the arc is smaller than the allowance: not generated"
        offset = Arc(a.cz, a.cr, radius, a.convex)
        segment = Segment(*_scaled(a, offset, section.z_free, section.d_free / 2),
                          *_scaled(a, offset, section.z_chuck, section.d_chuck / 2), offset)
        steepest = _steepest_descent(segment)
        if steepest is not None and (op.max_ramp_angle is None or steepest > op.max_ramp_angle + 0.5):
            return None, (f"the arc goes down at {steepest:.0f}° towards the chuck: the roughing tool "
                          + ("has no max in-copying angle set" if op.max_ramp_angle is None
                             else f"may go down at {op.max_ramp_angle:g}° only") + ": not generated")
        z0, r0, z1, r1 = _r3(segment.z0), _r3(segment.r0), _r3(segment.z1), _r3(segment.r1)
        # ((where the tool comes in: z, r), the moves from there); the way in is the caller's (_approach)
        return ((max(z0, section.z_free) + self.m.clearance_z, r0), [
                Feed(z=z0, f=op.f),
                ArcMove(x=_r3(2 * r1), z=z1, i=_r3(a.cr - r0), k=_r3(a.cz - z0),
                        clockwise=arc_direction(z0, r0, z1, r1, a.cz, a.cr)),
                Feed(x=_r3(2 * (r1 + self.m.retract))), Rapid(z=self.z_safe)]), None

    def _stock_r(self, z: float) -> float:
        """The material's radius at z as the roughing passes written so far leave it (an upper bound: other cuts are
        not counted); in front of Z0 there is none (faced first)."""
        if z > EPS:
            return 0.0
        r = self.stock_r
        for _, z_end, radius in self.roughed:
            if z_end - EPS <= z:
                r = min(r, radius)
        return r

    def _stock_samples(self, z_a: float, z_b: float):
        lo, hi = min(z_a, z_b), max(z_a, z_b)
        n = max(1, math.ceil((hi - lo) / STOCK_STEP_MM))
        return [(hi - (hi - lo) * i / n) for i in range(n + 1)]  # from the free end side

    def _stock_max(self, z_a: float, z_b: float) -> float:
        return max(self._stock_r(z) for z in self._stock_samples(z_a, z_b))

    def _air_until(self, radius: float, z_end: float) -> float | None:
        """Where a pass at radius from Z0 towards z_end may stop its rapid: the first z with material within the
        margin of radius, plus the margin along Z; None: no material to cut on the whole pass."""
        margin = RAPID_STOCK_MARGIN_MM
        if self._stock_max(0.0, z_end) <= radius + EPS:
            return None
        for z in self._stock_samples(0.0, z_end):
            if self._stock_r(z) > radius - margin + EPS:
                return z + margin
        return None

    def _approach(self, b: Block, r_to: float, z_to: float, f: float | None) -> None:
        """From where the block's moves leave the tool to (r_to, z_to): rapids only with the margin over the material
        (up in X first when the way along Z is lower), the last part down in X fed when the material is closer."""
        margin = RAPID_STOCK_MARGIN_MM
        x_now, z_now = _position(b.commands)
        r_now = (x_now if x_now is not None else self.x_safe) / 2
        z_now = z_now if z_now is not None else self.z_safe
        need = self._stock_max(z_to, z_now) + margin
        if r_now < need - EPS:
            b.commands.append(Rapid(x=_r3(2 * need)))
            r_now = need
        b.commands.append(Rapid(z=_r3(z_to)))
        floor = self._stock_r(z_to) + margin
        if r_to >= floor - EPS:
            b.commands.append(Rapid(x=_r3(2 * r_to)))
            return
        if r_now > floor + EPS:
            b.commands.append(Rapid(x=_r3(2 * floor)))
        b.commands.append(Feed(x=_r3(2 * r_to), f=f))

    def _roughed_to(self, z_end: float, radius: float) -> bool:
        """Earlier passes left no material above radius between Z0 and z_end."""
        cuts = [(lo, r) for _, lo, r in self.roughed if r <= radius + EPS]
        reach = min((lo for lo, _ in cuts), default=None)
        return reach is not None and reach <= z_end + EPS

    # finishing contour -------------------------------------------------------------------------------
    def finish(self, group: list[tuple[Section, OpData | None]]) -> Block:
        """One contour over adjacent sections (grooves between them bridged at the Ø they are cut from)."""
        first_op = next(op for _, op in group if op is not None)
        sections = [s for s, _ in group]
        names = ", ".join(_section_name(s) for s, op in group if op is not None)
        b = self.block(first_op, f"FINISH {names}", [op.id for _, op in group if op is not None])
        self._start(b, first_op)
        rnose = first_op.nose_radius
        ramp = first_op.max_ramp_angle
        self.comp = bool(rnose) and first_op.tip_direction is not None
        if self.comp:
            self.compensated[first_op.position] = (first_op.tool_name, rnose, first_op.tip_direction)
        on_contour = False
        current_f = first_op.f
        current_vc = round(first_op.vc)
        for section, op in group:
            if op is not None and op.f != current_f:
                current_f = op.f
            if op is not None and round(op.vc) != current_vc:
                current_vc = round(op.vc)
                b.commands.append(Spindle("css", current_vc))
            if not on_contour:
                self._entry(b, section, current_f)
                on_contour = True
            if self.comp and op is None and section.kind == "groove" and section.index + 1 < len(self.p.sections):
                x0, z0 = _position(b.commands)
                nxt = self.p.sections[section.index + 1].segments()[0]
                tz, tr = _tangent(nxt)
                r0 = x0 / 2
                if tr > EPS and tz < -EPS:
                    # the next section rises (a taper, an arc): onto its start along its own line, 2·rε of it in the
                    # air over the groove, so the nose turns onto that line there (inner corners in the air, both
                    # sides long enough) and meets the part's edge without a corner
                    zq, rq = nxt.z0 - 2 * rnose * tz, nxt.r0 - 2 * rnose * tr
                    down = math.degrees(math.atan2(r0 - rq, z0 - zq)) if rq < r0 else 0.0
                    if nxt.z0 < zq < z0 - EPS and rq > section.d_free / 2 + EPS and (
                            down == 0.0 or (ramp is not None and down <= ramp + 0.5)):
                        if not (self._contour_line(b, section, x0, z0, rq, zq, ramp, current_f)
                                and self._contour_line(b, section, *_position(b.commands), nxt.r0, nxt.z0, ramp,
                                                       current_f)):
                            on_contour = False
                        continue
                if self._short_step_after(section, rnose):
                    # a far side stepping up by less than rε: the nose cannot turn into that step under G42 (an
                    # interference), so the contour goes straight across the groove to the next section
                    if not self._contour_line(b, section, x0, z0, nxt.r0, nxt.z0, ramp, current_f):
                        on_contour = False
                    continue
            for segment in section.segments():
                x0, z0 = _position(b.commands)
                if (x0, z0) != (_r3(2 * segment.r0), _r3(segment.z0)):  # onto the segment's start (a step)
                    if not self._contour_line(b, section, x0, z0, segment.r0, segment.z0, ramp, current_f):
                        on_contour = False
                        break
                    x0, z0 = _position(b.commands)
                if segment.arc is None:
                    if not self._contour_line(b, section, x0, z0, segment.r1, segment.z1, ramp, current_f):
                        on_contour = False
                        break
                    continue
                if not self._contour_arc(b, section, segment, ramp, rnose, current_f):
                    on_contour = False
                    break
            if op is not None:
                self.finished.add(section.index)
        if on_contour:
            last = group[-1][0]
            nxt = self.p.sections[last.index + 1] if last.index + 1 < len(self.p.sections) else None
            self._leave(b, shoulder=nxt is not None
                        and nxt.segments()[0].r0 > _position(b.commands)[0] / 2 + EPS)  # as it is turned
        self._end(b)
        if self.comp:  # the control keeps the nose on the drawn chamfers, tapers and arcs
            b.notes.append(f"nose radius compensation G42: the contour as drawn (offset R{rnose:g} "
                           f"T{first_op.tip_direction})")
            self.comp = False
            return b
        sections = [s for s, _ in group]
        chamfers = [s for s in sections if s.chamfer_free or s.chamfer_chuck]
        if chamfers and rnose:
            offset = round(rnose * (math.sqrt(2) - 1), 3)
            b.warnings.append(f"no nose radius compensation: 45° chamfers come out about {offset:g} mm (normal) "
                              f"fuller than drawn, legs about {round(offset * math.sqrt(2), 2):g} mm shorter "
                              f"(rε {rnose:g}): check")
        if rnose and any(s.kind == "arc" or s.fillet_free or s.fillet_chuck for s in sections):
            b.warnings.append(f"no nose radius compensation: arcs come out up to {round(rnose * (math.sqrt(2) - 1), 3):g}"
                              f" mm (normal) off where their tangent is near 45° (rε {rnose:g}): check")
        for s in sections:
            if s.kind == "taper" and rnose:
                angle = math.atan2(abs(s.d_chuck - s.d_free) / 2, s.length)
                offset = round(rnose * (math.sin(angle) + math.cos(angle) - 1), 3)
                b.warnings.append(f"no nose radius compensation: the taper D{s.d_free:g}-D{s.d_chuck:g} "
                                  f"({math.degrees(angle):.1f}° to the axis) comes out about {offset:g} mm (normal) "
                                  f"fuller than drawn (rε {rnose:g}): check")
        return b

    def _contour_line(self, b, section, x0, z0, r, z, ramp, f) -> bool:
        """A straight piece of the contour; False (and off the contour) when it goes down towards the chuck
        steeper than the insert allows."""
        x, z = _r3(2 * r), _r3(z)
        if (x0, z0) == (x, z):
            return True
        if z <= z0 + EPS and x < x0 - EPS:  # down towards the chuck (a step down: 90°): allowed by the insert?
            angle = math.degrees(math.atan2((x0 - x) / 2, z0 - z)) if z < z0 - EPS else 90.0
            if ramp is None or angle > ramp + 0.5:
                self._not_cut(b, section, z0, z, x0, x, angle, ramp)
                return False
        b.commands.append(Feed(x=x, z=z, f=f))
        return True

    def _contour_arc(self, b, section, segment: Segment, ramp, rnose, f) -> bool:
        """An arc of the contour as G02 / G03 (the centre from the position the tool is at); False when the insert
        cannot cut it: a concave radius below the nose radius, or a way down towards the chuck too steep."""
        a = segment.arc
        x0, z0 = _position(b.commands)
        x1, z1 = _r3(2 * segment.r1), _r3(segment.z1)
        if not a.convex and rnose and a.radius < rnose - EPS:
            b.warnings.append(f"concave R{a.radius:g} is smaller than the nose radius {rnose:g}: not cut")
            self.descents.append((section, z0, z1, x0, x1, 0.0))
            self._leave(b)
            return False
        steepest = _steepest_descent(segment)
        if steepest is not None and (ramp is None or steepest > ramp + 0.5):
            self._not_cut(b, section, z0, z1, x0, x1, steepest, ramp, f"the arc R{a.radius:g}")
            return False
        r0 = x0 / 2
        b.commands.append(ArcMove(x=x1, z=z1, i=_r3(a.cr - r0), k=_r3(a.cz - z0),
                                  clockwise=arc_direction(z0, r0, z1, x1 / 2, a.cz, a.cr), f=f))
        return True

    def _not_cut(self, b, section, z0, z, x0, x, angle, ramp, what=None) -> None:
        self.descents.append((section, z0, z, x0, x, angle))
        b.warnings.append(
            (f"{what}: " if what else "") + f"Z{z0:g} to Z{z:g} goes down at {angle:.0f}° towards the chuck: "
            + ("the tool's max in-copying angle is not set" if ramp is None
               else f"above the tool's max in-copying angle {ramp:g}°") + ": not cut by this tool")
        self._leave(b)

    def _leave(self, b, shoulder: bool = False) -> None:
        """Off the contour: up to the safe X, then back in Z. Compensation is cancelled on the move up; at the face of
        a larger section not finished in this contour (an inner corner) it stays on up that face, so the nose follows
        the face, and is cancelled on the move back in Z."""
        if self.comp and shoulder:
            b.commands += [Feed(x=self.x_safe), Rapid(z=self.z_safe, comp="G40")]
            return
        b.commands += [Feed(x=self.x_safe, comp="G40" if self.comp else None), Rapid(z=self.z_safe)]

    def _short_step_after(self, groove: Section, rnose: float) -> bool:
        """A groove bridged in the contour whose next section starts higher than the bridge by less than rε."""
        if groove.kind != "groove" or groove.index + 1 >= len(self.p.sections):
            return False
        rise = self.p.sections[groove.index + 1].segments()[0].r0 - groove.segments()[0].r0
        return EPS < rise < rnose - EPS

    def _entry(self, b: Block, first: Section, f: float) -> None:
        """Onto the contour at a section's free-end side: from Z+clearance at the free end (a chamfer along its
        line), else above the material on the free-end side, then down beside the face.

        With compensation, G42 starts on the move onto the contour's first line (the start-up block: at its end the
        nose stands square to the next move), and at the free end the tool comes in along the first segment's line
        (no inner corner in the air before the part, which the nose could not reach round)."""
        cz = self.m.clearance_z
        g42 = "G42" if self.comp else None
        if first.index == 0 and self.comp:
            self._compensated_entry(b, first, f)
            return
        if first.index == 0:
            r_top = self.p.turned_points[0][1]
            if first.chamfer_free:
                b.commands += [Rapid(z=_r3(cz)), Rapid(x=_r3(2 * max(r_top - cz, 0.0)), comp=g42),
                               Feed(x=_r3(2 * r_top), z=0.0, f=f)]
            else:
                b.commands += [Rapid(z=_r3(cz)), Rapid(x=_r3(2 * r_top), comp=g42), Feed(z=0.0, f=f)]
            return
        z_face = first.z_free
        above = max(r for z, r in self.p.turned_points if z >= z_face - EPS) + self.allowance + self.m.retract
        r_prev = self._own_radius(self.p.sections[first.index - 1], False)  # the neighbour at this face
        b.commands += [Rapid(x=_r3(2 * above)), Rapid(z=_r3(z_face + self.allowance + cz)),
                       Feed(x=_r3(2 * r_prev), f=f, comp=g42), Feed(z=_r3(z_face))]

    def _compensated_entry(self, b: Block, first: Section, f: float) -> None:
        """Onto the free end under G42, along the first segment's line from Z+clearance: a rising line or arc (a
        chamfer, a taper, a convex end) is met straight, without a corner. Where that line reaches the axis before
        Z+clearance (a steep start, a spherical end at X0 Z0) the tool comes down beside the face to the axis there:
        the start-up ends with the nose on the axis, square to the line, never across it."""
        cz, rnose = self.m.clearance_z, self.compensated[b.tool_position][1]
        segment = first.segments()[0]
        z0, r0 = segment.z0, self.p.turned_points[0][1]
        tz, tr = _tangent(segment)
        if tr <= EPS:  # level or going down: along the level line
            b.commands += [Rapid(z=_r3(z0 + cz)), Rapid(x=_r3(2 * r0), comp="G42"), Feed(z=_r3(z0), f=f)]
            return
        back = min(cz, r0 * -tz / tr) if tz < -EPS else 0.0  # how far back in Z the line stays above the axis
        r_start = r0 - tr * back / -tz if tz < -EPS else 0.0
        if back >= cz - EPS:  # from Z+clearance on the line
            b.commands += [Rapid(z=_r3(z0 + cz)), Rapid(x=_r3(2 * r_start), comp="G42"),
                           Feed(x=_r3(2 * r0), z=_r3(z0), f=f)]
            return
        lead = _r3(2 * (self.m.clearance_x + rnose))  # beside the face: the start-up is longer than rε
        b.commands += [Rapid(z=_r3(z0 + cz)), Rapid(x=lead), Feed(z=_r3(z0 + back), f=f), Feed(x=0.0, comp="G42")]
        if back > EPS:
            b.commands.append(Feed(x=_r3(2 * r0), z=_r3(z0)))

    def _contour(self, section: Section) -> list[tuple[float, float]]:
        """The turned profile's points of a section, from its free-end side to its chuck side."""
        z0, z1 = section.z_free, section.z_chuck
        # at the section's ends the neighbours' step points share the z: this section's own radii are taken there
        inner = [(z, r) for z, r in self.p.turned_points if z1 + EPS < z < z0 - EPS]
        return [(z0, self._own_radius(section, True))] + inner + [(z1, self._own_radius(section, False))]

    def _own_radius(self, section: Section, free_side: bool) -> float:
        if section.kind == "groove":
            return (section.groove_from or section.d_free) / 2
        r = section.turned_d / 2 if section.kind in ("od_turn", "hex") else (
            section.d_free if free_side else section.d_chuck) / 2
        chamfer = section.chamfer_free if free_side else section.chamfer_chuck
        return r - chamfer if section.kind in ("od_turn", "hex") else r

    def build(self) -> list[Block | tuple[int, str]]:
        out: list = []
        rough = sorted((op for op in self.ops if op.tool_type == "turning_rough"),
                       key=lambda op: (self._copies(op), -(op.ref_diameter or 0), op.sequence))
        finish = [op for op in self.ops if op.tool_type == "turning_finish"]
        done_rough = done_finish = False
        for op in self.ops:
            section = self.p.section_of(op.feature_id)
            if op.tool_type == "facing":
                out.append(self._result(op, self.facing(op)))
            elif op.tool_type == "turning_rough":
                if done_rough:
                    continue
                done_rough = True
                for r_op in rough:
                    sec = self.p.section_of(r_op.feature_id)
                    out.append(self._result(r_op, self.rough(r_op, sec) if sec else "not in the profile"))
            elif op.tool_type == "turning_finish":
                if done_finish:
                    continue
                done_finish = True
                out += self._finish_groups(finish)
            elif section is None and op.tool_type not in ("drilling", "parting", "boring", "boring_rough"):
                out.append((op.id, "not in the profile: not generated"))
            else:
                out.append(self.other(op, section))
        return out

    def other(self, op: OpData, section: Section | None):
        if op.tool_type == "grooving":
            result = self.groove(op, section)
        elif op.tool_type == "threading":
            result = self.thread(op, section)
        elif op.tool_type == "drilling":
            result = self.drill(op)
        elif op.tool_type in ("boring", "boring_rough") and op.mode == "rough":
            result = self.bore_rough(op)
        elif op.tool_type == "boring" and op.feature_type == "bore":
            result = self.bore_finish(op)
        elif op.tool_type == "parting":
            result = self.parting(op)
        else:
            result = f"{op.tool_type}: not generated in this version"
        return self._result(op, result)

    def _above(self, z_top: float, z_bottom: float) -> float:
        """A radius above the material between two z: the turned profile there, its allowance, the clearance."""
        radii = [r for z, r in self.p.turned_points if z_bottom - EPS <= z <= z_top + EPS]
        radii += [self.p.turned(z_top), self.p.turned(z_bottom)]
        return max(radii) + self.allowance + self.m.clearance_x

    # grooving: plunges -------------------------------------------------------------------------------
    def groove(self, op: OpData, section: Section | None) -> Block | str:
        if section is None or section.kind != "groove":
            return "not a groove of the profile: not generated"
        plunges_first = any(o.tool_type == "grooving" and o.mode == "rough" and o.feature_id == op.feature_id
                            for o in self.ops)
        if op.mode == "finish" and plunges_first:  # the pass after the plunges (a groove with a fine finish)
            return "groove finishing pass (walls and bottom): not generated in this version"
        w = op.insert_width
        if not w:
            return "the grooving insert has no width: not generated"
        width = section.z_free - section.z_chuck
        if w > width + 0.01:
            return f"insert {w:g} wider than the groove {width:g}: not generated"
        bottom = section.d_free / 2
        start = (op.ref_diameter or section.groove_from or section.d_free) / 2
        wall = max(0.0, start - bottom - (op.depth or 0.0)) if op.depth is not None else 0.0  # finishing allowance
        first_edge, last_edge = section.z_free - wall, section.z_chuck + wall + w  # the insert's free-side edge
        plunges = max(1, op.passes or 1)
        step = (first_edge - last_edge) / (plunges - 1) if plunges > 1 else 0.0
        offset = 0.0 if self.m.groove_reference == "toward Z0" else -w  # programmed corner vs free-side edge
        b = self.block(op, f"GROOVE D{section.d_free:g} W{width:g}, INSERT {w:g}, {plunges} "
                           + ("PLUNGE" if plunges == 1 else "PLUNGES"))
        above = self._above(section.z_free, section.z_chuck)
        self._start(b, op)
        b.commands.append(Rapid(x=_r3(2 * above)))
        for i in range(plunges):
            z = _r3(first_edge - i * step + offset)
            b.commands += [Rapid(z=z), Feed(x=_r3(2 * (bottom + wall)), f=op.f)]
            if self.m.groove_dwell:
                b.commands.append(Dwell(self.m.groove_dwell))
            b.commands.append(Rapid(x=_r3(2 * above)))
        self._end(b)
        b.warnings.append(f"Z is the insert's corner {self.m.groove_reference} (the operator's touch-off): check")
        return b

    # threading: G92 per pass -------------------------------------------------------------------------
    def thread(self, op: OpData, section: Section | None) -> Block | str:
        from ..planner import thread_infeed_plan

        if section is None or not section.pitch:
            return "not an external thread of the profile: not generated"
        if not op.n:
            return "no spindle speed: not generated"
        infeed, _ = thread_infeed_plan(section.pitch, op.ap_min or 0.0, op.ap_max or 0.0) \
            if op.ap_max else thread_infeed_plan(section.pitch, 0.0, 1.0)
        d = section.turned_d
        machine_run_in = self.m.thread_run_in or 0.0
        rule = _r3(THREAD_RUN_IN_PITCHES * section.pitch)
        run_in = max(machine_run_in, rule)
        if rule > machine_run_in + EPS:
            why = f"RUN-IN {run_in:g} = {THREAD_RUN_IN_PITCHES}XP{section.pitch:g} (MACHINE VALUE {machine_run_in:g} IS LESS)"
        else:
            why = f"RUN-IN {run_in:g} = MACHINE VALUE (NOT LESS THAN {THREAD_RUN_IN_PITCHES}XP{section.pitch:g} = {rule:g})"
        z_start = _r3(section.z_free + run_in)
        # every pass drops to its diameter at the run-in's start: the material there (its allowance where no
        # finishing pass is written) must stay below the deepest pass
        root = d / 2 - sum(infeed)
        for other in self.p.sections:
            if other.z_chuck >= z_start - EPS or other.z_free <= section.z_free + EPS:
                continue
            radii = [r for z, r in self.p.turned_points if other.z_chuck - EPS <= z <= other.z_free + EPS]
            left = max(radii) + (0.0 if other.index in self.finished else self.allowance)
            if left > root - EPS:
                return (f"the thread's run-in passes over material above its root (a larger section, or one not "
                        f"finished by this program) towards the free end: not generated (the thread needs a free "
                        f"run-in of {run_in:g} mm: the machine value {machine_run_in:g}, at least "
                        f"{THREAD_RUN_IN_PITCHES} × P = {rule:g})")
        b = self.block(op, f"THREAD M{section.d_free:g}X{section.pitch:g} G92 {len(infeed)} PASSES")
        b.commands.append(Comment(why))
        x_start = _r3(d + 2 * self.m.clearance_x)
        b.commands.append(Spindle("rpm", int(op.n), self.m.spindle, self.m.max_rpm))
        b.commands.append(Rapid(self.x_safe, self.z_safe))
        if self.m.coolant:
            b.commands.append(Coolant(True))
        b.commands += [Rapid(z=z_start), Rapid(x=x_start)]
        depth = 0.0
        for cut in infeed:
            depth += cut
            b.commands.append(ThreadPass(_r3(d - 2 * depth), _r3(section.z_chuck), section.pitch))
        self._end(b)
        if section.chamfer_chuck == 0 and section.index + 1 < len(self.p.sections) \
                and self.p.sections[section.index + 1].kind != "groove":
            b.warnings.append("the thread ends at a shoulder without a relief groove: G92 pulls out at the thread "
                              "end: check")
        if self.m.max_thread_feed is None:
            b.warnings.append(f"check n·P = {op.n * section.pitch:g} mm/min for your machine (no threading limit "
                              "set)")
        return b

    # drilling on the axis: G97, pecks ---------------------------------------------------------------
    def drill(self, op: OpData) -> Block | str:
        if not op.n or not op.depth:
            return "no spindle speed or depth: not generated"
        point = (op.tool_diameter / 2 / math.tan(math.radians(op.point_angle / 2))
                 if op.tool_diameter and op.point_angle else None)
        if op.feature_type == "bore" and op.depth_mode is None:
            return "the bore's depth (to the full Ø or to the point) is not decided: not generated"
        if op.depth_mode == "full_diameter" and point is None:
            return "the depth to the full Ø needs the drill's point angle: not generated"
        bore = self.p.bore_of(op.feature_id) if op.feature_type == "bore" else None
        if bore is not None and bore.problem:
            return f"{bore.problem}: not generated"
        if bore is not None and bore.bottom == "flat" and op.depth_mode == "full_diameter" \
                and abs(-bore.z_bottom - op.depth) < EPS:
            return ("a flat bottom: the drill's point would go below it (decide the depth to the drill's point): not "
                    "generated")
        drawn = op.depth
        depth = _r3(drawn + point) if op.depth_mode == "full_diameter" else drawn
        q = self.m.peck_depth
        b = self.block(op, f"DRILL D{op.tool_diameter or 0:g} DEPTH {drawn:g} ON THE AXIS")
        if op.depth_mode == "full_diameter":
            b.commands.append(Comment(f"DEPTH {drawn:g} TO THE FULL D{op.tool_diameter:g}: POINT TO Z{-depth:g} "
                                      f"({op.point_angle:g} DEG POINT {point:.2f}), {ascii_text(op.depth_decided or '')}"))
        elif op.depth_mode == "tip":
            b.commands.append(Comment(f"DEPTH {drawn:g} TO THE DRILL'S POINT"
                                      + (f": FULL D{op.tool_diameter:g} {max(0.0, drawn - point):.2f} DEEP" if point else "")
                                      + f", {ascii_text(op.depth_decided or '')}"))
        cz = self.m.clearance_z
        b.commands.append(Spindle("rpm", int(op.n), self.m.spindle, self.m.max_rpm))  # G97: fixed rpm on X0
        b.commands.append(Rapid(self.x_safe, self.z_safe))
        if self.m.coolant:
            b.commands.append(Coolant(True))
        b.commands += [Rapid(x=0.0), Rapid(z=_r3(cz))]
        reached = 0.0
        while reached < depth - EPS:
            target = min(depth, reached + q)
            if reached > 0:
                b.commands.append(Rapid(z=_r3(-reached + cz)))  # back down the drilled hole
            b.commands += [Feed(z=_r3(-target), f=op.f), Rapid(z=_r3(cz))]
            reached = target
        b.commands.append(Rapid(z=self.z_safe))
        self._end(b)
        if op.depth_mode is None and op.tool_diameter:  # a tap drill: the drawing's depth at the point
            b.warnings.append("Z is the drill's point: its full Ø stops shorter: check the drawing's depth")
        if op.max_depth and depth > op.max_depth + EPS:
            b.warnings.append(f"the drill's point goes to {depth:g} mm, the drill reaches {op.max_depth:g}: use a "
                              "longer drill")
        if not self.p.inner_known:  # a bore without its place, or an internal thread
            b.warnings.append("the inner profile is not simulated (only the drill's path on the axis)")
        r = (op.tool_diameter or 0.0) / 2
        # where the drill's full Ø ends: the drawn depth when the operator decided "to the full Ø" (the point's Z is
        # written rounded to 0.001, which must not put the full Ø a fraction of a µm short of it)
        full = drawn if op.depth_mode == "full_diameter" else (depth - point if point else depth)
        self.holes.append((0.0, r, -full, r))
        if point:
            self.holes.append((-full, r, -depth, 0.0))
        return b

    # boring: rough passes, the drill's cone faced, the finishing contour (G41) ------------------------------
    def _hole_at(self, z: float) -> float:
        """The hole's radius at z that the blocks written so far leave (0: none)."""
        out = 0.0
        for za, ra, zb, rb in self.holes:
            if zb - EPS <= z <= za + EPS:
                out = max(out, ra if abs(za - zb) < EPS else ra + (rb - ra) * (z - za) / (zb - za))
        return out

    def _bore_problem(self, op: OpData, bore) -> str | None:
        if bore is None:
            return "the bore's place on the drawing is not set (the job page): not generated"
        if bore.problem:
            return f"{bore.problem}: not generated"
        if op.spindle_direction is None:
            return "the spindle direction for this bar is not set (the operator's, on the tool page): not generated"
        if op.dmin is None:
            return "DMIN of the bar is not set (the tool page): whether its head fits the hole is not known: not generated"
        if op.max_depth is None:
            return "the bar's overhang (its max depth, the tool page) is not set: not generated"
        if -bore.z_bottom > op.max_depth + EPS:
            return f"the bore goes {-bore.z_bottom:g} mm deep, the bar's overhang reaches {op.max_depth:g}: not generated"
        return None

    def _step_back(self, r_wall: float, r_narrow: float, dmin: float) -> float | None:
        """How far the bar's edge steps back from a wall of r_wall towards the axis: the machine's retract, less when
        its head (DMIN) across the axis would meet the narrowest hole it stands in (r_narrow); None: no room."""
        room = r_wall - (dmin - r_narrow)
        back = min(self.m.retract, room)
        return back if back > EPS else None

    def _bore_start(self, b: Block, op: OpData, r_in: float) -> None:
        if op.vc:
            b.commands.append(Spindle("css", int(round(op.vc)), op.spindle_direction, self.m.max_rpm))
        else:
            b.commands.append(Spindle("rpm", int(op.n), op.spindle_direction, self.m.max_rpm))
        b.commands.append(Rapid(self.x_safe, self.z_safe))
        if self.m.coolant:
            b.commands.append(Coolant(True))
        b.commands += [Rapid(x=_r3(2 * r_in)), Rapid(z=_r3(self.m.clearance_z))]
        self.boring_spindle[op.position] = (op.tool_name, op.spindle_direction)

    def _bore_end(self, b: Block) -> None:
        b.commands.append(Rapid(z=self.z_safe))
        self._end(b)

    def _drilled_to(self, bore, r: float) -> float:
        """The deepest z down to which the hole is at least r wide from the bore's top (the drill's full Ø)."""
        steps = int(round((bore.z_top - bore.z_bottom) / 0.01))
        k = 0  # whole steps of 0.01 (no running sum: it drifts); a hole within 0.001 of r is r (written to 0.001)
        while k < steps and self._hole_at(bore.z_top - (k + 1) * 0.01) >= r - DRILLED_TOL_MM:
            k += 1
        z = bore.z_top - k * 0.01
        return _r3(z) if z - bore.z_bottom > 0.005 else bore.z_bottom

    def bore_rough(self, op: OpData) -> Block | str | None:
        bore = self.p.bore_of(op.feature_id)
        why = self._bore_problem(op, bore)
        if why:
            return why
        if not op.passes or not op.ap:
            return None
        r0 = self._hole_at(bore.z_top - 0.01)
        if r0 <= EPS:
            return "the hole is not drilled in this program before boring: not generated"
        allow = next((o.ap or 0.0 for o in self.ops if o.tool_type == "boring" and o.mode == "finish"
                      and o.feature_id == op.feature_id), 0.0)
        target = bore.diameter / 2 - allow
        if target <= r0 + EPS:
            self.covered.append(f"operation {op.id} (rough boring D{bore.diameter:g}): the drill already took the "
                                "material")
            return Block(op.sequence, "", op.position, op.tool_name, [op.id], op.tool_type)
        z_end = bore.z_bottom + (allow if bore.bottom == "flat" else 0.0)
        end = max(_r3(z_end), self._drilled_to(bore, r0))
        n = op.passes
        step = (target - r0) / n
        back = self._step_back(r0, r0, op.dmin)  # none: the bar comes in over the face at its first pass's Ø
        b = self.block(op, f"ROUGH BORE D{bore.diameter:g} {n} X AP {round(step, 3):g} FROM D{2 * r0:g}")
        b.notes.append(f"leaves {allow:g} mm per side for finishing; spindle {op.spindle_direction} for this bar "
                       "(the operator's)")
        self._bore_start(b, op, r0 - back if back is not None else r0 + step)
        at = r0 - back if back is not None else r0 + step
        for k in range(1, n + 1):
            r = r0 + k * step
            back = self._step_back(r, r - (op.nose_radius or 0.0), op.dmin)  # the nose rounds the pass's end
            if back is None:
                return f"the bar's head (DMIN {op.dmin:g}) leaves no room to step back from D{2 * r:g}: not generated"
            out = [Feed(x=_r3(2 * r), f=op.f)] if abs(r - at) > EPS else []
            b.commands += out + [Feed(z=end, f=None if out else op.f), Feed(x=_r3(2 * (r - back))),
                                 Rapid(z=_r3(self.m.clearance_z))]
            at = r - back
            self.holes.append((0.0, r, end, r))
        if end > z_end + EPS and bore.bottom != "through":
            b.warnings.append(f"Z{end:g} to Z{z_end:g}: the drill's cone is left for the finishing bar to face")
        self._bore_end(b)
        return b

    def _face_cone(self, b: Block, op: OpData, bore, r_wall: float, z_from: float, z_to: float) -> float:
        """The drill's cone between z_from and z_to faced away from the centre outwards to r_wall, in levels no deeper
        than the bar's ap max; a level is faced only where the hole there is wide enough for the bar's head (its edge
        starts at the cone's surface, in the air). What it cannot reach is left (and said so). Returns the lowest z
        faced (z_from: none)."""
        if op.entering_angle is None or op.entering_angle < 90 - EPS:
            b.warnings.append(f"Z{z_from:g} to Z{z_to:g}: the drill's cone is not faced: facing from the centre outwards "
                              "needs κr ≥ 90° (κr of the holder " + ("is not set" if op.entering_angle is None else
                                                                   f"is {op.entering_angle:g}°") + ")")
            return z_from
        depth = op.ap_max or op.ap or 0.0
        if depth <= EPS:
            return z_from
        levels = max(1, math.ceil(round((z_from - z_to) / depth, 9)))
        cz = _r3(self.m.clearance_z)
        reached = z_from
        for k in range(1, levels + 1):
            level = _r3(z_from - (z_from - z_to) * k / levels)
            r_s = self._hole_at(level)
            if op.dmin - r_s > r_s + EPS:
                b.warnings.append(f"Z{level:g} to Z{z_to:g}: the drill's cone is left (the hole there is D{2 * r_s:g}, the "
                                  f"bar's head needs D{op.dmin:g} to reach in)")
                return reached
            back = self._step_back(r_wall, r_wall, op.dmin)
            b.commands += [Rapid(x=_r3(2 * r_s)), Rapid(z=_r3(level + self.m.retract)), Feed(z=level, f=op.f),
                           Feed(x=_r3(2 * r_wall)), Feed(x=_r3(2 * (r_wall - (back or 0.0)))), Rapid(z=cz)]
            self.holes.append((_r3(z_from), r_wall, level, r_wall))
            reached = level
        return reached

    def bore_finish(self, op: OpData) -> Block | str:
        bore = self.p.bore_of(op.feature_id)
        why = self._bore_problem(op, bore)
        if why:
            return why
        r = bore.diameter / 2
        r_now = self._hole_at(bore.z_top - 0.01)
        if r_now <= EPS:
            return "the hole is not drilled in this program before boring: not generated"
        back = self._step_back(r_now, r_now, op.dmin)  # none: in over the face at the hole's Ø
        b = self.block(op, f"FINISH BORE D{bore.diameter:g} TO Z{bore.z_bottom:g}")
        b.notes.append(f"spindle {op.spindle_direction} for this bar (the operator's)")
        self._bore_start(b, op, r_now - (back or 0.0))
        drilled = self._drilled_to(bore, r_now)
        wall_end = bore.z_bottom
        if bore.bottom != "through" and drilled > bore.z_bottom + EPS:
            wall_end = self._face_cone(b, op, bore, r_now, drilled, bore.z_bottom)
            if wall_end > bore.z_bottom + EPS:
                b.warnings.append(f"the wall is finished down to Z{wall_end:g} only (the drill's cone below is left)")
        comp = bool(op.nose_radius and op.tip_direction is not None)
        g41 = "G41" if comp else None
        cz = _r3(self.m.clearance_z)
        if bore.chamfer:
            top = r + bore.chamfer
            b.commands += [Feed(x=_r3(2 * (top + cz)), z=cz, f=op.f, comp=g41), Feed(x=_r3(2 * r), z=_r3(-bore.chamfer))]
        else:
            b.commands += [Feed(x=_r3(2 * r), f=op.f, comp=g41)]
        b.commands.append(Feed(z=_r3(wall_end)))
        step = self._step_back(r, r - (op.nose_radius or 0.0), op.dmin) or 0.0
        b.commands.append(Feed(x=_r3(2 * (r - step))))  # off the wall towards the axis (along the bottom)
        b.commands.append(Rapid(z=cz, comp="G40" if comp else None))
        self.holes.append((0.0, r, wall_end, r))
        self._bore_end(b)
        if comp:
            self.compensated_inner[op.position] = (op.tool_name, op.nose_radius, op.tip_direction)
            b.notes.append(f"nose radius compensation G41: the bore as drawn (offset R{op.nose_radius:g} "
                           f"T{op.tip_direction})")
        elif bore.chamfer and op.nose_radius:
            b.warnings.append(f"no nose radius compensation: the mouth chamfer comes out about "
                              f"{round(op.nose_radius * (math.sqrt(2) - 1), 3):g} mm (normal) off (rε {op.nose_radius:g}): "
                              "check")
        return b

    # parting ---------------------------------------------------------------------------------------
    def parting(self, op: OpData) -> Block | str:
        w = op.insert_width
        if not w:
            return "the parting insert has no width: not generated"
        length = self.p.length
        offset = 0.0 if self.m.groove_reference == "toward Z0" else -w  # programmed corner vs the Z0-side corner
        z = _r3(-length + offset)
        x_end = _r3(max(0.0, (op.ref_diameter or self.job.blank_diameter) - 2 * (op.depth or 0.0)))
        b = self.block(op, f"PART OFF AT Z{-length:g}, INSERT {w:g}")
        self._start(b, op)
        back = self._back_chamfer()
        if back is not None:  # the part's back chamfer, with the insert's Z0-side corner, before parting
            r, c = back
            b.commands += [Comment(f"BACK CHAMFER {c:g}X45 WITH THE INSERT'S CORNER: SLOT FIRST, THEN ALONG THE "
                                   "CHAMFER"),
                           Rapid(z=z), Feed(x=_r3(2 * (r - c)), f=op.f), Rapid(x=self.x_safe),
                           Rapid(z=_r3(-length + c + self.m.clearance_x + offset)),
                           Rapid(x=_r3(2 * (r + self.m.clearance_x))),
                           Feed(x=_r3(2 * (r - c)), z=_r3(-length + offset), f=op.f), Rapid(x=self.x_safe)]
            b.warnings.append(f"the back chamfer {c:g}×45° is cut with the parting insert's corner (side load): "
                              "check the insert allows it")
            self.back_chamfer_done = True
        if x_end == 0:
            x_end = _r3(-2 * (self.m.parting_overshoot or 0.0))
            b.commands.append(Comment(f"PARTING PAST THE AXIS TO X{x_end:g} IN G96: SPINDLE RUNS UP TO G50 "
                                      f"S{self.m.max_rpm} - OPERATOR MAY SWITCH TO G97"))
            b.warnings.append(f"parting to the axis in G96: the spindle runs up to G50 S{self.m.max_rpm}; the "
                              "operator may switch to G97")
        b.commands += [Rapid(z=z), Feed(x=x_end, f=op.f), Rapid(x=self.x_safe)]
        self._end(b)
        b.warnings.append("feed not reduced near the axis: that rule is a placeholder (not written)")
        b.warnings.append(f"Z is the insert's corner {self.m.groove_reference} (the operator's touch-off): check")
        return b

    def _back_chamfer(self) -> tuple[float, float] | None:
        """(radius, leg) of a chamfer at the part's back end that the finishing tool left (too steep for it)."""
        last = self.p.sections[-1] if self.p.sections else None
        if last is None or not last.chamfer_chuck or last.kind not in ("od_turn", "hex"):
            return None
        if not any(section is last for section, *_ in self.descents):
            return None
        return last.turned_d / 2, last.chamfer_chuck

    def _finish_groups(self, finish: list[OpData]):
        by_section = {}
        out = []
        for op in finish:
            sec = self.p.section_of(op.feature_id)
            if sec is None or sec.kind not in ("od_turn", "hex", "taper", "arc"):
                out.append((op.id, "not a cylinder, taper or arc of the profile: not generated"))
                continue
            if sec.kind == "arc" and sec.arc is None:
                out.append((op.id, f"the arc is not programmed: {sec.arc_problem}"))
                continue
            by_section[sec.index] = op
        groups, current = [], []
        for sec in self.p.sections:
            if sec.index in by_section and current and by_section[sec.index].position != next(
                    op for _, op in current if op is not None).position:
                while current and current[-1][1] is None:  # a bridged groove belongs to no tool's contour
                    current.pop()
                groups.append(current)  # another tool finishes this section: a contour of its own
                current = []
            if sec.index in by_section:
                current.append((sec, by_section[sec.index]))
            elif sec.kind == "groove" and current and any(
                    i in by_section for i in range(sec.index + 1, sec.index + 2)):
                current.append((sec, None))  # bridged at the Ø it is cut from
            elif current:
                groups.append(current)
                current = []
        if current:
            groups.append(current)
        for group in groups:
            while group and group[-1][1] is None:
                group.pop()
            out.append(self.finish(group))
        return out

    @staticmethod
    def _result(op, result):
        if result is None:
            return op.id, "no roughing stock left: nothing to cut"
        return (op.id, result) if isinstance(result, str) else result


def _section_name(section: Section) -> str:
    if section.kind == "taper":
        return f"TAPER D{section.d_free:g}-D{section.d_chuck:g}"
    if section.kind == "arc":
        return f"ARC R{section.radius:g} D{section.d_free:g}-D{section.d_chuck:g}"
    return f"D{section.turned_d:g}"


def _scaled(arc: Arc, offset: Arc, z: float, r: float) -> tuple[float, float]:
    """A point of an arc moved along its radius onto a concentric one."""
    length = math.hypot(z - arc.cz, r - arc.cr) or 1.0
    return arc.cz + (z - arc.cz) * offset.radius / length, arc.cr + (r - arc.cr) * offset.radius / length


def _tangent(segment: Segment) -> tuple[float, float]:
    """The unit direction (z, r) of a segment at its free-end start, towards the chuck."""
    dz, dr = segment.z1 - segment.z0, segment.r1 - segment.r0
    if segment.arc is not None:
        a = segment.arc
        uz, ur = segment.z0 - a.cz, segment.r0 - a.cr
        tz, tr = -ur, uz
        if tz * dz + tr * dr < 0:
            tz, tr = -tz, -tr
        dz, dr = tz, tr
    length = math.hypot(dz, dr) or 1.0
    return dz / length, dr / length


def _steepest_descent(segment: Segment) -> float | None:
    """The steepest angle (degrees to the axis) at which an arc goes down towards the chuck; None: it never does."""
    points = segment.points()
    steepest = None
    for (z0, r0), (z1, r1) in zip(points, points[1:]):
        if z1 < z0 - EPS and r1 < r0 - 1e-9:
            angle = math.degrees(math.atan2(r0 - r1, z0 - z1))
            steepest = angle if steepest is None else max(steepest, angle)
    return steepest


def _position(commands) -> tuple[float | None, float | None]:
    """Where the moves of a block leave the tool (x, z); None for an axis not moved yet."""
    x = z = None
    for c in commands:
        if isinstance(c, (Rapid, Feed, ArcMove)):
            x = c.x if c.x is not None else x
            z = c.z if c.z is not None else z
    return x, z


def ascii_text(text: str) -> str:
    """Upper-case ASCII for a control's comments (Ukrainian letters transliterated, others dropped)."""
    table = {"а": "a", "б": "b", "в": "v", "г": "h", "ґ": "g", "д": "d", "е": "e", "є": "ye", "ж": "zh", "з": "z",
             "и": "y", "і": "i", "ї": "yi", "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p",
             "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh",
             "щ": "shch", "ь": "", "ю": "yu", "я": "ya", "ё": "e", "ы": "y", "э": "e", "ъ": "", "ø": "D", "Ø": "D",
             "×": "X", "°": "DEG", "–": "-", "—": "-", "·": "*"}
    out = "".join(table.get(ch.lower(), ch) if ch.lower() in table else ch for ch in text)
    out = "".join(ch for ch in out if 32 <= ord(ch) < 127 and ch not in "()")
    return " ".join(out.upper().split())


def build(job: JobData, machine: MachineData, profile: Profile, ops: list[OpData],
          skipped: dict[int, str] | None = None, dialect: str = "FANUC 0I-T (G CODE SYSTEM A)") -> Program:
    """The program for the usable operations (in their sequence), the skipped ones listed with their reason."""
    program = Program(
        number=job.id % 10000 or 1,
        job_title=ascii_text(job.name),
        header=[f"JOB {job.id} {ascii_text(job.name)}", f"MATERIAL {ascii_text(job.material)}",
                f"BLANK {ascii_text(job.blank_label)}, STICK-OUT {job.stickout:g} MM",
                f"Z0 ON THE FREE END FACE ({job.free_end.upper()} ON THE DRAWING), X0 ON THE AXIS",
                "X IN DIAMETER, FEED PER REVOLUTION", "TOOL TIP: IMAGINARY POINT, NO NOSE RADIUS COMPENSATION",
                "GENERATED BY TURNPILOT - NOT RUN ON THE MACHINE YET",
                "CHECK: GRAPHICS, DRY RUN WITH AN OFFSET, SINGLE BLOCK, RAPID 25 PCT",
                f"DIALECT {dialect}"],
        skipped=dict(skipped or {}),
    )
    program.warnings += profile.warnings
    if machine.coolant is None:
        program.warnings.append("coolant not known for this machine: M08 not written; the catalogue's Vc are "
                                "with coolant: check")
    elif machine.coolant is False:
        program.warnings.append("the machine has no coolant: the catalogue's Vc are with coolant: check")
    builder = _Builder(job, machine, profile, ops)
    for item in builder.build():
        if isinstance(item, Block):
            if item.commands:
                program.blocks.append(item)
        else:
            op_id, reason = item
            program.skipped[op_id] = reason
    program.notes += builder.covered
    program.compensation = [(pos, name, r, t) for pos, (name, r, t) in sorted(builder.compensated.items())]
    program.inner_compensation = [(pos, name, r, t) for pos, (name, r, t) in sorted(builder.compensated_inner.items())]
    program.boring_spindle = [(pos, name, d) for pos, (name, d) in sorted(builder.boring_spindle.items())]
    if program.compensation or program.inner_compensation:
        i = program.header.index("TOOL TIP: IMAGINARY POINT, NO NOSE RADIUS COMPENSATION")
        lines = ["TOOL TIP: IMAGINARY POINT"]
        if program.compensation:
            lines += ["FINISHING CONTOUR: NOSE RADIUS COMPENSATION G42"] + [
                f"OFFSET PAGE T{pos:02d} {ascii_text(name)}: R{r:g} T{t}" for pos, name, r, t in program.compensation]
        if program.inner_compensation:
            lines += ["FINISHING BORE: NOSE RADIUS COMPENSATION G41"] + [
                f"OFFSET PAGE T{pos:02d} {ascii_text(name)}: R{r:g} T{t}" for pos, name, r, t in program.inner_compensation]
        program.header[i:i + 1] = lines
    if program.boring_spindle:
        at = program.header.index("X IN DIAMETER, FEED PER REVOLUTION")
        program.header[at:at] = [f"SPINDLE FOR BORING BAR T{pos:02d} {ascii_text(name)}: {d} (THE OPERATOR'S)"
                                 for pos, name, d in program.boring_spindle]
    for section, z0, z1, x0, x1, angle in builder.descents:
        if builder.back_chamfer_done and section is profile.sections[-1] and abs(z1 + profile.length) < EPS:
            continue
        program.warnings.append(f"Z{z0:g} to Z{z1:g} (D{x0:g} to D{x1:g}, {angle:.0f}° down towards the chuck) is not "
                                "cut: a tool that may go down at that angle, or a second set-up")
    return program
