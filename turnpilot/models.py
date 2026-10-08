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

    slots = db.relationship(
        "TurretSlot", back_populates="machine", order_by="TurretSlot.position", cascade="all, delete-orphan"
    )


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
    max_depth = db.Column(db.Float)  # mm, drills: the deepest hole they reach (e.g. 3 × D for a 3×D drill)
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
    face = db.Column(db.String(10))  # chamfers: "left" / "right" end face
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
