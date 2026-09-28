"""Glue between the database models, the pure planner and the drawing reader."""

import hashlib
import json
import os
import re
import uuid
from datetime import datetime, timedelta, timezone

from . import drawing_reader, planner
from .extraction_schema import DrawingData, normalize_tolerance
from .models import DrawingExtraction, Edit, Machine, Operation, TurretSlot, db

# Extra names a material may appear under on a drawing (compared after normalization).
MATERIAL_ALIASES = {
    "Steel 45 (C45)": ("steel 45", "c45", "c45e", "1045", "ст45", "сталь 45"),
    "AISI 304": ("aisi 304", "304", "1.4301", "x5crni18-10", "08х18н10"),
    "Aluminium 6061": ("aluminium 6061", "aluminum 6061", "6061", "en aw-6061", "almg1sicu"),
}

FILE_TYPE_BY_EXTENSION = {"png": "png", "jpg": "jpeg", "jpeg": "jpeg", "pdf": "pdf"}

# A second upload of a file that is still being read within this time is rejected (double submit).
IN_FLIGHT_SECONDS = 120


class UploadError(ValueError):
    pass


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
            location=f.location if f.type == "chamfer" else None,
            # normalized again for features saved before decimal commas were converted
            start_diameter=f.start_diameter, tolerance=normalize_tolerance(f.tolerance), radius=f.radius,
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


def parting_width(machine, default):
    """Insert width of the first parting tool in the turret, or the configured default."""
    for slot in machine.slots if machine else []:
        if slot.tool and slot.tool.type == "parting" and slot.tool.insert_width:
            return slot.tool.insert_width
    return default


def suggest_blank(data: DrawingData, machine, config):
    specs = [
        planner.FeatureSpec(id=None, type=f.type, diameter=f.diameter, length=f.length,
                            start_diameter=f.start_diameter)
        for f in data.features
    ]
    return planner.suggest_blank(
        specs,
        overall_length=data.overall_length,
        bar_diameters=tuple(config["BAR_STOCK_DIAMETERS"]),
        diameter_allowance=config["BLANK_DIAMETER_ALLOWANCE_MM"],
        facing_allowance=config["BLANK_FACING_ALLOWANCE_MM"],
        parting_width=parting_width(machine, config["DEFAULT_PARTING_WIDTH_MM"]),
    )


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


def read_again(extraction, instance_path, config, client=None):
    """New API call for a drawing that was already uploaded (the user asked for it explicitly)."""
    with open(os.path.join(drawings_dir(instance_path), extraction.stored_filename), "rb") as f:
        data = f.read()
    return read_drawing(extraction.original_filename, data, instance_path, config, client=client, force=True)


def extraction_data(extraction):
    return DrawingData.model_validate_json(extraction.parsed) if extraction.parsed else None
