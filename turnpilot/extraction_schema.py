"""Structured result of reading a drawing: the tool schema sent to Claude and pydantic validation."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, PositiveFloat, PositiveInt, field_validator, model_validator

FEATURE_TYPES = ("face", "od_turn", "groove", "thread", "bore", "chamfer", "parting", "taper", "fillet")
FeatureType = Literal["face", "od_turn", "groove", "thread", "bore", "chamfer", "parting", "taper", "fillet"]
PART_TYPES = ("turned", "not_turned", "unclear")
PartType = Literal["turned", "not_turned", "unclear"]

TOOL_NAME = "record_part"

# ISO 965 thread tolerance class: grade 3-9 + position, once or twice (pitch and crest diameter).
# External threads use e/f/g/h (6g, 4h6h), internal threads G/H (6H, 5H6H).
THREAD_CLASS = re.compile(r"(?:[3-9][efgh]){1,2}|(?:[3-9][GH]){1,2}")


class ExtractedFeature(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: FeatureType
    diameter: PositiveFloat | None = None
    start_diameter: PositiveFloat | None = None
    length: PositiveFloat | None = None
    tolerance: str | None = None
    ra: PositiveFloat | None = None
    pitch: PositiveFloat | None = None
    radius: PositiveFloat | None = None
    # Required in model output (strict tool schema); absent in hand-written expected files.
    confidence: float | None = Field(default=None, ge=0, le=1)

    @field_validator("tolerance")
    @classmethod
    def _blank_tolerance_is_none(cls, value):
        if value is None:
            return None
        value = value.strip()
        return value or None

    @model_validator(mode="after")
    def _check_consistency(self):
        if self.pitch is not None and self.type != "thread":
            raise ValueError(f"pitch is only valid for threads, got it on {self.type}")
        if self.radius is not None and self.type != "fillet":
            raise ValueError(f"radius is only valid for fillets, got it on {self.type}")
        if self.start_diameter is not None:
            if self.type not in ("groove", "taper"):
                raise ValueError(f"start_diameter is only valid for grooves and tapers, got it on {self.type}")
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
    features: list[ExtractedFeature]
    warnings: list[str] = Field(default_factory=list)

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


def _nullable(json_type: str, description: str) -> dict:
    return {"anyOf": [{"type": json_type}, {"type": "null"}], "description": description}


_FEATURE_SCHEMA = {
    "type": "object",
    "properties": {
        "type": {
            "type": "string",
            "enum": list(FEATURE_TYPES),
            "description": (
                "od_turn: an external cylindrical section. groove: a recess cut into an external diameter "
                "(including a thread relief groove). thread: an external thread. bore: an internal diameter. "
                "chamfer: an edge chamfer. taper: a conical section. fillet: a radius between two sections. "
                "face / parting: only if the drawing explicitly annotates them."
            ),
        },
        "diameter": _nullable(
            "number",
            "mm. od_turn/bore: the section diameter. groove: the groove BOTTOM diameter. "
            "thread: the major (nominal) diameter. chamfer: the diameter whose edge is chamfered. "
            "taper: the diameter at the END of the taper (smaller or larger). fillet: null.",
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
            "Surface roughness Ra in µm marked on this feature itself. null if the feature has no own mark "
            "(the general roughness goes to general_ra, not here).",
        ),
        "pitch": _nullable("number", "mm. thread only: the pitch (1.5 for M20x1.5). null for other types."),
        "radius": _nullable("number", "mm. fillet only: the radius (10 for R10). null for other types."),
        "confidence": {
            "type": "number",
            "description": "0..1: how sure you are that this feature and its values are read correctly.",
        },
    },
    "required": [
        "type", "diameter", "start_diameter", "length", "tolerance", "ra", "pitch", "radius", "confidence",
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
                "µm. Ra of the roughness symbol without a leader in the top-right corner of the sheet: it applies "
                "to every surface without its own mark. null if there is no such symbol.",
            ),
            "features": {"type": "array", "items": _FEATURE_SCHEMA},
            "warnings": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Anything unclear, illegible, contradictory or not representable by the feature types.",
            },
        },
        "required": [
            "part_type", "material", "blank_diameter", "blank_length", "overall_length", "quantity",
            "general_ra", "features", "warnings",
        ],
        "additionalProperties": False,
    },
}
