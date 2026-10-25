"""Cutting data rows read from a catalogue page's text, by code (no model, no paid call). Written on the Sandvik
Coromant 2020 pages (TT A278, A283, A285, A289, B130, C73; SRT B76, B80): in-sample for this parser.

Two kinds of page:
- the depth of cut and feed tables (TT A283 …): an insert code, then ap Rec / Min / Max and fn Rec / Min / Max;
  only the codes asked for are taken, as geometry rows of the group's ISO letter (the table names no material);
- the cutting speed tables: the material's row (MC code, CMC, description, kc1, HB) and a Vc per grade column, the
  grades and feeds in the header (turning, grooving / parting, threading); or one grade per row with Vc min-start-max
  (drilling). The material's row is the one of its group whose kc1 is the material's (M1.0: M1.0.Z.AQ, not .PH or
  .C.UT); a table without kc1 takes that MC code from a page that has it.

Every row keeps its page and a quote copied from the page; a value that does not check is not taken (with the
reason). The rows go to the review screen as rows to check: the operator confirms them against the page.
"""
from __future__ import annotations

import re

from .catalogue_tools import DRILL_CODE, page_grade, page_label

MC_CODE = re.compile(r"^([PMKNSH]\d+\.\d+)\.[A-Z]\.[A-Z]{2}$")
ISO_SECTION = re.compile(r"^ISO\s*([PMKNSH])?\s*(?:MC No\.)?$")
CMC = re.compile(r"^\d{2}\.\d{1,2}$")
INT = re.compile(r"^\d+$")
CELL = re.compile(r"^(?:\d+(?:\.\d+)?(?:\s*-\s*\d+(?:\.\d+)?)*|-)$")
GRADE = re.compile(r"^(?:GC|CT|H1|S)\w{2,5}$|^H13A$")
GEOMETRY_HEADER = ("Rec", "Min", "Max.", "Rec", "Min", "Max.")
APPLICATIONS = (("THREAD TURNING", ("threading",)), ("PARTING AND GROOVING", ("grooving", "parting")),
                ("GENERAL TURNING", ("turning",)), ("DRILLING", ("drilling",)))
ORIGIN = "page_text"


def _lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


def _numbers(cell: str) -> list[float]:
    return [float(n) for n in re.findall(r"\d+(?:\.\d+)?", cell)]


def _squeeze(code: str) -> str:
    return re.sub(r"\s+", "", code).upper()


def _ordered(values) -> bool:
    return list(values) == sorted(values)


def applications(text: str) -> tuple[str, ...]:
    head = " ".join(_lines(text)[:12]).upper()
    for words, apps in APPLICATIONS:
        if words in head:
            return apps
    return ()


# --- ap / f of insert codes ------------------------------------------------------------------------------------------

def geometry_rows(text: str, pdf_page: int, codes, group: str) -> tuple[list[dict], list[dict]]:
    """(rows, not taken) of the codes asked for found on a depth of cut and feed page. The header must give the
    columns as Rec / Min / Max (ap) then Rec / Min / Max (fn)."""
    lines = _lines(text)
    if not any(tuple(lines[i:i + 6]) == GEOMETRY_HEADER for i in range(len(lines))):
        return [], []
    wanted = {_squeeze(c) for c in codes if c and c.strip()}
    rows, not_taken = [], []
    for i, line in enumerate(lines):
        code = _squeeze(line)
        if code not in wanted:
            continue
        cells = lines[i + 1:i + 7]
        quote = " ".join([line] + cells)
        if len(cells) < 6 or not all(re.fullmatch(r"\d+(?:\.\d+)?", c) for c in cells):
            not_taken.append({"code": code, "pdf_page": pdf_page, "reason": "not six numbers after the code: not taken",
                              "quote": quote})
            continue
        ap_rec, ap_min, ap_max, f_rec, f_min, f_max = (float(c) for c in cells)
        if not (_ordered((ap_min, ap_rec, ap_max)) and _ordered((f_min, f_rec, f_max))):
            not_taken.append({"code": code, "pdf_page": pdf_page, "quote": quote,
                              "reason": "min / rec / max are not in order: not taken"})
            continue
        rows.append(dict(kind="geometry", insert_code=code, material_group=group[:1].upper(), ap_min=ap_min,
                         ap_rec=ap_rec, ap_max=ap_max, f_min=f_min, f_rec=f_rec, f_max=f_max, page=page_label(text),
                         pdf_page=pdf_page, quote=quote, checks=[]))
    return rows, not_taken


# --- Vc of grades ----------------------------------------------------------------------------------------------------

def _header(lines, start):
    """(grades, feeds) of a grade-column header starting at `start`: the grades after "WEAR RESISTANCE" or "Grades",
    then as many feed cells after "feed fn" (none in a table without feeds, e.g. threading)."""
    grades, i = [], start
    while i < len(lines) and GRADE.match(lines[i]):
        grades.append(lines[i])
        i += 1
    feeds = []
    if i < len(lines) and "feed fn" in lines[i]:
        feeds = lines[i + 1:i + 1 + len(grades)]
    return grades, feeds


def material_rows(text: str) -> list[dict]:
    """Every material row of a cutting speed page: MC code, kc1, HB, and its cells with the header's grades and
    feeds (grade-column tables) or its own grade (drilling: one grade per row, Vc min-start-max)."""
    lines = _lines(text)
    out, grades, feeds, inch = [], [], [], False
    for i, line in enumerate(lines):
        if "m/min" in line or "ft/min" in line:  # a table in inch values (ft/min) repeats the metric one: left out
            inch = "ft/min" in line
        if line.endswith("WEAR RESISTANCE") or line.endswith("Grades"):
            grades, feeds = _header(lines, i + 1)
            continue
        if not MC_CODE.match(line) or inch:
            continue
        j = i + 1
        cmc = lines[j] if j < len(lines) and CMC.match(lines[j]) else None
        j += 1 if cmc else 0
        while j < len(lines) and not INT.match(lines[j]) and not MC_CODE.match(lines[j]):
            j += 1  # the description
        ints = []
        while j < len(lines) and INT.match(lines[j]):
            ints.append(int(lines[j]))
            j += 1
        row = {"mc": line, "group": MC_CODE.match(line).group(1), "cmc": cmc, "index": i}
        if grades:  # kc1, HB, then a cell per grade (single Vc values are INT lines too)
            cells = [str(n) for n in ints[2:]]
            while j < len(lines) and len(cells) < len(grades) and CELL.match(lines[j]):
                cells.append(lines[j])
                j += 1
            row.update(kc1=ints[0] if ints else None, hb=ints[1] if len(ints) > 1 else None,
                       cells=list(zip(grades, feeds or [None] * len(grades), cells[:len(grades)])),
                       complete=len(cells) == len(grades) and (not feeds or len(feeds) == len(grades)))
        else:  # HB [, grade], then "min - start - max"
            cell = lines[j] if j < len(lines) and CELL.match(lines[j]) and "-" in lines[j] else None
            j += 1 if cell else 0
            row.update(kc1=None, hb=ints[0] if ints else None, cells=[(str(ints[1]) if len(ints) > 1 else None, None,
                                                                         cell)] if cell else [], complete=bool(cell))
        row["quote"] = " ".join(lines[i:j])
        out.append(row)
    return out


def full_code(sources, group: str, kc1s: set[float]) -> tuple[str | None, str]:
    """(MC code, where) of the material's row (e.g. "M1.0.Z.AQ"), from the tables that give kc1: the rows of the group
    whose kc1 is the material's. sources: (catalogue, {pdf page: text}), the first that has such a row decides.
    (None, why) when there is none or more than one."""
    for title, pages in sources:
        found = {}
        for n, text in pages.items():
            for r in material_rows(text) if applications(text) else []:
                if r["group"] == group and r["kc1"] is not None and r["kc1"] in kc1s:
                    found.setdefault(r["mc"], f"{title} {page_label(text)} (PDF {n})")
        if len(found) == 1:
            mc, where = found.popitem()
            return mc, where
        if found:
            return None, f"several {group} rows with the material's kc1: {', '.join(sorted(found))}"
    return None, f"no {group} row with kc1 {', '.join(f'{k:g}' for k in sorted(kc1s)) or '(not set)'} in the catalogues"


def _vc_points(feed: str | None, cell: str) -> dict | None:
    vcs = _numbers(cell)
    if not vcs or any(v <= 0 for v in vcs):
        return None
    if feed is None:
        if len(vcs) == 1:
            return {"vc_points": f"{vcs[0]:g}"}
        if len(vcs) == 3 and _ordered(vcs):  # min - start - max
            return {"vc_min": vcs[0], "vc_max": vcs[2], "vc_points": f"{vcs[1]:g}"}
        return None
    feeds = _numbers(feed)
    if len(feeds) != len(vcs) or feeds != sorted(set(feeds)):
        return None
    return {"vc_points": ", ".join(f"{f:g}:{v:g}" for f, v in zip(feeds, vcs))}


def vc_rows(text: str, pdf_page: int, group: str, mc: str, hb: int | None,
            drill_grade: tuple[str, str] | None = None, mc_where: str = "") -> tuple[list[dict], list[dict]]:
    """(rows, not taken) of the material's row `mc` on a cutting speed page: a grade_vc row per grade and application.
    A grade that has no value ("-") is left out without a reason. drill_grade: (grade, where) for a drilling table
    that names no grade (SRT B80: the grade is on the series' product pages)."""
    apps = applications(text)
    rows, not_taken = [], []
    if not apps:
        return rows, not_taken
    coolant = True if ("valid for use with cutting fluid" in text or "coolant supply" in text) else None
    for r in material_rows(text):
        if r["mc"] != mc:
            continue
        if not r["complete"]:
            not_taken.append({"code": mc, "pdf_page": pdf_page, "quote": r["quote"],
                              "reason": "the row's cells do not match the header's grades: not taken"})
            continue
        for grade, feed, cell in r["cells"]:
            checks = []
            if grade is None and drill_grade:
                grade, where = drill_grade
                checks.append(f"grade {grade} from {where}: not on this page")
            if cell == "-":
                continue
            values = _vc_points(feed, cell)
            if not grade or values is None:
                not_taken.append({"code": f"{mc} {grade or '(no grade)'}", "pdf_page": pdf_page, "quote": r["quote"],
                                  "reason": "no grade or Vc that checks: not taken"})
                continue
            if hb is not None and r["hb"] is not None and r["hb"] != hb:
                checks.append(f"HB {r['hb']} in the table, the material's {hb}")
            for app in apps:
                rows.append(dict(kind="grade_vc", grade=grade, application=app, material_group=group, coolant=coolant,
                                 page=page_label(text), pdf_page=pdf_page, quote=r["quote"],
                                 checks=checks + [_row_said(mc, r, mc_where)], **values))
    return rows, not_taken


def _row_said(mc, row, mc_where) -> str:
    said = f"row {mc}" + (f" (CMC {row['cmc']})" if row["cmc"] else "")
    if row["kc1"] is None and mc_where:
        said += f": the table gives no kc1, the row is the one with the material's kc1 on {mc_where}"
    return said


def drill_series_grade(texts: list[str], geometry: str) -> tuple[str, str] | None:
    """(grade, "PDF a–b") when every product page of drills of this geometry (e.g. "MM": 860.1-…-MM) gives the same
    one grade column; else None."""
    pages = {n: page_grade(t)[0] for n, t in enumerate(texts, start=1)
             if t and any(m.group(5) == geometry for m in DRILL_CODE.finditer(t)) and page_grade(t)[0]}
    grades = set(pages.values())
    if len(grades) != 1:
        return None
    return grades.pop(), f"the {geometry} drill pages (PDF {min(pages)}–{max(pages)})"


def drill_geometry(text: str) -> str | None:
    """The drill geometry a drilling cutting data page is for ("CoroDrill® 860-MM" -> "MM"), when it names one."""
    found = set(re.findall(r"CoroDrill®\s*\d{3}-([A-Z]{2})", text))
    return found.pop() if len(found) == 1 else None


def read(texts: list[str], pages: list[int], groups: str, codes: str | None, materials, title: str = "",
         others=()) -> tuple[list[dict], list[dict]]:
    """(rows, not taken) of the picked pages (1-based) for the groups asked for. `materials`: (group, kc1, HB) of the
    materials, to find their row in the tables; others: (catalogue, page texts) of the other catalogues, for a table
    without kc1 (SRT: the row is found by kc1 in TT)."""
    picked = {n: texts[n - 1] for n in pages}
    sources = [(title, picked), (title, dict(enumerate(texts, start=1)))]
    sources += [(t, dict(enumerate(other, start=1))) for t, other in others]
    wanted_codes = [c for c in re.split(r"[\n,]", codes or "") if c.strip()]
    rows, not_taken = [], []
    for group in [g.strip() for g in groups.split(",") if g.strip()]:
        kc1s = {m[1] for m in materials if m[0] == group and m[1]}
        hbs = {m[2] for m in materials if m[0] == group and m[2]}
        hb = hbs.pop() if len(hbs) == 1 else None
        mc, why = full_code(sources, group, kc1s)
        for n, text in picked.items():
            got, lost = geometry_rows(text, n, wanted_codes, group)
            rows += got
            not_taken += lost
            if not applications(text) or not any(r["group"] == group for r in material_rows(text)):
                continue
            if mc is None:
                not_taken.append({"code": group, "pdf_page": n, "quote": "", "reason": why + ": not taken"})
                continue
            geometry = drill_geometry(text)
            grade = drill_series_grade(texts, geometry) if geometry else None
            got, lost = vc_rows(text, n, group, mc, hb, grade, why)
            rows += got
            not_taken += lost
    return rows, not_taken
