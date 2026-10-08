"""Structured result of reading a drawing: the tool schema sent to Claude and pydantic validation."""

from __future__ import annotations

import math
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, PositiveFloat, PositiveInt, field_validator, model_validator

FEATURE_TYPES = (
    "face", "od_turn", "groove", "thread", "bore", "chamfer", "parting", "taper", "fillet", "hex", "arc",
)
FeatureType = Literal[
    "face", "od_turn", "groove", "thread", "bore", "chamfer", "parting", "taper", "fillet", "hex", "arc",
]
# Types the model may send: all but "arc", which only the DXF reader gives (a formed section: an arc of the
# outer profile that is not a fillet). Leaving it out keeps the tool schema and the prompt version unchanged.
# (dimensions_first has its own schema and does not know hex.)
TOOL_FEATURE_TYPES = tuple(t for t in FEATURE_TYPES if t != "arc")
# Types with a radius and with a start diameter (an arc: the diameter at its start, its end in diameter).
RADIUS_TYPES = ("fillet", "arc")
START_DIAMETER_TYPES = ("groove", "taper", "arc")
PART_TYPES = ("turned", "not_turned", "unclear")
# Roughness parameter as written on the drawing: GOST drawings often give Rz instead of Ra.
ROUGHNESS_PARAMS = ("Ra", "Rz")
RoughnessParam = Literal["Ra", "Rz"]
# Where a chamfer is: on an outside diameter or at the entrance of a bore / internal thread, and on
# which end face. In the tool schema "none" stands for "not a chamfer / not visible" (not nullable,
# so the strict schema keeps few union types).
LOCATIONS = ("external", "internal")
FACES = ("left", "right")
Location = Literal["external", "internal"]
Face = Literal["left", "right"]
PartType = Literal["turned", "not_turned", "unclear"]

TOOL_NAME = "record_part"

DECIMAL_COMMA = re.compile(r"(?<=\d),(?=\d)")


def normalize_tolerance(value: str | None) -> str | None:
    """Tolerance text with a decimal point: GOST drawings write "±0,05" or "0/-0,021".

    Only a comma between two digits is replaced, so the rest of the text stays as written.
    Empty text becomes None.
    """
    if value is None:
        return None
    value = DECIMAL_COMMA.sub(".", value.strip())
    return value or None

# ISO 965 thread tolerance class: grade 3-9 + position, once or twice (pitch and crest diameter).
# External threads use e/f/g/h (6g, 4h6h), internal threads G/H (6H, 5H6H).
THREAD_CLASS = re.compile(r"(?:[3-9][efgh]){1,2}|(?:[3-9][GH]){1,2}")

# ISO 261 coarse pitch of metric threads (mm). A metric thread written without a pitch ("M14-7H")
# has the coarse pitch.
COARSE_PITCH_MM = {
    1: 0.25, 1.2: 0.25, 1.6: 0.35, 2: 0.4, 2.5: 0.45, 3: 0.5, 3.5: 0.6, 4: 0.7, 5: 0.8, 6: 1, 7: 1,
    8: 1.25, 10: 1.5, 12: 1.75, 14: 2, 16: 2, 18: 2.5, 20: 2.5, 22: 2.5, 24: 3, 27: 3, 30: 3.5,
    33: 3.5, 36: 4, 39: 4, 42: 4.5, 45: 4.5, 48: 5, 52: 5, 56: 5.5, 60: 5.5, 64: 6,
}


# --- hexagons --------------------------------------------------------------------------------
#
# A hex is given by its size across flats S (the wrench size) and/or its diameter across corners
# (the circumscribed circle, what the lathe sees): D = S / cos 30°.

HEX_CORNERS_PER_FLATS = 2 / math.sqrt(3)
# ISO 272 / GOST 13682 wrench sizes, mm.
WRENCH_SIZES_MM = (
    3.2, 4, 5, 5.5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 21, 22, 24, 27, 30, 32, 34, 36,
    41, 46, 50, 55, 60, 65, 70, 75, 80, 85, 90, 95, 100,
)


def hex_across_corners(across_flats: float) -> float:
    """Diameter across corners of a hex with the given size across flats: D = S / cos 30°."""
    return round(across_flats * HEX_CORNERS_PER_FLATS, 2)


def hex_across_flats(across_corners: float) -> float:
    """Size across flats of a hex with the given diameter across corners: S = D · cos 30°."""
    return round(across_corners / HEX_CORNERS_PER_FLATS, 2)


def nearest_wrench_size(across_flats: float) -> float:
    return min(WRENCH_SIZES_MM, key=lambda s: abs(s - across_flats))


def coarse_pitch(diameter: float | None) -> float | None:
    """ISO 261 coarse pitch for a nominal metric thread diameter, or None if it is not in the table."""
    if diameter is None:
        return None
    for nominal, pitch in COARSE_PITCH_MM.items():
        if abs(nominal - diameter) < 1e-6:
            return pitch
    return None


class ExtractedFeature(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: FeatureType
    diameter: PositiveFloat | None = None
    start_diameter: PositiveFloat | None = None
    length: PositiveFloat | None = None
    tolerance: str | None = None
    ra: PositiveFloat | None = None  # roughness value in µm, of the parameter in ra_param
    ra_param: RoughnessParam = "Ra"
    pitch: PositiveFloat | None = None
    radius: PositiveFloat | None = None
    across_flats: PositiveFloat | None = None  # hex only: size across flats S; diameter is across corners
    location: Location | None = None  # chamfers and threads: external / internal
    face: Face | None = None  # chamfers only: left / right end face
    # Required in model output (strict tool schema); absent in hand-written expected files.
    confidence: float | None = Field(default=None, ge=0, le=1)
    # Set by the code, not by the model: the length is not dimensioned directly but computed from
    # other dimensions (e.g. a baseline minus a groove), so the operator should check it.
    length_derived: bool = False
    # Set by the code: the dimensions allow a second reading of this length (a face dimension that
    # may or may not include a neighbouring groove), see dimensions_first.ambiguous_face_bindings().
    length_ambiguous: bool = False
    # Set by the code: the drawing gives no pitch, so the ISO 261 coarse pitch was filled in.
    pitch_assumed: bool = False
    # Set by the code: a hex has only one of its sizes on the drawing, the other one ("diameter" or
    # "across_flats") was computed from it.
    size_derived: Literal["diameter", "across_flats"] | None = None

    @field_validator("tolerance")
    @classmethod
    def _normalize_tolerance(cls, value):
        return normalize_tolerance(value)

    @field_validator("location", "face", mode="before")
    @classmethod
    def _none_means_none(cls, value):
        return None if value in ("none", "") else value

    @field_validator("across_flats", mode="before")
    @classmethod
    def _zero_means_none(cls, value):
        """The tool schema sends 0 for "no size across flats" (not nullable, to keep few union types).
        It becomes None here, so a 0 never reaches the planner, the geometry check or the scorer."""
        return None if value == 0 else value

    @model_validator(mode="after")
    def _check_consistency(self):
        if self.pitch is not None and self.type != "thread":
            raise ValueError(f"pitch is only valid for threads, got it on {self.type}")
        if self.type not in ("chamfer", "thread"):
            # only chamfers and threads carry a position; the model may send it for others, it is dropped
            self.location = self.face = None
        elif self.type == "thread":
            self.face = None
        if self.radius is not None and self.type not in RADIUS_TYPES:
            raise ValueError(f"radius is only valid for fillets and arcs, got it on {self.type}")
        if self.across_flats is not None and self.type != "hex":
            raise ValueError(f"across_flats is only valid for hexes, got it on {self.type}")
        if self.type == "hex":
            if self.diameter is None and self.across_flats is not None:
                self.diameter = hex_across_corners(self.across_flats)
                self.size_derived = "diameter"
            elif self.across_flats is None and self.diameter is not None:
                self.across_flats = hex_across_flats(self.diameter)
                self.size_derived = "across_flats"
        if self.start_diameter is not None:
            if self.type not in START_DIAMETER_TYPES:
                raise ValueError(
                    f"start_diameter is only valid for grooves, tapers and arcs, got it on {self.type}"
                )
            if self.type == "groove" and self.diameter is not None and self.start_diameter <= self.diameter:
                raise ValueError("groove start_diameter must be larger than the groove bottom diameter")
        return self


class DrawingData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    part_type: PartType
    material: str | None = None
    blank_diameter: PositiveFloat | None = None
    blank_length: PositiveFloat | None = None
    overall_length: PositiveFloat | None = None
    quantity: PositiveInt | None = None
    # Roughness symbol without a leader in the top-right corner: applies to surfaces without their own Ra.
    general_ra: PositiveFloat | None = None
    general_ra_param: RoughnessParam = "Ra"
    # General (unspecified) tolerance note as written, e.g. "H14, h14, ±IT14/2"; applied by the code.
    general_tolerance: str | None = None
    features: list[ExtractedFeature]
    warnings: list[str] = Field(default_factory=list)

    @field_validator("general_tolerance")
    @classmethod
    def _blank_general_tolerance_is_none(cls, value):
        return normalize_tolerance(value)

    @field_validator("material")
    @classmethod
    def _blank_material_is_none(cls, value):
        if value is None:
            return None
        value = value.strip()
        return value or None

    @model_validator(mode="after")
    def _check_thread_classes(self):
        """A thread tolerance must be an ISO thread class (6g, 6H, 4h6h, ...); anything else is cleared.

        A misread class ("69" for "6g") or a whole designation ("M48x1.5-6g") would otherwise be
        taken as a tolerance, so it is removed and reported for the reviewer.
        """
        for feature in self.features:
            if feature.type == "thread" and feature.tolerance and not THREAD_CLASS.fullmatch(feature.tolerance):
                self.warnings.append(f"thread class unclear: '{feature.tolerance}'")
                feature.tolerance = None
        return self

    @model_validator(mode="after")
    def _check_thread_location(self):
        """The class letter tells the side: capital (7H, 6G) internal, small (6g, 6h) external.

        An unknown location is taken from the class; a location that contradicts it is kept and
        reported for checking.
        """
        for feature in self.features:
            if feature.type != "thread" or not feature.tolerance:
                continue
            by_class = "internal" if any(ch.isupper() for ch in feature.tolerance) else "external"
            if feature.location is None:
                feature.location = by_class
            elif feature.location != by_class:
                self.warnings.append(
                    f"thread M{feature.diameter:g}: class {feature.tolerance} means an {by_class} thread, "
                    f"but it was read as {feature.location}: check"
                )
        return self

    @model_validator(mode="after")
    def _bind_chamfers_to_hexes(self):
        """A chamfer on a hex is machined on its diameter across corners. The model records the chamfer
        as drawn, with the hex's size it sees (S or the diameter across corners): the code puts it on
        the diameter across corners."""
        hexes = [f for f in self.features if f.type == "hex" and f.diameter]
        for chamfer in (f for f in self.features if f.type == "chamfer" and f.diameter and f.location != "internal"):
            for hex_ in hexes:
                sizes = [hex_.diameter] + ([hex_.across_flats] if hex_.across_flats else [])
                if any(abs(chamfer.diameter - size) < 0.05 for size in sizes):
                    chamfer.diameter = hex_.diameter
                    break
        return self

    @model_validator(mode="after")
    def _check_wrench_sizes(self):
        """A hex size across flats that is not a standard wrench size is reported, never changed."""
        for feature in self.features:
            if feature.type != "hex" or feature.across_flats is None:
                continue
            nearest = nearest_wrench_size(feature.across_flats)
            if abs(nearest - feature.across_flats) > 0.01:
                derived = " (computed from the diameter across corners)" if feature.size_derived == "across_flats" else ""
                self.warnings.append(
                    f"hex S{feature.across_flats:g}{derived} is not a standard wrench size "
                    f"(nearest S{nearest:g}): check"
                )
        return self

    @model_validator(mode="after")
    def _fill_coarse_pitch(self):
        """A metric thread without a pitch on the drawing has the ISO 261 coarse pitch: fill it, mark it."""
        for feature in self.features:
            if feature.type != "thread" or feature.pitch is not None or feature.pitch_assumed:
                continue
            pitch = coarse_pitch(feature.diameter)
            if pitch is None:
                continue
            feature.pitch = pitch
            feature.pitch_assumed = True
            self.warnings.append(
                f"thread M{feature.diameter:g}: no pitch on the drawing, coarse pitch {pitch:g} assumed "
                f"(ISO 261): check"
            )
        return self


def _nullable(json_type: str, description: str) -> dict:
    return {"anyOf": [{"type": json_type}, {"type": "null"}], "description": description}


_FEATURE_SCHEMA = {
    "type": "object",
    "properties": {
        "type": {
            "type": "string",
            "enum": list(TOOL_FEATURE_TYPES),
            "description": (
                "od_turn: an external cylindrical section. groove: a recess cut into an external diameter "
                "(including a thread relief groove). thread: an external thread. bore: an internal diameter. "
                "chamfer: an edge chamfer. taper: a conical section. fillet: a radius between two sections. "
                "hex: a hexagon with wrench flats (size across flats S). "
                "face / parting: only if the drawing explicitly annotates them."
            ),
        },
        "diameter": _nullable(
            "number",
            "mm. od_turn/bore: the section diameter. groove: the groove BOTTOM diameter. "
            "thread: the major (nominal) diameter. chamfer: the diameter whose edge is chamfered. "
            "taper: the diameter at the END of the taper (smaller or larger). fillet: null. "
            "hex: the diameter across corners only if it is dimensioned, else null.",
        ),
        "start_diameter": _nullable(
            "number",
            "mm. groove: the external diameter the groove is cut from. taper: the diameter at the START of "
            "the taper. null for other types.",
        ),
        "length": _nullable(
            "number",
            "mm. od_turn/bore: section length. groove: groove width. thread: threaded length. "
            "chamfer: chamfer leg size (1 for 1x45°). taper: axial length of the cone. fillet: null.",
        ),
        "tolerance": _nullable(
            "string",
            "Tolerance exactly as written for this diameter, e.g. 'h6', 'f7', 'H7', '±0.05', '+0.02/-0.01', "
            "thread class '6g'. null if none is written.",
        ),
        "ra": _nullable(
            "number",
            "Surface roughness value in µm marked on this feature itself (Ra or Rz, see ra_param). null if the "
            "feature has no own mark (the general roughness goes to general_ra, not here).",
        ),
        "ra_param": {
            "type": "string",
            "enum": list(ROUGHNESS_PARAMS),
            "description": "The roughness parameter written with the value: Ra or Rz. Do not convert. Ra if none.",
        },
        "pitch": _nullable("number", "mm. thread only: the pitch (1.5 for M20x1.5). null for other types."),
        "radius": _nullable("number", "mm. fillet only: the radius (10 for R10). null for other types."),
        "across_flats": {
            "type": "number",
            "description": "mm. hex only: the size across flats S (17 for S17). 0 for other types or if not "
                           "on the drawing.",
        },
        "location": {
            "type": "string",
            "enum": [*LOCATIONS, "none"],
            "description": "chamfer: external (on an outside diameter) or internal (at the entrance of a bore "
                           "or internal thread). thread: external (on a shaft) or internal (in a hole). none for "
                           "other types or if not visible.",
        },
        "face": {
            "type": "string",
            "enum": [*FACES, "none"],
            "description": "chamfer only: the end face it is on, left or right. none for other types or if "
                           "not visible.",
        },
        "confidence": {
            "type": "number",
            "description": "0..1: how sure you are that this feature and its values are read correctly.",
        },
    },
    "required": [
        "type", "diameter", "start_diameter", "length", "tolerance", "ra", "ra_param", "pitch", "radius",
        "across_flats", "location", "face", "confidence",
    ],
    "additionalProperties": False,
}

RECORD_PART_TOOL = {
    "name": TOOL_NAME,
    "description": (
        "Record the turned part read from the drawing. Use null for any value that is not visible on the "
        "drawing; never estimate or guess a value."
    ),
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {
            "part_type": {
                "type": "string",
                "enum": list(PART_TYPES),
                "description": (
                    "turned: a body of revolution made on a lathe (shafts, bushings, pins). "
                    "not_turned: clearly not a lathe part, e.g. a milled or flat part (plates, forks, "
                    "brackets, housings). unclear: cannot tell from the drawing."
                ),
            },
            "material": _nullable("string", "Material exactly as written in the title block."),
            "blank_diameter": _nullable(
                "number", "mm. Only if the drawing states the blank/stock size. Never derive it from the part."
            ),
            "blank_length": _nullable(
                "number", "mm. Only if the drawing states the blank/stock size. Never derive it from the part."
            ),
            "overall_length": _nullable("number", "mm. The overall length dimension of the part, if dimensioned."),
            "quantity": _nullable("integer", "Quantity from the title block."),
            "general_ra": _nullable(
                "number",
                "µm. Value of the roughness symbol without a leader in the top-right corner of the sheet (Ra or "
                "Rz, see general_ra_param): it applies to every surface without its own mark. null if none.",
            ),
            "general_ra_param": {
                "type": "string",
                "enum": list(ROUGHNESS_PARAMS),
                "description": "Parameter of the general roughness as written: Ra or Rz. Do not convert. Ra if none.",
            },
            "general_tolerance": {
                "type": "string",
                "description": "The general (unspecified) tolerance note exactly as written, e.g. "
                               "'H14, h14, ±IT14/2' or 'ISO 2768-m'. Empty string if there is none. Do not copy "
                               "it into the features' tolerance.",
            },
            "features": {"type": "array", "items": _FEATURE_SCHEMA},
            "warnings": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Anything unclear, illegible, contradictory or not representable by the feature types.",
            },
        },
        "required": [
            "part_type", "material", "blank_diameter", "blank_length", "overall_length", "quantity",
            "general_ra", "general_ra_param", "general_tolerance", "features", "warnings",
        ],
        "additionalProperties": False,
    },
}
