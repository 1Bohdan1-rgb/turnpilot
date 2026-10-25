"""Tools found in an uploaded catalogue's text layer (no model, no paid call): a drill by its Ø and depth, a boring bar
by the smallest bore it enters and its overhang, a grooving insert by its width and depth.

A product table's text runs in its columns' order: the header words between "Dimensions" and "Ordering code", then
row after row, each row's values before the ordering code in the headers' order. The values are taken only where the
row checks against its own code (a value written twice must agree), else the row is not taken:
- a drill (CoroDrill 860 / 460 style, "860.1-2000-055A1-PM"): DC = the code's four digits / 100, and DC" / LU" (inch)
  = DC / LU ÷ 25.4; then DC (Ø), LU (the usable length: the deepest hole), ULDR (× D);
- a boring bar ("A16R-SCLCR/L 09-R"): CZCMS = the bar's Ø in the code; then DMIN1 (the smallest bore), OHX / OHN (the
  longest / shortest overhang);
- a grooving insert ("N123E2-0200-0002-CM"): the row starts with its seat letter (SSC = the code's letter) and CW = the
  code's width digits / 100; then CW (width) and CDX (the deepest cut);
- a turning insert ("CNMG 12 04 08-MM", the T-Max P / CoroTurn 107 tables): the numbers just before the code are LE,
  S, RE (and BS for a wiper); S must be the code's thickness (04 = 4.76) and RE its nose radius (08 = 0.8 ± 0.02). The
  page's ★ / ✩ grade marks lose their columns in the text: the grade is the operator's.
A value of the page (the drill's point angle SIG, the holder's entering angle KAPR, the coolant supply) is taken only
when the page states one. Every value keeps its quote and page; what does not check is "not found", with the reason.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

INCH = 25.4
INCH_TOL = 0.0015  # the inch values are printed to three decimals
MARKS = {"★", "✩", "☆"}
NUMBER = re.compile(r"^-?\d*[.,]?\d+$")
LABEL = re.compile(r"^\s*([A-H])\s?(\d{1,3})\s*$")

DRILL_CODE = re.compile(r"\b([48]60\.\d)-(\d{4})-(\d{3})(A\d)-([A-Z]{2})\b")
BAR_CODE = re.compile(r"\b([A-F])(\d{2})([A-Z])-(S[A-Z]{3}[RL](?:/[RL])?) (\d{2})-([RL])\b")
GROOVE_CODE = re.compile(r"\b([LNR]123)([A-Z])(\d)-(\d{4})-(\d{4})-([A-Z]{2})\b")
INSERT_CODE = re.compile(r"\b([CDSTVW][A-Z][GM][A-Z]) (\d{2}) (\d{2}|T\d) (\d{2})-([A-Z]{1,3}\d?)\b")
# ISO 1832 thickness code -> S, mm (T3 = 3.97)
INSERT_THICKNESS = {"01": 1.59, "T1": 1.98, "02": 2.38, "T2": 2.78, "03": 3.18, "T3": 3.97, "04": 4.76, "05": 5.56,
                    "06": 6.35, "07": 7.94, "09": 9.52}
NOSE_TOL = 0.02  # "08" is printed as 0.79 or 0.8


def _num(token: str) -> float | None:
    token = token.replace(",", ".")
    return float(token) if NUMBER.match(token) else None


@dataclass
class Found:
    kind: str  # "drilling" / "boring" / "grooving"
    code: str
    document_id: int | None
    document: str  # the catalogue's title
    pdf_page: int  # 1-based
    page_label: str  # as printed on the page, e.g. "B35"; empty when not found
    values: dict = field(default_factory=dict)  # name -> number (or text)
    sources: dict = field(default_factory=dict)  # name -> the quote it is read from
    row: str = ""  # the row's text as in the PDF
    series: str = ""  # the page's title line, e.g. "CoroDrill® 860 solid carbide drill / For steel"

    @property
    def where(self) -> str:
        return f"{self.document}, " + (f"{self.page_label} " if self.page_label else "") + f"(PDF {self.pdf_page})"


def page_label(text: str) -> str:
    """The printed page ("B 35" in the text's first lines -> "B35"), or ""."""
    for line in text.splitlines()[:3]:
        m = LABEL.match(line)
        if m:
            return m.group(1) + m.group(2)
    return ""


def headers(text: str) -> list[str]:
    """The column headers before the ordering code, in order."""
    lines = text.splitlines()
    start = next((i for i, line in enumerate(lines) if "Dimensions" in line), None)
    if start is None:
        return []
    out = []
    for line in lines[start + 1:]:
        words = line.split()
        if "Ordering" in words:
            out += words[:words.index("Ordering")]
            return [w for w in out if w not in ("mm", "inch", "mm,")]
        out += words
    return []


def page_value(text: str, name: str) -> tuple[str | None, str | None]:
    """(value, quote) of a value the page states once ("SIG\\n147°" -> "147°"); (None, why) otherwise."""
    found = {m.group(1) for m in re.finditer(rf"(?m)^{name}\s*\n\s*(\d+(?:[.,]\d+)?°)\s*$", text)}
    if len(found) == 1:
        value = found.pop()
        return value, f"{name} {value}"
    return None, (f"{name} not on the page" if not found else f"{name} has {len(found)} values on the page")


def page_grade(text: str) -> tuple[str | None, str | None]:
    """The grade the page's table is in (the column header after "Ordering code", e.g. "4234"), when it has one."""
    lines = text.splitlines()
    start = next((i for i, line in enumerate(lines) if "Ordering code" in line), None)
    if start is None:
        return None, None
    grades = []
    for line in lines[start + 1:]:
        word = line.strip()
        if not re.fullmatch(r"[A-Z0-9]{3,5}", word) or word.startswith(("DCON", "OAL", "LF", "LCF", "PL", "BD", "WF")):
            break
        grades.append(word)
    distinct = set(grades)
    if len(distinct) == 1:
        grade = distinct.pop()
        return grade, f"grade column {grade}"
    return None, None


def coolant(text: str) -> tuple[str | None, str | None]:
    said = {kind for kind in ("Internal", "External") if f"{kind} coolant supply" in text}
    if len(said) == 1:
        kind = said.pop()
        return kind.lower(), f"{kind} coolant supply"
    return None, None


def _series(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    for i, line in enumerate(lines):
        if "®" in line and i + 1 < len(lines):
            nxt = lines[i + 1]
            return line + (f" / {nxt}" if nxt.startswith("For ") else "")
    return ""


def _tokens_before(flat: str, start: int, count: int) -> list[str]:
    return flat[:start].split()[-count:] if count else []


def _close(a: float, b: float, tol: float) -> bool:
    return abs(a - b) <= tol + 1e-9


def _drills(text, flat, heads, base) -> tuple[list[Found], list[str]]:
    out, why = [], []
    if not all(h in heads for h in ("DC", 'DC"', "LU", 'LU"')):
        return out, why
    for m in DRILL_CODE.finditer(flat):
        window = _tokens_before(flat, m.start(), len(heads))
        values = dict(zip(heads, window))
        nums = {h: _num(v) for h, v in values.items()}
        dc, dc_in, lu, lu_in = (nums.get(h) for h in ("DC", 'DC"', "LU", 'LU"'))
        code_d = int(m.group(2)) / 100
        if len(window) != len(heads) or None in (dc, dc_in, lu, lu_in):
            why.append(f"{m.group(0)}: the row's values do not match the headers")
            continue
        if not (_close(dc, code_d, 0.001) and _close(dc_in, dc / INCH, INCH_TOL) and _close(lu_in, lu / INCH, INCH_TOL)):
            why.append(f"{m.group(0)}: DC {dc:g} / DC\" / LU\" do not check against the code and each other")
            continue
        row = " ".join(window + [m.group(0)])
        found = Found("drilling", m.group(0), row=row, **base)
        found.values.update(diameter=dc, max_depth=lu)
        found.sources.update(diameter=f"DC {window[heads.index('DC')]} ({row})",
                             max_depth=f"LU {window[heads.index('LU')]} ({row})")
        if nums.get("ULDR") is not None:
            found.values["depth_ratio"] = nums["ULDR"]
            found.sources["depth_ratio"] = f"ULDR {values['ULDR']} ({row})"
        sig, quote = page_value(text, "SIG")
        if sig:
            found.values["point_angle"] = float(sig.rstrip("°").replace(",", "."))
            found.sources["point_angle"] = quote
        kind, quote = coolant(text)
        if kind:
            found.values["coolant"], found.sources["coolant"] = kind, quote
        grade, quote = page_grade(text)
        if grade:
            found.values["grade"], found.sources["grade"] = grade, quote
        out.append(found)
    return out, why


def _bars(text, flat, heads, base) -> tuple[list[Found], list[str]]:
    out, why = [], []
    if not all(h in heads for h in ("CZCMS", "DMIN1")):
        return out, why
    for m in BAR_CODE.finditer(flat):
        window = _tokens_before(flat, m.start(), len(heads))
        values = dict(zip(heads, window))
        nums = {h: _num(v) for h, v in values.items()}
        if len(window) != len(heads) or nums.get("CZCMS") is None or nums.get("DMIN1") is None \
                or not _close(nums["CZCMS"], int(m.group(2)), 0.001):
            why.append(f"{m.group(0)}: the row does not check (CZCMS against the bar's Ø in the code)")
            continue
        row = " ".join(window + [m.group(0)])
        found = Found("boring", m.group(0), row=row, **base)
        found.values["dmin"] = nums["DMIN1"]
        found.sources["dmin"] = f"DMIN1 {values['DMIN1']} ({row})"
        for header, name in (("OHX", "max_overhang"), ("OHN", "min_overhang")):
            if nums.get(header) is not None:
                found.values[name] = nums[header]
                found.sources[name] = f"{header} {values[header]} ({row})"
        kapr, quote = page_value(text, "KAPR")
        if kapr:
            found.values["entering_angle"] = float(kapr.rstrip("°").replace(",", "."))
            found.sources["entering_angle"] = quote
        kind, quote = coolant(text)
        if kind:
            found.values["coolant"], found.sources["coolant"] = kind, quote
        out.append(found)
    return out, why


def _grooves(text, flat, heads, base) -> tuple[list[Found], list[str]]:
    out, why = [], []
    if not all(h in heads for h in ("SSC", "CW")):
        return out, why
    for m in GROOVE_CODE.finditer(flat):
        before = flat[:m.start()].split()[-len(heads):]
        seat, width = m.group(2), int(m.group(4)) / 100
        # the row starts with its seat letter (the columns at its end may be empty: the row is shorter)
        starts = [i for i, t in enumerate(before) if t == seat]
        window = before[starts[-1]:] if starts else []
        values = dict(zip(heads, window))
        cw = _num(values.get("CW", ""))
        if not window or heads[0] != "SSC" or cw is None or not _close(cw, width, 0.001) \
                or any(t in MARKS for t in window):
            why.append(f"{m.group(0)}: the row does not check (its seat letter and CW against the code)")
            continue
        row = " ".join(window + [m.group(0)])
        found = Found("grooving", m.group(0), row=row, **base)
        found.values["width"] = cw
        found.sources["width"] = f"CW {values['CW']} ({row})"
        cdx = _num(values.get("CDX", ""))
        if cdx is not None:
            found.values["max_depth"] = cdx
            found.sources["max_depth"] = f"CDX {values['CDX']} ({row})"
        out.append(found)
    return out, why


def _inserts(text, flat, heads, base) -> tuple[list[Found], list[str]]:
    out, why = [], []
    if "RE" not in text.split() or "ISO CODE" not in flat:
        return out, why
    for m in INSERT_CODE.finditer(flat):
        before = [t for t in flat[:m.start()].split()[-6:] if t not in MARKS]
        nums = [_num(t) for t in before]
        s, nose = INSERT_THICKNESS.get(m.group(3)), int(m.group(4)) / 10
        tail = None
        for bs in (0, 1):  # a wiper has BS after RE
            k = len(nums) - 1 - bs
            if k >= 1 and None not in (nums[k], nums[k - 1], s) and _close(nums[k - 1], s, 0.01) \
                    and _close(nums[k], nose, NOSE_TOL):
                tail = (k, bs)
                break
        if tail is None:
            why.append(f"{m.group(0)}: the row does not check (S and RE against the code)")
            continue
        k, bs = tail
        row = " ".join(before[max(0, k - 2):] + [m.group(0)])
        found = Found("turning", m.group(0), row=row, **base)
        found.values.update(nose_radius=nums[k], thickness=nums[k - 1])
        found.sources.update(nose_radius=f"RE {before[k]} ({row})", thickness=f"S {before[k - 1]} ({row})")
        if k >= 2 and nums[k - 2] is not None:
            found.values["edge_length"] = nums[k - 2]
            found.sources["edge_length"] = f"LE {before[k - 2]} ({row})"
        if bs:
            found.values["wiper"] = nums[-1]
            found.sources["wiper"] = f"BS {before[-1]} ({row})"
        out.append(found)
    return out, why


PARSERS = {"drilling": _drills, "boring": _bars, "grooving": _grooves, "turning": _inserts}


def read_page(text: str, pdf_page: int, document: str, document_id: int | None = None, kinds=None):
    """(found tools, reasons for rows not taken) of one page's text."""
    heads = headers(text)
    kinds = [k for k in kinds or PARSERS if heads or k == "turning"]  # the inserts' tables have no such header
    if not kinds:
        return [], []
    flat = " ".join(text.split())
    base = dict(document_id=document_id, document=document, pdf_page=pdf_page, page_label=page_label(text),
                series=_series(text))
    found, why = [], []
    for kind in kinds:
        f, w = PARSERS[kind](text, flat, heads, base)
        found += f
        why += w
    return found, why


@dataclass
class Search:
    found: list[Found] = field(default_factory=list)
    not_taken: list[str] = field(default_factory=list)  # rows that matched the code's shape but did not check
    pages: int = 0  # pages with text that were searched


def search(documents, kind: str) -> Search:
    """Every tool of a kind in the documents: [(document id, title, pages dict from catalogue_reader)]. The same code on
    several pages: its first page."""
    result, seen = Search(), set()
    for document_id, title, pages in documents:
        for number, text in enumerate(pages["texts"], start=1):
            if not text or not text.strip():
                continue
            result.pages += 1
            found, why = read_page(text, number, title, document_id, kinds=(kind,))
            for f in found:
                if f.code not in seen:
                    seen.add(f.code)
                    result.found.append(f)
            result.not_taken += [f"{title} PDF {number}: {w}" for w in why]
    return result


def find(documents, document_id: int, pdf_page: int, code: str) -> Found | None:
    """The tool of this code on this page, read again from the page's text (not from a form)."""
    for doc_id, title, pages in documents:
        if doc_id != document_id or not 1 <= pdf_page <= len(pages["texts"]):
            continue
        found, _ = read_page(pages["texts"][pdf_page - 1] or "", pdf_page, title, doc_id)
        return next((f for f in found if f.code == code), None)
    return None


def drills(documents, smallest: float, largest: float, depth: float | None) -> tuple[list[Found], Search]:
    """Drills of Ø smallest..largest that reach `depth` (their LU), the largest Ø first, then the shortest."""
    s = search(documents, "drilling")
    fit = [f for f in s.found if smallest - 1e-6 <= f.values["diameter"] <= largest + 1e-6
           and (depth is None or f.values["max_depth"] >= depth - 1e-6)]
    fit.sort(key=lambda f: (-f.values["diameter"], f.values["max_depth"], f.code))
    return fit, s


def boring_bars(documents, bore: float, depth: float | None) -> tuple[list[Found], Search]:
    """Boring bars whose DMIN goes into the bore and whose longest overhang reaches `depth`, the thickest first."""
    s = search(documents, "boring")
    fit = [f for f in s.found if f.values["dmin"] <= bore + 1e-6
           and (depth is None or f.values.get("max_overhang", 0) >= depth - 1e-6)]
    fit.sort(key=lambda f: (-f.values["dmin"], f.code))
    return fit, s


def turning_inserts(documents, nose_radius: float, code: str = "") -> tuple[list[Found], Search]:
    """Turning inserts of this nose radius whose code has the parts of `code` in their order (spaces ignored:
    "CNMG-MM" finds CNMG 12 04 08-MM, "-MM" every MM insert), in the code's order."""
    s = search(documents, "turning")
    parts = re.sub(r"\s+", "", code).upper().split("-")
    pattern = re.compile(".*-".join(re.escape(p) for p in parts) + (r"$" if len(parts) > 1 else ""))
    fit = [f for f in s.found if _close(f.values["nose_radius"], nose_radius, NOSE_TOL)
           and pattern.search(re.sub(r"\s+", "", f.code))]
    fit.sort(key=lambda f: f.code)
    return fit, s


def grooving_inserts(documents, width: float, depth: float | None) -> tuple[list[Found], Search]:
    """Grooving inserts not wider than the groove that cut its depth (CDX), the widest first."""
    s = search(documents, "grooving")
    fit = [f for f in s.found if f.values["width"] <= width + 1e-6
           and (depth is None or f.values.get("max_depth", 0) >= depth - 1e-6)]
    fit.sort(key=lambda f: (-f.values["width"], f.code))
    return fit, s
