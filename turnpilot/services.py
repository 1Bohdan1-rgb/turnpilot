"""Glue between the database models, the pure planner and the drawing reader."""

import hashlib
import json
import os
import re
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone

from . import catalogue_file, catalogue_reader, cutting_data, drawing_reader, dxf_adapter, dxf_input, dxf_reader, machine_spec, passport_reader, pdf_text, planner
from .extraction_schema import DrawingData, normalize_tolerance
from .models import (
    CatalogueDocument, CuttingDataRow, DrawingExtraction, Edit, Machine, MachineDocument, MachineSpecSource, Operation, Tool, TurretSlot, db,
)

# Extra names a material may appear under on a drawing (compared after normalization).
MATERIAL_ALIASES = {
    "Steel 45 (C45)": ("steel 45", "c45", "c45e", "1045", "ст45", "сталь 45"),
    "AISI 304": ("aisi 304", "304", "1.4301", "x5crni18-10", "08х18н10"),
    "S355J2+N (EN 10025-2)": ("s355j2+n", "s355j2", "s355j2g3", "1.0570"),
    "Aluminium 6061": ("aluminium 6061", "aluminum 6061", "6061", "en aw-6061", "almg1sicu"),
}

FILE_TYPE_BY_EXTENSION = {"png": "png", "jpg": "jpeg", "jpeg": "jpeg", "pdf": "pdf", "dxf": "dxf"}

# The code that reads a DXF (reader + its mapping to rows): recorded as the "prompt version" of a DXF reading.
DXF_SOURCES = (dxf_reader.__file__, dxf_input.__file__, dxf_adapter.__file__)

# A second upload of a file that is still being read within this time is rejected (double submit).
IN_FLIGHT_SECONDS = 120


class UploadError(ValueError):
    pass


def get_machine():
    return db.session.execute(db.select(Machine).order_by(Machine.id)).scalars().first()


def machines():
    return db.session.execute(db.select(Machine).order_by(Machine.id)).scalars().all()


NEW_MACHINE_FIELDS = ("max_rpm", "power_kw", "max_diameter", "turret_positions")  # required, typed by the operator


def add_machine(name, values):
    """A new machine with the operator's name and required values (no default; the rest stays empty: no turret
    tools, no programming values, no passport). Raises MachineError."""
    name = (name or "").strip()[:100]
    if not name:
        raise MachineError("The machine's name is required")
    if db.session.execute(db.select(Machine).filter_by(name=name)).scalar() is not None:
        raise MachineError(f"A machine named {name} exists already")
    missing = [machine_spec.FIELDS_BY_NAME[k].label for k in NEW_MACHINE_FIELDS if values.get(k) is None]
    if missing:
        raise MachineError("Required: " + ", ".join(missing))
    machine = Machine(name=name, max_rpm=values["max_rpm"], power_kw=values["power_kw"],
                      max_diameter=values["max_diameter"])
    db.session.add(machine)
    for key in NEW_MACHINE_FIELDS:
        set_machine_value(machine, key, values[key], machine_spec.SOURCE_OPERATOR)
    check_machine(machine)
    return machine


class MachineError(ValueError):
    pass


MAX_TURRET_POSITIONS = 48


def set_turret_positions(machine, count):
    """Add empty positions up to count, or remove the last ones; a position holding a tool is not removed."""
    if not 1 <= count <= MAX_TURRET_POSITIONS:
        raise MachineError(f"Turret positions: 1 to {MAX_TURRET_POSITIONS}")
    have = {slot.position for slot in machine.slots}
    for position in range(1, count + 1):
        if position not in have:
            machine.slots.append(TurretSlot(position=position))
    extra = [slot for slot in machine.slots if slot.position > count]
    busy = [slot.label for slot in extra if slot.tool_id is not None]
    if busy:
        raise MachineError(f"{', '.join(busy)} hold tools: take them off before reducing the turret to {count}")
    for slot in extra:
        machine.slots.remove(slot)
    machine.turret_positions = count


def set_machine_value(machine, name, value, source, page=None, quote=None, document_id=None):
    """Set a machine field and record where its value comes from (machine_spec.SOURCE_*). A "not in passport"
    source records the fact only: the value the machine has is kept."""
    field = machine_spec.FIELDS_BY_NAME[name]
    if source != machine_spec.SOURCE_NOT_IN_PASSPORT:
        if value is None and field.required:
            raise MachineError(f"{field.label} is required")
        if name == "turret_positions":
            set_turret_positions(machine, value)
        else:
            setattr(machine, name, value)
    row = machine.source_of(name)
    if row is None:
        row = MachineSpecSource(field=name)
        machine.sources.append(row)
    row.value = machine_spec.format_value(field, value) or None
    row.source, row.page, row.quote, row.document_id = source, page, quote, document_id
    row.confirmed_at = datetime.now(timezone.utc)


def machine_warnings(job, machine):
    """What the machine's documented limits say about a job's blank (empty fields: not checked)."""
    if machine is None:
        return []
    warnings = []
    if machine.max_turning_length and job.blank_length > machine.max_turning_length:
        warnings.append(f"Blank length {job.blank_length:g} mm is above the machine's max turning length "
                        f"{machine.max_turning_length:g} mm.")
    if machine.max_bar_diameter and (job.blank_shape or "round") == "round" \
            and job.blank_diameter > machine.max_bar_diameter:
        warnings.append(f"Bar Ø{job.blank_diameter:g} does not pass through the spindle (max Ø"
                        f"{machine.max_bar_diameter:g}): chuck a cut piece.")
    return warnings


def check_machine(machine):
    """Rules across fields; raises MachineError."""
    if machine.drive_efficiency is not None and machine.drive_efficiency > 1:
        raise MachineError("Drive efficiency is a share of the power: 0 to 1 (e.g. 0.8)")
    if machine.min_rpm is not None and machine.min_rpm > machine.max_rpm:
        raise MachineError("Min spindle speed is above the max")


def tool_spec(tool, material=None, rows=()):
    """The tool as the planner sees it. With a material: ap / f / Vc from the confirmed catalogue rows (rows) of
    the material's group where they give them, with the note naming the source, else the tool's own values with
    a warning (cutting_data.catalogue_values)."""
    spec = _tool_spec(tool)
    if material is None:
        return spec
    return replace(spec, **cutting_data.catalogue_values(
        tool.type, tool.insert_code, tool.grade, material.name, material.catalogue_group, rows))


def confirmed_rows():
    return db.session.execute(db.select(CuttingDataRow).filter_by(status="confirmed")).scalars().all()


def _tool_spec(tool):
    return planner.ToolSpec(
        id=tool.id,
        name=tool.name,
        type=tool.type,
        iso_group=tool.iso_group,
        insert_code=tool.insert_code or "",
        vc_min=tool.vc_min,
        vc_max=tool.vc_max,
        f_min=tool.f_min,
        f_max=tool.f_max,
        ap_min=tool.ap_min,
        ap_max=tool.ap_max,
        insert_width=tool.insert_width,
        diameter=tool.diameter,
        max_depth=tool.max_depth,
        source=tool.source,
        ap_rec=tool.ap_rec,
        f_rec=tool.f_rec,
        vc_points=planner.parse_vc_points(tool.vc_points),
        nose_radius=tool.nose_radius,
        max_ramp_angle=tool.max_ramp_angle,
        tip_direction=tool.tip_direction,
        min_bore_diameter=tool.min_bore_diameter,
    )


@dataclass
class JobSlot:
    """A turret position as a job uses it: the machine's tool, or the job's own change."""
    position: int
    tool: object  # Tool or None
    change: object = None  # the active JobTurretChange of the position, if any

    @property
    def tool_id(self):
        return self.tool.id if self.tool is not None else None

    @property
    def label(self):
        return f"T{self.position}"


def job_turret(job, machine) -> list[JobSlot]:
    """The machine's turret with the job's active changes over it (a job without changes: the machine's)."""
    changes = {c.position: c for c in (job.turret_changes if job is not None else []) if c.active}
    return [JobSlot(s.position, changes[s.position].tool, changes[s.position]) if s.position in changes
            else JobSlot(s.position, s.tool) for s in machine.slots]


class TurretChangeError(ValueError):
    pass


def change_job_turret(job, machine, position, tool, name, note=None):
    """For this job only, `position` takes `tool` (None: empty). The position's earlier change of the job, if any, is
    undone by the same operator. Returns the new JobTurretChange."""
    from .models import JobTurretChange

    name = (name or "").strip()[:100]
    if not name:
        raise TurretChangeError("The operator's name is required for a turret change")
    slot = next((s for s in machine.slots if s.position == position), None)
    if slot is None:
        raise TurretChangeError(f"The machine has no position T{position}")
    if tool is not None and tool.is_retired:
        raise TurretChangeError(f"Tool '{tool.name}' is retired")
    current = next(s for s in job_turret(job, machine) if s.position == position)
    if current.tool_id == (tool.id if tool else None):
        raise TurretChangeError(f"T{position} already holds {tool.name if tool else 'nothing'} for this job")
    now = datetime.now(timezone.utc)
    if current.change is not None:
        current.change.removed_by, current.change.removed_at = name, now
    change = JobTurretChange(job=job, position=position, tool=tool, machine_tool=slot.tool, decided_by=name,
                             note=(note or "").strip()[:200] or None, created_at=now)
    db.session.add(change)
    db.session.commit()
    return change


@dataclass
class TurretSuggestion:
    """The smallest change of the job's turret that gives it a tool it lacks."""
    tool: object  # the library Tool to put in
    reason: str
    position: int | None  # where (None: no position the job leaves free)
    replaces: object = None  # the Tool now there (None: empty)


def turret_suggestions(job, machine) -> list[TurretSuggestion]:
    """From the job's current calculation: tools it lacks that the library has (a drill of a bore's size, a copying
    tool whose RMPX covers a concave arc, a missing tool type, a grooving insert narrow enough), each with the position
    to free: an empty one first, else one whose tool this job does not use (the lowest)."""
    ops = job.current_operations
    if not ops:
        return []
    slots = job_turret(job, machine)
    in_turret = {s.tool_id for s in slots if s.tool_id}
    iso = job.material.iso_group
    library = [t for t in db.session.execute(db.select(Tool).filter_by(is_retired=False)).scalars()
               if t.id not in in_turret and iso in t.iso_group]
    features = {f.id: f for f in job.active_features}
    specs = {f.id: f for f in job_spec(job).features}
    wanted = []  # (tool, reason)

    def want(tool, reason):
        if tool is not None and all(t.id != tool.id for t, _ in wanted):
            wanted.append((tool, reason))

    for op in ops:
        feature = features.get(op.feature_id)
        if feature is None:
            continue
        warning = op.warning or ""
        if op.tool_type == "drilling" and feature.type == "bore" and feature.diameter and not (
                op.tool_id and op.ref_diameter and abs(op.ref_diameter - feature.diameter) <= 0.01):
            want(next((t for t in library if t.type == "drilling" and t.diameter
                       and abs(t.diameter - feature.diameter) <= 0.01), None),
                 f"the bore Ø{feature.diameter:g} drilled to size in one operation")
        if feature.type == "arc" and op.tool_type in ("turning_finish", "turning_rough") and job.axial_order_known:
            angle = planner.concave_arc_angle(specs[feature.id])
            have = op.tool.max_ramp_angle if op.tool else None
            if angle is not None and (have is None or angle > have + 0.5):
                types = ("turning_finish",) if op.tool_type == "turning_finish" else ("turning_rough",
                                                                                      "turning_finish")
                able = [t for t in library if t.type in types and t.max_ramp_angle is not None
                        and angle <= t.max_ramp_angle + 0.5]
                able.sort(key=lambda t: types.index(t.type))
                want(able[0] if able else None, f"the concave R{feature.radius:g} goes down at {angle:.0f}°: "
                                                f"a tool with RMPX ≥ {angle:.0f}°")
        if op.tool_id is None and warning.startswith(f"No {op.tool_type} tool in the turret"):
            want(next((t for t in library if t.type == op.tool_type), None), f"no {op.tool_type} tool in the turret")
        if op.tool_type == "grooving" and op.tool_id is None and "narrower than the narrowest" in warning \
                and feature.length:
            fits = [t for t in library if t.type == "grooving" and t.insert_width
                    and t.insert_width <= feature.length + 0.01]
            want(max(fits, key=lambda t: t.insert_width) if fits else None,
                 f"the groove {feature.length:g} mm needs a narrower insert")
    used = {op.turret_position for op in ops if op.tool_id is not None and op.turret_position}
    free = [s for s in slots if s.tool is None] + [s for s in slots if s.tool is not None and s.position not in used]
    out = []
    for tool, reason in wanted:
        slot = free.pop(0) if free else None
        out.append(TurretSuggestion(tool, reason, slot.position if slot else None, slot.tool if slot else None))
    return out


def undo_job_turret_change(change, name):
    name = (name or "").strip()[:100]
    if not name:
        raise TurretChangeError("The operator's name is required to undo a turret change")
    if not change.active:
        raise TurretChangeError("This change is already undone")
    change.removed_by, change.removed_at = name, datetime.now(timezone.utc)
    db.session.commit()


def turret_entries(machine, material=None, job=None):
    """The turret as the planner sees it (a job's: with its own changes); with a material, its cutting data from the
    confirmed catalogue rows."""
    rows = confirmed_rows() if material is not None else ()
    return [planner.TurretEntry(s.position, tool_spec(s.tool, material, rows))
            for s in job_turret(job, machine) if s.tool is not None]


def _roughness_for_planner(feature):
    """(Ra for the planner, the original Rz or None): the planner works with Ra only."""
    if feature.ra is not None and feature.ra_param == "Rz":
        return planner.rz_to_ra(feature.ra), feature.ra
    return feature.ra, None


def job_spec(job):
    features = tuple(
        planner.FeatureSpec(
            id=f.id, type=f.type, diameter=f.diameter, length=f.length,
            ra=_roughness_for_planner(f)[0], ra_from_rz=_roughness_for_planner(f)[1], pitch=f.pitch,
            location=f.location if f.type in ("chamfer", "thread") else None,
            # normalized again for features saved before decimal commas were converted
            start_diameter=f.start_diameter, tolerance=normalize_tolerance(f.tolerance), radius=f.radius,
            across_flats=f.across_flats if f.type == "hex" else None,
            arc_convex=f.arc_convex if f.type == "arc" else None,
        )
        for f in job.active_features
    )
    return planner.JobSpec(
        iso_group=job.material.iso_group,
        blank_diameter=job.blank_diameter,
        blank_length=job.blank_length,
        features=features,
        blank_shape=job.blank_shape or "round",
        axial_order=bool(job.axial_order_known),
        material_name=job.material.name,
        kc1=job.material.kc1,
        mc=job.material.mc,
        kc1_source=job.material.kc1_source,
        mc_source=job.material.mc_source,
    )


def _library_drill_for(job, machine, planned):
    """A warning when a bore is not drilled to its size although a drill of that size is in the tool library (not in
    the turret): the planner takes the turret's tools only."""
    if planned.tool_type != "drilling":
        return None
    feature = next((f for f in job.active_features if f.id == planned.feature_id), None)
    if feature is None or feature.type != "bore" or not feature.diameter:
        return None
    if planned.tool_id is not None and planned.ref_diameter and abs(planned.ref_diameter - feature.diameter) <= 0.01:
        return None
    in_turret = {slot.tool_id for slot in job_turret(job, machine) if slot.tool_id}
    drills = db.session.execute(db.select(Tool).filter_by(type="drilling", is_retired=False)).scalars().all()
    found = next((t for t in drills if t.id not in in_turret and t.diameter
                  and abs(t.diameter - feature.diameter) <= 0.01 and job.material.iso_group in t.iso_group), None)
    if found is None:
        return None
    return (f"a Ø{feature.diameter:g} drill ({found.name}, {found.insert_code}) is in the tool library, not in the "
            "turret: with it in the turret the hole is drilled to size in one operation")


def calculate_operations(job, machine):
    """Archive the current operations of a job and add a fresh proposal as a new version.

    Nothing is deleted: archived operations keep their status and edit log.
    """
    version = job.last_calculation_version + 1
    # the operator's skips go on to the same operation (feature, type, mode) of the new version
    skips = {(op.feature_id, op.tool_type, op.rough_finish): op for op in job.current_operations
             if op.status == "skipped"}
    for op in job.current_operations:
        op.is_archived = True
    power = planner.PowerSpec(machine.power_kw, machine.drive_efficiency)
    limits = planner.MachineLimits(machine.min_rpm, machine.coolant, machine.live_tooling, machine.c_axis)
    for planned in planner.plan_job(job_spec(job), turret_entries(machine, job.material, job), machine.max_rpm,
                                    machine.max_thread_feed,
                                    power, limits):
        library_drill = _library_drill_for(job, machine, planned)
        if library_drill:
            planned.warnings.append(library_drill)
        job.operations.append(
            Operation(
                feature_id=planned.feature_id,
                tool_id=planned.tool_id,
                turret_position=planned.turret_position,
                sequence=planned.sequence,
                tool_type=planned.tool_type,
                rough_finish=planned.mode,
                vc=planned.vc,
                n=planned.n,
                f=planned.f,
                ap=planned.ap,
                passes=planned.passes,
                insert_width=planned.insert_width,
                depth=planned.depth,
                ref_diameter=planned.ref_diameter,
                cutting_data_origin=planned.data_origin,
                note="; ".join(planned.notes) or None,
                warning="; ".join(planned.warnings) or None,
                status="proposed",
                calculation_version=version,
            )
        )
        skipped = skips.get((planned.feature_id, planned.tool_type, planned.mode))
        if skipped is not None:
            new = job.operations[-1]
            new.status, new.skipped_by, new.skipped_at, new.skipped_note = (
                "skipped", skipped.skipped_by, skipped.skipped_at, skipped.skipped_note)
            new.skip_carried_from = skipped.skip_carried_from or skipped.calculation_version
    db.session.commit()


BORE_DEPTH_MODES = {"full_diameter": "to the full Ø (the point goes beyond)", "tip": "to the drill's point"}


class DepthError(ValueError):
    pass


def decide_bore_depth(feature, mode, name, note=None):
    """The operator's decision on what a bore's depth means for the drill (first, or a change): a new history row,
    the feature's current mode. The name is required."""
    from .models import BoreDepthDecision

    name = (name or "").strip()[:100]
    if not name:
        raise DepthError("The operator's name is required for the decision")
    if mode not in BORE_DEPTH_MODES:
        raise DepthError("Choose: to the full Ø or to the drill's point")
    now = datetime.now(timezone.utc)
    feature.depth_decisions.append(BoreDepthDecision(mode=mode, note=(note or "").strip()[:200] or None,
                                                     decided_by=name, decided_at=now))
    feature.depth_mode, feature.depth_decided_by, feature.depth_decided_at = mode, name, now
    db.session.commit()


def drill_point_length(diameter, angle):
    """The length of a drill's point: D / 2 / tan(angle / 2) (140°, Ø10: 1.82)."""
    import math

    return diameter / 2 / math.tan(math.radians(angle / 2))


def bore_drills(job):
    """{bore feature id: the drilling operation's tool} in the current calculation."""
    return {op.feature_id: op.tool for op in job.current_operations
            if op.tool_type == "drilling" and op.tool and op.feature and op.feature.type == "bore"}


def bore_depth_hints(job):
    """{bore feature id: text} for the job page: where the drill's point and its full Ø end in each case."""
    hints = {}
    for feature_id, tool in bore_drills(job).items():
        feature = next((f for f in job.active_features if f.id == feature_id), None)
        if feature is None or not feature.length or not tool.diameter:
            continue
        if not tool.point_angle_deg:
            hints[feature_id] = (f"{tool.name}: no point angle in the tool data, so only \"to the drill's point\" "
                                 f"(Z-{feature.length:g}) can be programmed")
            continue
        cone = drill_point_length(tool.diameter, tool.point_angle_deg)
        hints[feature_id] = (f"{tool.name}: point {cone:.2f} mm ({tool.point_angle_deg:g}°, Ø{tool.diameter:g}; "
                             f"{tool.point_angle_source or 'source not given'}): to the full Ø the point goes to "
                             f"Z-{feature.length + cone:.2f}; to the point Z-{feature.length:g}, the full Ø "
                             f"{max(0.0, feature.length - cone):.2f} deep")
    return hints


class SkipError(ValueError):
    pass


def skip_operation(op, name, note):
    """The operator leaves an operation out: it blocks nothing and is not in the program. Name and note required."""
    name, note = (name or "").strip()[:100], (note or "").strip()[:200]
    if op.is_archived:
        raise SkipError("Archived operations are read-only")
    if not name or not note:
        raise SkipError("The operator's name and a note are required to skip an operation")
    db.session.add(Edit(operation=op, field="status", old_value=op.status, new_value=f"skipped ({name})"))
    op.status, op.skipped_by, op.skipped_at, op.skipped_note = "skipped", name, datetime.now(timezone.utc), note
    op.skip_carried_from = None
    db.session.commit()


def unskip_operation(op, name):
    name = (name or "").strip()[:100]
    if op.is_archived:
        raise SkipError("Archived operations are read-only")
    if not name:
        raise SkipError("The operator's name is required")
    if op.status != "skipped":
        raise SkipError("The operation is not skipped")
    db.session.add(Edit(operation=op, field="status", old_value="skipped", new_value=f"proposed ({name})"))
    op.status, op.skipped_by, op.skipped_at, op.skipped_note, op.skip_carried_from = "proposed", None, None, None, None
    db.session.commit()


def gcode_readiness(job, machine):
    """What blocks a program for this job, and which operations go into it (gcode.readiness)."""
    from .gcode import readiness

    operations = [
        readiness.OperationInfo(
            id=op.id, sequence=op.sequence, tool_type=op.tool_type,
            feature_type=op.feature.type if op.feature else "", status=op.status, has_tool=op.tool_id is not None,
            origin=op.cutting_data_origin, vc=op.vc, n=op.n, f=op.f,
            skip=f"skipped by the operator ({op.skipped_by}): {op.skipped_note}" if op.status == "skipped" else None)
        for op in job.current_operations
    ]
    source = machine.source_of("max_rpm") if machine else None
    programming = {f.name: getattr(machine, f.name) for f in machine_spec.PROGRAMMING_FIELDS} if machine else {}
    labels = {f.name: f.label for f in machine_spec.PROGRAMMING_FIELDS}
    job_setup = {"free_end": job.free_end, "stickout_mm": job.stickout_mm, "face_stock_mm": job.face_stock_mm}
    result = readiness.check(bool(job.axial_order_known), operations,
                             [t.name for t in job.tools_changed_since_calculation], source.source if source else None,
                             programming, labels, job_setup)
    by_id = {op.id: op for op in job.current_operations}
    for op_id in result.usable:  # a bore's drill: the operator decides what the drawing's depth means
        op = by_id[op_id]
        if op.tool_type != "drilling" or not op.feature or op.feature.type != "bore":
            continue
        name = f"bore Ø{op.feature.diameter:g} × {op.feature.length:g}"
        if op.feature.depth_mode is None:
            result.blockers.append(f"{name}: the depth to the full Ø or to the drill's point: the operator's "
                                   "choice on the job page")
        elif op.feature.depth_mode == "full_diameter" and op.tool and not op.tool.point_angle_deg:
            result.blockers.append(f"{name}: the depth to the full Ø needs the point angle of {op.tool.name} "
                                   "(tool data)")
    return result


def job_profile(job, radius_of=None):
    """The job's outer profile from the part zero (gcode.profile); radius_of: {feature id: radius} to try instead."""
    from .gcode import profile as gprofile

    radius_of = radius_of or {}
    features = [gprofile.FeatureData(id=f.id, type=f.type, diameter=f.diameter, start_diameter=f.start_diameter,
                                     length=f.length, pitch=f.pitch, radius=radius_of.get(f.id, f.radius),
                                     face=f.face, location=f.location, across_flats=f.across_flats,
                                     arc_convex=f.arc_convex,
                                     drawn_radius=radius_of.get(f.id, f.drawn_radius))
                for f in job.active_features]
    return gprofile.build(features, job.free_end or "left")


def radius_conflicts(job):
    """{feature id: section} of the arcs whose drawn radius ≠ their dimension, waiting for the operator."""
    return {s.feature_id: s for s in job_profile(job).sections
            if s.kind == "arc" and s.arc_problem and s.arc_problem.startswith("drawn R")}


def check_own_radius(job, feature, radius):
    """None when an arc of this radius goes through the section's ends (its shape as known), else why, with the
    smallest radius possible (half the chord)."""
    import math

    from .gcode import profile as gprofile

    section = next((s for s in job_profile(job).sections if s.feature_id == feature.id), None)
    if section is None:
        return "the arc is not in the profile (no order along the axis, or a row before it is incomplete)"
    p0, p1 = (section.z_free, section.d_free / 2), (section.z_chuck, section.d_chuck / 2)
    smallest = math.hypot(p1[0] - p0[0], p1[1] - p0[1]) / 2
    if feature.arc_convex is None:
        return "its shape (convex / concave) is not known"
    arc, why = gprofile.arc_through(p0, p1, radius, feature.arc_convex)
    if arc is None:
        return f"{why}; the smallest R through the ends Z{p0[0]:g} D{2 * p0[1]:g} and Z{p1[0]:g} D{2 * p1[1]:g} is " \
               f"R{smallest:.3f} (half the chord)"
    return None


def arc_top(job, feature, radius):
    """A convex arc of this radius through its section's ends: why it is to be checked when its top is above the
    larger end's Ø (the neighbouring section's Ø there), else None."""
    from .gcode import profile as gprofile

    section = next((s for s in job_profile(job).sections if s.feature_id == feature.id), None)
    if section is None or not feature.arc_convex or not radius:
        return None
    p0, p1 = (section.z_free, section.d_free / 2), (section.z_chuck, section.d_chuck / 2)
    arc, _ = gprofile.arc_through(p0, p1, radius, True)
    if arc is None or not min(p0[0], p1[0]) < arc.cz < max(p0[0], p1[0]):
        return None
    z_end, r_end = max(p0, p1, key=lambda p: p[1])
    top = arc.cr + radius
    if 2 * top <= 2 * r_end + 0.005:
        return None
    return (f"R{radius:g}: the arc's top D{2 * top:.2f} at Z{arc.cz:.2f} is above D{2 * r_end:g} at Z{z_end:g} "
            "(the neighbouring section's Ø there): check")


def arc_top_warnings(job):
    """{arc feature id: {"dimension" / "drawn": why}} for the arcs whose radius is the operator's choice."""
    warnings = {}
    for f in job.active_features:
        if f.type != "arc" or f.drawn_radius is None:
            continue
        radii = {"dimension": f.radius_dimension if f.radius_decisions else f.radius,
                 "drawn": f.radius_geometry if f.radius_decisions else f.drawn_radius}
        found = {k: w for k, r in radii.items() if (w := arc_top(job, f, r))}
        if f.radius_decisions and (w := arc_top(job, f, f.radius)):
            found["current"] = w
        if found:
            warnings[f.id] = found
    return warnings


RADIUS_SOURCES = {"dimension": "the dimension", "drawn": "the geometry", "operator": "own value"}
# what makes an operation the same after a recalculation (its approval is then kept)
OPERATION_VALUES = ("tool_id", "turret_position", "tool_type", "rough_finish", "vc", "n", "f", "ap", "passes",
                    "insert_width", "depth", "ref_diameter", "cutting_data_origin")


def decide_arc_radius(feature, source, radius, note, name, machine):
    """The operator's decision on an arc whose drawn radius ≠ its dimension (first, or a change): a new history row,
    the arc's current radius. A change of a calculated job recalculates it: operations whose values stay the same
    keep their approval, the arc's own and any whose values changed are proposed again (the old version stays in
    the history). Returns a message."""
    from .models import RadiusDecision

    first = not feature.radius_decisions
    dimension = feature.radius if first else feature.radius_dimension
    geometry = feature.drawn_radius if first else feature.radius_geometry
    now = datetime.now(timezone.utc)
    feature.radius_decisions.append(RadiusDecision(radius=radius, source=source, dimension=dimension,
                                                   geometry=geometry, note=note, decided_by=name, decided_at=now))
    feature.radius_dimension, feature.radius_geometry = dimension, geometry
    feature.radius, feature.drawn_radius = radius, radius  # resolved: they agree now
    feature.radius_source, feature.radius_decided_at, feature.radius_note = source, now, note
    db.session.commit()
    job = feature.job
    if first or not job.current_operations:
        return f"Arc radius R{radius:g} chosen ({RADIUS_SOURCES[source]}). Calculate the job again."
    approved = {}
    for op in job.current_operations:
        if op.status == "approved":
            key = (op.feature_id, op.tool_type, op.rough_finish)
            approved.setdefault(key, []).append(tuple(getattr(op, k) for k in OPERATION_VALUES))
    calculate_operations(job, machine)
    kept = again = 0
    for op in job.current_operations:
        values = tuple(getattr(op, k) for k in OPERATION_VALUES)
        same = approved.get((op.feature_id, op.tool_type, op.rough_finish), [])
        if values in same and op.feature_id != feature.id:  # the arc's own operations: always approved again
            same.remove(values)
            op.status = "approved"
            kept += 1
        else:
            again += 1
    db.session.commit()
    return (f"Arc radius changed to R{radius:g} ({RADIUS_SOURCES[source]}); the job was calculated again: {kept} "
            f"operation(s) kept their approval, {again} to approve again.")


def gcode_inputs(job, machine, readiness=None):
    """The generator's inputs from the database: the job, the machine, the profile, the usable operations."""
    from .gcode import program as gprogram

    readiness = readiness or gcode_readiness(job, machine)
    profile = job_profile(job)
    by_id = {op.id: op for op in job.current_operations}
    ops = []
    for op_id in readiness.usable:
        op = by_id[op_id]
        tool = op.tool
        ops.append(gprogram.OpData(
            id=op.id, sequence=op.sequence, tool_type=op.tool_type, mode=op.rough_finish, feature_id=op.feature_id,
            feature_type=op.feature.type, position=op.turret_position, tool_name=tool.name if tool else "",
            insert_code=tool.insert_code if tool else None, vc=op.vc, n=op.n, f=op.f, ap=op.ap, passes=op.passes,
            depth=op.depth, insert_width=op.insert_width, ref_diameter=op.ref_diameter,
            ap_min=tool.ap_min if tool else None, ap_max=tool.ap_max if tool else None,
            tool_diameter=tool.diameter if tool else None, pitch=op.feature.pitch,
            max_ramp_angle=tool.max_ramp_angle if tool else None,
            nose_radius=(tool.nose_radius or planner.insert_nose_radius(tool.insert_code)) if tool else None,
            tip_direction=tool.tip_direction if tool else None,
            point_angle=tool.point_angle_deg if tool else None, max_depth=tool.max_depth if tool else None,
            depth_mode=op.feature.depth_mode if op.feature.type == "bore" else None,
            depth_decided=(f"{op.feature.depth_decided_by} {op.feature.depth_decided_at:%Y-%m-%d}"
                           if op.feature.depth_decided_at else None),
            warnings=tuple(w.strip() for w in (op.warning or "").split(";") if w.strip())))
    stock = planner.stock_diameter(job.blank_shape or "round", job.blank_diameter)
    job_data = gprogram.JobData(id=job.id, name=job.name, material=job.material.name, blank_diameter=stock,
                                blank_label=f"{job.blank_label} x {job.blank_length:g}",
                                stickout=job.stickout_mm or 0.0, face_stock=job.face_stock_mm,
                                free_end=job.free_end or "left")
    machine_data = gprogram.MachineData(
        max_rpm=machine.max_rpm, spindle=machine.spindle_right_hand or "", clearance_x=machine.clearance_x or 0.0,
        clearance_z=machine.clearance_z or 0.0, retract=machine.retract_mm or 0.0,
        chuck_safety=machine.chuck_safety_mm or 0.0, coolant=machine.coolant, thread_run_in=machine.thread_run_in_mm,
        peck_depth=machine.peck_depth_mm, facing_overshoot=machine.facing_overshoot_mm,
        groove_reference=machine.groove_reference, min_rpm=machine.min_rpm, max_thread_feed=machine.max_thread_feed,
        control=machine.control, parting_overshoot=machine.parting_overshoot_mm, groove_dwell=machine.groove_dwell_s)
    return job_data, machine_data, profile, ops


def turret_setup_lines(job) -> list[str]:
    """The job's active turret changes for the setter: "T12: Copy finishing VNMG (P) -> Drill 860-GM Ø10 (Bohdan,
    2026-10-16)"."""
    return [f"T{c.position}: {c.machine_tool.name if c.machine_tool else 'empty'} -> "
            f"{c.tool.name if c.tool else 'empty'} ({c.decided_by}, {c.created_at:%Y-%m-%d})"
            for c in job.turret_changes if c.active]


def gcode_program(job, machine):
    """(readiness, program or None): no program while anything blocks it."""
    from .gcode import program as gprogram

    readiness = gcode_readiness(job, machine)
    if not readiness.ok:
        return readiness, None
    job_data, machine_data, profile, ops = gcode_inputs(job, machine, readiness)
    program = gprogram.build(job_data, machine_data, profile, ops, readiness.skipped)
    setup = turret_setup_lines(job)
    if setup:  # for the setter, right after the blank
        at = next((i + 1 for i, line in enumerate(program.header) if line.startswith("BLANK ")), 0)
        program.header[at:at] = ["TURRET SET-UP FOR THIS JOB (NOT THE MACHINE'S):"] + [
            gprogram.ascii_text(line) for line in setup]
    return readiness, program


def gcode_simulation(job, machine, text, profile=None):
    """The simulation of a program's text for this job and machine (gcode.simulate)."""
    from .gcode import simulate as gsim

    if profile is None:
        _, _, profile, _ = gcode_inputs(job, machine)
    tools = {}
    for slot in job_turret(job, machine):
        if slot.tool is not None:
            tools[slot.position] = gsim.ToolInfo(
                slot.tool.type, slot.tool.ap_max, slot.tool.insert_width, slot.tool.max_ramp_angle,
                slot.tool.nose_radius or planner.insert_nose_radius(slot.tool.insert_code), slot.tool.tip_direction,
                slot.tool.name, slot.tool.insert_code)
    data = gsim.SimInput(
        profile=profile, stock_radius=planner.stock_diameter(job.blank_shape or "round", job.blank_diameter) / 2,
        face_stock=job.face_stock_mm or 0.0, stickout=job.stickout_mm or 0.0,
        chuck_safety=machine.chuck_safety_mm or 0.0, max_rpm=machine.max_rpm, spindle=machine.spindle_right_hand,
        tools=tools, groove_reference=machine.groove_reference, facing_overshoot=machine.facing_overshoot_mm or 0.0,
        parting_overshoot=machine.parting_overshoot_mm or 0.0,
        min_rpm=machine.min_rpm, max_thread_feed=machine.max_thread_feed)
    result = gsim.simulate(text, data)
    for section in profile.sections:  # an arc waiting for the operator's radius: never ready to run
        if section.kind == "arc" and section.arc_problem and section.arc_problem.startswith("drawn R"):
            result.errors.append((0, f"Z{section.z_free:g} to Z{section.z_chuck:g} (D{section.d_free:g} to "
                                     f"D{section.d_chuck:g}): arc {section.arc_problem} (the operator's decision)"))
    return result


def is_demo(machine, name):
    source = machine.source_of(name)
    return source is not None and source.source == machine_spec.SOURCE_DEMO


GCODE_CHECKLIST = (
    ("zero", "Z0 is on the free end face and X0 on the axis; the free end is the right one"),
    ("tools", "the tool numbers and offsets match the turret on the machine"),
    ("clamping", "the stick-out and the jaws match the real clamping"),
    ("spindle", "the spindle direction matches the way the tools are mounted"),
    ("simulation", "the simulation is looked through; what is left for manual work is understood"),
    ("machine_check", "on the machine: the control's graphics, a dry run with an offset, single block, rapid 25%"),
)


def gcode_checklist(record):
    """The operator's checklist of a program: with nose radius compensation, also the offset page's R and T."""
    sim = json.loads(record.simulation)
    items = GCODE_CHECKLIST
    setup = sim.get("turret_setup") or []
    if setup:
        items += (("turret_setup", "the turret is set up for this job: " + "; ".join(setup)),)
    compensation = sim.get("compensation") or []
    if compensation:
        offsets = ", ".join(f"T{pos:02d} R{r:g} T{t}" for pos, _, r, t in compensation)
        items += (("nose_compensation", f"nose radius compensation G42: on the offset page {offsets} "
                                        "(R the nose radius, T the tip direction as the tool is mounted)"),)
    return items


def gcode_settings(job, machine):
    """The values a program uses, each with its source (the passport, the operator, DEMO)."""
    rows = []
    for field in (machine_spec.FIELDS_BY_NAME["max_rpm"],) + machine_spec.PROGRAMMING_FIELDS:
        value = getattr(machine, field.name)
        source = machine.source_of(field.name)
        rows.append({"name": field.name, "label": field.label, "unit": field.unit,
                     "value": machine_spec.format_value(field, value),
                     "source": source.source if source else None, "source_label": source.label if source else "seed"})
    setup_source = machine_spec.SOURCE_DEMO if job.gcode_setup_demo else machine_spec.SOURCE_OPERATOR
    for name, label, unit in (("free_end", "Free end (Z0) on the drawing", ""),
                              ("stickout_mm", "Stick-out from the jaws", "mm"),
                              ("face_stock_mm", "Stock beyond Z0", "mm")):
        value = getattr(job, name)
        rows.append({"name": name, "label": label, "unit": unit, "value": "" if value is None else f"{value:g}"
                     if isinstance(value, float) else value, "source": setup_source,
                     "source_label": "DEMO value, not from the machine" if job.gcode_setup_demo else "the operator"})
    return rows


def demo_settings(settings):
    """The values typed by the demo command: no program is ready to run while one is used."""
    return [row for row in settings if row["source"] == machine_spec.SOURCE_DEMO and row["value"] != ""]


def generate_gcode(job, machine):
    """Build, print and simulate a program for the job; kept as a new GcodeProgram. (readiness, program or None)."""
    from .gcode import fanuc
    from .models import GcodeProgram

    readiness, program = gcode_program(job, machine)
    if program is None:
        return readiness, None
    text = fanuc.render(program)
    result = gcode_simulation(job, machine, text)
    simulation = {
        "errors": result.errors, "warnings": result.warnings, "incomplete": result.incomplete,
        "segments": [[s.kind, s.x0, s.z0, s.x1, s.z1, s.line, s.tool] + ([list(s.arc)] if s.arc else [])
                     for s in result.segments],
        "final_stock": [round(r, 3) for r in result.final_stock[::10]], "z_top": result.z_top, "dz": 0.1,
        "program_warnings": program.warnings, "skipped": {str(k): v for k, v in program.skipped.items()},
        "compensation": [list(c) for c in program.compensation],
        "turret_setup": turret_setup_lines(job),
    }
    record = GcodeProgram(job=job, calculation_version=job.last_calculation_version, dialect=fanuc.NAME, text=text,
                          sha256=hashlib.sha256(text.encode("ascii")).hexdigest(),
                          settings=json.dumps(gcode_settings(job, machine), ensure_ascii=False),
                          simulation=json.dumps(simulation, ensure_ascii=False))
    db.session.add(record)
    db.session.commit()
    return readiness, record


def gcode_stale(record, machine):
    """Why a program no longer matches the job and the machine (empty: it still does)."""
    job = record.job
    reasons = []
    if record.calculation_version != job.last_calculation_version:
        reasons.append("the job was calculated again after this program")
    for feature in job.active_features:
        changed = [d for d in feature.radius_decisions[1:] if d.decided_at and record.created_at
                   and d.decided_at.replace(tzinfo=None) > record.created_at.replace(tzinfo=None)]
        if changed:
            d = changed[-1]
            reasons.append(f"the decision on the arc's radius was changed to R{d.radius:g} by {d.decided_by} on "
                           f"{d.decided_at:%Y-%m-%d %H:%M}")
    for feature in job.active_features:
        if feature.depth_decided_at and record.created_at \
                and feature.depth_decided_at.replace(tzinfo=None) > record.created_at.replace(tzinfo=None):
            reasons.append(f"the decision on the bore Ø{feature.diameter:g}'s depth "
                           f"({BORE_DEPTH_MODES[feature.depth_mode]}) was made by {feature.depth_decided_by} on "
                           f"{feature.depth_decided_at:%Y-%m-%d %H:%M}")
    if job.tools_changed_since_calculation:
        reasons.append("tools changed since the calculation")
    made = record.created_at.replace(tzinfo=None) if record.created_at else None
    if made and any((c.created_at and c.created_at.replace(tzinfo=None) > made)
                    or (c.removed_at and c.removed_at.replace(tzinfo=None) > made) for c in job.turret_changes):
        reasons.append("the job's turret set-up changed after this program")
    then = {row["name"]: (row["value"], row["source"]) for row in json.loads(record.settings)}
    now = {row["name"]: (row["value"], row["source"]) for row in gcode_settings(job, machine)}
    changed = [row["label"] for row in gcode_settings(job, machine) if then.get(row["name"]) != now[row["name"]]]
    if changed:
        reasons.append("values changed since this program: " + ", ".join(changed))
    return reasons


CYCLE_TIME_FIELDS = ("rapid_x_m_min", "rapid_z_m_min", "tool_change_s")


def cycle_time(record, machine):
    """The program's time by operation, from its text and the machine's current rapid rates and tool change time,
    with where those come from: an estimate unless all three are from the passport."""
    from .gcode import cycle_time as gtime

    result = gtime.estimate(record.text, machine.rapid_x_m_min, machine.rapid_z_m_min, machine.tool_change_s)
    sources = {}
    for name in CYCLE_TIME_FIELDS:
        value, source = getattr(machine, name), machine.source_of(name)
        sources[machine_spec.FIELDS_BY_NAME[name].label] = (
            value, source.source if source and value is not None else None)
    from_passport = all(src == machine_spec.SOURCE_PASSPORT for _, src in sources.values())
    quantity = record.job.quantity or 1
    return {"time": result, "sources": sources, "estimate": not from_passport,
            "demo": any(src == "demo" for _, src in sources.values()),
            "quantity": quantity, "order_s": quantity * result.total_s}


class GcodeError(ValueError):
    pass


def mark_gcode_ready(record, machine, form):
    """The operator's "Ready to run": only a program without errors that machines the whole part, with no DEMO
    value, still matching the job and the machine, every item of the checklist ticked and the operator's name."""
    sim = json.loads(record.simulation)
    problems = []
    if sim["errors"]:
        problems.append("the simulation found errors")
    if sim["incomplete"]:
        problems.append("the part is not complete")
    demo = demo_settings(json.loads(record.settings))
    if demo:
        problems.append("DEMO values are used: " + ", ".join(row["label"] for row in demo))
    problems += gcode_stale(record, machine)
    ticked = {key: bool(form.get(f"check-{key}")) for key, _ in gcode_checklist(record)}
    if not all(ticked.values()):
        problems.append("not every item of the checklist is ticked")
    name = (form.get("confirmed_by") or "").strip()
    if not name:
        problems.append("the operator's name is required")
    if problems:
        raise GcodeError("; ".join(problems))
    record.status, record.checklist = "ready", json.dumps(ticked)
    record.confirmed_by, record.confirmed_at = name[:100], datetime.now(timezone.utc)
    db.session.commit()


DEMO_PROGRAMMING = dict(max_rpm=3500, spindle_right_hand="M04", clearance_x=1.0, clearance_z=2.0, retract_mm=0.5,
                        chuck_safety_mm=5.0, thread_run_in_mm=6.0, peck_depth_mm=5.0, facing_overshoot_mm=0.4,
                        groove_reference="toward Z0", parting_overshoot_mm=0.25)


def set_gcode_demo(machine, job=None):
    """DEMO values to look at a program before the machine's are known: recorded with the source "demo" (never
    ready to run). Fields the operator or the passport already gave are kept."""
    for name, value in DEMO_PROGRAMMING.items():
        source = machine.source_of(name)
        if source is None or source.source == machine_spec.SOURCE_DEMO:
            set_machine_value(machine, name, value, machine_spec.SOURCE_DEMO)
    if job is not None and job.axial_order_known and not (job.stickout_mm and not job.gcode_setup_demo):
        job.free_end = job.free_end or suggest_free_end(job) or "left"
        # DEMO: the part, the parting insert and the jaws' safety distance, plus 5 mm
        part = sum(f.length or 0 for f in job.active_features if f.type in PROFILE_TYPES)
        safety = machine.chuck_safety_mm or 0.0
        job.stickout_mm, job.face_stock_mm = round(max(job.blank_length, part + 3 + safety) + 5, 3), 1.0
        job.gcode_setup_demo = True
    db.session.commit()


PROFILE_TYPES = ("od_turn", "hex", "taper", "arc", "groove")


def suggest_free_end(job):
    """The end of the drawing opposite the largest Ø (the chuck holds the largest Ø, as the planner assumes);
    None when the order is not known or the largest Ø is in the middle. A suggestion: the operator chooses."""
    if not job.axial_order_known:
        return None
    sizes = [max(f.diameter or 0, f.start_diameter or 0) for f in job.active_features if f.type in PROFILE_TYPES]
    if len(sizes) < 2:
        return None
    largest = max(sizes)
    where = [i for i, d in enumerate(sizes) if d >= largest - 1e-9]
    if where[-1] == len(sizes) - 1 and where[0] > 0:
        return "left"
    if where[0] == 0 and where[-1] < len(sizes) - 1:
        return "right"
    return None


def _fmt(value):
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def apply_operation_edit(op, machine, turret_position, vc, f, ap, passes):
    """Apply user changes to an operation and log every changed field in Edit.

    Returns the list of Edit rows created (empty if nothing changed).
    """
    if op.is_archived:
        raise ValueError("Archived operations are read-only")
    changes = {}

    if turret_position != op.turret_position:
        slot = None
        if turret_position is not None:
            slot = next((s for s in job_turret(op.job, machine) if s.position == turret_position), None)
            if slot is None or slot.tool is None:
                raise ValueError(f"Turret position T{turret_position} is empty")
        changes["turret_position"] = (op.turret_position, turret_position)
        new_tool_id = slot.tool.id if slot else None
        if new_tool_id != op.tool_id:
            old_name = op.tool.name if op.tool else None
            changes["tool"] = (old_name, slot.tool.name if slot else None)
            op.tool_id = new_tool_id
        op.turret_position = turret_position

    for name, value in (("vc", vc), ("f", f), ("ap", ap), ("passes", passes)):
        old = getattr(op, name)
        if value != old:
            changes[name] = (old, value)
            setattr(op, name, value)

    if any(name in changes for name in ("vc", "f", "ap", "passes")):
        op.cutting_data_origin = "operator"  # the operator's numbers: confirmed by the operator

    if "vc" in changes and op.vc and op.ref_diameter:
        new_n, _ = planner.spindle_speed(op.vc, op.ref_diameter, machine.max_rpm)
        if new_n != op.n:
            changes["n"] = (op.n, new_n)
            op.n = new_n

    edits = [
        Edit(operation=op, field=name, old_value=_fmt(old), new_value=_fmt(new))
        for name, (old, new) in changes.items()
    ]
    if edits:
        op.status = "edited"
        db.session.add_all(edits)
    db.session.commit()
    return edits


# --- drawings ----------------------------------------------------------------

def _normalize(text):
    return re.sub(r"[\s\-_().,/]+", "", text.lower())


def match_material(text, materials):
    """Find the material named on a drawing among the known materials. None if unsure."""
    if not text:
        return None
    wanted = _normalize(text)
    for material in materials:
        names = (material.name,) + MATERIAL_ALIASES.get(material.name, ())
        if any(_normalize(name) and _normalize(name) in wanted for name in names):
            return material
    return None


# Round bar blank in a title block: "Круг 29 ГОСТ 2590-2006", the GOST 2590 size designation
# "29-В1-ГОСТ 2590-2006" (often after the steel grade, e.g. "Круг Р6М5Ф3 ...; 29-В1-ГОСТ 2590-2006"),
# "Круг Ø29", "bar Ø55", "round bar 55". A plain "first number after Круг" would pick the 6 of Р6М5Ф3.
_NUMBER = r"(\d+(?:[.,]\d+)?)"
BLANK_PATTERNS = (
    re.compile(_NUMBER + r"\s*-\s*[А-ЯA-Z]\d?\s*-?\s*ГОСТ\s*2590", re.IGNORECASE),
    re.compile(r"\bкруг\s*[Øø⌀]?\s*" + _NUMBER + r"(?![А-ЯA-Zа-яa-z])", re.IGNORECASE),  # not "6М5..."
    re.compile(r"\b(?:round\s+)?bar\s*[Øø⌀]?\s*" + _NUMBER, re.IGNORECASE),
)


def blank_from_title_block(text):
    """Round bar diameter (mm) of a blank named in title-block text, or None."""
    if not text:
        return None
    for pattern in BLANK_PATTERNS:
        match = pattern.search(text)
        if match:
            value = float(match.group(1).replace(",", "."))
            return value if value > 0 else None
    return None


def parse_sizes(text):
    """Bar sizes from text such as "8, 10, 11" (commas, semicolons or spaces). Raises ValueError."""
    sizes = sorted({float(p) for p in re.split(r"[,;\s]+", text or "") if p})
    if any(s <= 0 for s in sizes):
        raise ValueError("sizes must be greater than zero")
    return tuple(sizes)


def hex_bar_sizes(machine, config):
    """Hex bar sizes across flats in stock: the machine's list, or the default from the config."""
    if machine is not None and machine.hex_bar_sizes:
        try:
            return parse_sizes(machine.hex_bar_sizes)
        except ValueError:
            pass
    return tuple(float(s) for s in config["HEX_BAR_SIZES"])


def parting_width(machine, default):
    """Insert width of the first parting tool in the turret, or the configured default."""
    for slot in machine.slots if machine else []:
        if slot.tool and slot.tool.type == "parting" and slot.tool.insert_width:
            return slot.tool.insert_width
    return default


def suggest_blank(data: DrawingData, machine, config):
    specs = [
        planner.FeatureSpec(id=None, type=f.type, diameter=f.diameter, length=f.length,
                            start_diameter=f.start_diameter, across_flats=f.across_flats, tolerance=f.tolerance)
        for f in data.features
    ]
    return planner.suggest_blank(
        specs,
        overall_length=data.overall_length,
        bar_diameters=tuple(config["BAR_STOCK_DIAMETERS"]),
        diameter_allowance=config["BLANK_DIAMETER_ALLOWANCE_MM"],
        facing_allowance=config["BLANK_FACING_ALLOWANCE_MM"],
        parting_width=parting_width(machine, config["DEFAULT_PARTING_WIDTH_MM"]),
        hex_bar_sizes=hex_bar_sizes(machine, config),
    )


def drawing_numbers(extraction, instance_path):
    """Numbers written as vector text on the uploaded drawing (a CAD PDF), or None (image, scan, no text)."""
    if extraction is None or extraction.file_type != "pdf":
        return None
    return pdf_text.read_numbers(os.path.join(drawings_dir(instance_path), extraction.stored_filename))


def machine_docs_dir(instance_path):
    path = os.path.join(instance_path, "machine_docs")
    os.makedirs(path, exist_ok=True)
    return path


def store_passport(machine, filename, data, instance_path):
    """Keep an uploaded passport PDF and count its pages with text. Raises UploadError for anything else."""
    name = display_filename(filename)
    if not data:
        raise UploadError("The file is empty.")
    if drawing_reader.detect_file_type(data) != "pdf":
        raise UploadError("The passport must be a PDF file.")
    stored = f"{uuid.uuid4().hex}.pdf"
    path = os.path.join(machine_docs_dir(instance_path), stored)
    with open(path, "wb") as f:
        f.write(data)
    try:
        texts = passport_reader.page_texts(path)
    except Exception as exc:  # a broken PDF: keep no record
        os.remove(path)
        raise UploadError(f"The PDF cannot be read: {exc}") from None
    document = MachineDocument(
        machine=machine, original_filename=name, stored_filename=stored, sha256=hashlib.sha256(data).hexdigest(),
        size_bytes=len(data), pages=len(texts), text_pages=sum(passport_reader.has_text(t) for t in texts),
    )
    db.session.add(document)
    db.session.commit()
    return document


def passport_path(document, instance_path):
    return os.path.join(machine_docs_dir(instance_path), document.stored_filename)


def confirm_passport(document, machine, form):
    """Write the values the operator ticked (as read: source passport with page and quote; changed: the
    operator's), record "not in passport" where the machine has no value from the operator or a passport, and
    return how many values were confirmed. Raises MachineError (nothing is written then)."""
    values = {v.field: v for v in passport_reader.reading_from_json(document.reading)}
    confirmed = 0
    for field in machine_spec.MACHINE_FIELDS:
        v = values.get(field.name)
        if v is None:
            continue
        if not v.found:
            source = machine.source_of(field.name)
            if source is None or source.source == machine_spec.SOURCE_NOT_IN_PASSPORT:
                set_machine_value(machine, field.name, None, machine_spec.SOURCE_NOT_IN_PASSPORT,
                                  document_id=document.id)
            continue
        if not form.get(f"confirm-{field.name}"):
            continue
        value = machine_spec.parse_value(field, form.get(f"value-{field.name}"))
        if value is None:
            raise MachineError(f"{field.label}: empty value ticked")
        source = machine_spec.SOURCE_PASSPORT if value == v.value else machine_spec.SOURCE_OPERATOR
        set_machine_value(machine, field.name, value, source, v.page, v.quote, document.id)
        confirmed += 1
        if field.name == "max_z_feed" and form.get("thread-limit"):
            # the operator's decision: the passport's Z feed taken as the threading limit
            set_machine_value(machine, "max_thread_feed", value, machine_spec.SOURCE_OPERATOR, v.page,
                              f"max Z feed from the passport, taken as the threading limit: {v.quote}", document.id)
    check_machine(machine)
    document.status, document.confirmed_at = "confirmed", datetime.now(timezone.utc)
    return confirmed


def read_passport_document(document, instance_path, config, client=None):
    """One paid call on the picked pages; the values are checked by the code and kept for the review."""
    pages = document.selected
    if not pages:
        raise UploadError("Pick the pages with the machine's technical data first.")
    path = passport_path(document, instance_path)
    model = config["ANTHROPIC_MODEL"]
    document.model, document.prompt_version = model, passport_reader.prompt_version()
    document.read_pages, document.read_at = document.selected_pages, datetime.now(timezone.utc)
    document.error = document.raw_response = document.reading = None
    try:
        tool_input, raw = passport_reader.read_passport(path, pages, client or drawing_reader.make_client(), model)
    except passport_reader.PassportReadError as exc:
        document.status, document.error = "failed", str(exc)
        document.raw_response = json.dumps(exc.raw) if exc.raw is not None else None
    except Exception as exc:  # API / network / auth errors: keep a record instead of a 500
        document.status, document.error = "failed", f"{type(exc).__name__}: {exc}"
    else:
        texts = passport_reader.page_texts(path)
        values = passport_reader.check_reading(tool_input, {n: texts[n - 1] for n in pages})
        document.status = "read"
        document.raw_response = json.dumps(raw)
        document.reading = passport_reader.reading_to_json(values)
    db.session.commit()
    return document


def hand_typed_files(docs_dir):
    """The hand-typed catalogue files that can be imported: docs/turnpilot_catalog_*.md."""
    if not os.path.isdir(docs_dir):
        return []
    return sorted(n for n in os.listdir(docs_dir) if re.fullmatch(r"turnpilot_catalog_[\w.-]+\.md", n))


def import_hand_typed(docs_dir, filename):
    """Add the rows of a hand-typed catalogue file as rows to check (status "read"). A row already there with the
    same values from the same file is not added again. Returns (added, already there)."""
    if filename not in hand_typed_files(docs_dir):
        raise UploadError(f"No such file: {filename}")
    with open(os.path.join(docs_dir, filename), encoding="utf-8") as f:
        parsed = catalogue_file.parse_hand_typed(f.read(), filename)
    existing = db.session.execute(db.select(CuttingDataRow).filter_by(origin="hand_typed")).scalars().all()
    added = already = 0
    for data in parsed:
        checks = data.pop("checks")
        row = CuttingDataRow(status="read", checks=json.dumps(checks, ensure_ascii=False) if checks else None, **data)
        if any(cutting_data.row_key(e) == cutting_data.row_key(row) and cutting_data.same_values(e, row)
               and e.note == row.note for e in existing):
            already += 1
            continue
        db.session.add(row)
        added += 1
    db.session.commit()
    return added, already


def catalogues_dir(instance_path):
    """Uploaded catalogues: instance/catalogues/ (instance/ is not in git: the catalogues are the makers' files)."""
    path = os.path.join(instance_path, "catalogues")
    os.makedirs(path, exist_ok=True)
    return path


def catalogue_path(document, instance_path):
    return os.path.join(catalogues_dir(instance_path), document.stored_filename)


def catalogue_pages(document, instance_path):
    """The text and label of every page, read once at the upload."""
    return catalogue_reader.load_pages(catalogue_path(document, instance_path) + ".pages.json")


def store_catalogue(upload, title, instance_path):
    """Keep an uploaded catalogue PDF (saved as it streams in, it may be large) and its pages' text. Raises
    UploadError for anything else."""
    name = display_filename(upload.filename)
    stored = f"{uuid.uuid4().hex}.pdf"
    path = os.path.join(catalogues_dir(instance_path), stored)
    upload.save(path)
    digest, size = hashlib.sha256(), 0
    with open(path, "rb") as f:
        head = f.read(4096)
        f.seek(0)
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
            size += len(chunk)
    if not size or drawing_reader.detect_file_type(head) != "pdf":
        os.remove(path)
        raise UploadError("The catalogue must be a PDF file." if size else "The file is empty.")
    try:
        pages = catalogue_reader.read_pages(path)
    except Exception as exc:  # a broken PDF: keep no record
        os.remove(path)
        raise UploadError(f"The PDF cannot be read: {exc}") from None
    catalogue_reader.save_pages(pages, path + ".pages.json")
    document = CatalogueDocument(
        original_filename=name, stored_filename=stored, title=(title or "").strip()[:200] or name,
        sha256=digest.hexdigest(), size_bytes=size, pages=len(pages["texts"]),
        text_pages=sum(passport_reader.has_text(t) for t in pages["texts"]),
    )
    db.session.add(document)
    db.session.commit()
    return document


class CuttingDataError(ValueError):
    pass


def conflicting_rows(row, confirmed):
    """The confirmed rows about the same thing (same key) as a row."""
    return [c for c in confirmed if c.id != row.id and cutting_data.row_key(c) == cutting_data.row_key(row)]


def _confirm(row, values, replace_ticked, confirmed, now):
    """Confirm one row with the values the operator left or changed. A confirmed row about the same thing with
    other values is replaced only when the operator ticked it; with the same values it is replaced silently."""
    edits = cutting_data.changes(row, values)
    for key, value in values.items():
        setattr(row, key, value)
    others = conflicting_rows(row, confirmed)
    if any(not cutting_data.same_values(o, row) for o in others) and not replace_ticked:
        raise CuttingDataError(f"{row.insert_code or row.grade} ({row.material_group}): a confirmed row gives other "
                               "values; tick \"replace\" to use this one instead")
    for other in others:
        other.status = "replaced"
    if edits:
        row.origin = "operator"
        row.note = ((row.note + "; ") if row.note else "") + "changed by the operator: " + ", ".join(edits)
        row.note = row.note[:300]
    row.status, row.confirmed_at = "confirmed", now
    confirmed.append(row)


def review_cutting_data(rows, form):
    """The operator's decision on each row to check: confirm (with the values as left or changed), reject, or
    leave. Returns (confirmed, rejected). Raises CuttingDataError: nothing is written then (the caller rolls back)."""
    confirmed = db.session.execute(db.select(CuttingDataRow).filter_by(status="confirmed")).scalars().all()
    now = datetime.now(timezone.utc)
    counts = {"confirm": 0, "reject": 0}
    for row in rows:
        decision = form.get(f"decision-{row.id}", "")
        if decision == "reject":
            row.status = "rejected"
        elif decision == "confirm":
            try:
                values = cutting_data.values_from_form(form, f"{row.id}-", row.kind)
            except ValueError as e:
                raise CuttingDataError(f"{row.insert_code or row.grade} ({row.material_group}): {e}") from None
            _confirm(row, values, bool(form.get(f"replace-{row.id}")), confirmed, now)
        else:
            continue
        counts[decision] += 1
    return counts["confirm"], counts["reject"]


def add_cutting_data_by_hand(form):
    """A row the operator enters (e.g. a value read off a graph): confirmed by entering it. Raises CuttingDataError."""
    kind = form.get("kind")
    if kind not in ("geometry", "grade_vc"):
        raise CuttingDataError("Kind: geometry or grade_vc")
    catalogue, group = (form.get("catalogue") or "").strip(), (form.get("material_group") or "").strip()
    page = (form.get("page") or "").strip()
    if not catalogue or not page or not group:
        raise CuttingDataError("Catalogue, page and material group are required: every number keeps its source")
    row = CuttingDataRow(kind=kind, catalogue=catalogue[:200], page=page[:60], material_group=group[:20],
                         origin="operator", from_graph=bool(form.get("from_graph")),
                         note=(form.get("note") or "").strip()[:300] or None)
    if kind == "geometry":
        row.insert_code = re.sub(r"\s+", "", form.get("insert_code") or "")[:60]
        if not row.insert_code:
            raise CuttingDataError("Insert / drill code is required")
    else:
        row.grade, row.application = (form.get("grade") or "").strip()[:30], form.get("application")
        if not row.grade or row.application not in cutting_data.APPLICATION_BY_TOOL_TYPE.values():
            raise CuttingDataError("Grade and application are required")
    try:
        values = cutting_data.values_from_form(form, "", kind)
    except ValueError as e:
        raise CuttingDataError(str(e)) from None
    confirmed = db.session.execute(db.select(CuttingDataRow).filter_by(status="confirmed")).scalars().all()
    for key, value in values.items():
        setattr(row, key, value)
    db.session.add(row)
    _confirm(row, {}, bool(form.get("replace")), confirmed, datetime.now(timezone.utc))
    return row


def tool_catalogue_status(tools, groups, rows):
    """{tool id: [(group, has ap / f row, has Vc row)]} for the tool library: which tools have confirmed rows. A group
    shared by several materials (C45 and S355J2+N are both P1.2) is listed once."""
    groups = list(dict.fromkeys(groups))
    return {
        t.id: [(g, cutting_data.geometry_row(rows, t.insert_code, g) is not None,
                cutting_data.grade_row(rows, t.grade, t.type, g) is not None) for g in groups]
        for t in tools if cutting_data.application_for(t.type)
    }


def read_catalogue_document(document, instance_path, config, client=None):
    """One paid call on the picked pages. The rows the code takes become rows to check (status "read", with this
    document); the earlier unconfirmed rows of this document are set aside (rejected, never used)."""
    pages = document.selected
    if not pages or not document.material_groups:
        raise UploadError("Pick the pages and the material groups first.")
    path = catalogue_path(document, instance_path)
    model = config["ANTHROPIC_MODEL"]
    document.model, document.prompt_version = model, catalogue_reader.prompt_version()
    document.read_pages, document.read_at = document.selected_pages, datetime.now(timezone.utc)
    document.error = document.raw_response = document.not_taken = None
    try:
        tool_input, raw = catalogue_reader.read_catalogue(path, pages, document.material_groups, document.codes,
                                                          client or drawing_reader.make_client(), model)
    except catalogue_reader.CatalogueReadError as exc:
        document.status, document.error = "failed", str(exc)
        document.raw_response = json.dumps(exc.raw) if exc.raw is not None else None
        db.session.commit()
        return document
    except Exception as exc:  # API / network / auth errors: keep a record instead of a 500
        document.status, document.error = "failed", f"{type(exc).__name__}: {exc}"
        db.session.commit()
        return document
    texts = catalogue_pages(document, instance_path)["texts"]
    taken, not_taken = catalogue_reader.check_reading(
        tool_input, {n: texts[n - 1] for n in pages}, document.material_groups, document.codes)
    for row in db.session.execute(db.select(CuttingDataRow).filter_by(document_id=document.id, status="read")).scalars():
        row.status, row.note = "rejected", "set aside: the document was read again"
    for data in taken:
        checks = data.pop("checks")
        db.session.add(CuttingDataRow(
            catalogue=document.title, document_id=document.id, origin="model", status="read",
            checks=json.dumps(checks, ensure_ascii=False) if checks else None,
            note=f"read by {model} ({document.prompt_version})", **data))
    document.status = "read"
    document.raw_response = json.dumps(raw)
    document.not_taken = json.dumps(not_taken, ensure_ascii=False)
    db.session.commit()
    return document


def drawings_dir(instance_path):
    path = os.path.join(instance_path, "drawings")
    os.makedirs(path, exist_ok=True)
    return path


def display_filename(filename):
    """The uploaded file's name for display and audit: no path, no control characters, any script.

    Files are stored under a generated name, so the original name never reaches the file system.
    (werkzeug's secure_filename() drops non-ASCII letters: "Втулка.pdf" became "pdf".)
    """
    name = (filename or "").replace("\\", "/").rsplit("/", 1)[-1]
    name = "".join(ch for ch in name if ch.isprintable()).strip()
    return name[-200:] or "drawing"


def check_upload(filename, data, allowed_extensions):
    """Validate an uploaded drawing. Returns (display_name, extension, file_type) or raises UploadError."""
    name = display_filename(filename)
    extension = os.path.splitext(name)[1].lstrip(".").lower()
    if extension not in allowed_extensions:
        raise UploadError(f"Unsupported file type. Allowed: {', '.join(allowed_extensions)}.")
    if not data:
        raise UploadError("The file is empty.")
    file_type = drawing_reader.detect_file_type(data)
    if file_type != FILE_TYPE_BY_EXTENSION[extension]:
        raise UploadError(f"The file content is not a valid .{extension} file.")
    return name, extension, file_type


def _previous_result(sha256, model, prompt_version):
    """The latest successful reading of the same file by the same model and prompt version, if any."""
    return db.session.execute(
        db.select(DrawingExtraction)
        .filter(DrawingExtraction.sha256 == sha256, DrawingExtraction.model == model,
                DrawingExtraction.prompt_version == prompt_version,
                DrawingExtraction.status.in_(("extracted", "confirmed")), DrawingExtraction.parsed.is_not(None))
        .order_by(DrawingExtraction.id.desc())
    ).scalars().first()


def _reading_in_progress(sha256, model, prompt_version):
    since = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=IN_FLIGHT_SECONDS)
    return db.session.execute(
        db.select(DrawingExtraction)
        .filter(DrawingExtraction.sha256 == sha256, DrawingExtraction.model == model,
                DrawingExtraction.prompt_version == prompt_version,
                DrawingExtraction.status == "pending", DrawingExtraction.created_at >= since)
    ).scalars().first()


def _cached_copy(previous, name):
    """A new extraction row that reuses an earlier result: same files, same response, no API call."""
    origin = previous.cached_from or previous
    extraction = DrawingExtraction(
        original_filename=name,
        stored_filename=origin.stored_filename,
        sent_filename=origin.sent_filename,
        file_type=origin.file_type,
        size_bytes=origin.size_bytes,
        sha256=origin.sha256,
        model=origin.model,
        prompt_version=origin.prompt_version,
        read_mode=origin.read_mode,
        status="extracted",
        raw_response=origin.raw_response,
        parsed=origin.parsed,
        cached_from_id=origin.id,
    )
    db.session.add(extraction)
    db.session.commit()
    return extraction


def read_drawing(filename, data, instance_path, config, client=None, force=False):
    """Store an uploaded drawing, send it to the model and record everything in DrawingExtraction.

    The same file already read by the same model with the same prompt version (prompts + tool
    schema) is not sent again: a new row reuses that result (cached). force=True ("Read again")
    always calls the API.

    Always returns the DrawingExtraction row; on failure its status is "failed" and `error` says why.
    Raises UploadError for files that are rejected before anything is stored.
    """
    name, extension, file_type = check_upload(filename, data, config["ALLOWED_DRAWING_EXTENSIONS"])
    sha256 = hashlib.sha256(data).hexdigest()
    if file_type == "dxf":
        return read_dxf(name, extension, data, sha256, instance_path)
    model = config["ANTHROPIC_MODEL"]
    mode = config.get("DRAWING_READ_MODE", drawing_reader.DEFAULT_READ_MODE)
    version = drawing_reader.prompt_version(mode)  # differs per mode, so modes never share a cache
    if not force:
        previous = _previous_result(sha256, model, version)
        if previous:
            return _cached_copy(previous, name)
    if _reading_in_progress(sha256, model, version):
        raise UploadError("This drawing is already being read. Wait for the result instead of uploading it again.")

    folder = drawings_dir(instance_path)
    token = uuid.uuid4().hex
    stored = f"{token}.{extension}"  # generated name on disk; the original name is only stored in the DB
    with open(os.path.join(folder, stored), "wb") as f:
        f.write(data)

    extraction = DrawingExtraction(
        original_filename=name,
        stored_filename=stored,
        file_type=file_type,
        size_bytes=len(data),
        sha256=sha256,
        model=model,
        prompt_version=version,
        read_mode=mode,
        status="pending",
    )
    db.session.add(extraction)
    db.session.commit()

    try:
        image = drawing_reader.prepare_image(data, file_type)
        sent = f"{token}.sent.{'png' if image.media_type == 'image/png' else 'jpg'}"
        with open(os.path.join(folder, sent), "wb") as f:
            f.write(image.data)
        extraction.sent_filename = sent
        result = drawing_reader.extract_drawing(image, client=client, model=config["ANTHROPIC_MODEL"], mode=mode)
    except drawing_reader.ExtractionError as exc:
        extraction.status = "failed"
        extraction.error = str(exc)
        extraction.raw_response = json.dumps(exc.raw) if exc.raw is not None else None
    except Exception as exc:  # API/network/auth errors: keep a record instead of a 500
        extraction.status = "failed"
        extraction.error = f"{type(exc).__name__}: {exc}"
    else:
        extraction.status = "extracted"
        extraction.raw_response = json.dumps(result.raw)
        extraction.parsed = result.data.model_dump_json()
    db.session.commit()
    return extraction


def dxf_code_version():
    """"dxf:" + 12 hex digits of the reader's and the mapping's source: which code read a DXF."""
    digest = hashlib.sha256()
    for path in DXF_SOURCES:
        with open(path, "rb") as f:
            digest.update(f.read().replace(b"\r\n", b"\n"))  # the same on a CRLF checkout
    return "dxf:" + digest.hexdigest()[:12]


def read_dxf(name, extension, data, sha256, instance_path):
    """Store an uploaded DXF and read it with the code (no model, no API call).

    One DrawingExtraction per part on the sheet (dxf_part = 1, 2, ...), each with its own DrawingData and
    binding report; the first one is returned. A file with no readable part gives one "failed" row.
    """
    stored = f"{uuid.uuid4().hex}.{extension}"
    path = os.path.join(drawings_dir(instance_path), stored)
    with open(path, "wb") as f:
        f.write(data)
    common = dict(original_filename=name, stored_filename=stored, file_type="dxf", size_bytes=len(data),
                  sha256=sha256, prompt_version=dxf_code_version(), read_mode="dxf")
    try:
        reader_path, adapted = dxf_adapter.prepare(path)  # a SolidWorks sheet rewritten the KOMPAS way
        results = dxf_input.part_results(dxf_reader.read_dxf(reader_path))
        for result in results:  # what the adapter did, for the review page
            result.data.warnings[:0] = adapted.warnings
            result.data.material = result.data.material or adapted.material
            result.report["cad"], result.report["scale"] = adapted.cad, adapted.scale
    except dxf_reader.DxfReadError as exc:
        results, error = None, str(exc)
    except Exception as exc:  # a malformed file must leave a record, not a 500
        results, error = None, f"The DXF file cannot be read: {type(exc).__name__}: {exc}"
    if not results:
        extraction = DrawingExtraction(**common, status="failed", error=error)
        db.session.add(extraction)
        db.session.commit()
        return extraction
    extractions = [
        DrawingExtraction(**common, status="extracted", dxf_part=n, parsed=result.data.model_dump_json(),
                          binding=json.dumps(result.report, ensure_ascii=False))
        for n, result in enumerate(results, start=1)
    ]
    db.session.add_all(extractions)
    db.session.commit()
    return extractions[0]


def dxf_parts(extraction):
    """All extractions of the same uploaded DXF sheet, in part order (the extraction itself if it is alone)."""
    if not extraction.is_dxf:
        return [extraction]
    return db.session.execute(
        db.select(DrawingExtraction)
        .filter(DrawingExtraction.stored_filename == extraction.stored_filename, DrawingExtraction.read_mode == "dxf")
        .order_by(DrawingExtraction.dxf_part)
    ).scalars().all()


def dxf_arc_shapes(extraction, instance_path):
    """Per row of a DXF part: whether an arc or a fillet bulges away from the axis (convex) or into it, and a
    fillet's side, from the file's geometry (dxf_reader, called as it is). {} when the file cannot be read."""
    report = dxf_report(extraction)
    if not extraction.is_dxf or not report:
        return {}
    try:
        reading = dxf_reader.read_dxf(dxf_adapter.reader_path(
            os.path.join(drawings_dir(instance_path), extraction.stored_filename)))
        part = reading.parts[report["part"] - 1]
    except Exception:  # the stored file is gone or unreadable: the operator chooses
        return {}
    data = extraction_data(extraction)
    by_index = {s.index: s for s in part.sections}  # the reader numbers its sections (from 1)
    shapes, fillets_seen = {}, {}
    for i, (row, feature) in enumerate(zip(report["rows"], data.features)):
        section = by_index.get(row.get("section"))
        if section is None:
            continue
        if feature.type == "arc" and section.prim.arc:
            mid = (section.x0 + section.x1) / 2
            chord = (section.r_at(section.x0) + section.r_at(section.x1)) / 2
            shapes[i] = {"arc_shape": "convex" if section.r_at(mid) > chord + 1e-6 else "concave",
                         "drawn_radius": round(section.prim.arc[2], 3)}
        elif feature.type == "fillet":
            k = fillets_seen.get(section.index, 0)
            fillets_seen[section.index] = k + 1
            if k >= len(section.fillets):
                continue
            side, _radius = section.fillets[k]
            neighbour = by_index.get(section.index + (1 if side == "right" else -1))
            here = section.r_at(section.x1 if side == "right" else section.x0)
            there = neighbour.r_at(neighbour.x0 if side == "right" else neighbour.x1) if neighbour else 0.0
            # into a larger neighbour: an inside corner (concave); towards a smaller one or the end: an edge
            shapes[i] = {"arc_shape": "concave" if there > here + 1e-6 else "convex", "face": side}
    return shapes


def dxf_report(extraction):
    return json.loads(extraction.binding) if extraction.binding else None


def read_again(extraction, instance_path, config, client=None):
    """New API call for a drawing that was already uploaded (the user asked for it explicitly)."""
    with open(os.path.join(drawings_dir(instance_path), extraction.stored_filename), "rb") as f:
        data = f.read()
    return read_drawing(extraction.original_filename, data, instance_path, config, client=client, force=True)


def extraction_data(extraction):
    return DrawingData.model_validate_json(extraction.parsed) if extraction.parsed else None
