"""The machine passport (PDF): its pages' text, the pages that look like technical data, and (next) reading the
machine's data from the pages the operator picked. No value is taken without a page: see machine_spec.
"""
from __future__ import annotations

import re

import pymupdf

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
