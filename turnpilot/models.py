from datetime import datetime, timezone

from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()

FEATURE_TYPES = (
    "face", "od_turn", "groove", "thread", "bore", "chamfer", "parting", "taper", "fillet", "hex",
    "arc",  # a formed section (an arc of the outer profile); only the DXF reader gives it
)
TOOL_TYPES = (
    "facing", "turning_rough", "turning_finish", "grooving", "threading", "boring", "parting",
    "drilling", "tapping", "threading_internal", "milling",  # milling: a driven tool (live tooling)
    "centre_drilling",  # a centre (spot) drill: starts every drilled hole
    "boring_rough",  # roughs a bore; "boring" finishes it (and roughs too when there is no boring_rough)
)
ISO_GROUPS = ("P", "M", "N")
BLANK_SHAPES = ("round", "hex")
OPERATION_STATUSES = ("proposed", "approved", "edited")
TURRET_POSITIONS = range(1, 13)  # T1..T12


def _now():
    return datetime.now(timezone.utc)


class Machine(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    max_rpm = db.Column(db.Integer, nullable=False)
    power_kw = db.Column(db.Float, nullable=False)
    max_diameter = db.Column(db.Float, nullable=False)
    # Z feed the machine allows when threading (feed = pitch, so n·P), mm/min. Empty: not known, and every
    # thread operation asks the operator to check n·P (no number is guessed).
    max_thread_feed = db.Column(db.Float)
    # Share of power_kw the spindle delivers (0..1, from the machine's documentation). Empty: not known, and
    # roughing asks for the power to be checked (no efficiency is guessed).
    drive_efficiency = db.Column(db.Float)
    # Hex bar sizes across flats in stock, "8, 10, 11"; empty: HEX_BAR_SIZES from the config.
    hex_bar_sizes = db.Column(db.String(200))
    # From the machine documentation (machine_spec.MACHINE_FIELDS); empty: not known, nothing is guessed.
    min_rpm = db.Column(db.Integer)
    power_s6_kw = db.Column(db.Float)  # shown only: the power check takes power_kw (S1)
    max_turning_length = db.Column(db.Float)
    max_bar_diameter = db.Column(db.Float)  # the bar that passes through the spindle
    turret_positions = db.Column(db.Integer, nullable=False, default=12, server_default="12")
    max_z_feed = db.Column(db.Float)  # mm/min; not the threading limit (max_thread_feed) unless set there
    coolant = db.Column(db.Boolean)
    coolant_pressure_bar = db.Column(db.Float)
    live_tooling = db.Column(db.Boolean)
    c_axis = db.Column(db.Boolean)
    control = db.Column(db.String(100))
    # G-code programming values (machine_spec.PROGRAMMING_FIELDS): entered by the operator, no default.
    spindle_right_hand = db.Column(db.String(3))  # "M03" / "M04"
    clearance_x = db.Column(db.Float)
    clearance_z = db.Column(db.Float)
    retract_mm = db.Column(db.Float)
    chuck_safety_mm = db.Column(db.Float)
    thread_run_in_mm = db.Column(db.Float)
    peck_depth_mm = db.Column(db.Float)
    facing_overshoot_mm = db.Column(db.Float)
    groove_reference = db.Column(db.String(12))
    parting_overshoot_mm = db.Column(db.Float)
    groove_dwell_s = db.Column(db.Float)  # optional: empty, no dwell at a groove's bottom

    slots = db.relationship(
        "TurretSlot", back_populates="machine", order_by="TurretSlot.position", cascade="all, delete-orphan"
    )
    sources = db.relationship("MachineSpecSource", back_populates="machine", cascade="all, delete-orphan")

    def source_of(self, field):
        return next((s for s in self.sources if s.field == field), None)


class MachineDocument(db.Model):
    """An uploaded machine passport (PDF) and the pages the operator picked for reading. Kept for the record."""

    id = db.Column(db.Integer, primary_key=True)
    machine_id = db.Column(db.Integer, db.ForeignKey("machine.id", name="fk_machine_document_machine_id"),
                           nullable=False)
    original_filename = db.Column(db.String(255), nullable=False)
    stored_filename = db.Column(db.String(100), nullable=False)  # in instance/machine_docs/
    sha256 = db.Column(db.String(64), nullable=False)
    size_bytes = db.Column(db.Integer, nullable=False)
    pages = db.Column(db.Integer, nullable=False)
    text_pages = db.Column(db.Integer, nullable=False)  # pages with a text layer (0: a scan)
    selected_pages = db.Column(db.String(200))  # "3, 4, 12": the pages to read
    created_at = db.Column(db.DateTime, default=_now, nullable=False)
    # The reading of the picked pages (one paid call, started by the operator); kept for the record.
    status = db.Column(db.String(12), nullable=False, default="uploaded", server_default="uploaded")  # uploaded/read/failed/confirmed
    model = db.Column(db.String(100))
    prompt_version = db.Column(db.String(20))  # passport_reader.prompt_version()
    read_pages = db.Column(db.String(200))  # the pages that were sent
    raw_response = db.Column(db.Text)
    reading = db.Column(db.Text)  # passport_reader.PassportValue list as JSON, checked by the code
    error = db.Column(db.Text)
    read_at = db.Column(db.DateTime)
    confirmed_at = db.Column(db.DateTime)

    machine = db.relationship("Machine")

    @property
    def selected(self):
        return [int(p) for p in (self.selected_pages or "").replace(" ", "").split(",") if p]


class MachineSpecSource(db.Model):
    """Where a machine value comes from: the passport (page, quote), the operator, or "not in passport"."""

    __table_args__ = (db.UniqueConstraint("machine_id", "field", name="uq_machine_spec_source_field"),)

    id = db.Column(db.Integer, primary_key=True)
    machine_id = db.Column(db.Integer, db.ForeignKey("machine.id", name="fk_machine_spec_source_machine_id"),
                           nullable=False)
    field = db.Column(db.String(40), nullable=False)
    value = db.Column(db.String(100))  # as confirmed, in the field's unit
    source = db.Column(db.String(20), nullable=False)  # machine_spec.SOURCE_*
    page = db.Column(db.Integer)
    quote = db.Column(db.Text)
    document_id = db.Column(db.Integer)  # MachineDocument.id, when from a passport
    confirmed_at = db.Column(db.DateTime, default=_now, nullable=False)

    machine = db.relationship("Machine", back_populates="sources")

    @property
    def label(self):
        if self.source == "passport":
            return f"passport p. {self.page}" if self.page else "passport"
        if self.source == "operator":
            return f"entered by the operator {self.confirmed_at:%Y-%m-%d}"
        if self.source == "demo":
            return "DEMO value, not from the machine"
        return self.source


class Tool(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    type = db.Column(db.String(30), nullable=False)
    insert_code = db.Column(db.String(50))
    grade = db.Column(db.String(30))
    iso_group = db.Column(db.String(10), nullable=False)  # e.g. "P" or "PMN"
    vc_min = db.Column(db.Float, nullable=False)
    vc_max = db.Column(db.Float, nullable=False)
    f_min = db.Column(db.Float, nullable=False)
    f_max = db.Column(db.Float, nullable=False)
    ap_min = db.Column(db.Float, nullable=False)
    ap_max = db.Column(db.Float, nullable=False)
    insert_width = db.Column(db.Float)  # mm, grooving / parting inserts
    diameter = db.Column(db.Float)  # mm, drills: the hole they make
    max_depth = db.Column(db.Float)  # mm, drills: the deepest hole they reach (e.g. 3 × D for a 3×D drill);
    # grooving: the holder's max cutting depth
    holder_code = db.Column(db.String(50))  # the holder's code, the operator's (e.g. RF123E08-2525B); empty: not known
    # Turning tools: the steepest angle (degrees to the axis) the insert in its holder may cut going down towards
    # the chuck (the catalogue's max in-copying angle). Empty: not known, no such move is programmed for it.
    max_ramp_angle = db.Column(db.Float)
    max_ramp_source = db.Column(db.String(200))  # where RMPX comes from (catalogue page, holder) and who chose it
    # Turning tools: the nose radius rε (empty: from the insert code) and the imaginary tip's direction number T
    # (0-9, as on the control's offset page; empty: not known, no nose radius compensation for this tool).
    nose_radius = db.Column(db.Float)
    tip_direction = db.Column(db.Integer)
    source = db.Column(db.String(300))  # where the Vc / f / ap ranges come from: catalogue, insert grade, page
    # The catalogue's recommended values: the planner takes these instead of positions in the ranges. vc_points:
    # Vc at given feeds, "0.1:455, 0.4:305, 0.8:215" (linear between them), or one Vc for every feed ("125").
    ap_rec = db.Column(db.Float)
    f_rec = db.Column(db.Float)
    vc_points = db.Column(db.String(200))
    # A tool used by operations is never deleted (the operations keep their audit): it is retired instead,
    # out of the library and the turret.
    is_retired = db.Column(db.Boolean, nullable=False, default=False, server_default=db.false())
    updated_at = db.Column(db.DateTime, default=_now, onupdate=_now)


class TurretSlot(db.Model):
    __table_args__ = (db.UniqueConstraint("machine_id", "position"),)

    id = db.Column(db.Integer, primary_key=True)
    machine_id = db.Column(db.Integer, db.ForeignKey("machine.id"), nullable=False)
    position = db.Column(db.Integer, nullable=False)
    tool_id = db.Column(db.Integer, db.ForeignKey("tool.id"))

    machine = db.relationship("Machine", back_populates="slots")
    tool = db.relationship("Tool")

    @property
    def label(self):
        return f"T{self.position}"


class Material(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    iso_group = db.Column(db.String(1), nullable=False)
    hardness_hb = db.Column(db.Integer)
    # Specific cutting force for the cutting power: kc = kc1 * hm^-mc (kc1 in N/mm² at hm = 1 mm), as the tool
    # makers' catalogues give it per material group; kc_source says where the numbers come from.
    kc1 = db.Column(db.Float)
    mc = db.Column(db.Float)
    kc_source = db.Column(db.String(200))
    # The tool maker's material group (e.g. Sandvik Coromant "P1.2"): the planner takes the cutting data of the
    # confirmed catalogue rows of this group. Empty: not known, no catalogue data.
    catalogue_group = db.Column(db.String(20))


CUTTING_DATA_KINDS = ("geometry", "grade_vc")  # ap / f of an insert or drill; Vc of a grade
CUTTING_DATA_APPLICATIONS = ("turning", "grooving", "parting", "threading", "drilling")
CUTTING_DATA_ORIGINS = ("hand_typed", "model", "operator")
CUTTING_DATA_STATUSES = ("read", "confirmed", "rejected", "replaced")


class CatalogueDocument(db.Model):
    """An uploaded tool maker's catalogue (PDF, in instance/catalogues/, never in git) and its readings."""

    id = db.Column(db.Integer, primary_key=True)
    original_filename = db.Column(db.String(255), nullable=False)
    stored_filename = db.Column(db.String(100), nullable=False)  # in instance/catalogues/
    title = db.Column(db.String(200))  # e.g. "Sandvik Coromant Turning tools 2020"
    sha256 = db.Column(db.String(64), nullable=False)
    size_bytes = db.Column(db.Integer, nullable=False)
    pages = db.Column(db.Integer, nullable=False)
    text_pages = db.Column(db.Integer, nullable=False)
    selected_pages = db.Column(db.String(200))  # PDF page numbers to read: "280, 281"
    material_groups = db.Column(db.String(100))  # the groups to record, e.g. "P1.2"
    codes = db.Column(db.Text)  # the insert codes / grades to record (the tools the shop has), one per line
    created_at = db.Column(db.DateTime, default=_now, nullable=False)
    # The last reading (one paid call, started by the operator); its rows are CuttingDataRow with this document.
    status = db.Column(db.String(12), nullable=False, default="uploaded", server_default="uploaded")  # uploaded/read/failed
    model = db.Column(db.String(100))
    prompt_version = db.Column(db.String(20))
    read_pages = db.Column(db.String(200))
    raw_response = db.Column(db.Text)
    not_taken = db.Column(db.Text)  # JSON: rows the code did not take, with the reason
    error = db.Column(db.Text)
    read_at = db.Column(db.DateTime)

    @property
    def selected(self):
        return [int(p) for p in (self.selected_pages or "").replace(" ", "").split(",") if p]


class CuttingDataRow(db.Model):
    """One row of a catalogue's cutting data, with its source. The planner uses confirmed rows only.

    geometry: ap / f (min, recommended, max) of an insert or drill code, for a material group or an ISO letter.
    grade_vc: Vc of a grade for an application and a material group: Vc at given feeds (vc_points) and/or a
    range (vc_min .. vc_max, the start value as one point).
    """

    id = db.Column(db.Integer, primary_key=True)
    kind = db.Column(db.String(10), nullable=False)
    catalogue = db.Column(db.String(200), nullable=False)  # the catalogue's name and edition
    document_id = db.Column(db.Integer, db.ForeignKey("catalogue_document.id",
                                                      name="fk_cutting_data_row_document_id"))
    insert_code = db.Column(db.String(60))  # geometry
    grade = db.Column(db.String(30))  # grade_vc
    application = db.Column(db.String(12))  # grade_vc: CUTTING_DATA_APPLICATIONS
    material_group = db.Column(db.String(20), nullable=False)  # "P1.2", or an ISO letter "P" for geometry
    ap_min = db.Column(db.Float)
    ap_rec = db.Column(db.Float)
    ap_max = db.Column(db.Float)
    f_min = db.Column(db.Float)
    f_rec = db.Column(db.Float)
    f_max = db.Column(db.Float)
    vc_min = db.Column(db.Float)
    vc_max = db.Column(db.Float)
    vc_points = db.Column(db.String(200))  # as Tool.vc_points: "0.1:455, 0.4:305, 0.8:215" or "125"
    coolant = db.Column(db.Boolean)  # the catalogue gives the Vc with coolant
    from_graph = db.Column(db.Boolean, nullable=False, default=False, server_default=db.false())
    page = db.Column(db.String(60))  # the page as printed in the catalogue, e.g. "A283"
    pdf_page = db.Column(db.Integer)  # the PDF page it was read from
    quote = db.Column(db.Text)
    origin = db.Column(db.String(12), nullable=False)  # CUTTING_DATA_ORIGINS
    status = db.Column(db.String(10), nullable=False, default="read", server_default="read")
    checks = db.Column(db.Text)  # JSON list: why the row needs a look ("check" badges)
    note = db.Column(db.String(300))
    created_at = db.Column(db.DateTime, default=_now, nullable=False)
    confirmed_at = db.Column(db.DateTime)

    document = db.relationship("CatalogueDocument")


class Job(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    material_id = db.Column(db.Integer, db.ForeignKey("material.id"), nullable=False)
    quantity = db.Column(db.Integer, nullable=False, default=1)
    blank_diameter = db.Column(db.Float, nullable=False)  # round bar: Ø; hex bar: size across flats S
    blank_length = db.Column(db.Float, nullable=False)
    blank_shape = db.Column(db.String(10), nullable=False, default="round", server_default="round")
    # The features are in their order along the axis (a job confirmed from a DXF). A feature added by hand
    # goes to the end of the list, so it clears the flag; deleting one keeps the order.
    axial_order_known = db.Column(db.Boolean, nullable=False, default=False, server_default=db.false())
    # G-code set-up, entered by the operator: which end of the drawing is the free end (Z0, "left" / "right"),
    # how far the part sticks out of the jaws, and the stock beyond Z0 to face off. Empty: no program.
    free_end = db.Column(db.String(5))
    stickout_mm = db.Column(db.Float)
    face_stock_mm = db.Column(db.Float)
    # The set-up was filled in by the demo command (`flask gcode-demo`), not by the operator: no program is ready to
    # run until the operator saves the set-up.
    gcode_setup_demo = db.Column(db.Boolean, nullable=False, default=False, server_default=db.false())
    created_at = db.Column(db.DateTime, default=_now)

    material = db.relationship("Material")
    # Features and operations are never deleted (only flagged), so the edit log keeps its context.
    features = db.relationship("Feature", back_populates="job", order_by="Feature.id")
    operations = db.relationship(
        "Operation",
        back_populates="job",
        order_by=lambda: (Operation.calculation_version.desc(), Operation.sequence),
    )
    extraction = db.relationship("DrawingExtraction", back_populates="job", uselist=False)

    @property
    def blank_label(self):
        """"Ø16" for a round bar, "hex S11" for a hex bar."""
        size = f"{self.blank_diameter:g}"
        return f"hex S{size}" if self.blank_shape == "hex" else f"Ø{size}"

    @property
    def active_features(self):
        return [f for f in self.features if not f.is_deleted]

    @property
    def current_operations(self):
        return [op for op in self.operations if not op.is_archived]

    @property
    def archived_operations(self):
        return [op for op in self.operations if op.is_archived]

    @property
    def last_calculation_version(self):
        return max((op.calculation_version for op in self.operations), default=0)

    @property
    def tools_changed_since_calculation(self):
        """Tools of the current operations edited or retired after those operations were calculated (operations
        calculated before their time was recorded are not compared)."""
        changed = {
            op.tool for op in self.current_operations
            if op.tool and op.tool.updated_at and op.created_at and op.tool.updated_at > op.created_at
        }
        return sorted(changed, key=lambda t: t.name)


class Feature(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    job_id = db.Column(db.Integer, db.ForeignKey("job.id"), nullable=False)
    type = db.Column(db.String(20), nullable=False)
    diameter = db.Column(db.Float)
    length = db.Column(db.Float)
    tolerance = db.Column(db.String(30))  # free text, e.g. "h7" or "+0/-0.05"
    ra = db.Column(db.Float)  # um, of the parameter in ra_param
    ra_param = db.Column(db.String(2))  # "Ra" (default when empty) or "Rz", as written on the drawing
    pitch = db.Column(db.Float)  # mm, threads only
    start_diameter = db.Column(db.Float)  # mm, groove: diameter it is cut from; taper / arc: diameter at its start
    radius = db.Column(db.Float)  # mm, fillets and arcs only
    across_flats = db.Column(db.Float)  # mm, hex only: size across flats S (diameter = across corners)
    location = db.Column(db.String(10))  # chamfers: "external" / "internal"
    face = db.Column(db.String(10))  # chamfers, fillets: "left" / "right" end face (on the drawing)
    # arcs and fillets: bulging away from the axis (True) or into it (False); empty: not known, not programmed
    arc_convex = db.Column(db.Boolean)
    # arcs from a DXF: the radius drawn, when it differs from the dimension the operator has to choose (the job page)
    drawn_radius = db.Column(db.Float)
    confidence = db.Column(db.Float)  # 0..1 when the feature was read from a drawing
    is_deleted = db.Column(db.Boolean, nullable=False, default=False)

    job = db.relationship("Job", back_populates="features")
    operations = db.relationship("Operation", back_populates="feature")


class Operation(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    job_id = db.Column(db.Integer, db.ForeignKey("job.id"), nullable=False)
    feature_id = db.Column(db.Integer, db.ForeignKey("feature.id"), nullable=False)
    tool_id = db.Column(db.Integer, db.ForeignKey("tool.id"))
    turret_position = db.Column(db.Integer)
    sequence = db.Column(db.Integer, nullable=False)
    tool_type = db.Column(db.String(30), nullable=False)
    rough_finish = db.Column(db.String(10), nullable=False)
    vc = db.Column(db.Float)
    n = db.Column(db.Integer)
    f = db.Column(db.Float)
    ap = db.Column(db.Float)
    passes = db.Column(db.Integer)
    insert_width = db.Column(db.Float)  # grooving: insert width
    depth = db.Column(db.Float)  # per side: groove depth or thread profile depth h
    ref_diameter = db.Column(db.Float)  # diameter used to compute n
    # Where vc / f / ap come from: "catalogue" (confirmed catalogue rows), "tool" (the tool's own values),
    # "operator" (changed by the operator); empty: not known. The G-code takes only catalogue / operator values.
    cutting_data_origin = db.Column(db.String(12))
    note = db.Column(db.Text)
    warning = db.Column(db.Text)
    status = db.Column(db.String(10), nullable=False, default="proposed")
    # Each "Calculate" creates a new version; older operations are archived, never deleted.
    calculation_version = db.Column(db.Integer, nullable=False, default=1)
    created_at = db.Column(db.DateTime, default=_now)  # empty for operations calculated before it was recorded
    is_archived = db.Column(db.Boolean, nullable=False, default=False)

    job = db.relationship("Job", back_populates="operations")
    feature = db.relationship("Feature", back_populates="operations")
    tool = db.relationship("Tool")
    edits = db.relationship("Edit", back_populates="operation", order_by="Edit.created_at")


class Edit(db.Model):
    """Audit log: one row per changed field of an operation."""

    id = db.Column(db.Integer, primary_key=True)
    operation_id = db.Column(db.Integer, db.ForeignKey("operation.id"), nullable=False)
    field = db.Column(db.String(30), nullable=False)
    old_value = db.Column(db.String(100))
    new_value = db.Column(db.String(100))
    created_at = db.Column(db.DateTime, default=_now, nullable=False)

    operation = db.relationship("Operation", back_populates="edits")


class DrawingExtraction(db.Model):
    """An uploaded drawing and what the model read from it. Kept for audit, never deleted."""

    id = db.Column(db.Integer, primary_key=True)
    original_filename = db.Column(db.String(255), nullable=False)
    stored_filename = db.Column(db.String(100), nullable=False)  # original file in instance/drawings/
    sent_filename = db.Column(db.String(100))  # the image actually sent to the model
    file_type = db.Column(db.String(10), nullable=False)  # png / jpeg / pdf / dxf
    size_bytes = db.Column(db.Integer, nullable=False)
    sha256 = db.Column(db.String(64), nullable=False)
    model = db.Column(db.String(100))
    prompt_version = db.Column(db.String(16))  # drawing_reader.prompt_version() used for this reading
    read_mode = db.Column(db.String(20))  # "features" or "dimensions_first"; "dxf": read by the code, no model
    status = db.Column(db.String(10), nullable=False, default="pending")  # pending/extracted/failed/confirmed
    raw_response = db.Column(db.Text)  # full API response as JSON
    parsed = db.Column(db.Text)  # validated DrawingData as JSON
    # DXF only: which part of the sheet (1, 2, ...; one extraction per part) and the binding report of
    # dxf_input (rows with their dimensions and notes, every dimension with what it is bound to) as JSON.
    dxf_part = db.Column(db.Integer)
    binding = db.Column(db.Text)
    error = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=_now, nullable=False)
    confirmed_at = db.Column(db.DateTime)
    job_id = db.Column(db.Integer, db.ForeignKey("job.id"))
    # Set when the result was reused from an earlier upload of the same file and model (no API call).
    cached_from_id = db.Column(db.Integer, db.ForeignKey("drawing_extraction.id"))

    job = db.relationship("Job", back_populates="extraction")
    cached_from = db.relationship("DrawingExtraction", remote_side=[id])

    @property
    def is_cached(self):
        return self.cached_from_id is not None

    @property
    def is_dxf(self):
        return self.read_mode == "dxf"


GCODE_STATUSES = ("simulated", "ready")


class GcodeProgram(db.Model):
    """A generated program, its simulation and the operator's confirmation. Kept for the record: a new generation
    makes a new row. Nothing is sent to the machine: the operator downloads the file once it is ready to run."""

    id = db.Column(db.Integer, primary_key=True)
    job_id = db.Column(db.Integer, db.ForeignKey("job.id", name="fk_gcode_program_job_id"), nullable=False)
    calculation_version = db.Column(db.Integer, nullable=False)
    dialect = db.Column(db.String(60), nullable=False)
    text = db.Column(db.Text, nullable=False)
    sha256 = db.Column(db.String(64), nullable=False)
    settings = db.Column(db.Text, nullable=False)  # JSON: the machine / job values used, with their sources
    simulation = db.Column(db.Text, nullable=False)  # JSON: errors, warnings, incomplete
    status = db.Column(db.String(10), nullable=False, default="simulated", server_default="simulated")
    checklist = db.Column(db.Text)  # JSON: the operator's ticks
    confirmed_by = db.Column(db.String(100))
    confirmed_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=_now, nullable=False)

    job = db.relationship("Job")
