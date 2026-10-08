"""The machine passport (PDF): its pages' text, the pages that look like technical data, and reading the machine's
data from the pages the operator picked. No value is taken without a page and a quote: see check_reading.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
from dataclasses import asdict, dataclass, field

import pymupdf

from .machine_spec import MACHINE_FIELDS, parse_value

MIN_TEXT_CHARS = 20  # a page with fewer characters of text is taken as a scan (an image)
# Words that mark the technical data pages (Ukrainian, Russian, English), to help the operator pick pages.
SPEC_WORDS = re.compile(
    r"технічн|характеристик|техническ|specification|technical data|об/хв|об/мин|мин-1|min-1|rpm|квт|kw\b",
    re.IGNORECASE,
)


def page_texts(path) -> list[str]:
    """The text of every page (empty for a scanned page)."""
    with pymupdf.open(path) as doc:
        return [page.get_text("text") for page in doc]


def has_text(text: str) -> bool:
    return len("".join(text.split())) >= MIN_TEXT_CHARS


def likely_spec_page(text: str) -> bool:
    return bool(SPEC_WORDS.search(text))


def parse_pages(text: str, page_count: int) -> list[int]:
    """"3, 4, 10-12" -> [3, 4, 10, 11, 12] (1-based, within the document); ValueError otherwise."""
    pages = set()
    for item in (part.strip() for part in text.replace(";", ",").split(",")):
        if not item:
            continue
        m = re.fullmatch(r"(\d+)\s*[-–]\s*(\d+)", item)
        first, last = (int(m.group(1)), int(m.group(2))) if m else (int(item), int(item)) if item.isdigit() else (0, -1)
        if first < 1 or last < first or last > page_count:
            raise ValueError(f"pages: '{item}' is not within 1–{page_count}")
        pages.update(range(first, last + 1))
    return sorted(pages)


# --- reading the picked pages with the model ------------------------------------------------------------

TOOL_NAME = "record_machine"
MAX_OUTPUT_TOKENS = 8000
PAGE_IMAGE_WIDTH = 1600  # px, a scanned page sent as an image
IMAGE_TOKENS_ESTIMATE = 1600  # per page image, for the estimate shown before the call
PROMPT_TOKENS_ESTIMATE = 2500  # the instructions and the tool schema

SYSTEM_PROMPT = """You read a CNC lathe's passport (the manufacturer's documentation) and record the machine's data.
Rules:
- Record a value only if the given pages state it. Never estimate, derive or guess a value; never take it from
  general knowledge of the machine model. If a value is not stated, set found = false.
- For every value give the page number it is on (the number after "Page" in the input) and a quote copied
  exactly from that page (the line or phrase with the number), as short as possible but containing it.
- Write the value in the unit asked for the field. If the passport uses another unit, convert, and write the
  unit as the passport writes it in unit_as_written.
- power_kw is the continuous (S1) spindle motor power; power_s6_kw the short-time (S6) one. A single main
  motor power without S1 / S6 goes to power_kw.
- max_thread_feed only if the passport states a limit for threading; a general maximum feed of the Z axis goes
  to max_z_feed.
- max_diameter is the largest diameter that can be turned (over the carriage / cross slide if both are given).
- coolant, live_tooling, c_axis: "yes" or "no", only if the passport says so.
"""
USER_PROMPT = f"Record the machine's data from these passport pages with the {TOOL_NAME} tool."


def _field_schema(f) -> dict:
    unit = f" in {f.unit}" if f.unit else ""
    kind = {"int": "a whole number", "float": "a number", "bool": '"yes" or "no"', "text": "the text"}[f.kind]
    return {
        "type": "object",
        "description": f"{f.label}: {kind}{unit}.",
        "properties": {
            "found": {"type": "boolean", "description": "true only if the pages state it"},
            "value": {"type": "string", "description": f"{kind}{unit}; empty if not found"},
            "unit_as_written": {"type": "string", "description": "the unit as the passport writes it; empty if none"},
            "page": {"type": "integer", "description": "the page it is on; 0 if not found"},
            "quote": {"type": "string", "description": "exact words from that page with the value; empty if not found"},
        },
        "required": ["found", "value", "unit_as_written", "page", "quote"],
        "additionalProperties": False,
    }


RECORD_MACHINE_TOOL = {
    "name": TOOL_NAME,
    "description": "Record the machine's data stated on the given passport pages, each with its page and quote.",
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {f.name: _field_schema(f) for f in MACHINE_FIELDS},
        "required": [f.name for f in MACHINE_FIELDS],
        "additionalProperties": False,
    },
}


def prompt_version() -> str:
    """A hash of the instructions and the tool schema, kept with every reading."""
    text = SYSTEM_PROMPT + USER_PROMPT + json.dumps(RECORD_MACHINE_TOOL, sort_keys=True, ensure_ascii=False)
    return "passport:" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]


class PassportReadError(Exception):
    def __init__(self, message, raw=None):
        super().__init__(message)
        self.raw = raw


def _page_image(page) -> bytes:
    zoom = PAGE_IMAGE_WIDTH / page.rect.width
    return page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom)).tobytes("png")


def build_content(path, pages: list[int], with_images: bool = True) -> tuple[list[dict], dict]:
    """The message content for the picked pages (text pages as text, scanned ones as images) and an estimate."""
    content, chars, images = [], 0, 0
    with pymupdf.open(path) as doc:
        for number in pages:
            page = doc[number - 1]
            text = page.get_text("text")
            if has_text(text):
                content.append({"type": "text", "text": f"=== Page {number} ===\n{text}"})
                chars += len(text)
                continue
            images += 1
            content.append({"type": "text", "text": f"=== Page {number} (a scanned image) ==="})
            if with_images:
                data = base64.standard_b64encode(_page_image(page)).decode("ascii")
                content.append({"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": data}})
    content.append({"type": "text", "text": USER_PROMPT})
    estimate = {"calls": 1, "input_tokens": chars // 3 + images * IMAGE_TOKENS_ESTIMATE + PROMPT_TOKENS_ESTIMATE,
                "text_pages": len(pages) - images, "image_pages": images}
    return content, estimate


def estimate(path, pages: list[int]) -> dict:
    """What a reading would send (no call): pages as text / as images, input tokens, calls."""
    return build_content(path, pages, with_images=False)[1]


def _raw_dict(response) -> dict:
    if hasattr(response, "to_dict"):
        return response.to_dict()
    return json.loads(json.dumps(response, default=lambda o: getattr(o, "__dict__", str(o))))


def read_passport(path, pages: list[int], client, model: str) -> tuple[dict, dict]:
    """One call: the tool input (raw values per field) and the raw response for the record."""
    content, _ = build_content(path, pages)
    # tool_choice "auto" with an explicit instruction, as for drawings (newer models reject a forced tool)
    with client.messages.stream(
        model=model, max_tokens=MAX_OUTPUT_TOKENS, system=SYSTEM_PROMPT, tools=[RECORD_MACHINE_TOOL],
        tool_choice={"type": "auto"}, messages=[{"role": "user", "content": content}],
    ) as stream:
        response = stream.get_final_message()
    raw = _raw_dict(response)
    if response.stop_reason == "refusal":
        raise PassportReadError("The model declined to read the passport.", raw)
    if response.stop_reason == "max_tokens":
        raise PassportReadError("The reading did not finish (output token limit).", raw)
    calls = [b for b in response.content if b.type == "tool_use" and b.name == TOOL_NAME]
    if not calls:
        raise PassportReadError(f"The model did not return the data (no {TOOL_NAME} call).", raw)
    return calls[0].input, raw


# --- checking the reading by code ---------------------------------------------------------------------

NUMBER = re.compile(r"\d+(?:[.,]\d+)?")


def _norm(text: str) -> str:
    return " ".join(text.replace(" ", " ").split()).lower()


@dataclass
class PassportValue:
    field: str
    found: bool  # taken as stated in the passport: a page among the picked ones, a quote, a readable value
    value: object = None  # parsed, in the field's unit
    unit_as_written: str = ""
    page: int | None = None
    quote: str = ""
    quote_checked: bool = False  # the code found the quote on that page (text pages only)
    checks: list = field(default_factory=list)  # reasons for a "check" badge, or for not taking the value


def check_reading(tool_input: dict, page_texts_by_number: dict[int, str]) -> list[PassportValue]:
    """Each field's value, checked against the passport: a page among the picked ones, the quote on that page,
    the value in the quote. Without a page or a quote a value is not taken (not in passport)."""
    out = []
    for f in MACHINE_FIELDS:
        item = tool_input.get(f.name) or {}
        v = PassportValue(f.name, False, unit_as_written=(item.get("unit_as_written") or "").strip())
        out.append(v)
        if not item.get("found"):
            continue
        page, quote = item.get("page") or 0, (item.get("quote") or "").strip()
        if page not in page_texts_by_number or not quote:
            v.checks.append("no page among the picked ones or no quote: not taken")
            continue
        try:
            value = parse_value(f, item.get("value"))
        except ValueError as e:
            v.checks.append(f"value not readable ({e}): not taken")
            continue
        if value is None:
            v.checks.append("empty value: not taken")
            continue
        v.found, v.value, v.page, v.quote = True, value, page, quote
        text = page_texts_by_number[page]
        if has_text(text):
            v.quote_checked = _norm(quote) in _norm(text)
            if not v.quote_checked:
                v.checks.append(f"the quote is not on page {page}")
        else:
            v.checks.append("a scanned page: the code cannot check the quote")
        if f.kind in ("int", "float"):
            numbers = [float(n.replace(",", ".")) for n in NUMBER.findall(quote)]
            if not any(abs(n - float(value)) < 1e-9 for n in numbers):
                note = f" (written in {v.unit_as_written})" if v.unit_as_written else ""
                v.checks.append(f"{value:g} is not in the quote{note}: converted or misread?")
    return out


def reading_to_json(values: list[PassportValue]) -> str:
    return json.dumps([asdict(v) for v in values], ensure_ascii=False)


def reading_from_json(text: str) -> list[PassportValue]:
    return [PassportValue(**item) for item in json.loads(text)]
