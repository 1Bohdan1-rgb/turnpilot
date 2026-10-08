"""A tool maker's catalogue (PDF): its pages' text, finding the cutting data pages, and reading the cutting data of
the pages the operator picked. No value is taken without a page and a quote: see check_reading.
"""
from __future__ import annotations

import json
import re

import pymupdf

from .passport_reader import has_text

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
