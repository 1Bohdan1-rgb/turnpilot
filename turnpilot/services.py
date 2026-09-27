"""Glue between the database models and the pure planner."""

from . import planner
from .models import Edit, Machine, Operation, TurretSlot, db


def get_machine():
    return db.session.execute(db.select(Machine).order_by(Machine.id)).scalars().first()


def tool_spec(tool):
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
    )


def turret_entries(machine):
    return [planner.TurretEntry(s.position, tool_spec(s.tool)) for s in machine.slots if s.tool is not None]


def job_spec(job):
    features = tuple(
        planner.FeatureSpec(
            id=f.id, type=f.type, diameter=f.diameter, length=f.length, ra=f.ra, pitch=f.pitch,
            start_diameter=f.start_diameter,
        )
        for f in job.active_features
    )
    return planner.JobSpec(
        iso_group=job.material.iso_group,
        blank_diameter=job.blank_diameter,
        blank_length=job.blank_length,
        features=features,
    )


def calculate_operations(job, machine):
    """Archive the current operations of a job and add a fresh proposal as a new version.

    Nothing is deleted: archived operations keep their status and edit log.
    """
    version = job.last_calculation_version + 1
    for op in job.current_operations:
        op.is_archived = True
    for planned in planner.plan_job(job_spec(job), turret_entries(machine), machine.max_rpm):
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
                note="; ".join(planned.notes) or None,
                warning="; ".join(planned.warnings) or None,
                status="proposed",
                calculation_version=version,
            )
        )
    db.session.commit()


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
            slot = db.session.execute(
                db.select(TurretSlot).filter_by(machine_id=machine.id, position=turret_position)
            ).scalar_one_or_none()
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
