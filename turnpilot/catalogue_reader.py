"""A tool maker's catalogue (PDF): its pages' text, finding the cutting data pages, and reading the cutting data of
the pages the operator picked. No value is taken without a page and a quote: see check_reading.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re

import pymupdf

from .passport_reader import _raw_dict, has_text

# Words of a cutting data page (Sandvik and others, English / Ukrainian / Russian), to help the operator pick pages.
CUTTING_DATA_WORDS = re.compile(r"m/min|м/хв|м/мин|cutting speed|cutting data|режим", re.IGNORECASE)
FEED_DEPTH_WORDS = re.compile(r"\bfn\b|\bap\b|mm/r|мм/об|feed|подач", re.IGNORECASE)


def read_pages(path) -> dict:
    """The text and the printed label (e.g. "A283", empty when the PDF has none) of every page."""
    texts, labels = [], []
    with pymupdf.open(path) as doc:
        for page in doc:
            texts.append(page.get_text("text"))
            labels.append(page.get_label() or "")
    return {"texts": texts, "labels": labels}


def save_pages(pages: dict, path) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(pages, f, ensure_ascii=False)


def load_pages(path) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def likely_cutting_data_page(text: str) -> bool:
    return bool(CUTTING_DATA_WORDS.search(text) and FEED_DEPTH_WORDS.search(text))


def _squeeze(text: str) -> str:
    return re.sub(r"\s+", "", text).lower()


def search(pages: dict, query: str, limit: int = 50) -> list[int]:
    """The pages (1-based) whose text or label contains the query; spaces are ignored, so "CNMG 12 04 08-PM" finds
    "CNMG120408-PM" too."""
    needle = _squeeze(query)
    if not needle:
        return []
    found = []
    for number, (text, label) in enumerate(zip(pages["texts"], pages["labels"]), start=1):
        if needle in _squeeze(text) or needle == _squeeze(label):
            found.append(number)
            if len(found) >= limit:
                break
    return found


def snippet(text: str, query: str = "", width: int = 300) -> str:
    """A piece of the page's text around the query (the start of the page without one)."""
    flat = " ".join(text.split())
    if query:
        m = re.search(r"\s*".join(map(re.escape, re.sub(r"\s+", "", query))), flat, re.IGNORECASE)
        if m:
            start = max(0, m.start() - width // 3)
            return ("…" if start else "") + flat[start:start + width] + ("…" if start + width < len(flat) else "")
    return flat[:width] + ("…" if len(flat) > width else "")


def resolve_pages(text: str, pages: dict) -> list[int]:
    """"280-282, A285" -> PDF page numbers: numbers are PDF pages, anything else a printed label. ValueError
    for a label that is not in the PDF or a number outside it."""
    count = len(pages["texts"])
    result = set()
    for item in (part.strip() for part in text.replace(";", ",").split(",")):
        if not item:
            continue
        m = re.fullmatch(r"(\d+)\s*[-–]\s*(\d+)", item)
        if m or item.isdigit():
            first, last = (int(m.group(1)), int(m.group(2))) if m else (int(item), int(item))
            if first < 1 or last < first or last > count:
                raise ValueError(f"pages: '{item}' is not within 1–{count}")
            result.update(range(first, last + 1))
            continue
        matches = [i for i, label in enumerate(pages["labels"], start=1) if _squeeze(label) == _squeeze(item)]
        if not matches:
            raise ValueError(f"pages: no page is labelled '{item}' in this PDF (write its PDF page number)")
        result.update(matches)
    return sorted(result)


def page_has_text(pages: dict, number: int) -> bool:
    return has_text(pages["texts"][number - 1])


# --- reading the picked pages with the model ------------------------------------------------------------

TOOL_NAME = "record_cutting_data"
MAX_OUTPUT_TOKENS = 16000
PAGE_IMAGE_WIDTH = 1600  # px; every page is also sent as an image: the text layer loses the tables' layout
IMAGE_TOKENS_ESTIMATE = 1600  # per page image, for the estimate shown before the call
PROMPT_TOKENS_ESTIMATE = 3000  # the instructions and the tool schema
APPLICATIONS = ("turning", "grooving", "parting", "threading", "drilling")

SYSTEM_PROMPT = """You read pages of a cutting tool maker's catalogue and record its cutting data as rows.
Each page is given as its text layer and as an image; the image shows the tables' layout.
Two kinds of rows:
- geometry: the depth of cut ap and the feed f (min, recommended or starting value, max) of an insert geometry or a
  drill, by its ordering code (e.g. CNMG 12 04 08-PM, 860.1-0600-016A0-GM). material_group is the ISO letter (P, M,
  K, N, S, H) or the material group the values are given for.
- grade_vc: the cutting speed Vc of a grade (e.g. GC4325) for an application (turning, grooving, parting,
  threading, drilling) and a material group (e.g. P1.2): Vc at the feeds the table gives (vc_points), and/or a
  range vc_min .. vc_max with a recommended (starting) value as the one point of vc_points (f = 0).
Rules:
- Record a value only if the given pages state it as a number. Never estimate, interpolate, derive or guess; never
  take it from general knowledge of the catalogue. A value not stated is null.
- A value that can only be read off a graph or a chart: set from_graph = true and give the values you see; they
  are not used, the operator enters them by hand.
- Record only the material groups asked for (a geometry row may be for the ISO letter of one of them) and, when a
  list of codes and grades is given, only those.
- For every row give the PDF page number it is on (the number after "Page" in the input), the page label as
  printed on the page (e.g. A283) if there is one, and a quote copied exactly from the page's text layer: the row
  or line of the table with the numbers, as short as possible but containing them.
- Feeds in mm/rev, ap in mm, Vc in m/min. coolant: "yes" if the table says its values are with coolant (wet), "no"
  if dry, else "not_stated".
"""


def user_prompt(material_groups: str, codes: str | None) -> str:
    codes_list = ", ".join(c.strip() for c in (codes or "").splitlines() if c.strip())
    only = f" Only these codes and grades: {codes_list}." if codes_list else ""
    return (f"Record the cutting data of these catalogue pages with the {TOOL_NAME} tool. "
            f"Material groups: {material_groups}.{only}")


def _nullable_number(description: str) -> dict:
    return {"anyOf": [{"type": "number"}, {"type": "null"}], "description": description}


_ROW_SCHEMA = {
    "type": "object",
    "properties": {
        "kind": {"type": "string", "enum": ["geometry", "grade_vc"]},
        "insert_code": {"type": "string", "description": "geometry: the insert or drill code; else empty"},
        "grade": {"type": "string", "description": "grade_vc: the grade; else empty"},
        "application": {"type": "string", "enum": list(APPLICATIONS) + ["none"],
                        "description": "grade_vc: what the Vc table is for; geometry: none"},
        "material_group": {"type": "string", "description": "e.g. P1.2, or an ISO letter for geometry"},
        "ap_min": _nullable_number("mm"), "ap_rec": _nullable_number("mm, recommended / starting"),
        "ap_max": _nullable_number("mm"),
        "f_min": _nullable_number("mm/rev"), "f_rec": _nullable_number("mm/rev, recommended / starting"),
        "f_max": _nullable_number("mm/rev"),
        "vc_min": _nullable_number("m/min"), "vc_max": _nullable_number("m/min"),
        "vc_points": {
            "type": "array", "description": "Vc at the feeds of the table; f = 0 for one recommended Vc",
            "items": {"type": "object", "properties": {"f": {"type": "number"}, "vc": {"type": "number"}},
                      "required": ["f", "vc"], "additionalProperties": False},
        },
        "coolant": {"type": "string", "enum": ["yes", "no", "not_stated"]},
        "from_graph": {"type": "boolean"},
        "pdf_page": {"type": "integer"},
        "page_label": {"type": "string"},
        "quote": {"type": "string"},
    },
    "required": ["kind", "insert_code", "grade", "application", "material_group", "ap_min", "ap_rec", "ap_max",
                 "f_min", "f_rec", "f_max", "vc_min", "vc_max", "vc_points", "coolant", "from_graph", "pdf_page",
                 "page_label", "quote"],
    "additionalProperties": False,
}

RECORD_CUTTING_DATA_TOOL = {
    "name": TOOL_NAME,
    "description": "Record the cutting data rows stated on the given catalogue pages, each with its page and quote.",
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {"rows": {"type": "array", "items": _ROW_SCHEMA}},
        "required": ["rows"],
        "additionalProperties": False,
    },
}


def prompt_version() -> str:
    """A hash of the instructions and the tool schema, kept with every reading."""
    text = SYSTEM_PROMPT + user_prompt("{groups}", "{codes}") + json.dumps(RECORD_CUTTING_DATA_TOOL, sort_keys=True)
    return "catalogue:" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]


class CatalogueReadError(Exception):
    def __init__(self, message, raw=None):
        super().__init__(message)
        self.raw = raw


def build_content(path, pages: list[int], material_groups: str, codes: str | None,
                  with_images: bool = True) -> tuple[list[dict], dict]:
    """The message content (each page as text and as an image) and an estimate of the input tokens."""
    content, chars = [], 0
    with pymupdf.open(path) as doc:
        for number in pages:
            page = doc[number - 1]
            text = page.get_text("text")
            label = page.get_label() or ""
            head = f"=== Page {number}" + (f" (printed label {label})" if label else "") + " ==="
            content.append({"type": "text", "text": f"{head}\n{text}" if has_text(text) else f"{head}\n(no text layer)"})
            chars += len(text)
            if with_images:
                zoom = PAGE_IMAGE_WIDTH / page.rect.width
                data = base64.standard_b64encode(page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom)).tobytes("png"))
                content.append({"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                                            "data": data.decode("ascii")}})
    content.append({"type": "text", "text": user_prompt(material_groups, codes)})
    estimate = {"calls": 1, "pages": len(pages), "max_output_tokens": MAX_OUTPUT_TOKENS,
                "input_tokens": chars // 3 + len(pages) * IMAGE_TOKENS_ESTIMATE + PROMPT_TOKENS_ESTIMATE}
    return content, estimate


def estimate(path, pages: list[int], material_groups: str, codes: str | None) -> dict:
    return build_content(path, pages, material_groups, codes, with_images=False)[1]


def read_catalogue(path, pages: list[int], material_groups: str, codes: str | None, client,
                   model: str) -> tuple[dict, dict]:
    """One call: the tool input (rows as the model gives them) and the raw response for the record."""
    content, _ = build_content(path, pages, material_groups, codes)
    with client.messages.stream(
        model=model, max_tokens=MAX_OUTPUT_TOKENS, system=SYSTEM_PROMPT, tools=[RECORD_CUTTING_DATA_TOOL],
        tool_choice={"type": "auto"}, messages=[{"role": "user", "content": content}],
    ) as stream:
        response = stream.get_final_message()
    raw = _raw_dict(response)
    if response.stop_reason == "refusal":
        raise CatalogueReadError("The model declined to read the catalogue.", raw)
    if response.stop_reason == "max_tokens":
        raise CatalogueReadError("The reading did not finish (output token limit).", raw)
    calls = [b for b in response.content if b.type == "tool_use" and b.name == TOOL_NAME]
    if not calls:
        raise CatalogueReadError(f"The model did not return the data (no {TOOL_NAME} call).", raw)
    return calls[0].input, raw


# --- checking the reading by code ---------------------------------------------------------------------

NUMBER = re.compile(r"(?<![\d.,])\d+(?:[.,]\d+)?(?!\d)")
GRAPH_NOT_TAKEN = "read from a graph: not taken, enter it by hand"
NUMBER_FIELDS = ("ap_min", "ap_rec", "ap_max", "f_min", "f_rec", "f_max", "vc_min", "vc_max")


def _norm(text: str) -> str:
    return " ".join(text.replace(" ", " ").split()).lower()


def page_numbers(text: str) -> set[float]:
    """Every number written on the page (a decimal comma read as a point; ".3" as 0.3)."""
    numbers = {float(n.replace(",", ".")) for n in NUMBER.findall(text)}
    numbers |= {float("0" + n) for n in re.findall(r"(?<![\d.])\.\d+", text)}
    return numbers


def _wanted_codes(codes: str | None) -> set[str]:
    return {re.sub(r"\s+", "", c).upper() for c in (codes or "").splitlines() if c.strip()}


def _wanted_groups(material_groups: str) -> set[str]:
    return {g.strip().upper() for g in (material_groups or "").split(",") if g.strip()}


def _values(item) -> dict:
    return {k: item.get(k) for k in NUMBER_FIELDS if item.get(k) is not None}


def check_reading(tool_input: dict, page_texts: dict[int, str], material_groups: str,
                  codes: str | None) -> tuple[list[dict], list[dict]]:
    """(rows taken, as CuttingDataRow fields plus "checks"; rows not taken, with the reason). A row is taken with a
    picked page and a quote, its values in order (min <= rec <= max, feeds rising), within the groups and codes asked
    for and not read off a graph. The quote and every number are looked for in the page's text: what is not found
    gets a check."""
    groups, wanted = _wanted_groups(material_groups), _wanted_codes(codes)
    taken, not_taken = [], []
    for item in tool_input.get("rows") or []:
        reason = _reject(item, page_texts, groups, wanted)
        if reason:
            not_taken.append({"row": item, "reason": reason})
        else:
            taken.append(_taken_row(item, page_texts[item["pdf_page"]]))
    return taken, not_taken


def _reject(item, page_texts, groups, wanted) -> str | None:
    if item.get("from_graph"):
        return GRAPH_NOT_TAKEN
    if item.get("pdf_page") not in page_texts or not (item.get("quote") or "").strip():
        return "no page among the picked ones or no quote: not taken"
    group = (item.get("material_group") or "").strip().upper()
    if item.get("kind") == "geometry":
        key = item.get("insert_code") or ""
        if not key.strip():
            return "no insert code: not taken"
        if group not in groups and group not in {g[:1] for g in groups}:
            return f"material group '{group}' was not asked for: not taken"
    else:
        key = item.get("grade") or ""
        if not key.strip() or item.get("application") not in APPLICATIONS:
            return "no grade or application: not taken"
        if group not in groups:
            return f"material group '{group}' was not asked for: not taken"
    if wanted and re.sub(r"\s+", "", key).upper() not in wanted:
        return f"{key} is not in the list to record: not taken"
    values, points = _values(item), item.get("vc_points") or []
    if not values and not points:
        return "no values: not taken"
    if any(v <= 0 for v in values.values()) or any(p["f"] < 0 or p["vc"] <= 0 for p in points):
        return "a value is not positive: not taken"
    for name in ("ap", "f", "vc"):
        chain = [item.get(f"{name}_{k}") for k in ("min", "rec", "max") if item.get(f"{name}_{k}") is not None]
        if chain != sorted(chain):
            return f"{name} min / rec / max are not in order: not taken"
    feeds = [p["f"] for p in points]
    if feeds != sorted(set(feeds)) or (0 in feeds and len(feeds) > 1):
        return "Vc points: the feeds do not rise: not taken"
    return None


def _taken_row(item, text) -> dict:
    checks = []
    geometry = item["kind"] == "geometry"
    values = {k: v for k, v in _values(item).items() if not (geometry and k.startswith("vc"))}
    points = sorted((p["f"], p["vc"]) for p in item.get("vc_points") or []) if not geometry else []
    if not has_text(text):
        checks.append("a page without text: the code cannot check the quote or the numbers")
    else:
        if _norm(item["quote"]) not in _norm(text):
            checks.append(f"the quote is not on page {item['pdf_page']}")
        on_page = page_numbers(text)
        numbers = list(values.values()) + [v for point in points for v in point if v]
        missing = [n for n in numbers if not any(abs(n - m) < 1e-9 for m in on_page)]
        if missing:
            checks.append("not on the page: " + ", ".join(f"{n:g}" for n in dict.fromkeys(missing)))
    if len(points) == 1 and points[0][0] == 0:
        vc_points = f"{points[0][1]:g}"
    else:
        vc_points = ", ".join(f"{f:g}:{vc:g}" for f, vc in points) or None
    return dict(
        kind=item["kind"], insert_code=re.sub(r"\s+", "", item["insert_code"]) if geometry else None,
        grade=None if geometry else item["grade"].strip(), application=None if geometry else item["application"],
        material_group=item["material_group"].strip(), vc_points=vc_points,
        coolant={"yes": True, "no": False}.get(item.get("coolant")),
        page=(item.get("page_label") or "").strip() or None, pdf_page=item["pdf_page"],
        quote=item["quote"].strip(), checks=checks, **values,
    )
