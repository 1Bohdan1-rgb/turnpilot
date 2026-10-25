"""The operator's decisions a job needs before its G-code, on one screen: what the program proposes for each (with why
and where it comes from), what is decided already, and what cannot be proposed without inventing a number (left
empty). Nothing here writes: the operator confirms all of it at once, with a name (apply_decisions).

Proposals and their sources:
- an arc whose drawn radius differs from its dimension: the dimension, unless its top rises above the neighbouring Ø
  (arc_top), then the geometry, with that reason;
- a bore's depth: to the full Ø (a drawn depth is the full Ø's: the drafting convention), or to the drill's point when
  the drill has no point angle (the only one that can be programmed);
- the free end (Z0): the end opposite the largest Ø (assumption: the chuck holds the largest Ø, as the planner's);
- the stock beyond Z0: the facing allowance the suggested blank is made with (BLANK_FACING_ALLOWANCE_MM, a placeholder);
- the stick-out: the hint (services.stickout_hint) with that stock;
- the turret set-up: services.turret_setup_proposal;
- an operation with no tool: skipped only where there is a reason (a centring operation: no centre drill in the
  library's catalogues; the operator's decision for jobs/8, 2026-10-16), else empty;
- the operations: all approved, their warnings shown.
An arc's or fillet's shape and a bore's place are proposed only from the DXF (they are set there); else empty.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from flask import current_app

from . import services

MACHINE_BLOCKERS = ("the machine's max spindle speed", "programming values not set")
CENTRING_SKIP_NOTE = ("no centre drill in the catalogues of the library (Sandvik TT / SRT 2020); the 860 drills are "
                      "self-centring (140°): the operator's decision for jobs/8, 2026-10-16")
CONVENTION_FULL_DIAMETER = "a depth on a drawing is the full Ø's (the drafting convention)"
CHUCK_ASSUMPTION = "the end opposite the largest Ø: the chuck holds the largest Ø (an assumption, as the planner's)"
FACE_STOCK_PLACEHOLDER = ("the facing allowance the suggested blank is made with (BLANK_FACING_ALLOWANCE_MM, "
                          "a placeholder)")


@dataclass
class Decision:
    key: str  # the form field
    kind: str
    title: str
    options: list = field(default_factory=list)  # [(value, label)]; empty: a number to type
    value: object = None  # what the screen shows chosen: the decision taken, else the proposal, else None
    decided: str | None = None  # who and when, when it is decided already
    why: str = ""
    source: str = ""
    required: bool = True  # the program cannot be made while it is empty
    notes: list = field(default_factory=list)
    feature_id: int | None = None

    @property
    def empty(self) -> bool:
        return self.value is None or self.value == ""


@dataclass
class OperationRow:
    key: str  # "feature id:tool type:rough/finish": the same operation after a recalculation
    op: object
    approve: bool
    skip_note: str | None = None  # proposed skip with its reason (an operation with no tool)


@dataclass
class Decisions:
    drawing: list = field(default_factory=list)
    setup: list = field(default_factory=list)
    turret: object = None  # services.SetupProposal (rows, missing, unplaced) or None
    signature: str = ""
    operations: list = field(default_factory=list)  # OperationRow
    machine: list = field(default_factory=list)  # what the machine's / tools' data lack: not the job's decisions
    left_out: list = field(default_factory=list)  # (operation, why) the program leaves out (not skipped by hand)
    calculated: bool = False

    @property
    def items(self):
        return self.drawing + self.setup

    @property
    def empty_required(self):
        return [d for d in self.items if d.required and d.empty]


def op_key(op) -> str:
    return f"{op.feature_id}:{op.tool_type}:{op.rough_finish}"


def _who(name, at) -> str | None:
    if not name and not at:
        return None
    from .localtime import local
    return f"{name or '—'}, {local(at, '%Y-%m-%d %H:%M') if at else ''}".strip(", ")


def _feature_title(f) -> str:
    return services._feature_label(f)


def _arcs(job) -> list[Decision]:
    out = []
    conflicts = services.radius_conflicts(job) if job.axial_order_known else {}
    for f in job.active_features:
        if f.type != "arc" or f.drawn_radius is None:
            continue
        decided = f.radius_decisions[-1] if f.radius_decisions else None
        dimension = f.radius_dimension if decided else f.radius
        geometry = f.radius_geometry if decided else f.drawn_radius
        if decided is None and f.id not in conflicts:
            continue
        options = [("dimension", f"R{dimension:g} (the dimension)"), ("drawn", f"R{geometry:g} (the geometry)")]
        d = Decision(f"arc-{f.id}", "arc_radius", f"{_feature_title(f)}: radius", options, feature_id=f.id)
        top = services.arc_top(job, f, dimension)
        if decided is not None:
            d.value = decided.source if decided.source in ("dimension", "drawn") else None
            d.decided = _who(decided.decided_by, decided.decided_at)
            d.why = f"R{decided.radius:g} decided" + (f": {decided.note}" if decided.note else "")
            d.source = services.RADIUS_SOURCES.get(decided.source, decided.source)
            if decided.source == "operator":
                d.options.append(("operator", f"R{decided.radius:g} (own value)"))
                d.value = "operator"
        elif top:
            d.value, d.source = "drawn", "the drawing's geometry"
            d.why = f"{top}; R{geometry:g} (the geometry) keeps the profile under it"
        else:
            d.value, d.source = "dimension", "the dimension on the drawing"
            d.why = f"the dimension R{dimension:g}; the arc is drawn R{geometry:g}"
        if top and d.value == "dimension":
            d.notes.append(top)
        out.append(d)
    return out


def _shapes(job) -> list[Decision]:
    out = []
    for f in job.active_features:
        if f.type in ("arc", "fillet") and f.arc_convex is None:
            options = [("convex", "convex"), ("concave", "concave")]
            if f.type == "fillet":
                options = [(f"{s}-{side}", f"{s}, {side}") for s in ("convex", "concave") for side in ("left", "right")]
            out.append(Decision(f"shape-{f.id}", "arc_shape", f"{_feature_title(f)}: shape", options,
                                why="not known from the drawing (a row confirmed before the shapes were kept)",
                                source="—", feature_id=f.id))
    return out


def _bores(job) -> list[Decision]:
    out = []
    drills = services.bore_drills(job)
    to_size = services.bored_to_size(job)
    for f in job.active_features:
        if f.type != "bore":
            continue
        title = _feature_title(f)
        if f.bore_open_end is None:
            out.append(Decision(f"bore-{f.id}", "bore_position", f"{title}: its place (open end face, from Z, bottom)",
                                why="not on the drawing's rows (a bore entered by hand)", source="—",
                                feature_id=f.id))
        tool = drills.get(f.id)
        if tool is not None and f.length:
            options = [(m, label) for m, label in services.BORE_DEPTH_MODES.items()]
            d = Decision(f"depth-{f.id}", "bore_depth", f"{title}: the depth {f.length:g}", options, feature_id=f.id)
            if f.depth_mode:
                d.value, d.decided = f.depth_mode, _who(f.depth_decided_by, f.depth_decided_at)
                d.source = "decided"
            elif tool.point_angle_deg:
                d.value, d.why, d.source = "full_diameter", CONVENTION_FULL_DIAMETER, "drafting convention"
            else:
                d.value, d.source = "tip", "the drill's data"
                d.why = f"{tool.name} has no point angle: only to the drill's point can be programmed"
            hint = services.bore_depth_hints(job).get(f.id)
            if hint:
                d.notes.append(hint)
            out.append(d)
        if f.id in to_size:
            d = Decision(f"finish-{f.id}", "bore_finish", f"{title}: the finishing pass",
                         [("planner", "bored to size (as calculated)"), ("keep", "keep the finishing pass")],
                         required=False, feature_id=f.id, why=to_size[f.id], source="the planner")
            d.value = "keep" if f.bore_finish_kept_at else "planner"
            if f.bore_finish_kept_at:
                d.decided = _who(f.bore_finish_kept_by, f.bore_finish_kept_at)
            out.append(d)
    return out


def _setup(job, machine) -> list[Decision]:
    sides = [("left", "left end of the drawing"), ("right", "right end of the drawing")]
    free = Decision("free_end", "free_end", "Z0: the free end face", sides)
    if job.free_end:
        free.value, free.source = job.free_end, "the job's set-up"
    elif (suggested := services.suggest_free_end(job)) is not None:
        free.value, free.why, free.source = suggested, CHUCK_ASSUMPTION, "assumption (placeholder)"
    else:
        free.why = "the largest Ø is not at one end, or the order of the sections is not known"
    stock = Decision("face_stock", "face_stock", "Stock beyond Z0 to face off, mm")
    if job.face_stock_mm is not None:
        stock.value, stock.source = job.face_stock_mm, "the job's set-up"
    else:
        stock.value = current_app.config.get("BLANK_FACING_ALLOWANCE_MM")
        stock.why, stock.source = FACE_STOCK_PLACEHOLDER, "placeholder"
    stick = Decision("stickout", "stickout", "Stick-out from the jaws to the raw end face, mm")
    hint = services.stickout_hint(job, machine, face_stock=stock.value)
    if job.stickout_mm is not None:
        stick.value, stick.source = job.stickout_mm, "the job's set-up"
        stick.why = job.stickout_note or "typed"
    elif "value" in hint:
        stick.value, stick.source = hint["value"], "the stick-out hint"
        stick.why = (f"{hint['length']:g} + {hint['face_stock']:g} + {hint['parting']:g} + {hint['clearance']:g}"
                     f"{' (the safety distance to the jaws)' if hint['from_safety'] else ''}")
    else:
        stick.why = "no hint: " + "; ".join(hint["missing"])
    return [free, stock, stick]


def operator_decisions(job) -> Decisions:
    machine = services.job_machine(job)
    out = Decisions()
    out.drawing = _arcs(job) + _shapes(job) + _bores(job)
    out.setup = _setup(job, machine)
    out.calculated = bool(job.current_operations)
    if not out.calculated:
        return out
    proposal = services.turret_setup_proposal(job, machine)
    out.turret, out.signature = proposal, services.setup_signature(proposal)
    gives = set()
    if proposal.rows:  # what the set-up would give: the operations waiting for it are not "no tool"
        gives = {r.install.type for r in proposal.rows}
    for op in job.current_operations:
        row = OperationRow(op_key(op), op, approve=op.status != "skipped")
        if op.tool_id is None and op.status != "skipped" and op.tool_type not in gives:
            row.approve = False
            if op.tool_type == "centre_drilling":
                row.skip_note = CENTRING_SKIP_NOTE
        out.operations.append(row)
    readiness = services.gcode_readiness(job, machine)
    out.machine = [b for b in readiness.blockers if b.startswith(MACHINE_BLOCKERS) or "(tool data)" in b]
    by_id = {op.id: op for op in job.current_operations}
    out.left_out = [(by_id[i], why) for i, why in readiness.skipped.items()
                    if i in by_id and by_id[i].status != "skipped"]
    return out

