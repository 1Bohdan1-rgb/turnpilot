import json
import os
import re
from datetime import datetime, timezone
from types import SimpleNamespace

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

from . import catalogue_reader, cutting_data as cd, drawing_reader, number_check, passport_reader, planner, services
from .extraction_schema import (
    RADIUS_TYPES,
    START_DIAMETER_TYPES,
    hex_across_corners,
    hex_across_flats,
    normalize_tolerance,
)
from .machine_spec import MACHINE_FIELDS, PROGRAMMING_FIELDS, SOURCE_OPERATOR, format_value, parse_value
from .models import (
    BLANK_SHAPES,
    FEATURE_TYPES,
    CatalogueDocument,
    GcodeProgram,
    CuttingDataRow,
    DrawingExtraction,
    ISO_GROUPS,
    TOOL_TYPES,
    Feature,
    Job,
    MachineDocument,
    Material,
    Operation,
    Tool,
    TurretSlot,
    db,
)

bp = Blueprint("main", __name__)


@bp.app_template_filter("fromjson")
def _fromjson(text):
    return json.loads(text) if text else {}


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
                # a field the form does not send is kept; a changed one is recorded as the operator's
                for field in MACHINE_FIELDS:
                    if field.name not in request.form:
                        continue
                    try:
                        value = parse_value(field, request.form.get(field.name))
                        if value != getattr(machine, field.name) or services.is_demo(machine, field.name):
                            services.set_machine_value(machine, field.name, value, SOURCE_OPERATOR)
                    except ValueError as e:
                        raise FormError(str(e)) from None
                try:
                    services.check_machine(machine)
                except services.MachineError as e:
                    raise FormError(str(e)) from None
                flash("Machine profile saved.")
            elif request.form.get("action") == "programming":
                for field in PROGRAMMING_FIELDS:
                    try:
                        value = parse_value(field, request.form.get(field.name))
                    except ValueError as e:
                        raise FormError(str(e)) from None
                    if value != getattr(machine, field.name) or services.is_demo(machine, field.name):
                        services.set_machine_value(machine, field.name, value, SOURCE_OPERATOR)
                flash("Programming values saved.")
            elif request.form.get("action") == "stock":
                text = request.form.get("hex_bar_sizes", "").strip()
                try:
                    sizes = services.parse_sizes(text)
                except ValueError:
                    raise FormError("Hex bar sizes: numbers across flats in mm, e.g. 8, 10, 11") from None
                machine.hex_bar_sizes = ", ".join(f"{s:g}" for s in sizes) or None
                flash("Bar stock saved.")
            elif request.form.get("action") == "materials":
                for material in db.session.execute(db.select(Material)).scalars():
                    prefix = f"m{material.id}-"
                    material.kc1 = _number(request.form, prefix + "kc1")
                    material.mc = _number(request.form, prefix + "mc")
                    if material.mc is not None and material.mc >= 1:
                        raise FormError(f"{material.name}: mc is an exponent below 1 (e.g. 0.25)")
                    material.kc_source = (request.form.get(prefix + "kc_source") or "").strip()[:200] or None
                    material.catalogue_group = (request.form.get(prefix + "catalogue_group") or "").strip()[:20] or None
                flash("Materials saved.")
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

    tools = db.session.execute(db.select(Tool).filter_by(is_retired=False).order_by(Tool.name)).scalars().all()
    materials = db.session.execute(db.select(Material).order_by(Material.name)).scalars().all()
    hex_sizes = services.hex_bar_sizes(machine, current_app.config)
    groups = sorted({m.catalogue_group for m in materials if m.catalogue_group})
    catalogue_status = services.tool_catalogue_status(tools, groups, services.confirmed_rows())
    return render_template(
        "machine.html", machine=machine, tools=tools, materials=materials, tool_types=TOOL_TYPES,
        catalogue_status=catalogue_status,
        iso_groups=ISO_GROUPS, machine_fields=MACHINE_FIELDS, format_value=format_value,
        programming_fields=PROGRAMMING_FIELDS,
        hex_bar_sizes=", ".join(f"{s:g}" for s in hex_sizes),
    )


MAX_PASSPORT_PAGES = 20  # pages sent to the model in one reading (cost)


@bp.route("/machine/passport", methods=["GET", "POST"])
def machine_passport():
    machine = _machine_or_404()
    if request.method == "POST":
        upload = request.files.get("passport")
        if upload is None or not upload.filename:
            flash("Choose the machine passport (PDF).", "error")
            return redirect(url_for("main.machine_passport"))
        try:
            document = services.store_passport(machine, upload.filename, upload.read(), current_app.instance_path)
        except services.UploadError as e:
            flash(str(e), "error")
            return redirect(url_for("main.machine_passport"))
        return redirect(url_for("main.passport_pages", document_id=document.id))
    documents = db.session.execute(
        db.select(MachineDocument).order_by(MachineDocument.created_at.desc())
    ).scalars().all()
    return render_template("passport.html", machine=machine, documents=documents)


@bp.route("/machine/passport/<int:document_id>", methods=["GET", "POST"])
def passport_pages(document_id):
    """The passport's pages with their text, for the operator to pick the technical data pages."""
    document = db.get_or_404(MachineDocument, document_id)
    if request.method == "POST":
        pages = sorted({int(p) for p in request.form.getlist("page") if p.isdigit() and 1 <= int(p) <= document.pages})
        if not pages:
            flash("Pick the pages with the machine's technical data.", "error")
        elif len(pages) > MAX_PASSPORT_PAGES:
            flash(f"At most {MAX_PASSPORT_PAGES} pages in one reading.", "error")
        else:
            document.selected_pages = ", ".join(str(p) for p in pages)
            db.session.commit()
            flash(f"Pages {document.selected_pages} picked.")
        return redirect(url_for("main.passport_pages", document_id=document.id))
    texts = passport_reader.page_texts(services.passport_path(document, current_app.instance_path))
    pages = [
        dict(number=i, text=" ".join(t.split())[:400], has_text=passport_reader.has_text(t),
             likely=passport_reader.likely_spec_page(t), selected=i in document.selected)
        for i, t in enumerate(texts, start=1)
    ]
    estimate = passport_reader.estimate(services.passport_path(document, current_app.instance_path),
                                        document.selected) if document.selected else None
    return render_template("passport_pages.html", document=document, pages=pages, max_pages=MAX_PASSPORT_PAGES,
                           estimate=estimate, model=current_app.config["ANTHROPIC_MODEL"])


@bp.route("/machine/passport/<int:document_id>/read", methods=["POST"])
def read_passport(document_id):
    """The paid call: only on the operator's explicit request, for the picked pages."""
    document = db.get_or_404(MachineDocument, document_id)
    if not request.form.get("paid"):
        flash("Tick that you start a paid reading (one API call).", "error")
        return redirect(url_for("main.passport_pages", document_id=document.id))
    try:
        services.read_passport_document(document, current_app.instance_path, current_app.config,
                                        client=current_app.config.get("ANTHROPIC_CLIENT"))
    except services.UploadError as e:
        flash(str(e), "error")
        return redirect(url_for("main.passport_pages", document_id=document.id))
    if document.status != "read":
        flash(f"The passport could not be read: {document.error}", "error")
        return redirect(url_for("main.passport_pages", document_id=document.id))
    return redirect(url_for("main.passport_review", document_id=document.id))


@bp.route("/machine/passport/<int:document_id>/review")
def passport_review(document_id):
    document = db.get_or_404(MachineDocument, document_id)
    if document.status not in ("read", "confirmed") or not document.reading:
        abort(404)
    machine = _machine_or_404()
    values = {v.field: v for v in passport_reader.reading_from_json(document.reading)}
    rows = [dict(field=f, value=values[f.name], current=getattr(machine, f.name), source=machine.source_of(f.name))
            for f in MACHINE_FIELDS if f.name in values]
    return render_template("passport_review.html", document=document, rows=rows, format_value=format_value)


@bp.route("/machine/passport/<int:document_id>/confirm", methods=["POST"])
def confirm_passport(document_id):
    document = db.get_or_404(MachineDocument, document_id)
    if document.status not in ("read", "confirmed") or not document.reading:
        abort(400)
    try:
        count = services.confirm_passport(document, _machine_or_404(), request.form)
    except (services.MachineError, ValueError) as e:
        db.session.rollback()
        flash(str(e), "error")
        return redirect(url_for("main.passport_review", document_id=document.id))
    db.session.commit()
    flash(f"{count} value(s) confirmed from the passport.")
    return redirect(url_for("main.machine"))


@bp.route("/machine/passport/<int:document_id>/file")
def passport_file(document_id):
    document = db.get_or_404(MachineDocument, document_id)
    return send_from_directory(services.machine_docs_dir(current_app.instance_path), document.stored_filename)


@bp.route("/cutting-data")
def cutting_data():
    """The catalogue cutting data: confirmed rows (the planner uses these), rows to check, rejected ones."""
    rows = db.session.execute(db.select(CuttingDataRow).order_by(
        CuttingDataRow.kind, CuttingDataRow.material_group, CuttingDataRow.insert_code, CuttingDataRow.grade,
        CuttingDataRow.id)).scalars().all()
    by_status = {status: [r for r in rows if r.status == status] for status in ("confirmed", "read", "rejected", "replaced")}
    return render_template("cutting_data.html", by_status=by_status, cd=cd,
                           hand_typed=services.hand_typed_files(_docs_dir()),
                           applications=sorted(set(cd.APPLICATION_BY_TOOL_TYPE.values())))


def _rows_to_check(document_id=None):
    query = db.select(CuttingDataRow).filter_by(status="read")
    if document_id:
        query = query.filter_by(document_id=document_id)
    return db.session.execute(query.order_by(CuttingDataRow.kind, CuttingDataRow.insert_code, CuttingDataRow.grade,
                                             CuttingDataRow.id)).scalars().all()


@bp.route("/cutting-data/review", methods=["GET", "POST"])
def cutting_data_review():
    """The operator checks each row against the catalogue page: confirm (as read or changed), reject, or leave."""
    document_id = request.args.get("document", type=int)
    rows = _rows_to_check(document_id)
    if request.method == "POST":
        try:
            confirmed, rejected = services.review_cutting_data(rows, request.form)
        except services.CuttingDataError as e:
            db.session.rollback()
            flash(f"{e}. Nothing was saved.", "error")
            return redirect(url_for("main.cutting_data_review", document=document_id))
        db.session.commit()
        flash(f"{confirmed} row(s) confirmed, {rejected} rejected.")
        return redirect(url_for("main.cutting_data_review", document=document_id) if _rows_to_check(document_id)
                        else url_for("main.cutting_data"))
    confirmed = db.session.execute(db.select(CuttingDataRow).filter_by(status="confirmed")).scalars().all()
    items = [dict(row=r, checks=cd.row_checks(r), others=services.conflicting_rows(r, confirmed)) for r in rows]
    return render_template("cutting_data_review.html", items=items, cd=cd, document_id=document_id)


@bp.route("/cutting-data/add", methods=["POST"])
def add_cutting_data():
    """A row entered by the operator (e.g. a value read off a graph), with its catalogue and page."""
    try:
        row = services.add_cutting_data_by_hand(request.form)
    except services.CuttingDataError as e:
        db.session.rollback()
        flash(f"{e}. Nothing was saved.", "error")
        return redirect(url_for("main.cutting_data"))
    db.session.commit()
    flash(f"Row {row.insert_code or row.grade} ({row.material_group}) entered and confirmed.")
    return redirect(url_for("main.cutting_data"))


def _docs_dir():
    return os.path.join(os.path.dirname(current_app.root_path), "docs")


@bp.route("/cutting-data/import", methods=["POST"])
def import_cutting_data():
    """Rows of a hand-typed catalogue file (docs/) as rows to check: nothing is used before it is confirmed."""
    try:
        added, already = services.import_hand_typed(_docs_dir(), request.form.get("filename", ""))
    except (services.UploadError, ValueError) as e:
        flash(str(e), "error")
    else:
        flash(f"{added} row(s) to check added; {already} already there.")
    return redirect(url_for("main.cutting_data"))


MAX_CATALOGUE_PAGES = 20  # pages sent to the model in one reading (cost)


@bp.route("/cutting-data/catalogues", methods=["GET", "POST"])
def catalogues():
    if request.method == "POST":
        request.max_content_length = current_app.config["MAX_CATALOGUE_LENGTH"]  # before the form is read
        upload = request.files.get("catalogue")
        if upload is None or not upload.filename:
            flash("Choose the catalogue (PDF).", "error")
            return redirect(url_for("main.catalogues"))
        try:
            document = services.store_catalogue(upload, request.form.get("title"), current_app.instance_path)
        except services.UploadError as e:
            flash(str(e), "error")
            return redirect(url_for("main.catalogues"))
        return redirect(url_for("main.catalogue_pages", document_id=document.id))
    documents = db.session.execute(
        db.select(CatalogueDocument).order_by(CatalogueDocument.created_at.desc())
    ).scalars().all()
    limit_mb = current_app.config["MAX_CATALOGUE_LENGTH"] // (1024 * 1024)
    return render_template("catalogues.html", documents=documents, limit_mb=limit_mb)


def _default_targets():
    """The material groups of the materials and the insert codes / grades of the library: what to record."""
    groups = sorted({m.catalogue_group for m in db.session.execute(db.select(Material)).scalars() if m.catalogue_group})
    tools = db.session.execute(db.select(Tool).filter_by(is_retired=False).order_by(Tool.name)).scalars()
    codes = list(dict.fromkeys(c for t in tools for c in (t.insert_code, t.grade) if c))
    return ", ".join(groups), "\n".join(codes)


@bp.route("/cutting-data/catalogues/<int:document_id>", methods=["GET", "POST"])
def catalogue_pages(document_id):
    """Find the cutting data pages (search, "likely" marks) and pick the ones to read, with what to record."""
    document = db.get_or_404(CatalogueDocument, document_id)
    pages = services.catalogue_pages(document, current_app.instance_path)
    if request.method == "POST":
        try:
            picked = catalogue_reader.resolve_pages(request.form.get("pages", ""), pages)
        except ValueError as e:
            flash(str(e), "error")
            return redirect(url_for("main.catalogue_pages", document_id=document.id))
        groups = ", ".join(g.strip() for g in request.form.get("material_groups", "").split(",") if g.strip())
        if not picked:
            flash("Pick the pages with the cutting data.", "error")
        elif len(picked) > MAX_CATALOGUE_PAGES:
            flash(f"At most {MAX_CATALOGUE_PAGES} pages in one reading.", "error")
        elif not groups:
            flash("Write the material groups to record (e.g. P1.2).", "error")
        else:
            document.selected_pages = ", ".join(str(p) for p in picked)
            document.material_groups = groups[:100]
            document.codes = request.form.get("codes", "").strip() or None
            db.session.commit()
            flash(f"Pages {document.selected_pages} picked.")
        return redirect(url_for("main.catalogue_pages", document_id=document.id))
    query = request.args.get("q", "").strip()
    found = catalogue_reader.search(pages, query) if query else []
    if not query:  # without a search: the pages that look like cutting data
        found = [i for i, t in enumerate(pages["texts"], start=1) if catalogue_reader.likely_cutting_data_page(t)][:50]

    def page_row(number):
        text = pages["texts"][number - 1]
        return dict(number=number, label=pages["labels"][number - 1], has_text=catalogue_reader.page_has_text(pages, number),
                    likely=catalogue_reader.likely_cutting_data_page(text), text=catalogue_reader.snippet(text, query))

    default_groups, default_codes = _default_targets()
    estimate = None
    if document.selected and document.material_groups:
        estimate = catalogue_reader.estimate(services.catalogue_path(document, current_app.instance_path),
                                             document.selected, document.material_groups, document.codes)
    return render_template(
        "catalogue_pages.html", document=document, query=query, found=[page_row(n) for n in found],
        estimate=estimate, model=current_app.config["ANTHROPIC_MODEL"],
        not_taken=json.loads(document.not_taken) if document.not_taken else [],
        picked=[page_row(n) for n in document.selected], max_pages=MAX_CATALOGUE_PAGES,
        groups=document.material_groups or default_groups, codes=document.codes if document.codes is not None else default_codes,
    )


@bp.route("/cutting-data/catalogues/<int:document_id>/read", methods=["POST"])
def read_catalogue(document_id):
    """The paid call: only on the operator's explicit request, for the picked pages."""
    document = db.get_or_404(CatalogueDocument, document_id)
    if not request.form.get("paid"):
        flash("Tick that you start a paid reading (one API call).", "error")
        return redirect(url_for("main.catalogue_pages", document_id=document.id))
    try:
        services.read_catalogue_document(document, current_app.instance_path, current_app.config,
                                         client=current_app.config.get("ANTHROPIC_CLIENT"))
    except services.UploadError as e:
        flash(str(e), "error")
        return redirect(url_for("main.catalogue_pages", document_id=document.id))
    if document.status != "read":
        flash(f"The catalogue could not be read: {document.error}", "error")
        return redirect(url_for("main.catalogue_pages", document_id=document.id))
    return redirect(url_for("main.cutting_data_review", document=document.id))


@bp.route("/cutting-data/catalogues/<int:document_id>/file")
def catalogue_file(document_id):
    document = db.get_or_404(CatalogueDocument, document_id)
    return send_from_directory(services.catalogues_dir(current_app.instance_path), document.stored_filename)


def _tip_direction(form):
    value = _number(form, "tip_direction", int, positive=False)
    if value is not None and not 0 <= value <= 9:
        raise FormError("Tip direction T: 0 to 9, as on the control's offset page")
    return value


def _holder_code(form):
    code = form.get("holder_code", "").strip()[:50] or None
    problem = planner.seat_mismatch(form.get("insert_code", "").strip(), code)
    if problem:
        raise FormError(f"Holder: {problem}")
    return code


def _ramp_angle(form):
    angle = _number(form, "max_ramp_angle")
    if angle is not None and angle >= 90:
        raise FormError("RMPX (max in-copying angle): degrees to the axis, below 90")
    return angle


def _tool_fields(form):
    """The editable fields of a tool from its form (not its type) or raise FormError."""
    name = form.get("name", "").strip()
    iso_group = "".join(g for g in ISO_GROUPS if g in form.getlist("iso_group"))
    if not name:
        raise FormError("Tool name is required")
    if not iso_group:
        raise FormError("Select at least one ISO group")
    values = {k: _number(form, k, required=True) for k in ("vc_min", "vc_max", "f_min", "f_max", "ap_min", "ap_max")}
    for lo, hi in (("vc_min", "vc_max"), ("f_min", "f_max"), ("ap_min", "ap_max")):
        if values[lo] > values[hi]:
            raise FormError(f"{lo} must not exceed {hi}")
    recommended = {k: _number(form, k) for k in ("ap_rec", "f_rec")}
    for key, (lo, hi) in (("ap_rec", ("ap_min", "ap_max")), ("f_rec", ("f_min", "f_max"))):
        if recommended[key] is not None and not values[lo] <= recommended[key] <= values[hi]:
            raise FormError(f"{key} must be within {lo}..{hi}")
    try:
        points = planner.parse_vc_points(form.get("vc_points"))
    except ValueError as e:
        raise FormError(f"Vc(f) points: {e} (write e.g. 0.1:455, 0.4:305, 0.8:215, or one Vc)") from None
    return dict(
        **recommended,
        vc_points=planner.format_vc_points(points) or None,
        name=name,
        insert_code=form.get("insert_code", "").strip(),
        grade=form.get("grade", "").strip(),
        iso_group=iso_group,
        insert_width=_number(form, "insert_width"),
        diameter=_number(form, "diameter"),
        max_depth=_number(form, "max_depth"),
        holder_code=_holder_code(form),
        max_ramp_angle=_ramp_angle(form),
        max_ramp_source=form.get("max_ramp_source", "").strip()[:200] or None,
        nose_radius=_number(form, "nose_radius"),
        tip_direction=_tip_direction(form),
        source=form.get("source", "").strip()[:300] or None,
        **values,
    )


@bp.route("/tools", methods=["POST"])
def add_tool():
    form = request.form
    try:
        tool_type = form.get("type")
        if tool_type not in TOOL_TYPES:
            raise FormError("Unknown tool type")
        fields = _tool_fields(form)
        db.session.add(Tool(type=tool_type, **fields))
        db.session.commit()
        flash(f"Tool '{fields['name']}' added to the library. Assign it to a turret position.")
    except FormError as e:
        flash(str(e), "error")
    return redirect(url_for("main.machine"))


def _active_tool_or_404(tool_id):
    tool = db.get_or_404(Tool, tool_id)
    if tool.is_retired:
        abort(404)
    return tool


@bp.route("/tools/<int:tool_id>/edit", methods=["GET", "POST"])
def edit_tool(tool_id):
    """Everything but the type: a new type would change what the operations already made with it mean."""
    tool = _active_tool_or_404(tool_id)
    if request.method == "POST":
        try:
            for key, value in _tool_fields(request.form).items():
                setattr(tool, key, value)
            db.session.commit()
            flash(f"Tool '{tool.name}' saved. Jobs calculated with it before show a note until recalculated.")
            return redirect(url_for("main.machine"))
        except FormError as e:
            db.session.rollback()
            flash(str(e), "error")
    return render_template("tool_edit.html", tool=tool, iso_groups=ISO_GROUPS,
                           rmpx=planner.rmpx_suggestions(tool.insert_code, tool.holder_code))


@bp.route("/tools/<int:tool_id>/rmpx", methods=["POST"])
def take_rmpx(tool_id):
    """The operator takes the catalogue's RMPX for the holder style they confirm (planner.RMPX_TABLE)."""
    tool = _active_tool_or_404(tool_id)
    style = request.form.get("holder_style", "")
    chosen = next((s for s in planner.rmpx_suggestions(tool.insert_code, tool.holder_code) if s[0] == style), None)
    if chosen is None:
        flash("No catalogue RMPX for this insert and holder.", "error")
        return redirect(url_for("main.edit_tool", tool_id=tool.id))
    tool.max_ramp_angle = chosen[1]
    tool.max_ramp_source = f"{chosen[2]}, holder {style} (confirmed by the operator)"
    db.session.commit()
    flash(f"RMPX {chosen[1]:g}° set for '{tool.name}' with the holder {style}.")
    return redirect(url_for("main.edit_tool", tool_id=tool.id))


@bp.route("/tools/<int:tool_id>/delete", methods=["POST"])
def delete_tool(tool_id):
    """Out of the turret first. A tool no operation used is deleted; one that was used is retired (its
    operations keep it for the audit)."""
    tool = _active_tool_or_404(tool_id)
    slots = db.session.execute(db.select(TurretSlot).filter_by(tool_id=tool.id)).scalars().all()
    if slots:
        positions = ", ".join(slot.label for slot in slots)
        flash(f"Tool '{tool.name}' is in the turret at {positions}: remove it from there first.", "error")
        return redirect(url_for("main.machine"))
    used = db.session.execute(db.select(db.func.count(Operation.id)).filter_by(tool_id=tool.id)).scalar()
    if used:
        tool.is_retired = True
        flash(f"Tool '{tool.name}' is used by {used} operation(s): retired (kept for their record).")
    else:
        db.session.delete(tool)
        flash(f"Tool '{tool.name}' deleted.")
    db.session.commit()
    return redirect(url_for("main.machine"))


def _job_from_form(form):
    """Build an unsaved Job from form fields or raise FormError."""
    name = form.get("name", "").strip()
    if not name:
        raise FormError("Job name is required")
    material = db.session.get(Material, _number(form, "material_id", int, required=True))
    if material is None:
        raise FormError("Unknown material")
    blank_shape = form.get("blank_shape") or "round"
    if blank_shape not in BLANK_SHAPES:
        raise FormError("Blank shape must be round or hex")
    job = Job(
        name=name,
        material=material,
        quantity=_number(form, "quantity", int) or 1,
        blank_diameter=_number(form, "blank_diameter", required=True),
        blank_length=_number(form, "blank_length", required=True),
        blank_shape=blank_shape,
    )
    machine = services.get_machine()
    if machine and _stock_diameter(job) > machine.max_diameter:
        raise FormError(f"Blank diameter exceeds machine max diameter ({machine.max_diameter:g} mm)")
    return job


def _stock_diameter(job):
    """The largest diameter of the blank: a hex bar's diameter across corners."""
    return planner.stock_diameter(job.blank_shape or "round", job.blank_diameter)


# "Where" of a feature: chamfers "external right" etc., threads "external" / "internal".
POSITIONS = ("external", "internal", "external left", "external right", "internal left", "internal right")


def _position(form, prefix, feature_type):
    """location / face from the combined "position" field; chamfers, threads and fillets (their face) have one."""
    value = (form.get(prefix + "position") or "").strip()
    if feature_type not in ("chamfer", "thread", "fillet") or not value:
        return {"location": None, "face": None}
    if value not in POSITIONS:
        raise FormError("Unknown position")
    location, *face = value.split()
    if feature_type == "fillet":
        return {"location": None, "face": face[0] if face else None}
    return {"location": location, "face": face[0] if face and feature_type == "chamfer" else None}


ARC_SHAPES = {"convex": True, "concave": False}


def _arc_convex(form, prefix, feature_type):
    """Arcs and fillets: convex (away from the axis) / concave; empty: not known (the arc is not programmed)."""
    value = (form.get(prefix + "arc_shape") or "").strip()
    if feature_type not in RADIUS_TYPES or not value:
        return None
    if value not in ARC_SHAPES:
        raise FormError("Arc shape: convex or concave")
    return ARC_SHAPES[value]


def _roughness_param(form, prefix=""):
    value = form.get(prefix + "ra_param") or "Ra"
    if value not in ("Ra", "Rz"):
        raise FormError("Roughness parameter must be Ra or Rz")
    return value


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
    across_flats = _number(form, prefix + "across_flats") if feature_type == "hex" else None
    if feature_type == "hex":
        # a hex needs one of its sizes; the other one is computed (D = S / cos 30°)
        if diameter is None and across_flats is None:
            raise FormError("Hex needs its size across flats S or its diameter across corners")
        if across_flats is None:
            across_flats = hex_across_flats(diameter)
        if diameter is None:
            diameter = hex_across_corners(across_flats)
        # D need not be S / cos 30° (it may be the diameter turned before milling), but not below S
        if diameter < across_flats:
            raise FormError("Hex diameter must not be smaller than its size across flats")
    start_diameter = _number(form, prefix + "start_diameter") if feature_type in START_DIAMETER_TYPES else None
    if start_diameter is not None:
        if start_diameter > blank_diameter:
            raise FormError("Start diameter is larger than the blank diameter")
        if feature_type == "groove" and diameter is not None and start_diameter <= diameter:
            raise FormError("Groove start diameter must be larger than the groove bottom diameter")
    return Feature(
        type=feature_type,
        diameter=diameter,
        length=_number(form, prefix + "length"),
        tolerance=normalize_tolerance(form.get(prefix + "tolerance")),
        ra=_number(form, prefix + "ra"),
        ra_param=_roughness_param(form, prefix),
        **_position(form, prefix, feature_type),
        pitch=pitch if feature_type == "thread" else None,
        start_diameter=start_diameter,
        radius=_number(form, prefix + "radius") if feature_type in RADIUS_TYPES else None,
        across_flats=across_flats,
        arc_convex=_arc_convex(form, prefix, feature_type),
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
    return render_template("jobs.html", jobs=all_jobs, materials=materials, blank_shapes=BLANK_SHAPES)


@bp.route("/jobs/<int:job_id>")
def job_detail(job_id):
    job = db.get_or_404(Job, job_id)
    return render_template("job_detail.html", job=job, feature_types=FEATURE_TYPES, positions=POSITIONS,
                           machine_warnings=services.machine_warnings(job, services.get_machine()),
                           suggested_free_end=services.suggest_free_end(job))


@bp.route("/jobs/<int:job_id>/gcode", methods=["GET", "POST"])
def job_gcode(job_id):
    """What a program needs, the values it would use with their sources, and the programs made so far."""
    job = db.get_or_404(Job, job_id)
    machine = _machine_or_404()
    if request.method == "POST":
        readiness, record = services.generate_gcode(job, machine)
        if record is None:
            flash("No program: " + "; ".join(readiness.blockers), "error")
            return redirect(url_for("main.job_gcode", job_id=job.id))
        return redirect(url_for("main.gcode_program", program_id=record.id))
    readiness = services.gcode_readiness(job, machine)
    settings = services.gcode_settings(job, machine)
    ops = {op.id: op for op in job.current_operations}
    programs = db.session.execute(db.select(GcodeProgram).filter_by(job_id=job.id)
                                  .order_by(GcodeProgram.id.desc())).scalars().all()
    return render_template("job_gcode.html", job=job, readiness=readiness, settings=settings, ops=ops,
                           demo=services.demo_settings(settings), programs=programs)


@bp.route("/gcode/<int:program_id>")
def gcode_program(program_id):
    from .gcode import svg

    record = db.get_or_404(GcodeProgram, program_id)
    machine = _machine_or_404()
    job = record.job
    sim = json.loads(record.simulation)
    settings = json.loads(record.settings)
    stale = services.gcode_stale(record, machine)
    _, _, profile, _ = services.gcode_inputs(job, machine)
    tools = {s.position: s.tool.name for s in machine.slots if s.tool}
    setup = {row["name"]: row["value"] for row in settings}
    drawing = svg.render(
        sim["segments"], sim["final_stock"], sim["z_top"], sim["dz"], profile.final_points,
        planner.stock_diameter(job.blank_shape or "round", job.blank_diameter) / 2,
        float(setup.get("stickout_mm") or 0), float(setup.get("chuck_safety_mm") or 0),
        [line for line, _ in sim["errors"]], tools)
    lines = record.text.splitlines()
    flagged = {}
    for line, message in sim["errors"]:
        flagged.setdefault(line, []).append(("error", message))
    for line, message in sim["warnings"]:
        flagged.setdefault(line, []).append(("warning", message))
    return render_template(
        "gcode_program.html", record=record, job=job, sim=sim, settings=settings, stale=stale, drawing=drawing,
        lines=lines, flagged=flagged, demo=services.demo_settings(settings), checklist=services.gcode_checklist(record),
        ticked=json.loads(record.checklist) if record.checklist else {})


@bp.route("/gcode/<int:program_id>/ready", methods=["POST"])
def gcode_ready(program_id):
    record = db.get_or_404(GcodeProgram, program_id)
    try:
        services.mark_gcode_ready(record, _machine_or_404(), request.form)
    except services.GcodeError as e:
        flash(f"Not ready to run: {e}.", "error")
    else:
        flash(f"Ready to run, confirmed by {record.confirmed_by}. The program is not sent anywhere: download it, and "
              "the operator presses Start after the machine check.")
    return redirect(url_for("main.gcode_program", program_id=record.id))


@bp.route("/gcode/<int:program_id>/download")
def gcode_download(program_id):
    record = db.get_or_404(GcodeProgram, program_id)
    if record.status != "ready" or services.gcode_stale(record, _machine_or_404()):
        abort(403, "Only a program marked ready to run, still matching the job and the machine, is downloaded.")
    name = f"O{record.job_id % 10000 or 1:04d}_job{record.job_id}_v{record.id}.nc"
    return current_app.response_class(record.text, mimetype="text/plain",
                                      headers={"Content-Disposition": f'attachment; filename="{name}"'})


@bp.route("/jobs/<int:job_id>/gcode-setup", methods=["POST"])
def gcode_setup(job_id):
    """The operator's set-up for a program: the free end (Z0), the stick-out, the stock beyond Z0."""
    job = db.get_or_404(Job, job_id)
    try:
        free_end = request.form.get("free_end") or None
        if free_end not in (None, "left", "right"):
            raise FormError("Free end: left or right")
        stickout, face_stock = _number(request.form, "stickout_mm"), _number(request.form, "face_stock_mm")
    except FormError as e:
        flash(str(e), "error")
        return redirect(url_for("main.job_detail", job_id=job.id))
    job.free_end, job.stickout_mm, job.face_stock_mm = free_end, stickout, face_stock
    job.gcode_setup_demo = False  # saved by the operator
    db.session.commit()
    flash("G-code set-up saved.")
    return redirect(url_for("main.job_detail", job_id=job.id))


@bp.route("/jobs/<int:job_id>/features", methods=["POST"])
def add_feature(job_id):
    job = db.get_or_404(Job, job_id)
    try:
        job.features.append(_feature_from_form(request.form, _stock_diameter(job)))
        job.axial_order_known = False  # the new feature is at the end of the list, not in its place on the axis
        db.session.commit()
    except FormError as e:
        flash(str(e), "error")
    return redirect(url_for("main.job_detail", job_id=job.id))


@bp.route("/jobs/<int:job_id>/features/<int:feature_id>/radius", methods=["POST"])
def choose_arc_radius(job_id, feature_id):
    """An arc whose drawn radius differs from its dimension: the operator chooses the one to program."""
    feature = db.get_or_404(Feature, feature_id)
    if feature.job_id != job_id or feature.type != "arc" or feature.drawn_radius is None:
        abort(404)
    choice = request.form.get("radius")
    if choice == "drawn":
        feature.radius = feature.drawn_radius
    elif choice != "dimension":
        abort(400)
    feature.drawn_radius = feature.radius  # resolved: they agree now
    db.session.commit()
    flash(f"Arc radius R{feature.radius:g} chosen. Calculate the job again.")
    return redirect(url_for("main.job_detail", job_id=job_id))


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
    machine = _machine_or_404()
    return render_template("operations.html", job=job, machine=machine,
                           machine_warnings=services.machine_warnings(job, machine))


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

REVIEW_FIELDS = (
    "type", "diameter", "start_diameter", "length", "tolerance", "ra", "ra_param", "pitch", "radius", "across_flats",
    "confidence",
)
# In the features mode the model reports a length it computed as "Ø60: length derived from chain dimensions".
DERIVED_LENGTH_WARNING = re.compile(r"Ø\s*(\d+(?:[.,]\d+)?)\s*:\s*length derived", re.IGNORECASE)
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
        row.get("tolerance") or None, _to_float(row.get("diameter")), _ra_value(row)
    )
    return row


def _ra_value(row):
    """Ra of a review row for checks; an Rz is converted (Ra ≈ Rz/4)."""
    value = _to_float(row.get("ra"))
    if value is not None and row.get("ra_param") == "Rz":
        return planner.rz_to_ra(value)
    return value


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


def _flag_derived_lengths(rows, data):
    """Mark lengths that are not dimensioned directly but computed (e.g. a baseline minus a groove)."""
    reported = {
        float(m.group(1).replace(",", "."))
        for w in data.warnings for m in [DERIVED_LENGTH_WARNING.search(w)] if m
    }
    for row, feature in zip(rows, data.features):
        from_model = feature.type == "od_turn" and feature.diameter is not None and any(
            abs(feature.diameter - d) < 1e-6 for d in reported
        )
        row["length_derived"] = feature.length is not None and (feature.length_derived or from_model)
        row["length_ambiguous"] = feature.length is not None and feature.length_ambiguous
        row["pitch_assumed"] = feature.pitch_assumed
        row["size_derived"] = feature.size_derived
        row["probably_s"] = feature.type == "hex" and planner.hex_probably_s(feature.diameter, feature.across_flats)
        row["position"] = " ".join(p for p in (feature.location, feature.face) if p) or None
        row["arc_shape"] = None
    return rows


def _apply_general_ra(rows, general_ra, general_ra_param="Ra"):
    """The general roughness (corner symbol) applies to every feature without its own mark."""
    for row in rows:
        row["ra_general"] = general_ra is not None and row.get("ra") is None
        if row["ra_general"]:
            row["ra"] = general_ra
            row["ra_param"] = general_ra_param
            row["grinding"] = planner.needs_grinding(
                row.get("tolerance") or None, _to_float(row.get("diameter")), _ra_value(row)
            )
    return rows


def _apply_general_tolerance(rows, note):
    """The general tolerance note applies to every size without its own: holes H.., shafts h.., rest ±IT../2."""
    grade = planner.general_tolerance_grade(note)
    for row in rows:
        row["tolerance_general"] = False
        if grade is None or row.get("tolerance"):
            continue
        tolerance = planner.general_tolerance_for(row["type"], grade)
        if tolerance:
            row["tolerance"] = tolerance
            row["tolerance_general"] = True
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
    _flag_derived_lengths(rows, data)
    for i, shape in services.dxf_arc_shapes(extraction, current_app.instance_path).items():
        rows[i]["arc_shape"] = shape["arc_shape"]  # from the file's geometry
        if shape.get("face"):
            rows[i]["position"] = f"external {shape['face']}"
    _flag_bore_ra(rows)  # on the marks read from the drawing, before the general Ra fills the gaps
    _apply_general_ra(rows, data.general_ra, data.general_ra_param)
    _apply_general_tolerance(rows, data.general_tolerance)
    # A blank written in the title block (e.g. "Круг 29") beats a suggestion; the drawing's own
    # blank_diameter beats both.
    title_block_diameter = None if data.blank_diameter else services.blank_from_title_block(data.material)
    blank_missing = data.blank_diameter is None or data.blank_length is None
    name = extraction.original_filename.rsplit(".", 1)[0]
    report = services.dxf_report(extraction)
    if report and report["parts"] > 1:
        name += f" (part {report['part']})"
    return {
        "name": name,
        "material_id": material.id if material else None,
        "quantity": data.quantity or 1,
        # A blank written on the drawing wins; otherwise the suggestion, flagged as such.
        "blank_diameter": data.blank_diameter or title_block_diameter or suggestion.diameter,
        # a hex bar only when the suggestion is used; a blank on the drawing is taken as round bar
        "blank_shape": suggestion.shape if not (data.blank_diameter or title_block_diameter) else "round",
        "blank_length": data.blank_length or suggestion.length,
        "blank_diameter_from_title_block": title_block_diameter is not None,
        "blank_diameter_suggested": (
            data.blank_diameter is None and title_block_diameter is None and suggestion.diameter is not None
        ),
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
        row["position"] = form.get(f"f{i}-position") or None
        row["arc_shape"] = form.get(f"f{i}-arc_shape") or None
        row["probably_s"] = row["type"] == "hex" and planner.hex_probably_s(
            _to_float(row.get("diameter")), _to_float(row.get("across_flats"))
        )
        rows.append(_decorate_row(row, threshold))
    _flag_bore_ra(rows)
    material_id = _to_float(form.get("material_id"))
    return {
        "name": form.get("name", ""),
        "material_id": int(material_id) if material_id else None,
        "quantity": form.get("quantity"),
        "blank_diameter": form.get("blank_diameter"),
        "blank_shape": form.get("blank_shape") or "round",
        "blank_length": form.get("blank_length"),
        "blank_diameter_suggested": form.get("blank_diameter_suggested") == "1",
        "blank_length_suggested": form.get("blank_length_suggested") == "1",
        "blank_notes": (),
        "add_face": bool(form.get("add_face")),
        "add_parting": bool(form.get("add_parting")),
        "override_part_type": bool(form.get("override_part_type")),
        "rows": rows,
    }


def _geometry_warnings(rows, overall_length, drawing_numbers=None, general_ra=None):
    """Checks on the rows as they stand on the review screen (edited values, included rows only).

    With the numbers of a CAD PDF, the rows' numbers are also checked against them (number_check);
    a length computed from two written ones gets row["length_computed"] = "44 − 14" (a check badge).
    """
    included = [r for r in rows if r["include"]]
    features = [
        SimpleNamespace(type=r["type"], diameter=_to_float(r.get("diameter")), length=_to_float(r.get("length")),
                        radius=_to_float(r.get("radius")), across_flats=_to_float(r.get("across_flats")),
                        start_diameter=_to_float(r.get("start_diameter")), pitch=_to_float(r.get("pitch")),
                        ra=_to_float(r.get("ra")), ra_general=bool(r.get("ra_general")),
                        length_derived=bool(r.get("length_derived")), size_derived=r.get("size_derived"),
                        pitch_assumed=bool(r.get("pitch_assumed")))
        for r in included
    ]
    warnings = planner.geometry_warnings(features, overall_length)
    numbers = number_check.check_numbers(features, overall_length, general_ra, drawing_numbers)
    for row in rows:
        row["length_computed"] = None
    for index, formula in numbers.computed.items():
        included[index]["length_computed"] = formula
    return warnings + numbers.warnings


def _attach_dxf_rows(rows, report):
    """The section, dimensions and notes of each DXF row (by position, as long as the rows are the read ones)."""
    if not report or len(report["rows"]) != len(rows):
        return
    for row, binding in zip(rows, report["rows"]):
        row["dxf_section"] = binding["section"]
        row["dxf_dims"] = binding["dims"]
        row["dxf_notes"] = binding["notes"]


def _render_review(extraction, values, status=200):
    materials = db.session.execute(db.select(Material).order_by(Material.name)).scalars().all()
    data = services.extraction_data(extraction)
    report = services.dxf_report(extraction)
    _attach_dxf_rows(values["rows"], report)
    page = render_template(
        "extraction_review.html",
        extraction=extraction,
        data=data,
        geometry_warnings=_geometry_warnings(
            values["rows"], data.overall_length if data else None,
            services.drawing_numbers(extraction, current_app.instance_path), data.general_ra if data else None,
        ),
        values=values,
        dxf_report=report,
        dxf_parts=[(p, services.dxf_report(p)) for p in services.dxf_parts(extraction)] if extraction.is_dxf else [],
        materials=materials,
        feature_types=FEATURE_TYPES,
        positions=POSITIONS,
        blank_shapes=BLANK_SHAPES,
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
        shapes = services.dxf_arc_shapes(extraction, current_app.instance_path)
        for i in range(_number(form, "feature_count", int, positive=False) or 0):
            if not form.get(f"f{i}-include"):
                continue
            try:
                feature = _feature_from_form(form, _stock_diameter(job), prefix=f"f{i}-")
                feature.confidence = _number(form, f"f{i}-confidence", positive=False)
            except FormError as e:
                raise FormError(f"Feature {i + 1}: {e}") from None
            if feature.type == "arc" and i in shapes:
                feature.drawn_radius = shapes[i].get("drawn_radius")  # compared with the dimension (the profile)
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
    # a DXF gives its rows left to right along the axis; a model's reading has no such guarantee
    job.axial_order_known = extraction.is_dxf
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
