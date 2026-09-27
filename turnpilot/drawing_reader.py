"""Read a lathe part drawing with Claude vision and return validated, structured data.

No Flask here: the web app and tools/eval_extraction.py both call extract_drawing().
"""

from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass, field

import anthropic
import pymupdf
from dotenv import load_dotenv
from pydantic import ValidationError

from .extraction_schema import RECORD_PART_TOOL, TOOL_NAME, DrawingData

DEFAULT_MODEL = "claude-sonnet-5"

# Claude API limits: 8000x8000 px and 10 MB (base64) per image. Current models see up to a
# 2576 px long edge without downscaling, so larger images are downscaled here to that size.
MAX_LONG_EDGE_PX = 2576
MAX_BASE64_BYTES = 10 * 1024 * 1024

MEDIA_TYPES = {"png": "image/png", "jpeg": "image/jpeg", "pdf": "application/pdf"}

SYSTEM_PROMPT = """\
You read engineering drawings of parts made on a CNC lathe and record them with the record_part tool.

Rules:
- Always answer by calling record_part exactly once.
- All dimensions are in millimetres.
- Record only what is written on the drawing. If a value is not visible or not legible, use null. \
Never estimate, compute or guess a missing value.
- blank_diameter / blank_length: only when the drawing states the blank or stock size \
(e.g. a note "Blank: bar Ø55 x 50"). Otherwise null.
- Each external cylindrical section with its own diameter is one od_turn feature (diameter + length). \
A threaded section is recorded twice: as an od_turn at the major diameter over the section length, and as \
a thread (major diameter, pitch, threaded length, thread class as tolerance).
- A groove is recorded with its bottom diameter, the diameter it is cut from (start_diameter) and its width \
(length). A chamfer "1x45°" on a diameter is recorded with that diameter and length 1.
- Internal diameters are bore features.
- Do not add face or parting features unless the drawing explicitly annotates them.
- Tolerances and Ra belong to the feature they are written on. Do not copy a value to other features.
- confidence reflects how legible and unambiguous the feature is on the drawing.
- Put anything unclear, contradictory or not representable into warnings.
"""

USER_PROMPT = "Read this drawing and record the part with the record_part tool."


class ExtractionError(Exception):
    """The drawing could not be turned into valid structured data. `raw` keeps the model response."""

    def __init__(self, message: str, raw: dict | None = None):
        super().__init__(message)
        self.raw = raw


@dataclass
class PreparedImage:
    """The image actually sent to the model."""

    data: bytes
    media_type: str
    width: int
    height: int
    notes: list[str] = field(default_factory=list)


@dataclass
class ExtractionResult:
    data: DrawingData
    raw: dict
    model: str


def detect_file_type(data: bytes) -> str | None:
    """Identify PNG / JPEG / PDF by their file signature ("magic bytes")."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if data.startswith(b"%PDF-"):
        return "pdf"
    return None


def _render(page, long_edge: int) -> pymupdf.Pixmap:
    zoom = long_edge / max(page.rect.width, page.rect.height)
    return page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)


def prepare_image(data: bytes, file_type: str) -> PreparedImage:
    """Turn an uploaded file into one image within the API limits.

    PDF: the first page is rendered to PNG. PNG/JPEG: sent unchanged when small enough,
    otherwise downscaled to MAX_LONG_EDGE_PX and re-encoded as PNG.
    """
    if file_type not in MEDIA_TYPES:
        raise ExtractionError(f"Unsupported file type: {file_type}")
    try:
        doc = pymupdf.open(stream=data, filetype=file_type)
    except Exception as exc:  # pymupdf raises several error types for broken files
        raise ExtractionError(f"Cannot open the {file_type.upper()} file: {exc}") from exc

    with doc:
        if doc.page_count == 0:
            raise ExtractionError("The file has no pages.")
        notes = []
        if file_type == "pdf":
            if doc.page_count > 1:
                notes.append(f"The PDF has {doc.page_count} pages; only page 1 was read.")
            pix = _render(doc[0], MAX_LONG_EDGE_PX)
            image = PreparedImage(pix.tobytes("png"), "image/png", pix.width, pix.height, notes)
        else:
            original = pymupdf.Pixmap(data)
            if max(original.width, original.height) <= MAX_LONG_EDGE_PX:
                image = PreparedImage(data, MEDIA_TYPES[file_type], original.width, original.height, notes)
            else:
                pix = _render(doc[0], MAX_LONG_EDGE_PX)
                notes.append(f"Image downscaled from {original.width}x{original.height} to {pix.width}x{pix.height}.")
                image = PreparedImage(pix.tobytes("png"), "image/png", pix.width, pix.height, notes)

    if len(base64.b64encode(image.data)) > MAX_BASE64_BYTES:
        raise ExtractionError("The image is larger than the 10 MB API limit after conversion.")
    return image


def _raw_dict(response) -> dict:
    """Serialize the API response for the audit log."""
    if hasattr(response, "to_dict"):
        return response.to_dict()
    return json.loads(json.dumps(response, default=lambda o: getattr(o, "__dict__", str(o))))


def parse_response(response) -> DrawingData:
    """Validate the record_part tool call in a Messages API response."""
    raw = _raw_dict(response)
    if response.stop_reason == "refusal":
        raise ExtractionError("The model declined to read this drawing.", raw)
    if response.stop_reason == "max_tokens":
        raise ExtractionError("The model response was cut off (max_tokens).", raw)

    calls = [b for b in response.content if b.type == "tool_use" and b.name == TOOL_NAME]
    if not calls:
        raise ExtractionError("The model did not return structured data (no record_part call).", raw)

    try:
        return DrawingData.model_validate(calls[0].input)
    except ValidationError as exc:
        raise ExtractionError(f"The model output failed validation: {exc}", raw) from exc


def make_client() -> anthropic.Anthropic:
    load_dotenv()  # ANTHROPIC_API_KEY (and optionally ANTHROPIC_MODEL) from .env
    return anthropic.Anthropic()


def extract_drawing(image: PreparedImage, client=None, model: str | None = None) -> ExtractionResult:
    """Send one prepared image to Claude and return the validated part data.

    `client` is injectable so tests can pass a fake; by default a real client is built from .env.
    """
    client = client or make_client()
    model = model or os.environ.get("ANTHROPIC_MODEL") or DEFAULT_MODEL
    image_block = {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": image.media_type,
            "data": base64.standard_b64encode(image.data).decode("ascii"),
        },
    }
    # tool_choice "auto" + an explicit instruction instead of forcing the tool: newer models
    # (e.g. Claude Opus 5.5, Fable 5.1) reject forced tool_choice, and ANTHROPIC_MODEL is configurable.
    response = client.messages.create(
        model=model,
        max_tokens=16000,
        system=SYSTEM_PROMPT,
        tools=[RECORD_PART_TOOL],
        tool_choice={"type": "auto"},
        messages=[{"role": "user", "content": [image_block, {"type": "text", "text": USER_PROMPT}]}],
    )
    data = parse_response(response)
    data.warnings = image.notes + data.warnings
    return ExtractionResult(data=data, raw=_raw_dict(response), model=model)
