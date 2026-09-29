"""Read a lathe part drawing with Claude vision and return validated, structured data.

No Flask here: the web app and tools/eval_extraction.py both call extract_drawing().
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import math
import os
from dataclasses import dataclass, field

import anthropic
import pymupdf
from dotenv import load_dotenv
from pydantic import ValidationError

from . import dimensions_first
from .extraction_schema import RECORD_PART_TOOL, TOOL_NAME, DrawingData

# "features": the model records features with their lengths (default).
# "dimensions_first": experiment, the model transcribes sections and dimensions and the code
# computes the lengths (see dimensions_first.py).
READ_MODES = ("features", "dimensions_first")
DEFAULT_READ_MODE = "features"

DEFAULT_MODEL = "claude-sonnet-5"

log = logging.getLogger(__name__)

# Output budget per reading, thinking included. With adaptive thinking a detailed drawing used all
# of 16000 tokens on thinking before answering, so the budget is 4x that (the model allows 128K).
# A budget this large needs a streaming request to stay within the HTTP timeout.
MAX_OUTPUT_TOKENS = 64000
TOO_COMPLEX_MESSAGE = "Drawing too complex, try again"

# Claude API limits: 8000x8000 px and 10 MB (base64) per image. Current models see up to a
# 2576 px long edge without downscaling, so larger images are downscaled here to that size.
MAX_LONG_EDGE_PX = 2576
MAX_BASE64_BYTES = 10 * 1024 * 1024
# The model also sees at most 4784 visual tokens (one per 28x28 px patch). A larger image is shrunk by
# the API on its side; it is shrunk here instead, so what the model sees is known and stored.
MAX_VISUAL_TOKENS = 4784
PATCH_PX = 28

MEDIA_TYPES = {"png": "image/png", "jpeg": "image/jpeg", "pdf": "application/pdf"}

SYSTEM_PROMPT = """\
You read engineering drawings of parts made on a CNC lathe and record them with the record_part tool.

Rules:
- Always answer by calling record_part exactly once.
- All dimensions are in millimetres.
- Record only what is written on the drawing. If a value is not visible or not legible, use null. \
Never estimate or guess a missing value; the only calculation allowed is the chain/baseline rule below.
- Chain and baseline dimensions: a section length that is not dimensioned directly may be computed from \
chain dimensions or from baseline dimensions measured from a common datum (e.g. 100 - 90 = 10). That is \
reading the drawing, not a guess. Give such a feature confidence 0.8 at most and add the warning \
"length derived from chain dimensions" (name the feature, e.g. "Ø60: length derived from chain dimensions").
- blank_diameter / blank_length: only when the drawing states the blank or stock size \
(e.g. a note "Blank: bar Ø55 x 50"). Otherwise null. A blank named in the title block counts: \
"Круг 29 ГОСТ 2590" or the size designation "29-В1-ГОСТ 2590-2006" means round bar Ø29, so \
blank_diameter = 29.
- Each external cylindrical section with its own diameter is one od_turn feature (diameter + length). \
A threaded section is recorded twice: as an od_turn at the major diameter over the section length, and as \
a thread (major diameter, pitch, threaded length, thread class as tolerance).
- A groove is recorded with its bottom diameter, the diameter it is cut from (start_diameter) and its width \
(length). A chamfer "1x45°" on a diameter is recorded with that diameter and length 1, and with where \
it is: location external (outside diameter) or internal (entrance of a bore or internal thread) and \
the end face (left/right); an internal chamfer at a thread entrance takes the thread's diameter.
- Thread relief: a narrow step (width up to about 5 mm) right next to a thread, with a diameter smaller \
than the thread's minor diameter, is a groove (thread relief groove), not an od_turn. Record it as a \
groove with start_diameter = the thread's major diameter.
- A thread in a hole (internal, e.g. "M14-7H" in a bushing) is recorded as a thread with location \
internal; a thread on a shaft has location external. The class letter agrees: capital (7H) internal, \
small (6g) external.
- A hexagon (wrench flats: "S17", a hexagon in a section or end view, edge lines across a section in the \
main view) is one hex feature: across_flats = the size across flats S, diameter = the diameter across \
corners only if it is dimensioned (else null), length = its axial length. Do not record it as an od_turn \
as well. A Ø on the end view or section of a hex is often on the circle tangent to the flats (the \
chamfer circle): that Ø is S, so record it as across_flats. Record diameter only when the Ø is \
explicitly on the corners.
- A conical section is a taper: start_diameter and diameter are the diameters at its two ends, length \
its axial length. A radius between two sections (R10) is a fillet with radius = 10.
- A diameter dimensioned at the end of a taper is the diameter of that end of the taper (the taper's \
diameter or start_diameter), not a separate od_turn cylinder. Add a cylinder only where the drawing \
shows one.
- A taper never replaces the cylinder next to it. If a diameter has its own dimension or tolerance on a \
cylindrical section (e.g. Ø60 ±0.05), that section is a separate od_turn with its own length and \
tolerance, and the taper is a separate feature that starts from that diameter (start_diameter = 60).
- Internal diameters are bore features. A bore marked THRU (through) runs the full part, so its \
length is the overall length of the part: that is reading the drawing, not a guess. A bore with \
neither THRU nor a length dimension gets length null.
- Do not add face or parting features unless the drawing explicitly annotates them.
- Tolerances and Ra belong to the feature they are written on. Do not copy a value to other features. \
A general tolerance note ("Неуказанные предельные отклонения: H14, h14, ±IT14/2", "ISO 2768-m") goes \
into general_tolerance as written, not into the features.
- A roughness (Ra) symbol on a leader line belongs to the surface the leader's arrow touches, \
not to the nearest dimension or the side of the part where the symbol is placed.
- A roughness symbol without a leader in the top-right corner of the sheet is the general roughness for \
every surface that has no roughness mark of its own. Record it as general_ra; keep each feature's ra for \
marks on that feature only.
- Roughness may be given as Ra or as Rz (GOST drawings often use Rz). Record the value with its \
parameter exactly as written (ra_param / general_ra_param); never convert Rz to Ra.
- confidence reflects how legible and unambiguous the feature is on the drawing.
- Put anything unclear, contradictory or not representable into warnings.
- Keep warnings short: one sentence each, and do not repeat what is already in the recorded fields.
"""

USER_PROMPT = "Read this drawing and record the part with the record_part tool."


def _mode(mode: str) -> dict:
    """Prompts, tool and parser of a reading mode (looked up at call time)."""
    if mode == "features":
        return {"system": SYSTEM_PROMPT, "user": USER_PROMPT, "tool": RECORD_PART_TOOL, "tool_name": TOOL_NAME,
                "parse": DrawingData.model_validate}
    if mode == "dimensions_first":
        return {
            "system": dimensions_first.SYSTEM_PROMPT,
            "user": dimensions_first.USER_PROMPT,
            "tool": dimensions_first.RECORD_DIMENSIONS_TOOL,
            "tool_name": dimensions_first.TOOL_NAME,
            "parse": dimensions_first.parse_tool_input,  # decodes the 0 / "" / -1 markers first
        }
    raise ValueError(f"Unknown reading mode {mode!r}; expected one of {READ_MODES}")


def prompt_version(mode: str = DEFAULT_READ_MODE) -> str:
    """Short hash of everything that shapes the model's answer: prompts and the tool's JSON schema.

    Stored with every extraction and part of the cache key, so a changed prompt or schema (or
    another reading mode) never reuses a result read under the old one.
    """
    spec = _mode(mode)
    payload = json.dumps(
        {"system": spec["system"], "user": spec["user"], "tool": spec["tool"]},
        sort_keys=True, ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


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


def visual_tokens(width: int, height: int) -> int:
    """Visual tokens the model spends on an image: one per started 28x28 px patch."""
    return math.ceil(width / PATCH_PX) * math.ceil(height / PATCH_PX)


def within_limits(width: int, height: int) -> bool:
    return max(width, height) <= MAX_LONG_EDGE_PX and visual_tokens(width, height) <= MAX_VISUAL_TOKENS


def fit_size(width: float, height: float) -> tuple[int, int]:
    """The largest size with the same aspect ratio that is within both limits (never enlarges)."""
    scale = min(
        1.0,
        MAX_LONG_EDGE_PX / max(width, height),
        math.sqrt(MAX_VISUAL_TOKENS * PATCH_PX * PATCH_PX / (width * height)),
    )
    while True:
        fitted = max(1, math.floor(width * scale)), max(1, math.floor(height * scale))
        if within_limits(*fitted):
            return fitted
        scale *= 0.995  # rounding up to whole patches can still exceed the token limit


def _render(page, width_px: int) -> pymupdf.Pixmap:
    """Render a page (or an image opened as a document) at a given pixel width, within the limits."""
    for _ in range(50):
        zoom = width_px / page.rect.width
        pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
        if within_limits(pix.width, pix.height):
            return pix
        width_px = int(width_px * 0.995)  # pymupdf rounds sizes; step down until it fits
    return pix


def prepare_image(data: bytes, file_type: str) -> PreparedImage:
    """Turn an uploaded file into one image within the API limits.

    Limits: 2576 px on the long edge and 4784 visual tokens. PDF: the first page is rendered to PNG
    at the largest size within both. PNG/JPEG: sent unchanged when within both, otherwise
    downscaled (same aspect ratio) and re-encoded as PNG.
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
            rect = doc[0].rect
            to_long_edge = MAX_LONG_EDGE_PX / max(rect.width, rect.height)  # vector: render as large as allowed
            width, _ = fit_size(rect.width * to_long_edge, rect.height * to_long_edge)
            pix = _render(doc[0], width)
            image = PreparedImage(pix.tobytes("png"), "image/png", pix.width, pix.height, notes)
        else:
            original = pymupdf.Pixmap(data)
            if within_limits(original.width, original.height):
                image = PreparedImage(data, MEDIA_TYPES[file_type], original.width, original.height, notes)
            else:
                width, _ = fit_size(original.width, original.height)
                pix = _render(doc[0], width)
                notes.append(
                    f"Image downscaled from {original.width}x{original.height} to {pix.width}x{pix.height} "
                    f"(API limits: {MAX_LONG_EDGE_PX} px long edge, {MAX_VISUAL_TOKENS} visual tokens)."
                )
                image = PreparedImage(pix.tobytes("png"), "image/png", pix.width, pix.height, notes)

    if len(base64.b64encode(image.data)) > MAX_BASE64_BYTES:
        raise ExtractionError("The image is larger than the 10 MB API limit after conversion.")
    return image


def _raw_dict(response) -> dict:
    """Serialize the API response for the audit log."""
    if hasattr(response, "to_dict"):
        return response.to_dict()
    return json.loads(json.dumps(response, default=lambda o: getattr(o, "__dict__", str(o))))


def parse_response(response, mode: str = DEFAULT_READ_MODE) -> DrawingData:
    """Validate the tool call of the reading mode in a Messages API response."""
    spec = _mode(mode)
    raw = _raw_dict(response)
    if response.stop_reason == "refusal":
        raise ExtractionError("The model declined to read this drawing.", raw)
    if response.stop_reason == "max_tokens":
        usage = raw.get("usage") or {}
        # Logged so the budget can be tuned: how much went to thinking, how much was missing.
        log.warning("Drawing reading hit max_tokens (%s): usage=%s", MAX_OUTPUT_TOKENS, json.dumps(usage))
        thinking = (usage.get("output_tokens_details") or {}).get("thinking_tokens")
        detail = f" (thinking: {thinking})" if thinking is not None else ""
        raise ExtractionError(
            f"{TOO_COMPLEX_MESSAGE}. The model used all {usage.get('output_tokens', MAX_OUTPUT_TOKENS)} "
            f"output tokens{detail} without finishing.",
            raw,
        )

    calls = [b for b in response.content if b.type == "tool_use" and b.name == spec["tool_name"]]
    if not calls:
        raise ExtractionError(f"The model did not return structured data (no {spec['tool_name']} call).", raw)

    try:
        return spec["parse"](calls[0].input)
    except ValidationError as exc:
        raise ExtractionError(f"The model output failed validation: {exc}", raw) from exc


def make_client() -> anthropic.Anthropic:
    load_dotenv()  # ANTHROPIC_API_KEY (and optionally ANTHROPIC_MODEL) from .env
    return anthropic.Anthropic()


def extract_drawing(image: PreparedImage, client=None, model: str | None = None,
                    mode: str = DEFAULT_READ_MODE) -> ExtractionResult:
    """Send one prepared image to Claude and return the validated part data.

    `client` is injectable so tests can pass a fake; by default a real client is built from .env.
    """
    spec = _mode(mode)
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
    # Streaming only because of the large max_tokens; the tool input is small, so it is not streamed
    # eagerly and keeps the strict schema validation.
    with client.messages.stream(
        model=model,
        max_tokens=MAX_OUTPUT_TOKENS,
        system=spec["system"],
        tools=[spec["tool"]],
        tool_choice={"type": "auto"},
        messages=[{"role": "user", "content": [image_block, {"type": "text", "text": spec["user"]}]}],
    ) as stream:
        response = stream.get_final_message()
    data = parse_response(response, mode)
    data.warnings = image.notes + data.warnings
    return ExtractionResult(data=data, raw=_raw_dict(response), model=model)


def parse_saved_response(raw: dict, mode: str = DEFAULT_READ_MODE) -> DrawingData:
    """Parse a response saved as a dict (Message.to_dict()) again, e.g. to re-score an eval run."""
    spec = _mode(mode)
    if raw.get("stop_reason") in ("refusal", "max_tokens"):
        raise ExtractionError(f"Saved response stopped with {raw.get('stop_reason')}.", raw)
    block = next(
        (b for b in raw.get("content", []) if b.get("type") == "tool_use" and b.get("name") == spec["tool_name"]),
        None,
    )
    if block is None:
        raise ExtractionError(f"Saved response has no {spec['tool_name']} call.", raw)
    try:
        return spec["parse"](block["input"])
    except ValidationError as exc:
        raise ExtractionError(f"The saved output failed validation: {exc}", raw) from exc
