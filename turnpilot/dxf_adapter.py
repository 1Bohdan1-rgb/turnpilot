"""DXF files from other CAD systems than KOMPAS-3D, before the KOMPAS reader (dxf_reader, frozen for the blind check
of roadmap stage 3, so it is not changed here).

detect_cad tells the producer of a file from what it writes:
- KOMPAS-3D: the application id "KOMPAS" or its linetypes K5LT_*; the file goes to dxf_reader as it is;
- SolidWorks: its blocks SW_* (surface finish symbols, centre marks) or its "FORMAT" layer: the file is rewritten in
  the KOMPAS way (normalize_solidworks) and that copy goes to dxf_reader;
- anything else: not supported, said in words.

What SolidWorks writes differently (seen on three SolidWorks 2000-format DXF files, which makes the adapter in-sample
on them):
- model space is the sheet, at the drawing's scale (2:1, 10:1), with the frame and the title block; KOMPAS writes the
  part at 1:1. The scale is taken from the dimensions (measured / written), checked against "SCALE" in the title
  block, and the geometry and the dimension points are brought back to 1:1;
- everything is on layer 0 in colour 7 with linetype Continuous: the contour is told by its line weight (the heaviest
  Continuous weight next to a horizontal centre line), thinner lines there are thin lines, CENTER* lines are axes;
  the frame and the title block (other weights, away from the axes) are left out; splines, circles and hatches are
  not read (the KOMPAS reader does not read them either);
- a dimension's text is in its block as pieces, Cyrillic letters as \\U+XXXX, %%c for Ø, %%d for °, a Cyrillic "х"
  in chamfers, deviations as separate pieces after empty ones: they are joined into one text as KOMPAS writes it.
  Diameter and angular dimensions (an end view, a chamfer angle) are not taken: the KOMPAS reader has no use for them.
"""
from __future__ import annotations

import os
import re
import statistics
from dataclasses import dataclass, field

import ezdxf
from ezdxf.tools.text import plain_mtext

from .dxf_reader import AXIS, CONTOUR, THIN, DxfReadError, components

KOMPAS, SOLIDWORKS, UNKNOWN = "KOMPAS-3D", "SolidWorks", "unknown"
SCALE_AGREEMENT = 0.01  # a dimension's measured / written ratio within 1% of the sheet's scale
AXIS_MARGIN = 5.0  # mm on the sheet: lines this far beyond a centre line's ends still belong to its view
DEFAULT_LINEWEIGHT = 25  # 0.25 mm: what a line "by layer / by block / default" is taken as


def detect_cad(doc) -> str:
    """The CAD system that wrote the document (KOMPAS, SOLIDWORKS or UNKNOWN)."""
    appids = {a.dxf.name.upper() for a in doc.appids}
    linetypes = {lt.dxf.name.upper() for lt in doc.linetypes}
    if "KOMPAS" in appids or any(name.startswith("K5LT_") for name in linetypes):
        return KOMPAS
    blocks = {b.name.upper() for b in doc.blocks}
    layers = {layer.dxf.name.upper() for layer in doc.layers}
    if any(name.startswith("SW_") for name in blocks) or "FORMAT" in layers:
        return SOLIDWORKS
    return UNKNOWN


@dataclass
class Adapted:
    """What the adapter did to a file, for the review page."""
    cad: str
    dxf_version: str
    scale: float = 1.0  # the drawing's scale (2.0 = 2:1); the geometry was divided by it
    material: str | None = None  # as written in the title block
    warnings: list = field(default_factory=list)


def _load(path):
    try:
        return ezdxf.readfile(path)
    except (OSError, ezdxf.DXFError) as exc:
        raise DxfReadError(f"The DXF file cannot be read: {exc}") from None


def check_supported(path) -> str:
    """The CAD of a DXF the reader can take (KOMPAS or SOLIDWORKS); DxfReadError naming it otherwise."""
    doc = _load(path)
    cad = detect_cad(doc)
    if cad in (KOMPAS, SOLIDWORKS):
        return cad
    raise DxfReadError("A DXF from an unknown CAD system (no KOMPAS-3D or SolidWorks marks). Only KOMPAS-3D and "
                       "SolidWorks DXF files are read.")


def normalized_path(path: str) -> str:
    return path + ".kompas.dxf"


def prepare(path: str) -> tuple[str, Adapted]:
    """The file the KOMPAS reader is to read, and what was done: a KOMPAS file as it is; a SolidWorks file rewritten
    the KOMPAS way next to it (<file>.kompas.dxf). DxfReadError for anything else or a file that cannot be read."""
    doc = _load(path)
    cad = detect_cad(doc)
    if cad == KOMPAS:
        return path, Adapted(KOMPAS, doc.dxfversion)
    if cad != SOLIDWORKS:
        check_supported(path)  # raises with the message
    out, adapted = normalize_solidworks(doc)
    target = normalized_path(path)
    out.saveas(target)
    return target, adapted


def reader_path(path: str) -> str:
    """The file the KOMPAS reader reads for a stored drawing: the normalized copy when there is one."""
    normalized = normalized_path(path)
    return normalized if os.path.exists(normalized) else path


# --- texts --------------------------------------------------------------------------------------------------------

def decode(text: str) -> str:
    """\\U+XXXX escapes as their letters, MTEXT formatting off, %% codes as their signs."""
    text = re.sub(r"\\U\+([0-9A-Fa-f]{4})", lambda m: chr(int(m.group(1), 16)), text)
    text = plain_mtext(text) if "\\" in text or "{" in text else text
    for code, sign in (("%%c", "Ø"), ("%%C", "Ø"), ("%%d", "°"), ("%%D", "°"), ("%%p", "±"), ("%%P", "±")):
        text = text.replace(code, sign)
    return text


def _deviation(sign: str, value: str) -> str | None:
    number = float(value.replace(",", ".")) if re.fullmatch(r"\d+(?:[.,]\d+)?", value) else None
    if number is None or number == 0:
        return None
    return f"{sign}{value}"


def dimension_text(pieces: list[str]) -> tuple[str, list[str]]:
    """One text, as KOMPAS writes it, from the pieces of a SolidWorks dimension block; and notes (a reference size)."""
    pieces = [decode(p) for p in pieces]
    notes = []
    if "" in pieces:  # the main text, then deviations after empty pieces: sign, upper, lower
        cut = pieces.index("")
        main, tail = pieces[:cut], [p for p in pieces[cut:] if p]
    else:
        main, tail = pieces, []
    text = "".join(main)
    text = re.sub(r"(\d)\s*[хxX]\s*45", r"\1×45", text)  # a Cyrillic or Latin x in a chamfer
    text = re.sub(r"°(?=\S)", "° ", text)  # "1×45°2 фаски" -> "1×45° 2 фаски"
    if "*" in text:
        text = text.replace("*", "")
        notes.append(f"{text.strip()}: a reference dimension (*)")
    has_fit = re.search(r"\d\s*[A-Za-z]{1,2}\d{1,2}\b", text)
    if len(tail) == 3 and tail[0] in "+-" and not has_fit:
        deviations = [d for d in (_deviation(tail[0], tail[1]), _deviation(tail[0], tail[2])) if d]
        text += "/".join(deviations)
    elif tail and not has_fit:
        text += "".join(tail)
    return text.strip(), notes


def block_pieces(doc, dim) -> list[str]:
    name = dim.dxf.get("geometry")
    if not name or name not in doc.blocks:
        return []
    return [e.dxf.text if e.dxftype() == "TEXT" else e.text for e in doc.blocks[name]
            if e.dxftype() in ("TEXT", "MTEXT")]


# --- scale ----------------------------------------------------------------------------------------------------------

def _written_number(text: str) -> float | None:
    m = re.search(r"\d+(?:[.,]\d+)?", re.sub(r"^\s*[ØRMМS]?\s*", "", text))
    return float(m.group().replace(",", ".")) if m else None


def sheet_scale(doc, msp) -> tuple[float, list[str]]:
    """The drawing's scale from its dimensions (measured on the sheet / written), checked against the title block."""
    ratios = []
    for d in msp.query("DIMENSION"):
        if d.dxf.dimtype & 7 not in (0, 1, 3, 4):
            continue
        text, _ = dimension_text(block_pieces(doc, d))
        number = _written_number(text)
        try:
            measured = d.get_measurement()
        except Exception:
            continue
        if number and isinstance(measured, (int, float)) and measured > 0:
            ratios.append(measured / number)
    if not ratios:
        raise DxfReadError("No dimension with a number: the drawing's scale cannot be found.")
    scale = statistics.median(ratios)
    agree = [r for r in ratios if abs(r / scale - 1) <= SCALE_AGREEMENT]
    if len(agree) < 0.6 * len(ratios):
        raise DxfReadError("The dimensions do not agree on one scale (views at different scales): not supported.")
    scale = statistics.mean(agree)
    for nice in (0.1, 0.2, 0.25, 0.5, 1, 2, 2.5, 4, 5, 10, 20, 50):
        if abs(scale / nice - 1) <= SCALE_AGREEMENT:
            scale = float(nice)
            break
    warnings = []
    off = len(ratios) - len(agree)
    if off:
        warnings.append(f"{off} dimension(s) do not agree with the drawing's scale {scale:g}:1 (geometry ≠ text)")
    for t in msp.query("MTEXT TEXT"):
        m = re.search(r"SCALE\s*:?\s*(\d+(?:[.,]\d+)?)\s*:\s*(\d+(?:[.,]\d+)?)", decode(t.text if t.dxftype() == "MTEXT"
                                                                                       else t.dxf.text), re.I)
        if m:
            written = float(m.group(1).replace(",", ".")) / float(m.group(2).replace(",", "."))
            if abs(written / scale - 1) > SCALE_AGREEMENT:
                warnings.append(f"the title block says SCALE {m.group(1)}:{m.group(2)}, the dimensions give "
                                f"{scale:g}:1 (taken)")
            break
    return scale, warnings


# --- lines ----------------------------------------------------------------------------------------------------------

def _lineweight(doc, e) -> int:
    lw = e.dxf.get("lineweight", -1)
    if lw < 0:
        layer = doc.layers.get(e.dxf.layer)
        lw = layer.dxf.get("lineweight", -3) if layer else -3
    return lw if lw >= 0 else DEFAULT_LINEWEIGHT


def _linetype(doc, e) -> str:
    name = e.dxf.get("linetype", "BYLAYER").upper()
    if name in ("BYLAYER", "BYBLOCK"):
        layer = doc.layers.get(e.dxf.layer)
        name = layer.dxf.linetype.upper() if layer else "CONTINUOUS"
    return name


def _bbox(e):
    if e.dxftype() == "LINE":
        xs, ys = (e.dxf.start.x, e.dxf.end.x), (e.dxf.start.y, e.dxf.end.y)
        return min(xs), max(xs), min(ys), max(ys)
    c, r = e.dxf.center, e.dxf.radius
    return c.x - r, c.x + r, c.y - r, c.y + r


def _near(box, axes) -> bool:
    """Within the x range of a horizontal centre line (and its margin)."""
    x0, x1, _, _ = box
    return any(a0 - AXIS_MARGIN <= x0 and x1 <= a1 + AXIS_MARGIN for a0, a1, _ in axes)


# --- the rewrite ------------------------------------------------------------------------------------------------------

def normalize_solidworks(doc):
    """A new document the KOMPAS reader can read: the part at 1:1, contour / thin lines / axes with the KOMPAS
    linetypes, linear and radius dimensions with their points at 1:1 and their texts joined; and what was done."""
    msp = doc.modelspace()
    adapted = Adapted(SOLIDWORKS, doc.dxfversion)
    scale, adapted.warnings = sheet_scale(doc, msp)
    adapted.scale = scale

    centre = [e for e in msp.query("LINE") if "CENTER" in _linetype(doc, e)]
    axes = [(min(e.dxf.start.x, e.dxf.end.x), max(e.dxf.start.x, e.dxf.end.x), e.dxf.start.y) for e in centre
            if abs(e.dxf.start.y - e.dxf.end.y) <= 1e-6]
    drawn = [e for e in msp.query("LINE ARC") if _linetype(doc, e) == "CONTINUOUS"]
    # the frame (the heaviest lines on the sheet) and all that hangs on it (the title block) are not a view
    frame_weight = max((_lineweight(doc, e) for e in drawn), default=0)
    sheet = [g for g in components(drawn) if any(_lineweight(doc, e) == frame_weight for e in g)]
    on_sheet = {id(e) for g in sheet for e in g}
    near = [e for e in drawn if id(e) not in on_sheet and _near(_bbox(e), axes)]
    if not axes or not near:
        raise DxfReadError("No horizontal centre line with a contour along it: no turned part on the sheet.")
    contour_weight = max(_lineweight(doc, e) for e in near)

    out = ezdxf.new("R2010", setup=True)
    for name in (CONTOUR, THIN):
        out.linetypes.add(name, pattern=[0.0], description=name)
    out.linetypes.add(AXIS, pattern=[20.0, 15.0, -2.5, 0.5, -2.5], description="axial")
    omsp = out.modelspace()
    k = 1.0 / scale

    def p(v):
        return (v[0] * k, v[1] * k)

    for e in near:
        role = CONTOUR if _lineweight(doc, e) == contour_weight else THIN
        if e.dxftype() == "LINE":
            omsp.add_line(p(e.dxf.start), p(e.dxf.end), dxfattribs={"linetype": role})
        else:
            omsp.add_arc(p(e.dxf.center), e.dxf.radius * k, e.dxf.start_angle, e.dxf.end_angle,
                         dxfattribs={"linetype": role})
    for e in centre:
        omsp.add_line(p(e.dxf.start), p(e.dxf.end), dxfattribs={"linetype": AXIS})

    left_out = []
    for d in msp.query("DIMENSION"):
        text, notes = dimension_text(block_pieces(doc, d))
        adapted.warnings += notes
        kind = d.dxf.dimtype & 7
        if kind in (0, 1):
            angle = d.dxf.get("angle", 0.0) if kind == 0 else 0.0
            dim = omsp.add_linear_dim(base=p(d.dxf.defpoint), p1=p(d.dxf.defpoint2), p2=p(d.dxf.defpoint3),
                                      angle=angle, text=text)
            dim.render()
        elif kind == 4:
            dim = omsp.add_radius_dim_2p(center=p(d.dxf.defpoint), mpoint=p(d.dxf.defpoint4), text=text)
            dim.render()
        else:
            left_out.append(f"{text} ({'diameter' if kind == 3 else 'angular' if kind in (2, 5) else 'other'})")
    if left_out:
        adapted.warnings.append("dimensions not read (an end view or an angle): " + ", ".join(left_out))
    splines = len(msp.query("SPLINE"))
    if splines:
        adapted.warnings.append(f"{splines} spline(s) on the sheet not read (break lines, thread lines)")

    for t in msp.query("MTEXT TEXT"):
        text = decode(t.text if t.dxftype() == "MTEXT" else t.dxf.text).strip()
        if adapted.material is None and re.search(r"\b(EN\s*10\d{3}|AISI\s*\d{3}|S\d{3}[A-Z0-9+]*|C\d{2}E?\b|"
                                                  r"\d\.\d{4})", text):
            adapted.material = text
        elif re.search(r"[А-Яа-яІіЇїЄєҐґ]", text) and len(text) > 12:
            adapted.warnings.append(f"note on the sheet: {text}")
    adapted.warnings.insert(0, f"read from a SolidWorks DXF ({doc.dxfversion}) drawn at {scale:g}:1, brought to 1:1")
    return out, adapted
