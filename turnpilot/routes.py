from datetime import datetime, timezone

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    send_from_directory,
    url_for,
)

from . import drawing_reader, planner, services
from .models import (
    FEATURE_TYPES,
    DrawingExtraction,
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
                insert_width=_number(form, "insert_width"),
                **values,
            )
        )
        db.session.commit()
        flash(f"Tool '{name}' added to the library. Assign it to a turret position.")
    except FormError as e:
        flash(str(e), "error")
    return redirect(url_for("main.machine"))


def _job_from_form(form):
    """Build an unsaved Job from form fields or raise FormError."""
    name = form.get("name", "").strip()
    if not name:
        raise FormError("Job name is required")
    material = db.session.get(Material, _number(form, "material_id", int, required=True))
    if material is None:
        raise FormError("Unknown material")
    job = Job(
        name=name,
        material=material,
        quantity=_number(form, "quantity", int) or 1,
        blank_diameter=_number(form, "blank_diameter", required=True),
        blank_length=_number(form, "blank_length", required=True),
    )
    machine = services.get_machine()
    if machine and job.blank_diameter > machine.max_diameter:
        raise FormError(f"Blank diameter exceeds machine max diameter ({machine.max_diameter:g} mm)")
    return job


def _feature_from_form(form, blank_diameter, prefix=""):
    """Build an unsaved Feature from form fields (optionally prefixed) or raise FormError."""
    feature_type = form.get(prefix + "type")
    if feature_type not in FEATURE_TYPES:
        raise FormError("Unknown feature type")
    diameter = _number(
        form, prefix + "diameter", required=feature_type in ("od_turn", "bore", "groove", "thread", "chamfer")
    )
    pitch = _number(form, prefix + "pitch", required=feature_type == "thread")
    if diameter and diameter > blank_diameter and feature_type != "bore":
        raise FormError("Feature diameter is larger than the blank diameter")
    start_diameter = _number(form, prefix + "start_diameter") if feature_type in ("groove", "taper") else None
    if start_diameter is not None:
        if start_diameter > blank_diameter:
            raise FormError("Start diameter is larger than the blank diameter")
        if feature_type == "groove" and diameter is not None and start_diameter <= diameter:
            raise FormError("Groove start diameter must be larger than the groove bottom diameter")
    return Feature(
        type=feature_type,
        diameter=diameter,
        length=_number(form, prefix + "length"),
        tolerance=form.get(prefix + "tolerance", "").strip() or None,
        ra=_number(form, prefix + "ra"),
        pitch=pitch if feature_type == "thread" else None,
        start_diameter=start_diameter,
        radius=_number(form, prefix + "radius") if feature_type == "fillet" else None,
    )


@bp.route("/jobs", methods=["GET", "POST"])
def jobs():
    if request.method == "POST":
        try:
            job = _job_from_form(request.form)
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
    try:
        job.features.append(_feature_from_form(request.form, job.blank_diameter))
        db.session.commit()
    except FormError as e:
        flash(str(e), "error")
    return redirect(url_for("main.job_detail", job_id=job.id))


@bp.route("/jobs/<int:job_id>/features/<int:feature_id>/delete", methods=["POST"])
def delete_feature(job_id, feature_id):
    feature = db.get_or_404(Feature, feature_id)
    if feature.job_id != job_id:
        abort(404)
    # Soft delete: archived operations and their edit log still refer to this feature.
    feature.is_deleted = True
    db.session.commit()
    return redirect(url_for("main.job_detail", job_id=job_id))


@bp.route("/jobs/<int:job_id>/calculate", methods=["POST"])
def calculate(job_id):
    job = db.get_or_404(Job, job_id)
    if not job.active_features:
        flash("Add at least one feature before calculating.", "error")
        return redirect(url_for("main.job_detail", job_id=job.id))
    had_operations = bool(job.current_operations)
    services.calculate_operations(job, _machine_or_404())
    if had_operations:
        flash("Previous operations were archived. See the calculation history.")
    return redirect(url_for("main.operations", job_id=job.id))


@bp.route("/jobs/<int:job_id>/operations")
def operations(job_id):
    job = db.get_or_404(Job, job_id)
    return render_template("operations.html", job=job, machine=_machine_or_404())


@bp.route("/jobs/<int:job_id>/history")
def history(job_id):
    job = db.get_or_404(Job, job_id)
    versions = {}
    for op in job.archived_operations:
        versions.setdefault(op.calculation_version, []).append(op)
    return render_template("history.html", job=job, versions=versions)


@bp.route("/operations/<int:op_id>/approve", methods=["POST"])
def approve_operation(op_id):
    op = db.get_or_404(Operation, op_id)
    if op.is_archived:
        flash("Archived operations are read-only.", "error")
    elif op.tool_id is None:
        flash(f"Operation {op.sequence} has no tool and cannot be approved.", "error")
    else:
        op.status = "approved"
        db.session.commit()
    return redirect(url_for("main.operations", job_id=op.job_id))


@bp.route("/operations/<int:op_id>/edit", methods=["GET", "POST"])
def edit_operation(op_id):
    op = db.get_or_404(Operation, op_id)
    if op.is_archived:
        flash("Archived operations are read-only.", "error")
        return redirect(url_for("main.history", job_id=op.job_id))
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


# --- drawing upload and review --------------------------------------------------

REVIEW_FIELDS = ("type", "diameter", "start_diameter", "length", "tolerance", "ra", "pitch", "radius", "confidence")
BORE_RA_WARNING = "Ra may belong to the bore — check"
NOT_TURNED_BANNER = "This does not look like a lathe part"


def _to_float(value):
    try:
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return None


def _decorate_row(row, threshold):
    confidence = _to_float(row.get("confidence"))
    row["low_confidence"] = confidence is not None and confidence < threshold
    row["grinding"] = planner.needs_grinding(
        row.get("tolerance") or None, _to_float(row.get("diameter")), _to_float(row.get("ra"))
    )
    return row


def _flag_bore_ra(rows):
    """Mark OD rows with an Ra and bore rows without one when both exist.

    In a sectioned bushing the model tends to put the bore's Ra on the outside diameter.
    """
    used = [r for r in rows if r["include"]]
    od_with_ra = [r for r in used if r["type"] == "od_turn" and _to_float(r.get("ra")) is not None]
    bore_without_ra = [r for r in used if r["type"] == "bore" and _to_float(r.get("ra")) is None]
    flagged = {id(r) for r in od_with_ra + bore_without_ra} if od_with_ra and bore_without_ra else set()
    for row in rows:
        row["bore_ra_check"] = id(row) in flagged
    return rows


def _apply_general_ra(rows, general_ra):
    """The general roughness (corner symbol) applies to every feature without its own Ra."""
    for row in rows:
        row["ra_general"] = general_ra is not None and row.get("ra") is None
        if row["ra_general"]:
            row["ra"] = general_ra
            row["grinding"] = planner.needs_grinding(
                row.get("tolerance") or None, _to_float(row.get("diameter")), general_ra
            )
    return rows


def _review_values_from_extraction(extraction):
    data = services.extraction_data(extraction)
    materials = db.session.execute(db.select(Material).order_by(Material.name)).scalars().all()
    material = services.match_material(data.material, materials)
    suggestion = services.suggest_blank(data, services.get_machine(), current_app.config)
    threshold = current_app.config["LOW_CONFIDENCE_THRESHOLD"]
    rows = [
        _decorate_row({**{k: getattr(f, k) for k in REVIEW_FIELDS}, "include": True}, threshold)
        for f in data.features
    ]
    _flag_bore_ra(rows)  # on the marks read from the drawing, before the general Ra fills the gaps
    _apply_general_ra(rows, data.general_ra)
    blank_missing = data.blank_diameter is None or data.blank_length is None
    return {
        "name": extraction.original_filename.rsplit(".", 1)[0],
        "material_id": material.id if material else None,
        "quantity": data.quantity or 1,
        # A blank written on the drawing wins; otherwise the suggestion, flagged as such.
        "blank_diameter": data.blank_diameter or suggestion.diameter,
        "blank_length": data.blank_length or suggestion.length,
        "blank_diameter_suggested": data.blank_diameter is None and suggestion.diameter is not None,
        "blank_length_suggested": data.blank_length is None and suggestion.length is not None,
        "blank_notes": suggestion.notes if blank_missing else (),
        # Already read from the drawing: the checkbox would only add a duplicate.
        "add_face": not any(r["type"] == "face" for r in rows),
        "add_parting": not any(r["type"] == "parting" for r in rows),
        "override_part_type": False,
        "rows": rows,
    }


def _review_values_from_form(form):
    """Re-render the review form with what the user submitted (after a validation error)."""
    threshold = current_app.config["LOW_CONFIDENCE_THRESHOLD"]
    count = _number(form, "feature_count", int, positive=False) or 0
    rows = []
    for i in range(count):
        row = {k: form.get(f"f{i}-{k}") or None for k in REVIEW_FIELDS}
        row["include"] = bool(form.get(f"f{i}-include"))
        rows.append(_decorate_row(row, threshold))
    _flag_bore_ra(rows)
    material_id = _to_float(form.get("material_id"))
    return {
        "name": form.get("name", ""),
        "material_id": int(material_id) if material_id else None,
        "quantity": form.get("quantity"),
        "blank_diameter": form.get("blank_diameter"),
        "blank_length": form.get("blank_length"),
        "blank_diameter_suggested": form.get("blank_diameter_suggested") == "1",
        "blank_length_suggested": form.get("blank_length_suggested") == "1",
        "blank_notes": (),
        "add_face": bool(form.get("add_face")),
        "add_parting": bool(form.get("add_parting")),
        "override_part_type": bool(form.get("override_part_type")),
        "rows": rows,
    }


def _render_review(extraction, values, status=200):
    materials = db.session.execute(db.select(Material).order_by(Material.name)).scalars().all()
    page = render_template(
        "extraction_review.html",
        extraction=extraction,
        data=services.extraction_data(extraction),
        values=values,
        materials=materials,
        feature_types=FEATURE_TYPES,
        grinding_warning=planner.GRINDING_WARNING,
        bore_ra_warning=BORE_RA_WARNING,
        not_turned_banner=NOT_TURNED_BANNER,
    )
    return page, status


def _reading_error(extraction):
    """Message for a failed reading; "too complex" is shown as is, it already tells what to do."""
    if extraction.error and extraction.error.startswith(drawing_reader.TOO_COMPLEX_MESSAGE):
        return extraction.error
    return f"The drawing could not be read: {extraction.error}"


@bp.route("/jobs/upload", methods=["GET", "POST"])
def upload_drawing():
    if request.method == "POST":
        upload = request.files.get("drawing")
        if upload is None or not upload.filename:
            flash("Choose a drawing file to upload.", "error")
            return redirect(url_for("main.upload_drawing"))
        try:
            extraction = services.read_drawing(
                upload.filename,
                upload.read(),
                current_app.instance_path,
                current_app.config,
                client=current_app.config.get("ANTHROPIC_CLIENT"),  # tests inject a fake client here
            )
        except services.UploadError as e:
            flash(str(e), "error")
            return redirect(url_for("main.upload_drawing"))
        if extraction.status != "extracted":
            flash(_reading_error(extraction), "error")
            return redirect(url_for("main.upload_drawing"))
        return redirect(url_for("main.review_extraction", extraction_id=extraction.id))

    recent = db.session.execute(
        db.select(DrawingExtraction).order_by(DrawingExtraction.created_at.desc()).limit(10)
    ).scalars().all()
    return render_template("upload.html", recent=recent)


@bp.route("/extractions/<int:extraction_id>/review")
def review_extraction(extraction_id):
    extraction = db.get_or_404(DrawingExtraction, extraction_id)
    if extraction.status == "confirmed" and extraction.job_id:
        return redirect(url_for("main.job_detail", job_id=extraction.job_id))
    if extraction.status != "extracted":
        abort(404)
    return _render_review(extraction, _review_values_from_extraction(extraction))


@bp.route("/extractions/<int:extraction_id>/confirm", methods=["POST"])
def confirm_extraction(extraction_id):
    extraction = db.get_or_404(DrawingExtraction, extraction_id)
    if extraction.status == "confirmed" and extraction.job_id:
        return redirect(url_for("main.job_detail", job_id=extraction.job_id))
    if extraction.status != "extracted":
        abort(400)

    form = request.form
    try:
        data = services.extraction_data(extraction)
        if data.part_type != "turned" and not form.get("override_part_type"):
            raise FormError(f"{NOT_TURNED_BANNER}. Tick “I understand, create anyway” to create the job.")
        job = _job_from_form(form)
        features = []
        for i in range(_number(form, "feature_count", int, positive=False) or 0):
            if not form.get(f"f{i}-include"):
                continue
            try:
                feature = _feature_from_form(form, job.blank_diameter, prefix=f"f{i}-")
                feature.confidence = _number(form, f"f{i}-confidence", positive=False)
            except FormError as e:
                raise FormError(f"Feature {i + 1}: {e}") from None
            features.append(feature)
        # The checkboxes add facing/parting only when the drawing rows do not already have them.
        if form.get("add_face") and not any(f.type == "face" for f in features):
            features.insert(0, Feature(type="face"))
        if form.get("add_parting") and not any(f.type == "parting" for f in features):
            features.append(Feature(type="parting"))
        if not features:
            raise FormError("Include at least one feature")
    except FormError as e:
        db.session.rollback()
        flash(str(e), "error")
        return _render_review(extraction, _review_values_from_form(form), status=400)

    job.features = features
    db.session.add(job)
    extraction.job = job
    extraction.status = "confirmed"
    extraction.confirmed_at = datetime.now(timezone.utc)
    db.session.commit()
    flash("Job created from the drawing. Check the features and press Calculate.")
    return redirect(url_for("main.job_detail", job_id=job.id))


@bp.route("/extractions/<int:extraction_id>/read-again", methods=["POST"])
def read_again(extraction_id):
    """Deliberate new API call for a drawing whose result came from the cache (or was wrong)."""
    extraction = db.get_or_404(DrawingExtraction, extraction_id)
    try:
        fresh = services.read_again(
            extraction, current_app.instance_path, current_app.config,
            client=current_app.config.get("ANTHROPIC_CLIENT"),
        )
    except services.UploadError as e:
        flash(str(e), "error")
        return redirect(url_for("main.upload_drawing"))
    if fresh.status != "extracted":
        flash(_reading_error(fresh), "error")
        return redirect(url_for("main.upload_drawing"))
    return redirect(url_for("main.review_extraction", extraction_id=fresh.id))


@bp.route("/extractions/<int:extraction_id>/drawing/<which>")
def extraction_file(extraction_id, which):
    """Serve the original upload or the image that was sent to the model."""
    extraction = db.get_or_404(DrawingExtraction, extraction_id)
    filename = {"original": extraction.stored_filename, "sent": extraction.sent_filename}.get(which)
    if not filename:
        abort(404)
    return send_from_directory(services.drawings_dir(current_app.instance_path), filename)
