"""A program as a neutral list of commands, built from the approved operations and the profile from the part zero.

Coordinates: X is a diameter, Z is negative towards the chuck. The tool point is the insert's imaginary tip
(no nose radius compensation in this version). Every number comes from the operation (confirmed catalogue
rows or the operator), the machine (passport or operator) or the operator's programming values; nothing is
guessed. A dialect prints the commands as the text of one control (gcode/fanuc.py).

Motions are explicit (G00 / G01 and G92 per thread pass), no canned cycles: the simulation reads exactly the
moves the machine makes, and canned cycles differ between controls.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from ..planner import nose_radius_from_insert
from .profile import EPS, Profile, Section

# --- commands -----------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Rapid:
    x: float | None = None
    z: float | None = None


@dataclass(frozen=True)
class Feed:
    x: float | None = None
    z: float | None = None
    f: float | None = None  # mm/rev; None: the modal feed


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
    pitch: float | None = None  # threading: the feature's pitch
    warnings: tuple = ()  # the planner's warnings on the operation


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
        self.allowance = max((op.ap or 0.0 for op in ops if op.tool_type == "turning_finish"), default=0.0)

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
        b = self.block(op, f"ROUGH D{section.turned_d:g} {op.passes} X AP {op.ap:g} FROM D{op.ref_diameter:g}")
        target = section.turned_d / 2
        allowance = max(0.0, op.ref_diameter / 2 - op.passes * op.ap - target)
        z_mid = (section.z_free + section.z_chuck) / 2
        passes = []
        for i in range(1, op.passes + 1):
            radius = op.ref_diameter / 2 - i * op.ap
            level = radius - allowance
            if self._scan(z_mid, False, level) is not None:
                return ("a larger diameter between this section and the free end: roughing it needs plunging, "
                        "not generated")
            shoulder = self._scan(z_mid, True, level)
            z_end = -self.p.length if shoulder is None else min(shoulder + allowance, section.z_free)
            if self._roughed_to(z_end, radius):
                continue  # an earlier pass already took the material down to this radius over the span
            passes.append((radius, _r3(z_end)))
        if not passes:
            self.covered.append(f"operation {op.id} (rough Ø{section.turned_d:g}): the passes before it already "
                                f"took the material down to its Ø")
            return b  # no commands: left out of the program
        self.roughed += [(0.0, z_end, radius) for radius, z_end in passes]
        self._start(b, op)
        for radius, z_end in passes:
            b.commands += [Rapid(x=_r3(2 * radius)), Feed(z=z_end, f=op.f),
                           Feed(x=_r3(2 * (radius + self.m.retract))), Rapid(z=self.z_safe)]
        self._end(b)
        if allowance:
            b.notes.append(f"leaves {allowance:g} mm per side for finishing")
        return b

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
        names = ", ".join(f"D{s.turned_d:g}" for s, op in group if op is not None)
        b = self.block(first_op, f"FINISH {names}", [op.id for _, op in group if op is not None])
        self._start(b, first_op)
        first = sections[0]
        rnose = nose_radius_from_insert(first_op.insert_code)
        cz = self.m.clearance_z
        if first.index == 0:
            r_top = self.p.turned_points[0][1]
            if first.chamfer_free:  # onto the chamfer along its 45° line, from Z+clearance
                b.commands += [Rapid(z=_r3(cz)), Rapid(x=_r3(2 * max(r_top - cz, 0.0))),
                               Feed(x=_r3(2 * r_top), z=0.0, f=first_op.f)]
            else:
                b.commands += [Rapid(z=_r3(cz)), Rapid(x=_r3(2 * r_top)), Feed(z=0.0, f=first_op.f)]
        else:
            z_face = first.z_free
            above = max(r for z, r in self.p.turned_points if z >= z_face - EPS) + self.allowance + self.m.retract
            r_prev = self._own_radius(self.p.sections[first.index - 1], False)  # the neighbour at this face
            b.commands += [Rapid(x=_r3(2 * above)),
                           Rapid(z=_r3(z_face + self.allowance + self.m.clearance_z)),
                           Feed(x=_r3(2 * r_prev), f=first_op.f), Feed(z=_r3(z_face))]
        current_f = first_op.f
        current_vc = round(first_op.vc)
        for section, op in group:
            if op is not None and op.f != current_f:
                current_f = op.f
            if op is not None and round(op.vc) != current_vc:
                current_vc = round(op.vc)
                b.commands.append(Spindle("css", current_vc))
            for z, r in self._contour(section):
                here = _position(b.commands)
                if here != (_r3(2 * r), _r3(z)):
                    b.commands.append(Feed(x=_r3(2 * r), z=_r3(z), f=current_f))
            if op is not None:
                self.finished.add(section.index)
        b.commands += [Feed(x=self.x_safe), Rapid(z=self.z_safe)]
        self._end(b)
        chamfers = [s for s in sections if s.chamfer_free or s.chamfer_chuck]
        if chamfers and rnose:
            offset = round(rnose * (math.sqrt(2) - 1), 3)
            b.warnings.append(f"no nose radius compensation: 45° chamfers come out about {offset:g} mm (normal) "
                              f"fuller than drawn, legs about {round(offset * math.sqrt(2), 2):g} mm shorter "
                              f"(rε {rnose:g}): check")
        return b

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
                       key=lambda op: (-(op.ref_diameter or 0), op.sequence))
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
            elif section is None and op.tool_type not in ("drilling", "parting"):
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
            b.commands += [Rapid(z=z), Feed(x=_r3(2 * (bottom + wall)), f=op.f), Rapid(x=_r3(2 * above))]
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
        b = self.block(op, f"THREAD M{section.d_free:g}X{section.pitch:g} G92 {len(infeed)} PASSES")
        z_start = _r3(section.z_free + self.m.thread_run_in)
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
        q = self.m.peck_depth
        b = self.block(op, f"DRILL D{op.tool_diameter or 0:g} DEPTH {op.depth:g} ON THE AXIS")
        cz = self.m.clearance_z
        b.commands.append(Spindle("rpm", int(op.n), self.m.spindle, self.m.max_rpm))  # G97: fixed rpm on X0
        b.commands.append(Rapid(self.x_safe, self.z_safe))
        if self.m.coolant:
            b.commands.append(Coolant(True))
        b.commands += [Rapid(x=0.0), Rapid(z=_r3(cz))]
        reached = 0.0
        while reached < op.depth - EPS:
            target = min(op.depth, reached + q)
            if reached > 0:
                b.commands.append(Rapid(z=_r3(-reached + cz)))  # back down the drilled hole
            b.commands += [Feed(z=_r3(-target), f=op.f), Rapid(z=_r3(cz))]
            reached = target
        b.commands.append(Rapid(z=self.z_safe))
        self._end(b)
        if op.tool_diameter:
            b.warnings.append(f"Z is the drill's tip: its full Ø stops {round(0.182 * op.tool_diameter, 2):g} mm "
                              "shorter (140° point): check the drawing's depth")
        b.warnings.append("the inner profile is not simulated (only the drill's path on the axis)")
        return b

    # parting ---------------------------------------------------------------------------------------
    def parting(self, op: OpData) -> Block | str:
        w = op.insert_width
        if not w:
            return "the parting insert has no width: not generated"
        z = _r3(-self.p.length if self.m.groove_reference == "toward Z0" else -self.p.length - w)
        x_end = _r3(max(0.0, (op.ref_diameter or self.job.blank_diameter) - 2 * (op.depth or 0.0)))
        b = self.block(op, f"PART OFF AT Z{-self.p.length:g}, INSERT {w:g}")
        self._start(b, op)
        if x_end == 0:
            b.commands.append(Comment(f"PARTING TO THE AXIS IN G96: SPINDLE RUNS UP TO G50 S{self.m.max_rpm} - "
                                      "OPERATOR MAY SWITCH TO G97"))
            b.warnings.append(f"parting to the axis in G96: the spindle runs up to G50 S{self.m.max_rpm}; the "
                              "operator may switch to G97")
        b.commands += [Rapid(z=z), Feed(x=x_end, f=op.f), Rapid(x=self.x_safe)]
        self._end(b)
        b.warnings.append("feed not reduced near the axis: that rule is a placeholder (not written)")
        b.warnings.append(f"Z is the insert's corner {self.m.groove_reference} (the operator's touch-off): check")
        return b

    def _finish_groups(self, finish: list[OpData]):
        by_section = {}
        out = []
        for op in finish:
            sec = self.p.section_of(op.feature_id)
            if sec is None or sec.kind not in ("od_turn", "hex"):
                out.append((op.id, "not a cylinder of the profile: not generated"))
                continue
            by_section[sec.index] = op
        groups, current = [], []
        for sec in self.p.sections:
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


def _position(commands) -> tuple[float | None, float | None]:
    """Where the moves of a block leave the tool (x, z); None for an axis not moved yet."""
    x = z = None
    for c in commands:
        if isinstance(c, (Rapid, Feed)):
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
    return program
