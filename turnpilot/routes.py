from flask import Blueprint, abort, flash, redirect, render_template, request, url_for

from . import services
from .models import (
    FEATURE_TYPES,
    ISO_GROUPS,
    TOOL_TYPES,
    Feature,
    Job,
    Material,
    Operation,
    Tool,
    db,
)

bp = Blueprint("main", __name__)


class FormError(ValueError):
    pass


def _number(form, key, cast=float, required=False, positive=True):
    raw = (form.get(key) or "").strip().replace(",", ".")
    if not raw:
        if required:
            raise FormError(f"{key} is required")
        return None
    try:
        value = cast(raw)
    except ValueError:
        raise FormError(f"{key} must be a number") from None
    if positive and value <= 0:
        raise FormError(f"{key} must be greater than zero")
    return value


def _machine_or_404():
    machine = services.get_machine()
    if machine is None:
        abort(404, "No machine configured. Run `flask --app turnpilot seed` first.")
    return machine


@bp.route("/")
def index():
    return redirect(url_for("main.jobs"))


@bp.route("/machine", methods=["GET", "POST"])
def machine():
    machine = _machine_or_404()
    if request.method == "POST":
        try:
            if request.form.get("action") == "profile":
                machine.name = request.form.get("name", "").strip() or machine.name
                machine.max_rpm = _number(request.form, "max_rpm", int, required=True)
                machine.power_kw = _number(request.form, "power_kw", required=True)
                machine.max_diameter = _number(request.form, "max_diameter", required=True)
                flash("Machine profile saved.")
            elif request.form.get("action") == "turret":
                for slot in machine.slots:
                    tool_id = request.form.get(f"slot_{slot.position}") or None
                    slot.tool_id = int(tool_id) if tool_id else None
                flash("Turret saved.")
            db.session.commit()
        except FormError as e:
            db.session.rollback()
            flash(str(e), "error")
        return redirect(url_for("main.machine"))

    tools = db.session.execute(db.select(Tool).order_by(Tool.name)).scalars().all()
    return render_template(
        "machine.html", machine=machine, tools=tools, tool_types=TOOL_TYPES, iso_groups=ISO_GROUPS
    )


@bp.route("/tools", methods=["POST"])
def add_tool():
    form = request.form
    try:
        name = form.get("name", "").strip()
        tool_type = form.get("type")
        iso_group = "".join(g for g in ISO_GROUPS if g in form.getlist("iso_group"))
        if not name:
            raise FormError("Tool name is required")
        if tool_type not in TOOL_TYPES:
            raise FormError("Unknown tool type")
        if not iso_group:
            raise FormError("Select at least one ISO group")
        values = {k: _number(form, k, required=True) for k in ("vc_min", "vc_max", "f_min", "f_max", "ap_min", "ap_max")}
        for lo, hi in (("vc_min", "vc_max"), ("f_min", "f_max"), ("ap_min", "ap_max")):
            if values[lo] > values[hi]:
                raise FormError(f"{lo} must not exceed {hi}")
        db.session.add(
            Tool(
                name=name,
                type=tool_type,
                insert_code=form.get("insert_code", "").strip(),
                grade=form.get("grade", "").strip(),
                iso_group=iso_group,
                **values,
            )
        )
        db.session.commit()
        flash(f"Tool '{name}' added to the library. Assign it to a turret position.")
    except FormError as e:
        flash(str(e), "error")
    return redirect(url_for("main.machine"))


@bp.route("/jobs", methods=["GET", "POST"])
def jobs():
    if request.method == "POST":
        try:
            name = request.form.get("name", "").strip()
            if not name:
                raise FormError("Job name is required")
            material = db.session.get(Material, _number(request.form, "material_id", int, required=True))
            if material is None:
                raise FormError("Unknown material")
            job = Job(
                name=name,
                material=material,
                quantity=_number(request.form, "quantity", int) or 1,
                blank_diameter=_number(request.form, "blank_diameter", required=True),
                blank_length=_number(request.form, "blank_length", required=True),
            )
            machine = services.get_machine()
            if machine and job.blank_diameter > machine.max_diameter:
                raise FormError(f"Blank diameter exceeds machine max diameter ({machine.max_diameter:g} mm)")
            db.session.add(job)
            db.session.commit()
            return redirect(url_for("main.job_detail", job_id=job.id))
        except FormError as e:
            flash(str(e), "error")
            return redirect(url_for("main.jobs"))

    all_jobs = db.session.execute(db.select(Job).order_by(Job.created_at.desc())).scalars().all()
    materials = db.session.execute(db.select(Material).order_by(Material.name)).scalars().all()
    return render_template("jobs.html", jobs=all_jobs, materials=materials)


@bp.route("/jobs/<int:job_id>")
def job_detail(job_id):
    job = db.get_or_404(Job, job_id)
    return render_template("job_detail.html", job=job, feature_types=FEATURE_TYPES)


@bp.route("/jobs/<int:job_id>/features", methods=["POST"])
def add_feature(job_id):
    job = db.get_or_404(Job, job_id)
    form = request.form
    try:
        feature_type = form.get("type")
        if feature_type not in FEATURE_TYPES:
            raise FormError("Unknown feature type")
        diameter = _number(form, "diameter", required=feature_type in ("od_turn", "bore", "groove", "thread", "chamfer"))
        pitch = _number(form, "pitch", required=feature_type == "thread")
        if diameter and diameter > job.blank_diameter and feature_type != "bore":
            raise FormError("Feature diameter is larger than the blank diameter")
        job.features.append(
            Feature(
                type=feature_type,
                diameter=diameter,
                length=_number(form, "length"),
                tolerance=form.get("tolerance", "").strip() or None,
                ra=_number(form, "ra"),
                pitch=pitch,
            )
        )
        db.session.commit()
    except FormError as e:
        flash(str(e), "error")
    return redirect(url_for("main.job_detail", job_id=job.id))


@bp.route("/jobs/<int:job_id>/features/<int:feature_id>/delete", methods=["POST"])
def delete_feature(job_id, feature_id):
    feature = db.get_or_404(Feature, feature_id)
    if feature.job_id != job_id:
        abort(404)
    db.session.delete(feature)
    db.session.commit()
    return redirect(url_for("main.job_detail", job_id=job_id))


@bp.route("/jobs/<int:job_id>/calculate", methods=["POST"])
def calculate(job_id):
    job = db.get_or_404(Job, job_id)
    if not job.features:
        flash("Add at least one feature before calculating.", "error")
        return redirect(url_for("main.job_detail", job_id=job.id))
    services.calculate_operations(job, _machine_or_404())
    return redirect(url_for("main.operations", job_id=job.id))


@bp.route("/jobs/<int:job_id>/operations")
def operations(job_id):
    job = db.get_or_404(Job, job_id)
    return render_template("operations.html", job=job, machine=_machine_or_404())


@bp.route("/operations/<int:op_id>/approve", methods=["POST"])
def approve_operation(op_id):
    op = db.get_or_404(Operation, op_id)
    if op.tool_id is None:
        flash(f"Operation {op.sequence} has no tool and cannot be approved.", "error")
    else:
        op.status = "approved"
        db.session.commit()
    return redirect(url_for("main.operations", job_id=op.job_id))


@bp.route("/operations/<int:op_id>/edit", methods=["GET", "POST"])
def edit_operation(op_id):
    op = db.get_or_404(Operation, op_id)
    machine = _machine_or_404()
    if request.method == "POST":
        try:
            edits = services.apply_operation_edit(
                op,
                machine,
                turret_position=_number(request.form, "turret_position", int),
                vc=_number(request.form, "vc"),
                f=_number(request.form, "f"),
                ap=_number(request.form, "ap"),
                passes=_number(request.form, "passes", int),
            )
            flash(f"Operation {op.sequence}: {len(edits)} field(s) changed." if edits else "No changes.")
            return redirect(url_for("main.operations", job_id=op.job_id))
        except (FormError, ValueError) as e:
            db.session.rollback()
            flash(str(e), "error")
    slots = [s for s in machine.slots if s.tool is not None]
    return render_template("operation_edit.html", op=op, slots=slots)
