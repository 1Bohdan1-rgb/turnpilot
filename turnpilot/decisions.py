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

import re
from dataclasses import dataclass, field

from flask import current_app

from . import services

MACHINE_BLOCKERS = ("the machine's max spindle speed", "programming values not set")
CENTRING_SKIP_NOTE = ("no centre drill in the catalogues of the library (Sandvik TT / SRT 2020); the 860 drills are "
                      "self-centring (140°): the operator's decision for jobs/8, 2026-10-16")
CONVENTION_FULL_DIAMETER = "a depth on a drawing is the full Ø's (the drafting convention)"
BORE_END = "the blind bore opens at this end face: it is drilled and bored from Z0"
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
        if top and not job.free_end:  # its Z are counted from the wrong end until Z0 is chosen: the diameters only
            top = re.sub(r" at Z-?[\d.]+", "", top)
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
    elif len(ends := {f.bore_open_end for f in job.active_features if f.type == "bore" and f.bore_open_end
                      and f.bore_bottom != "through"}) == 1:
        free.value, free.source = ends.pop(), "the drawing's bore"
        free.why = BORE_END
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


# --- the operator confirms all of it at once -------------------------------------------------------------------------

class DecisionError(ValueError):
    pass


@dataclass
class Applied:
    record: object = None  # the GcodeProgram made, None when stopped before it
    stopped: str | None = None  # where and why it stopped (what was written before stays, with its history)
    done: list = field(default_factory=list)  # what was written, in words


def proposal_form(d: Decisions) -> dict:
    """The screen's form as proposed (or as decided): {field: value}; the operations as shown and ticked."""
    form = {}
    for x in d.items:
        if x.kind == "bore_position" or x.empty:
            continue
        form[x.key] = x.value
    form["ops_shown"] = ",".join(r.key for r in d.operations)
    for r in d.operations:
        if r.approve:
            form[f"approve-{r.key}"] = "1"
        if r.skip_note:
            form[f"skip-{r.key}"] = "1"
            form[f"skipnote-{r.key}"] = r.skip_note
    if d.turret is not None and d.turret.rows:
        form["setup_accept"] = "1"
        form["setup_signature"] = d.signature
    return form


def _number(raw, what):
    raw = (str(raw) if raw is not None else "").strip().replace(",", ".")
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        raise DecisionError(f"{what}: '{raw}' is not a number") from None


def _feature_decisions(d, form, name, machine, out) -> bool:
    """The drawing's decisions; True when a feature changed (the job is to be calculated again)."""
    from .models import Feature, db

    changed = False
    for x in d.drawing:
        feature = db.session.get(Feature, x.feature_id)
        value = form.get(x.key)
        if x.kind == "arc_radius":
            if value not in ("dimension", "drawn") or (x.decided is not None and value == x.value):
                continue  # an own value, or the decision taken, stays
            dimension = feature.radius_dimension if feature.radius_decisions else feature.radius
            geometry = feature.radius_geometry if feature.radius_decisions else feature.drawn_radius
            radius = dimension if value == "dimension" else geometry
            services.decide_arc_radius(feature, value, radius, None, name, machine)
            out.done.append(f"{x.title}: R{radius:g}")
            changed = True
        elif x.kind == "arc_shape":
            shape, _, side = str(value).partition("-")
            feature.arc_convex = shape == "convex"
            if feature.type == "fillet":
                feature.face = side or None
            out.done.append(f"{x.title}: {value}")
            changed = True
        elif x.kind == "bore_position":
            try:
                services.set_bore_position(feature, form.get(f"{x.key}-open_end"),
                                           _number(form.get(f"{x.key}-from_mm"), x.title),
                                           form.get(f"{x.key}-bottom"), name)
            except services.BorePositionError as e:
                raise DecisionError(f"{x.title}: {e}") from None
            out.done.append(x.title)
            changed = True
        elif x.kind == "bore_depth" and (x.decided is None or value != x.value):
            services.decide_bore_depth(feature, value, name)
            out.done.append(f"{x.title}: {services.BORE_DEPTH_MODES[value]}")
        elif x.kind == "bore_finish" and value == "keep" and not feature.bore_finish_kept_at:
            services.keep_bore_finishing(feature, name)
            out.done.append(f"{x.title}: kept")
            changed = True
    db.session.commit()
    return changed


def _setup_decisions(job, machine, form, name, out) -> bool:
    """Z0, the stock and the stick-out; True when one changed."""
    from datetime import datetime, timezone

    from .models import db

    free_end = form.get("free_end")
    if free_end not in ("left", "right"):
        raise DecisionError("Z0: the left or the right end")
    stock, stickout = _number(form.get("face_stock"), "stock beyond Z0"), _number(form.get("stickout"), "stick-out")
    if stock is None or stock < 0 or stickout is None or stickout <= 0:
        raise DecisionError("The stock beyond Z0 (0 or more) and the stick-out (more than 0) are numbers in mm")
    changed = (job.free_end, job.face_stock_mm, job.stickout_mm) != (free_end, stock, stickout)
    job.free_end, job.face_stock_mm, job.gcode_setup_demo = free_end, stock, False
    db.session.commit()
    if changed:
        hint = services.stickout_hint(job, machine)
        if "value" in hint and abs(hint["value"] - stickout) < 1e-9:
            services.take_stickout_hint(job, machine, name, stickout)
        else:
            job.stickout_mm = stickout
            job.stickout_note = f"typed, {name} {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC"[:200]
            db.session.commit()
        out.done.append(f"Z0 {free_end}, stock {stock:g}, stick-out {stickout:g}")
    return changed


def apply_decisions(job, form, name) -> Applied:
    """Write every decision of the screen with the operator's name, in this order: the drawing's (arcs, shapes, bores),
    the set-up (Z0, stock, stick-out), the calculation when it is needed, the turret set-up shown, the skips, the
    approvals of the operations shown, then the G-code. Each through the same service as its own form (the same
    history). Stops, saying where, when the set-up or the operations are not the ones shown, or something blocks the
    program; what was written before stays. Raises DecisionError before writing anything when the name or a required
    value is missing."""
    from .models import Edit, db

    name = (name or "").strip()[:100]
    if not name:
        raise DecisionError("The operator's name is required to confirm the decisions")
    machine = services.job_machine(job)
    d = operator_decisions(job)
    missing = [x.title for x in d.items if x.required and x.kind != "bore_position"
               and not str(form.get(x.key) or "").strip()]
    missing += [x.title for x in d.items if x.kind == "bore_position"
                and not all(str(form.get(f"{x.key}-{k}") or "").strip() for k in ("open_end", "from_mm", "bottom"))]
    if missing:
        raise DecisionError("Not decided yet: " + "; ".join(missing))
    out = Applied()
    features_changed = _feature_decisions(d, form, name, machine, out)
    _setup_decisions(job, machine, form, name, out)
    readiness = services.gcode_readiness(job, machine)
    if (features_changed or not job.current_operations or job.calculated_machine_id not in (None, machine.id)
            or job.tools_changed_since_calculation or any("calculate" in b for b in readiness.blockers)):
        services.calculate_operations(job, machine)
        out.done.append(f"calculated (version {job.last_calculation_version})")
    proposal = services.turret_setup_proposal(job, machine)
    if proposal.rows:
        if not form.get("setup_accept"):
            out.stopped = "the turret set-up is not accepted: tick it, or set the turret by hand"
            return out
        if services.setup_signature(proposal) != form.get("setup_signature"):
            out.stopped = "the turret set-up changed with your decisions: look at it again on this screen"
            return out
        services.accept_turret_setup(job, machine, name, form.get("setup_signature"))
        out.done.append("turret set-up accepted")
    shown = set(filter(None, str(form.get("ops_shown") or "").split(",")))
    unseen = [op for op in job.current_operations if op.status not in ("approved", "skipped") and op_key(op) not in shown]
    if unseen:
        out.stopped = ("operations not shown on the screen came with the calculation ("
                       + ", ".join(f"{op.sequence} {op.tool_type}" for op in unseen) + "): look at them again")
        return out
    approved = skipped = 0
    for op in job.current_operations:
        key = op_key(op)
        if form.get(f"skip-{key}") and op.status != "skipped":
            try:
                services.skip_operation(op, name, form.get(f"skipnote-{key}"))
            except services.SkipError as e:
                raise DecisionError(f"operation {op.sequence}: {e}") from None
            skipped += 1
        elif form.get(f"approve-{key}") and op.status in ("proposed", "edited") and op.tool_id is not None:
            op.edits.append(Edit(field="status", old_value=op.status, new_value=f"approved ({name})"[:100]))
            op.status = "approved"
            approved += 1
    db.session.commit()
    out.done.append(f"{approved} operation(s) approved, {skipped} skipped")
    readiness = services.gcode_readiness(job, machine)
    if readiness.blockers:
        out.stopped = "no program: " + "; ".join(readiness.blockers)
        return out
    _, out.record = services.generate_gcode(job, machine)
    return out
