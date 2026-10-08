"""Import of a hand-typed catalogue file (docs/turnpilot_catalog_*.md): its tables become cutting data rows to
check. Pure: no Flask, no database.

The file's tables are read by their column headers: insert / code, grade, ap, f, Vc and Source. A cell that is
not a number (e.g. "за сплавом", "= крок") gives nothing; "~" marks a value read from a graph. The drill section
gives its Vc in a sentence and its grade in another; both are read with their own patterns.
"""
from __future__ import annotations

import re

CATALOGUES = {
    "TT": "Sandvik Coromant Turning tools 2020",
    "SRT": "Sandvik Coromant Solid round tools 2020",
}
GRAPH_CHECK = "read from a graph: check by eye"
NUMBER = r"\d+(?:[.,]\d+)?"
PAGE = re.compile(r"\b[A-H]\d+(?:\s*[–-]\s*[A-H]?\d+)?\b")
GROUP = re.compile(r"\b([PMKNSH]\d+\.\d+)\b")


def _num(text: str) -> float:
    return float(text.replace(",", "."))


def _numbers(text: str) -> list[float]:
    return [_num(n) for n in re.findall(NUMBER, text)]


def _is_value(cell: str) -> bool:
    """A cell holds numbers (not words like "за сплавом" or a page reference)."""
    return bool(re.match(r"^\s*~?\s*\d", cell))


def parse_triple(cell: str) -> tuple[dict, bool]:
    """"0.6 – 0.96 – 3.6" -> min, rec, max; "~0.07 стартова (діапазон ~0.04–0.14)" -> rec 0.07, min 0.04,
    max 0.14, from a graph; "0.15 – 0.5" -> min, max. Returns ({}, False) for a cell without values."""
    if not _is_value(cell):
        return {}, False
    graph = "~" in cell
    if graph:
        head, _, rest = cell.partition("(")
        values = {"rec": _numbers(head)[0]}
        span = _numbers(rest) if "діапазон" in rest else []
        if len(span) == 2:
            values.update(min=span[0], max=span[1])
        return values, True
    numbers = _numbers(cell.split("(")[0])
    if len(numbers) == 3:
        return dict(min=numbers[0], rec=numbers[1], max=numbers[2]), False
    if len(numbers) == 2:
        return dict(min=numbers[0], max=numbers[1]), False
    if len(numbers) == 1:
        return dict(rec=numbers[0]), False
    return {}, False


def parse_vc(cell: str, header_feeds: list[float]) -> str | None:
    """Vc as Tool.vc_points: "455 / 305 / 215" at the header's feeds, "315 → 140 (f 0.05 → 0.5)", or "195"."""
    cell = re.sub(r"^\s*GC\d{4}\s*:\s*", "", cell)
    if not _is_value(cell):
        return None
    head, _, rest = cell.partition("(")
    feeds = _numbers(rest) if re.search(r"\bf\b", rest) else header_feeds
    values = _numbers(head)
    if len(values) == 1:
        return f"{values[0]:g}"
    if len(values) != len(feeds):
        return None
    return ", ".join(f"{f:g}:{v:g}" for f, v in zip(feeds, values))


def parse_codes(cell: str) -> list[tuple[str, tuple | None]]:
    """The insert / drill codes of a cell, each with its pitch range (threading inserts):
    "266RG-16VM01A001M (крок 1–2) / A002M (1.5–3)" -> [("266RG-16VM01A001M", (1, 2)), ("266RG-16VM01A002M", (1.5, 3))].
    A second code written as its last characters only takes the start of the first."""
    codes = []
    for part in cell.split(" / "):
        name, _, rest = part.strip().partition(" (")
        name = re.sub(r"\s+", "", name)
        if not name:
            continue
        if codes and len(name) < len(codes[0][0]) and name[0].isalpha():
            name = codes[0][0][: -len(name)] + name
        pitch = _numbers(rest)
        codes.append((name, tuple(pitch[:2]) if len(pitch) >= 2 and ("крок" in rest or codes) else None))
    return codes


def parse_grade(cell: str) -> str | None:
    """The grade of a cell: "GC4315 ★ (GC4325 ☆)" -> GC4315; "звірити ★ на A45" -> None (not decided)."""
    m = re.search(r"\b(GC\d{4}|X1BM)\b", cell)
    return m.group(1) if m else None


def parse_source(cell: str) -> tuple[str | None, list[str], list[str]]:
    """(catalogue, pages for ap / f, pages for Vc) from "TT A283 (ap/f), A278–A279 (Vc)". A page without a tag
    goes to both; pages of the grade choice ("★", "код") to neither."""
    m = re.match(r"\s*(TT|SRT)\b", cell)
    catalogue = CATALOGUES[m.group(1)] if m else None
    geometry, vc = [], []
    for segment in re.split(r",(?![^()]*\))", cell):
        tag = re.search(r"\(([^)]*)\)", segment)
        tag = tag.group(1) if tag else ""
        pages = PAGE.findall(segment.split("(")[0])
        if not tag:
            geometry += pages
            vc += pages
        elif "Vc" in tag:
            vc += pages
        elif re.search(r"\bap\b|\bf\b", tag):
            geometry += pages
    return catalogue, geometry, vc


def _application(role: str, section: str) -> str:
    role = role.lower()
    if "канавк" in role:
        return "grooving"
    if "відрізк" in role:
        return "parting"
    if "різьб" in role:
        return "threading"
    if "свердл" in section.lower():
        return "drilling"
    return "turning"


def _columns(header: list[str]) -> dict:
    columns = {}
    for i, name in enumerate(h.strip().lower() for h in header):
        if name.startswith("пластина") or name == "код":
            columns["code"] = i
        elif name.startswith("сплав"):
            columns["grade"] = i
        elif name.startswith("ap"):
            columns["ap"] = i
        elif name.startswith("f,") or name.startswith("f "):
            columns["f"] = i
        elif name.startswith("vc"):
            columns["vc"] = i
            columns["vc_feeds"] = _numbers(name.split("при f")[1]) if "при f" in name else (
                _numbers(name.split("(f")[1]) if "(f" in name else [])
        elif name.startswith("source"):
            columns["source"] = i
        elif name.startswith("роль"):
            columns["role"] = i
    return columns


def _cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _sections(text: str):
    """(heading, lines) of every "## " section."""
    heading, lines = "", []
    for line in text.splitlines():
        if line.startswith("## "):
            if lines:
                yield heading, lines
            heading, lines = line[3:].strip(), []
        else:
            lines.append(line)
    if lines:
        yield heading, lines


def _tables(lines):
    """(header cells, [(line, cells), ...]) of every markdown table in the lines."""
    table = []
    for line in lines + [""]:
        if line.strip().startswith("|"):
            table.append(line)
            continue
        if len(table) >= 3:
            yield _cells(table[0]), [(row.strip(), _cells(row)) for row in table[2:]]
        table = []


def _row(kind, catalogue, group, pages, quote, note, **values):
    return dict(kind=kind, catalogue=catalogue, material_group=group, page=", ".join(dict.fromkeys(pages)) or None,
                quote=quote, note=note, origin="hand_typed", **values)


def parse_hand_typed(text: str, filename: str) -> list[dict]:
    """The cutting data rows of a hand-typed catalogue file (fields of CuttingDataRow, plus "checks").
    Rows giving the same thing with the same values are merged (pages joined); conflicting ones are both kept,
    each with a check."""
    title = next((line for line in text.splitlines() if line.startswith("# ")), "")
    group_match = GROUP.search(title)
    if not group_match:
        raise ValueError(f"{filename}: no material group (e.g. P1.2) in the title")
    group = group_match.group(1)
    coolant = "з охолодженням" in text
    rows = []
    for heading, lines in _sections(text):
        note = f"hand-typed file {filename}, section {heading.split('.')[0]}"
        for header, body in _tables(lines):
            columns = _columns(header)
            if "code" not in columns:
                continue
            for line, cells in body:
                def cell(name):
                    i = columns.get(name)
                    return cells[i] if i is not None and i < len(cells) else ""

                catalogue, geometry_pages, vc_pages = parse_source(cell("source"))
                catalogue = catalogue or CATALOGUES["TT"]
                application = _application(cell("role"), heading)
                ap, ap_graph = parse_triple(cell("ap"))
                f, f_graph = parse_triple(cell("f"))
                for code, pitch in parse_codes(cell("code")):
                    values = {f"ap_{k}": v for k, v in ap.items()} | {f"f_{k}": v for k, v in f.items()}
                    if pitch and not f:
                        values.update(f_min=pitch[0], f_max=pitch[1])  # threading: the feed is the pitch
                    if values:
                        rows.append(_row("geometry", catalogue, group, geometry_pages, line, note, insert_code=code,
                                         from_graph=ap_graph or f_graph, **values))
                grade = parse_grade(cell("grade"))
                vc = parse_vc(cell("vc"), columns.get("vc_feeds", []))
                if grade and vc:
                    rows.append(_row("grade_vc", catalogue, group, vc_pages, line, note, grade=grade,
                                     application=application, vc_points=vc, coolant=coolant or None))
        if "свердл" in heading.lower():
            rows += _drill_grade(heading, "\n".join(lines), group, note, coolant)
    return _merge(rows)


def _drill_grade(heading, body, group, note, coolant) -> list[dict]:
    """The drill section: "Vc для P1.2: **100 – 125 – 150 м/хв** (мін–старт–макс). Source: SRT B70." and
    "Сплав X1BM." give the grade's Vc range and start value."""
    vc = re.search(rf"Vc для {re.escape(group)}:\s*\**\s*({NUMBER})\s*–\s*({NUMBER})\s*–\s*({NUMBER})[^\n]*", body)
    grade = re.search(r"Сплав (\w+)", body)
    if not (vc and grade):
        return []
    source = re.search(r"Source:\s*([^\n]*)", vc.group(0))
    catalogue, _, pages = parse_source(source.group(1) if source else "SRT")
    return [_row("grade_vc", catalogue or CATALOGUES["SRT"], group, pages, vc.group(0).strip(), note,
                 grade=grade.group(1), application="drilling", vc_min=_num(vc.group(1)),
                 vc_points=f"{_num(vc.group(2)):g}", vc_max=_num(vc.group(3)), coolant=coolant or None)]


VALUES = ("ap_min", "ap_rec", "ap_max", "f_min", "f_rec", "f_max", "vc_min", "vc_max", "vc_points")


def _key(row):
    code = re.sub(r"\s+", "", row.get("insert_code") or "").upper()
    return (row["kind"], code, (row.get("grade") or "").upper(), row.get("application"), row["material_group"])


def _merge(rows):
    """One row per key: values that agree (or are missing on one side) are combined, pages and quotes joined.
    Values that disagree keep both rows, each with a check naming the other."""
    merged = []
    for row in rows:
        row.setdefault("from_graph", False)
        row["checks"] = [GRAPH_CHECK] if row["from_graph"] else []
        same = [m for m in merged if _key(m) == _key(row)]
        target = next((m for m in same if all(m.get(k) is None or row.get(k) is None or m.get(k) == row.get(k)
                                              for k in VALUES)), None)
        if target is None:
            for other in same:
                other["checks"].append("another line of the file gives other values: " + row["quote"])
                row["checks"].append("another line of the file gives other values: " + other["quote"])
            merged.append(row)
            continue
        for k in VALUES:
            if target.get(k) is None and row.get(k) is not None:
                target[k] = row[k]
        pages = [p for p in (target.get("page") or "").split(", ") + (row.get("page") or "").split(", ") if p]
        target["page"] = ", ".join(dict.fromkeys(pages)) or None
        if row["quote"] not in target["quote"]:
            target["quote"] += "\n" + row["quote"]
        target["from_graph"] = target["from_graph"] or row["from_graph"]
        target["checks"] = [GRAPH_CHECK] if target["from_graph"] else target["checks"]
    return merged
