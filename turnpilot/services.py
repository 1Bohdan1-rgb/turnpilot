"""Glue between the database models, the pure planner and the drawing reader."""

import hashlib
import json
import os
import re
import uuid
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from . import catalogue_file, catalogue_reader, cutting_data, drawing_reader, dxf_input, dxf_reader, machine_spec, passport_reader, pdf_text, planner
from .extraction_schema import DrawingData, normalize_tolerance
from .models import (
    CatalogueDocument, CuttingDataRow, DrawingExtraction, Edit, Machine, MachineDocument, MachineSpecSource, Operation, TurretSlot, db,
)

# Extra names a material may appear under on a drawing (compared after normalization).
MATERIAL_ALIASES = {
    "Steel 45 (C45)": ("steel 45", "c45", "c45e", "1045", "ст45", "сталь 45"),
    "AISI 304": ("aisi 304", "304", "1.4301", "x5crni18-10", "08х18н10"),
    "Aluminium 6061": ("aluminium 6061", "aluminum 6061", "6061", "en aw-6061", "almg1sicu"),
}

FILE_TYPE_BY_EXTENSION = {"png": "png", "jpg": "jpeg", "jpeg": "jpeg", "pdf": "pdf", "dxf": "dxf"}

# The code that reads a DXF (reader + its mapping to rows): recorded as the "prompt version" of a DXF reading.
DXF_SOURCES = (dxf_reader.__file__, dxf_input.__file__)

# A second upload of a file that is still being read within this time is rejected (double submit).
IN_FLIGHT_SECONDS = 120


class UploadError(ValueError):
    pass


def get_machine():
    return db.session.execute(db.select(Machine).order_by(Machine.id)).scalars().first()


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
    )


def turret_entries(machine, material=None):
    """The turret as the planner sees it; with a material, its cutting data from the confirmed catalogue rows."""
    rows = confirmed_rows() if material is not None else ()
    return [planner.TurretEntry(s.position, tool_spec(s.tool, material, rows))
            for s in machine.slots if s.tool is not None]


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
        kc_source=job.material.kc_source,
    )


def calculate_operations(job, machine):
    """Archive the current operations of a job and add a fresh proposal as a new version.

    Nothing is deleted: archived operations keep their status and edit log.
    """
    version = job.last_calculation_version + 1
    for op in job.current_operations:
        op.is_archived = True
    power = planner.PowerSpec(machine.power_kw, machine.drive_efficiency)
    limits = planner.MachineLimits(machine.min_rpm, machine.coolant, machine.live_tooling, machine.c_axis)
    for planned in planner.plan_job(job_spec(job), turret_entries(machine, job.material), machine.max_rpm,
                                    machine.max_thread_feed,
                                    power, limits):
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
    """{tool id: [(group, has ap / f row, has Vc row)]} for the tool library: which tools have confirmed rows."""
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
        results = dxf_input.part_results(dxf_reader.read_dxf(path))
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


def dxf_report(extraction):
    return json.loads(extraction.binding) if extraction.binding else None


def read_again(extraction, instance_path, config, client=None):
    """New API call for a drawing that was already uploaded (the user asked for it explicitly)."""
    with open(os.path.join(drawings_dir(instance_path), extraction.stored_filename), "rb") as f:
        data = f.read()
    return read_drawing(extraction.original_filename, data, instance_path, config, client=client, force=True)


def extraction_data(extraction):
    return DrawingData.model_validate_json(extraction.parsed) if extraction.parsed else None
