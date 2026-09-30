"""Numbers written on a drawing PDF as vector text (CAD exports), with their kind and position.

A CAD PDF (e.g. from KOMPAS-3D) carries every dimension as real text, so its numbers can be read
exactly, independently of what the model sees in the image. A scan, a photo or a PDF without text
gives None, and nothing else changes.

Only the numbers are read here; which section a number belongs to (the dimension lines) is not.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

log = logging.getLogger(__name__)

# Symbol fonts of CAD systems draw special signs with ordinary codes. KOMPAS-3D (font Symbol_A):
# "Ç" is the diameter sign, "Å" the degree sign.
SYMBOL_FONTS = {
    "Symbol_A": {"Ç": "Ø", "Å": "°"},
}
# Letters that look alike: a class "7Н" written with a Cyrillic Н is the ISO class 7H.
CYRILLIC_TO_LATIN = str.maketrans("АВЕКМНОРСТХаеорсух", "ABEKMHOPCTXaeopcyx")

# GOST 2.104 title block: 185 x 55 mm in the bottom-right corner; the numbers there (mass, scale, sheet,
# dates) are not dimensions of the part.
TITLE_BLOCK_MM = (185.0, 55.0)
MM = 72 / 25.4

NUMBER = r"\d+(?:[.,]\d+)?"
# a fit (h6, js12) and/or deviations: ±0,05, +0,5/-1,3, 0/-0,016, (±0,175)
TOLERANCE = (r"(?:\s*[a-zA-Z]{1,2}\d{1,2})?"
             r"(?:\s*\(?(?:[±+\-]\s*[\d.,]+(?:\s*/\s*[+\-]?\s*[\d.,]+)?|[\d.,]+\s*/\s*[+\-]\s*[\d.,]+)\)?)?")
PATTERNS = [
    # kind, regex (on the normalized text), groups: value[, second value]
    ("roughness", re.compile(rf"^R[az]\s*({NUMBER})\s*\(?\)?$")),
    ("thread", re.compile(rf"^M({NUMBER})(?:\s*[x×]\s*({NUMBER}))?(?:\s*-\s*\w+)?$")),
    ("chamfer", re.compile(rf"^({NUMBER})\s*[x×]\s*45\s*°?$")),
    ("diameter", re.compile(rf"^Ø\s*({NUMBER}){TOLERANCE}(?:\s*THRU)?$")),
    ("radius", re.compile(rf"^R\s*({NUMBER}){TOLERANCE}$")),
    ("across_flats", re.compile(rf"^S\s*({NUMBER}){TOLERANCE}$")),
    ("groove", re.compile(rf"^Groove\s+({NUMBER})\s*[x×]\s*Ø\s*({NUMBER})$")),  # synthetic test drawings
    ("linear", re.compile(rf"^({NUMBER})\*?{TOLERANCE}$")),
]


@dataclass(frozen=True)
class DrawingNumber:
    kind: str  # linear, diameter, thread, thread_pitch, chamfer, roughness, radius, across_flats
    value: float
    text: str  # the normalized text it was read from
    x: float
    y: float


def _value(text: str) -> float:
    return float(text.replace(",", "."))


def normalize(text: str, font: str = "") -> str:
    """Text of one span with CAD symbol codes, decimal commas, × and Cyrillic look-alikes made plain."""
    for name, table in SYMBOL_FONTS.items():
        if name in font:
            text = "".join(table.get(ch, ch) for ch in text)
    text = text.replace("•", "×").replace("⌀", "Ø").replace("∅", "Ø").replace("−", "-")
    return text.translate(CYRILLIC_TO_LATIN).strip()


def _spans(page):
    """(text, font, bbox, size) of every non-empty span, each drawn copy only once."""
    seen, spans = set(), []
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            for span in line["spans"]:
                text = span["text"].strip()
                key = (text, tuple(round(v, 1) for v in span["bbox"]))
                if text and key not in seen:
                    seen.add(key)
                    spans.append((text, span["font"], span["bbox"], span["size"]))
    return spans


def _in_title_block(bbox, page_width, page_height) -> bool:
    x0, y0, x1, y1 = bbox
    width, height = (v * MM for v in TITLE_BLOCK_MM)
    return x0 >= page_width - width and y0 >= page_height - height


def _attach_symbols(spans):
    """A CAD symbol drawn as its own span (Ø before a number) is joined to the nearest number span."""
    symbols = [s for s in spans if s[0] in ("Ø", "°")]
    others = [s for s in spans if s[0] not in ("Ø", "°")]
    texts = {id(s): s[0] for s in others}
    for symbol, _font, (sx0, sy0, sx1, sy1), size in symbols:
        cx, cy = (sx0 + sx1) / 2, (sy0 + sy1) / 2

        def gap(s):
            x0, y0, x1, y1 = s[2]
            return max(x0 - cx, 0, cx - x1) + max(y0 - cy, 0, cy - y1)

        near = [s for s in others if re.search(r"\d", s[0]) and gap(s) <= size]
        if not near:
            continue
        target = min(near, key=gap)
        texts[id(target)] = "Ø" + texts[id(target)] if symbol == "Ø" else texts[id(target)] + "°"
    return [(texts[id(s)], s[2]) for s in others]


def parse_numbers(text: str, x: float = 0.0, y: float = 0.0) -> list[DrawingNumber]:
    """The numbers of one normalized span, with their kind; [] if it is not a dimension."""
    for kind, pattern in PATTERNS:
        match = pattern.match(text)
        if not match:
            continue
        if kind == "thread":
            numbers = [DrawingNumber("thread", _value(match.group(1)), text, x, y)]
            if match.group(2):
                numbers.append(DrawingNumber("thread_pitch", _value(match.group(2)), text, x, y))
            return numbers
        if kind == "groove":
            return [DrawingNumber("linear", _value(match.group(1)), text, x, y),
                    DrawingNumber("diameter", _value(match.group(2)), text, x, y)]
        return [DrawingNumber(kind, _value(match.group(1)), text, x, y)]
    return []


def read_numbers(path) -> list[DrawingNumber] | None:
    """The dimension numbers on page 1 of a PDF, or None when there is no usable vector text.

    Never raises: an image, a scan, a damaged file or a missing library gives None, and the reading
    pipeline works as without this check.
    """
    if not str(path).lower().endswith(".pdf"):
        return None
    try:
        import pymupdf

        with pymupdf.open(path) as doc:
            page = doc[0]
            width, height = page.rect.width, page.rect.height
            spans = [(normalize(t, f), f, b, s) for t, f, b, s in _spans(page)]
    except Exception:  # noqa: BLE001 - any failure means "no text to check against"
        log.warning("could not read the text of %s", path, exc_info=True)
        return None
    for text, font, _bbox, _size in spans:
        if any(font_name in font for font_name in SYMBOL_FONTS) and text not in ("Ø", "°"):
            log.info("unmapped symbol %r in font %s", text, font)
    numbers = []
    for text, bbox in _attach_symbols(spans):
        if _in_title_block(bbox, width, height):
            continue
        numbers += parse_numbers(text, (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2)
    return numbers or None
