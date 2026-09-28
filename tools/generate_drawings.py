"""Generate test drawings of turned parts with their expected extraction results.

For every part in PARTS this writes to tests/fixtures/drawings/:
  <name>.png            clean drawing (A4 landscape)
  <name>.pdf            the same drawing as a vector PDF
  <name>.photo.jpg      a "photographed" copy: rotated 1-3°, lower resolution, JPEG artefacts, noise
  <name>.expected.json  the correct answer in the DrawingData format
and for the parts in LOWRES_OF also <name>_lowres.png (downscaled to 1169 px, same expected answer).

Drawing and expected answer are built from the same part description, so they always agree.

Usage:  pip install -r requirements-dev.txt
        python tools/generate_drawings.py
"""

from __future__ import annotations

import json
import random
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.patches import Polygon, Rectangle  # noqa: E402
from PIL import Image, ImageFilter  # noqa: E402

OUT_DIR = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "drawings"

PAPER_W, PAPER_H = 297.0, 210.0  # A4 landscape, mm
DPI = 220  # long edge ~2570 px: within the 2576 px the model reads without downscaling
# Fixed PDF metadata (no creation date), so regenerating unchanged drawings gives identical files.
PDF_METADATA = {"Creator": "turnpilot tools/generate_drawings.py", "Producer": "matplotlib", "CreationDate": None}
FONT = 8
LINE = 0.9
THIN = 0.5

# Part descriptions. Sections run left to right; x positions of grooves are from the part's left end.
PARTS = [
    {
        "name": "01_stepped_shaft",
        "title": "Stepped shaft",
        "number": "TP-001",
        "material": "Steel 45 (C45)",
        "quantity": 20,
        "sections": [
            {"d": 40, "l": 30},
            {"d": 32, "l": 50, "ra": 3.2},
            {"d": 25, "l": 40},
        ],
    },
    {
        "name": "02_threaded_shaft",
        "title": "Threaded shaft",
        "number": "TP-002",
        "material": "Steel 45 (C45)",
        "quantity": 50,
        "sections": [
            {"d": 30, "l": 60, "ra": 1.6},
            {"d": 20, "l": 30},
        ],
        "grooves": [{"x": 60, "width": 3, "bottom_d": 17}],
        "thread": {"section": 1, "pitch": 1.5, "length": 25, "cls": "6g"},
        "chamfers": [{"section": 1, "side": "right", "size": 1}],
    },
    {
        "name": "03_bushing",
        "title": "Bushing",
        "number": "TP-003",
        "material": "AISI 304",
        "quantity": 10,
        "sections": [{"d": 50, "l": 45, "tol": "±0.05"}],
        # Through bore marked THRU; its Ra sits on a leader to the bore surface.
        "bore": {"d": 30, "l": 45, "tol": "H7", "ra": 1.6, "thru": True, "ra_leader": True},
        "chamfers": [{"section": 0, "side": "right", "size": 1}],
        "blank": {"d": 55, "l": 50},
    },
    {
        "name": "04_fitted_shaft",
        "title": "Shaft with fits",
        "number": "TP-004",
        "material": "Aluminium 6061",
        "quantity": 30,
        "sections": [
            {"d": 35, "l": 25},
            {"d": 30, "l": 40, "tol": "h6", "ra": 0.8},
            {"d": 25, "l": 35, "tol": "f7", "ra": 1.6},
        ],
        "chamfers": [{"section": 2, "side": "right", "size": 1}],
    },
    {
        # The first bushing layout, kept on purpose: the bore length is not dimensioned and there
        # is no THRU note, so the expected bore length is null. Checks that the model does not guess.
        "name": "05_bushing_ambiguous",
        "title": "Bushing",
        "number": "TP-005",
        "material": "AISI 304",
        "quantity": 10,
        "sections": [{"d": 50, "l": 45, "tol": "±0.05"}],
        "bore": {"d": 30, "l": 45, "tol": "H7", "ra": 1.6, "thru": False, "ra_leader": False},
        "chamfers": [{"section": 0, "side": "right", "size": 1}],
        "blank": {"d": 55, "l": 50},
    },
]


# --- expected answer -------------------------------------------------------------

def _feature(type_, diameter=None, start_diameter=None, length=None, tolerance=None, ra=None, pitch=None,
             radius=None, location=None, face=None):
    feature = {
        "type": type_, "diameter": diameter, "start_diameter": start_diameter, "length": length,
        "tolerance": tolerance, "ra": ra, "pitch": pitch, "radius": radius,
    }
    if type_ == "chamfer":  # where the chamfer is; all drawn chamfers are on an outside diameter
        feature.update(location=location, face=face)
    return feature


def expected_answer(part: dict) -> dict:
    sections = part["sections"]
    # A groove is a section of its own (machinist's convention): the od_turn length excludes it.
    groove_width = {i: 0 for i in range(len(sections))}
    for g in part.get("grooves", []):
        groove_width[sections.index(_section_at(part, g["x"]))] += g["width"]
    features = [
        _feature("od_turn", s["d"], length=s["l"] - groove_width[i], tolerance=s.get("tol"), ra=s.get("ra"))
        for i, s in enumerate(sections)
    ]
    for g in part.get("grooves", []):
        features.append(_feature("groove", g["bottom_d"], start_diameter=_section_at(part, g["x"])["d"], length=g["width"]))
    for c in part.get("chamfers", []):
        features.append(_feature("chamfer", sections[c["section"]]["d"], length=c["size"],
                                 location="external", face=c["side"]))
    if "thread" in part:
        t = part["thread"]
        features.append(
            _feature("thread", sections[t["section"]]["d"], length=t["length"], tolerance=t["cls"], pitch=t["pitch"])
        )
    if "bore" in part:
        b = part["bore"]
        # The bore length is only readable from the drawing when the bore is marked THRU.
        length = b["l"] if b.get("thru") else None
        features.append(_feature("bore", b["d"], length=length, tolerance=b.get("tol"), ra=b.get("ra")))
    blank = part.get("blank", {})
    return {
        "part_type": "turned",
        "material": part["material"],
        "blank_diameter": blank.get("d"),
        "blank_length": blank.get("l"),
        "overall_length": sum(s["l"] for s in sections),
        "quantity": part["quantity"],
        "general_ra": None,
        "features": features,
        "warnings": [],
    }


def _section_bounds(part):
    x = 0.0
    for s in part["sections"]:
        yield x, x + s["l"], s
        x += s["l"]


def _section_at(part, x):
    for x0, x1, s in _section_bounds(part):
        if x0 <= x < x1:
            return s
    raise ValueError(f"No section at x={x}")


# --- drawing primitives ------------------------------------------------------------

def _hdim(ax, x1, x2, y, text, from_y1=None, from_y2=None):
    """Horizontal dimension with extension lines from the part edge down/up to the dimension line."""
    for x, fy in ((x1, from_y1), (x2, from_y2)):
        if fy is not None:
            ax.plot([x, x], [fy, y - 1.5 if y < fy else y + 1.5], color="k", lw=THIN)
    ax.annotate("", xy=(x1, y), xytext=(x2, y),
                arrowprops=dict(arrowstyle="<|-|>", lw=THIN, color="k", mutation_scale=7, shrinkA=0, shrinkB=0))
    ax.text((x1 + x2) / 2, y + 0.8, text, ha="center", va="bottom", fontsize=FONT)


def _vdim(ax, x, y1, y2, text):
    """Vertical (diameter) dimension across the part, text to the left of the line."""
    ax.annotate("", xy=(x, y1), xytext=(x, y2),
                arrowprops=dict(arrowstyle="<|-|>", lw=THIN, color="k", mutation_scale=7, shrinkA=0, shrinkB=0))
    ax.text(x - 1.0, (y1 + y2) / 2, text, ha="right", va="center", rotation=90, fontsize=FONT)


def _roughness(ax, x, y, value):
    """Surface roughness symbol (tick) with the Ra value, sitting on the edge at (x, y)."""
    ax.plot([x - 1.6, x, x + 3.2, x + 9.0], [y + 2.2, y, y + 5.2, y + 5.2], color="k", lw=THIN)
    ax.text(x + 3.6, y + 5.8, f"Ra {value:g}", ha="left", va="bottom", fontsize=FONT)


def _roughness_below(ax, x, y, value):
    """Roughness symbol hanging below an edge (used for the bore wall seen in section)."""
    ax.plot([x - 1.6, x, x + 3.2, x + 9.0], [y - 2.2, y, y - 5.2, y - 5.2], color="k", lw=THIN)
    ax.text(x + 3.6, y - 5.8, f"Ra {value:g}", ha="left", va="top", fontsize=FONT)


def _roughness_on_leader(ax, tip, start, value):
    """Leader from outside the part to a surface, with the roughness symbol on the leader's shelf."""
    ax.annotate("", xy=tip, xytext=start,
                arrowprops=dict(arrowstyle="-|>", lw=THIN, color="k", mutation_scale=7, shrinkA=0, shrinkB=0))
    x, y = start
    ax.plot([x, x + 12], [y, y], color="k", lw=THIN)  # shelf
    _roughness(ax, x + 2, y, value)


def _leader(ax, xy, text_xy, text):
    ax.annotate(text, xy=xy, xytext=text_xy, fontsize=FONT, ha="left", va="bottom",
                arrowprops=dict(arrowstyle="-|>", lw=THIN, color="k", mutation_scale=7, shrinkA=0, shrinkB=0))


def _scale_label(s):
    return {1: "1:1", 1.5: "3:2", 2: "2:1", 2.5: "5:2", 3: "3:1"}[s]


def _title_block(ax, part, scale):
    x0, y0, w, h = PAPER_W - 10 - 112, 10, 112, 40
    ax.add_patch(Rectangle((x0, y0), w, h, fill=False, lw=LINE))
    rows = [
        ("TURNPILOT TEST DRAWING", ""),
        ("Part", part["title"]),
        ("Drawing No.", part["number"]),
        ("Material", part["material"]),
        ("Quantity", f"{part['quantity']} pcs"),
        ("Scale", _scale_label(scale)),
        ("Units", "mm"),
    ]
    row_h = h / len(rows)
    for i, (label, value) in enumerate(rows):
        y = y0 + h - (i + 1) * row_h
        ax.plot([x0, x0 + w], [y, y], color="k", lw=THIN)
        if value:
            ax.plot([x0 + 32, x0 + 32], [y, y + row_h], color="k", lw=THIN)
            ax.text(x0 + 2, y + row_h / 2, label, va="center", fontsize=FONT - 1)
            ax.text(x0 + 34, y + row_h / 2, value, va="center", fontsize=FONT, weight="bold")
        else:
            ax.text(x0 + w / 2, y + row_h / 2, label, va="center", ha="center", fontsize=FONT, weight="bold")
    notes = ["General tolerances ISO 2768-m", "Break sharp edges 0.3"]
    if "blank" in part:
        notes.insert(0, f"BLANK: bar Ø{part['blank']['d']:g} x {part['blank']['l']:g}")
    for i, note in enumerate(notes):
        ax.text(x0, y0 + h + 3 + 5 * (len(notes) - 1 - i), note, fontsize=FONT)


def _upper_profile(part):
    """Points (x, r) of the upper outline in part mm, including chamfers and grooves."""
    chamfers = {(c["section"], c["side"]): c["size"] for c in part.get("chamfers", [])}
    grooves = sorted(part.get("grooves", []), key=lambda g: g["x"])
    pts = [(0.0, 0.0)]
    for i, (x0, x1, s) in enumerate(_section_bounds(part)):
        r = s["d"] / 2
        left_c, right_c = chamfers.get((i, "left"), 0), chamfers.get((i, "right"), 0)
        pts.append((x0, r - left_c))
        if left_c:
            pts.append((x0 + left_c, r))
        for g in grooves:
            if x0 <= g["x"] < x1:
                gr = g["bottom_d"] / 2
                pts += [(g["x"], r), (g["x"], gr), (g["x"] + g["width"], gr), (g["x"] + g["width"], r)]
        if right_c:
            pts += [(x1 - right_c, r), (x1, r - right_c)]
        else:
            pts.append((x1, r))
    pts.append((pts[-1][0], 0.0))
    return pts


def draw(part: dict):
    sections = part["sections"]
    length = sum(s["l"] for s in sections)
    d_max = max(s["d"] for s in sections)
    scale = max(s for s in (1, 1.5, 2, 2.5, 3) if length * s <= 175 and d_max * s <= 90)

    fig = plt.figure(figsize=(PAPER_W / 25.4, PAPER_H / 25.4))
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, PAPER_W)
    ax.set_ylim(0, PAPER_H)
    ax.set_aspect("equal")
    ax.axis("off")
    ax.add_patch(Rectangle((10, 10), PAPER_W - 20, PAPER_H - 20, fill=False, lw=LINE))

    ox = 20 + (190 - length * scale) / 2 + 20  # part origin on paper
    oy = 125.0  # axis height on paper

    def px(x):
        return ox + x * scale

    def py(r):
        return oy + r * scale

    profile = _upper_profile(part)
    xs = [px(x) for x, _ in profile]
    upper = [py(r) for _, r in profile]
    lower = [py(-r) for _, r in profile]
    bore = part.get("bore")

    if bore:
        # Upper half in section (hatched between bore and outside), lower half as a view.
        rb = bore["d"] / 2
        outline = [(px(x), py(max(r, rb))) for x, r in profile[1:-1]]
        ring = outline + [(px(bore["l"]), py(rb)), (px(0), py(rb))]
        ax.add_patch(Polygon(ring, closed=True, fill=False, hatch="////", lw=LINE))
        ax.plot(xs[1:-1], lower[1:-1], color="k", lw=LINE)
        ax.plot([px(0), px(0)], [py(-profile[1][1]), py(0)], color="k", lw=LINE)
        ax.plot([xs[-2], xs[-2]], [lower[-2], py(0)], color="k", lw=LINE)
        ax.plot([px(0), px(bore["l"])], [py(-rb), py(-rb)], color="k", lw=THIN, ls=(0, (4, 2)))  # hidden bore
    else:
        ax.plot(xs, upper, color="k", lw=LINE)
        ax.plot(xs, lower, color="k", lw=LINE)

    # Edges between sections, at chamfers and at grooves.
    edges = set()
    for i, (x0, x1, s) in enumerate(_section_bounds(part)):
        if i:
            edges.add((x0, max(s["d"], sections[i - 1]["d"]) / 2))
    for c in part.get("chamfers", []):
        x0, x1, s = list(_section_bounds(part))[c["section"]]
        edges.add((x1 - c["size"] if c["side"] == "right" else x0 + c["size"], s["d"] / 2 - 0))
    for g in part.get("grooves", []):
        r = _section_at(part, g["x"])["d"] / 2
        edges |= {(g["x"], r), (g["x"] + g["width"], r)}
    for x, r in edges:
        # With a bore the upper half is a section, so edges are only drawn in the lower (view) half.
        ax.plot([px(x), px(x)], [py(-r), oy if bore else py(r)], color="k", lw=LINE)

    ax.plot([px(0) - 6, px(length) + 6], [oy, oy], color="k", lw=THIN, ls=(0, (12, 3, 2, 3)))  # centre line

    # Thread: thin line at the minor diameter over the threaded length + designation.
    thread = part.get("thread")
    if thread:
        x0, x1, s = list(_section_bounds(part))[thread["section"]]
        r_minor = s["d"] / 2 - 0.613 * thread["pitch"]
        for sign in (1, -1):
            ax.plot([px(x1 - thread["length"]), px(x1)], [py(sign * r_minor)] * 2, color="k", lw=THIN)
        ax.plot([px(x1 - thread["length"])] * 2, [py(-r_minor), py(r_minor)], color="k", lw=THIN)

    # Diameter dimensions (inside the part, one per section).
    for i, (x0, x1, s) in enumerate(_section_bounds(part)):
        x = px(x0 + s["l"] * 0.55)
        if thread and thread["section"] == i:
            text = f"M{s['d']:g}x{thread['pitch']:g}-{thread['cls']}"
            x = px(x1 - thread["length"] * 0.45)
        else:
            text = f"Ø{s['d']:g}" + (f" {s['tol']}" if s.get("tol") else "")
        if bore:
            x = px(x0 + s["l"] * 0.8)
        _vdim(ax, x, py(-s["d"] / 2), py(s["d"] / 2), text)
        if s.get("ra"):
            _roughness(ax, px(x0 + s["l"] * 0.25), py(s["d"] / 2), s["ra"])

    if bore:
        _vdim(ax, px(bore["l"] * 0.35), py(-bore["d"] / 2), py(bore["d"] / 2),
              f"Ø{bore['d']:g}" + (f" {bore['tol']}" if bore.get("tol") else "") + (" THRU" if bore.get("thru") else ""))
        if bore.get("ra") and bore.get("ra_leader"):
            # Leader enters through the open bore end and touches the bore wall.
            rb = bore["d"] / 2
            _roughness_on_leader(ax, (px(bore["l"] * 0.6), py(rb)), (px(length) + 14, py(rb * 0.45)), bore["ra"])
        elif bore.get("ra"):
            _roughness_below(ax, px(bore["l"] * 0.5), py(bore["d"] / 2), bore["ra"])

    # Length dimensions below the part: chain of sections, then overall length.
    y_chain = py(-d_max / 2) - 12
    if len(sections) > 1:
        for x0, x1, s in _section_bounds(part):
            _hdim(ax, px(x0), px(x1), y_chain, f"{s['l']:g}", py(-s["d"] / 2), py(-s["d"] / 2))
        y_total = y_chain - 11
    else:
        y_total = y_chain
    _hdim(ax, px(0), px(length), y_total, f"{length:g}", py(-sections[0]["d"] / 2), py(-sections[-1]["d"] / 2))

    if thread:
        x0, x1, s = list(_section_bounds(part))[thread["section"]]
        y = py(s["d"] / 2) + 12
        _hdim(ax, px(x1 - thread["length"]), px(x1), y, f"{thread['length']:g}", py(s["d"] / 2), py(s["d"] / 2))

    # Leaders for grooves and chamfers.
    for g in part.get("grooves", []):
        r = g["bottom_d"] / 2
        _leader(ax, (px(g["x"] + g["width"] / 2), py(r)), (px(g["x"]) - 30, py(d_max / 2) + 16),
                f"Groove {g['width']:g} x Ø{g['bottom_d']:g}")
    for c in part.get("chamfers", []):
        x0, x1, s = list(_section_bounds(part))[c["section"]]
        x = x1 - c["size"] / 2 if c["side"] == "right" else x0 + c["size"] / 2
        _leader(ax, (px(x), py(s["d"] / 2 - c["size"] / 2)), (px(x) + 8, py(s["d"] / 2) + 22),
                f"{c['size']:g}x45°")

    _title_block(ax, part, scale)
    return fig


# --- "photo" version -----------------------------------------------------------------

# --- GOST-style drawing close to the real shaft --------------------------------------------------
#
# A separate, more demanding drawing: small text, decimal commas, tolerances on every size, a fillet,
# a taper, a thread relief groove, a thread with class 6g, a chamfer, baseline dimensions from the
# right end face plus chain dimensions, and the general roughness symbol in the top-right corner.
# Sections run left to right; boundaries are 0 (left face) .. N (right face).

GOST_FONT = 6

GOST_PARTS = [
    {
        "name": "06_gost_shaft",
        "title": "Shaft",
        "number": "TP-006",
        "material": "Steel 45 (C45)",
        "quantity": 12,
        "general_ra": 3.2,
        "sections": [
            {"type": "od_turn", "d": 80, "l": 40.1, "tol": "+0,5/-1,3"},
            {"type": "fillet", "r": 10, "l": 10, "tol": "±0,05"},
            {"type": "od_turn", "d": 60, "l": 40, "tol": "±0,05"},
            {"type": "taper", "d0": 60, "d": 55, "l": 30, "tol": "±0,85"},
            {"type": "groove", "d0": 48, "d": 44, "l": 4, "tol": "±0,85"},
            {"type": "od_turn", "d": 48, "l": 16},
        ],
        "thread": {"section": 5, "pitch": 1.5, "cls": "6g"},  # 0-based section, over its whole length
        "chamfer": {"size": 1.5},  # on the right end face
        # (text, from boundary, to boundary, side, row); rows count away from the part
        "length_dims": [
            ("140,1js12", 0, 6, "below", 3),
            ("100js12", 1, 6, "below", 2),
            ("90js12(±0,175)", 2, 6, "below", 1),
            ("30±0,105", 3, 4, "above", 1),
            ("4±0,08", 4, 5, "above", 2),
            ("20js12", 4, 6, "above", 3),
        ],
    },
]


def _dot(text):
    """Drawing text uses decimal commas; the expected answer uses points (as the app stores them)."""
    return text.replace(",", ".") if text else None


def _comma(value):
    return f"{value:g}".replace(".", ",")


def expected_gost(part: dict) -> dict:
    features = []
    for s in part["sections"]:
        if s["type"] == "fillet":
            features.append(_feature("fillet", tolerance=_dot(s.get("tol")), radius=s["r"]))
        elif s["type"] in ("taper", "groove"):
            features.append(_feature(s["type"], s["d"], start_diameter=s["d0"], length=s["l"],
                                     tolerance=_dot(s.get("tol"))))
        else:
            features.append(_feature("od_turn", s["d"], length=s["l"], tolerance=_dot(s.get("tol"))))
    thread_section = part["sections"][part["thread"]["section"]]
    features.append(_feature("thread", thread_section["d"], length=thread_section["l"],
                             tolerance=part["thread"]["cls"], pitch=part["thread"]["pitch"]))
    features.append(_feature("chamfer", part["sections"][-1]["d"], length=part["chamfer"]["size"],
                             location="external", face="right"))
    return {
        "part_type": "turned",
        "material": part["material"],
        "blank_diameter": None,
        "blank_length": None,
        "overall_length": round(sum(s["l"] for s in part["sections"]), 3),
        "quantity": part["quantity"],
        "general_ra": part["general_ra"],
        "features": features,
        "warnings": [],
    }


def draw_gost(part: dict):
    global FONT
    sections = part["sections"]
    bounds = [0.0]
    for s in sections:
        bounds.append(round(bounds[-1] + s["l"], 3))
    length = bounds[-1]

    fig = plt.figure(figsize=(PAPER_W / 25.4, PAPER_H / 25.4))
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, PAPER_W)
    ax.set_ylim(0, PAPER_H)
    ax.set_aspect("equal")
    ax.axis("off")
    ax.add_patch(Rectangle((10, 10), PAPER_W - 20, PAPER_H - 20, fill=False, lw=LINE))

    ox, oy = 32.0, 118.0  # scale 1:1

    def px(x):
        return ox + x

    def py(r):
        return oy + r

    # Upper outline (x, r), mirrored for the lower half.
    pts = [(0.0, 0.0)]
    chamfer = part["chamfer"]["size"]
    for i, s in enumerate(sections):
        x0, x1 = bounds[i], bounds[i + 1]
        if s["type"] == "fillet":
            r_prev = sections[i - 1]["d"] / 2
            r_next = sections[i + 1]["d"] / 2 if sections[i + 1]["type"] != "taper" else sections[i + 1]["d0"] / 2
            cx, cy = x1, r_next + s["r"]
            for k in range(0, 31):
                a = np.pi + (np.pi / 2) * k / 30
                pts.append((cx + s["r"] * np.cos(a), cy + s["r"] * np.sin(a)))
            assert abs(r_prev - cy) < 1e-6  # the shoulder is exactly one fillet radius high
        elif s["type"] == "taper":
            pts += [(x0, s["d0"] / 2), (x1, s["d"] / 2)]
        elif s["type"] == "groove":
            pts += [(x0, s["d"] / 2), (x1, s["d"] / 2)]
        elif i == len(sections) - 1:
            pts += [(x0, s["d"] / 2), (x1 - chamfer, s["d"] / 2), (x1, s["d"] / 2 - chamfer)]
        else:
            pts += [(x0, s["d"] / 2), (x1, s["d"] / 2)]
    pts.append((length, 0.0))
    xs = [px(x) for x, _ in pts]
    ax.plot(xs, [py(r) for _, r in pts], color="k", lw=LINE)
    ax.plot(xs, [py(-r) for _, r in pts], color="k", lw=LINE)

    # Visible edges across the part.
    edge_radius = {
        bounds[1]: sections[0]["d"] / 2,  # end of the Ø80 cylinder where the fillet starts
        bounds[2]: sections[2]["d"] / 2,  # fillet runs out on Ø60
        bounds[3]: sections[2]["d"] / 2,  # taper start
        bounds[4]: sections[3]["d"] / 2,  # taper end / groove
        bounds[5]: sections[5]["d"] / 2,  # groove / thread section
        length - chamfer: sections[5]["d"] / 2 - 0,
    }
    for x, r in edge_radius.items():
        ax.plot([px(x), px(x)], [py(-r), py(r)], color="k", lw=THIN if x in (bounds[2], length - chamfer) else LINE)
    ax.plot([px(-6), px(length + 6)], [oy, oy], color="k", lw=THIN, ls=(0, (12, 3, 2, 3)))

    # Thread: minor diameter as thin lines over the threaded section.
    t = part["thread"]
    ts = sections[t["section"]]
    r_minor = ts["d"] / 2 - 0.613 * t["pitch"]
    x0, x1 = bounds[t["section"]], bounds[t["section"] + 1]
    for sign in (1, -1):
        ax.plot([px(x0), px(x1)], [py(sign * r_minor)] * 2, color="k", lw=THIN)

    old_font, FONT = FONT, GOST_FONT
    try:
        # Diameters.
        _vdim(ax, px(bounds[0] + 22), py(-40), py(40), f"Ø80{sections[0]['tol'].replace('+', ' +')}")
        _vdim(ax, px(bounds[2] + 22), py(-30), py(30), f"Ø60{sections[2]['tol']}")
        _vdim(ax, px(bounds[4] - 2), py(-27.5), py(27.5), f"Ø55{sections[3]['tol']}")
        _vdim(ax, px(bounds[5] + 9), py(-24), py(24), f"M48×{_comma(t['pitch'])}-{t['cls']}")
        _leader(ax, (px(bounds[4] + 2), py(-22)), (px(bounds[4] - 16), py(-40)), f"Ø44{sections[4]['tol']}")
        _leader(ax, (px(bounds[1] + 3), py(-37)), (px(bounds[1] - 4), py(-52)), f"R10{sections[1]['tol']}")
        _leader(ax, (px(length - 0.7), py(23.2)), (px(length + 6), py(30)), f"{_comma(chamfer)}×45°")

        # Length dimensions: baseline from the right face below the part, chain above.
        for text, a, b, side, row in part["length_dims"]:
            if side == "below":
                y = py(-40) - 8 * row
                _hdim(ax, px(bounds[a]), px(bounds[b]), y, text, py(-40), py(-24))
            else:
                y = py(40) + 7 * row
                _hdim(ax, px(bounds[a]), px(bounds[b]), y, text, py(27.5), py(24))

        # General roughness in the top-right corner (no leader).
        _roughness(ax, PAPER_W - 38, PAPER_H - 22, part["general_ra"])
        ax.texts[-1].set_text(f"Ra {_comma(part['general_ra'])}")

        _title_block(ax, part, 1)
    finally:
        FONT = old_font
    return fig


# --- hold-out drawing ------------------------------------------------------------------------------
#
# A different part, used only to check prompt rules, never to tune them: baseline dimensions from the
# LEFT end face, a taper whose end diameter is dimensioned at the taper end (no cylinder there), a
# thread relief groove, a thread and a chamfer. The thread section is not dimensioned directly: its
# length is the overall length minus the last baseline minus the groove width.

HOLDOUT_PARTS = [
    {
        "name": "07_holdout",
        "title": "Spindle",
        "number": "TP-007",
        "material": "Aluminium 6061",
        "quantity": 25,
        "general_ra": 3.2,
        "sections": [
            {"type": "od_turn", "d": 36, "l": 25, "tol": "0/-0,016"},
            {"type": "od_turn", "d": 45, "l": 30, "tol": "±0,1"},
            {"type": "taper", "d0": 45, "d": 38, "l": 20, "tol": "±0,2"},
            {"type": "groove", "d0": 34, "d": 30, "l": 3, "tol": "±0,3"},
            {"type": "od_turn", "d": 34, "l": 22},
        ],
        "thread": {"section": 4, "pitch": 1.5, "cls": "6g"},
        "chamfer": {"size": 1},
        # (text, from boundary, to boundary, side, row): baselines from the left face below, chain above
        "length_dims": [
            ("25", 0, 1, "below", 1),
            ("55", 0, 2, "below", 2),
            ("75", 0, 3, "below", 3),
            ("100", 0, 5, "below", 4),
            ("3±0,1", 3, 4, "above", 1),
        ],
    },
]


def draw_holdout(part: dict):
    global FONT
    scale = 1.5
    sections = part["sections"]
    bounds = [0.0]
    for s in sections:
        bounds.append(round(bounds[-1] + s["l"], 3))
    length = bounds[-1]
    chamfer = part["chamfer"]["size"]

    fig = plt.figure(figsize=(PAPER_W / 25.4, PAPER_H / 25.4))
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, PAPER_W)
    ax.set_ylim(0, PAPER_H)
    ax.set_aspect("equal")
    ax.axis("off")
    ax.add_patch(Rectangle((10, 10), PAPER_W - 20, PAPER_H - 20, fill=False, lw=LINE))

    ox, oy = 40.0, 128.0

    def px(x):
        return ox + x * scale

    def py(r):
        return oy + r * scale

    def radius_at_start(s):
        return (s.get("d0") or s["d"]) / 2 if s["type"] == "taper" else s["d"] / 2

    pts = [(0.0, 0.0)]
    for i, s in enumerate(sections):
        x0, x1 = bounds[i], bounds[i + 1]
        if s["type"] == "taper":
            pts += [(x0, s["d0"] / 2), (x1, s["d"] / 2)]
        elif i == len(sections) - 1:
            pts += [(x0, s["d"] / 2), (x1 - chamfer, s["d"] / 2), (x1, s["d"] / 2 - chamfer)]
        else:
            pts += [(x0, s["d"] / 2), (x1, s["d"] / 2)]
    pts.append((length, 0.0))
    xs = [px(x) for x, _ in pts]
    ax.plot(xs, [py(r) for _, r in pts], color="k", lw=LINE)
    ax.plot(xs, [py(-r) for _, r in pts], color="k", lw=LINE)

    for i in range(1, len(sections)):
        r = max(sections[i - 1]["d"] / 2, radius_at_start(sections[i]))
        ax.plot([px(bounds[i])] * 2, [py(-r), py(r)], color="k", lw=LINE)
    r_last = sections[-1]["d"] / 2
    ax.plot([px(length - chamfer)] * 2, [py(-r_last), py(r_last)], color="k", lw=THIN)
    ax.plot([px(-6), px(length + 6)], [oy, oy], color="k", lw=THIN, ls=(0, (12, 3, 2, 3)))

    t = part["thread"]
    ts = sections[t["section"]]
    r_minor = ts["d"] / 2 - 0.613 * t["pitch"]
    for sign in (1, -1):
        ax.plot([px(bounds[t["section"]]), px(length)], [py(sign * r_minor)] * 2, color="k", lw=THIN)

    old_font, FONT = FONT, GOST_FONT
    try:
        s0, s1, taper, groove, thread_section = sections
        _vdim(ax, px(bounds[0] + 12), py(-s0["d"] / 2), py(s0["d"] / 2), f"Ø{_comma(s0['d'])} {s0['tol']}")
        _vdim(ax, px(bounds[1] + 15), py(-s1["d"] / 2), py(s1["d"] / 2), f"Ø{_comma(s1['d'])}{s1['tol']}")
        # the taper's end diameter, dimensioned right at the end of the taper
        _vdim(ax, px(bounds[3] - 1.5), py(-taper["d"] / 2), py(taper["d"] / 2), f"Ø{_comma(taper['d'])}{taper['tol']}")
        _vdim(ax, px(bounds[4] + 11), py(-thread_section["d"] / 2), py(thread_section["d"] / 2),
              f"M{_comma(thread_section['d'])}×{_comma(t['pitch'])}-{t['cls']}")
        _leader(ax, (px(bounds[3] + 1.5), py(groove["d"] / 2)), (px(bounds[3] - 22), py(27)),
                f"Ø{_comma(groove['d'])}{groove['tol']}")
        _leader(ax, (px(length - 0.5), py(r_last - 0.5)), (px(length + 4), py(r_last + 12)), f"{_comma(chamfer)}×45°")

        for text, a, b, side, row in part["length_dims"]:
            if side == "below":
                y = py(-s1["d"] / 2) - 8 * row
                _hdim(ax, px(bounds[a]), px(bounds[b]), y, text, py(-s0["d"] / 2), py(-radius_at_start(sections[b - 1])))
            else:
                y = py(s1["d"] / 2) + 8 * row
                _hdim(ax, px(bounds[a]), px(bounds[b]), y, text, py(taper["d"] / 2), py(thread_section["d"] / 2))

        _roughness(ax, PAPER_W - 38, PAPER_H - 22, part["general_ra"])
        ax.texts[-1].set_text(f"Ra {_comma(part['general_ra'])}")
        _title_block(ax, part, scale)
    finally:
        FONT = old_font
    return fig


# --- low resolution copies -----------------------------------------------------------------------

LOWRES_LONG_EDGE = 1169  # the long edge of the real drawing photo real_01
LOWRES_OF = ("02_threaded_shaft", "06_gost_shaft")


def lowres_copy(name: str):
    """<name>_lowres.png: the clean PNG downscaled to the real drawing's resolution, same expected answer."""
    image = Image.open(OUT_DIR / f"{name}.png").convert("RGB")
    factor = LOWRES_LONG_EDGE / max(image.size)
    small = image.resize((round(image.width * factor), round(image.height * factor)), Image.LANCZOS)
    small.save(OUT_DIR / f"{name}_lowres.png", optimize=True)
    (OUT_DIR / f"{name}_lowres.expected.json").write_text(
        (OUT_DIR / f"{name}.expected.json").read_text(encoding="utf-8"), encoding="utf-8"
    )
    return small.size


def photo_version(png_path: Path, out_path: Path, seed: int):
    """Simulate a phone photo of a printout: slight rotation, lower resolution, noise, JPEG artefacts."""
    rng = random.Random(seed)
    image = Image.open(png_path).convert("RGB")
    angle = rng.uniform(1.0, 3.0) * rng.choice((-1, 1))
    paper = (238, 235, 226)
    image = image.rotate(angle, resample=Image.BICUBIC, expand=True, fillcolor=paper)
    width, height = image.size
    factor = rng.uniform(0.5, 0.6)
    image = image.resize((int(width * factor), int(height * factor)), Image.LANCZOS)
    image = image.filter(ImageFilter.GaussianBlur(radius=0.6))

    pixels = np.asarray(image).astype(np.float32)
    shade = np.linspace(0.93, 1.0, pixels.shape[1], dtype=np.float32)[None, :, None]  # uneven lighting
    noise = np.random.default_rng(seed).normal(0, 7, pixels.shape).astype(np.float32)
    pixels = np.clip(pixels * shade + noise, 0, 255).astype(np.uint8)
    Image.fromarray(pixels).save(out_path, "JPEG", quality=60)
    return angle


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for i, part in enumerate(PARTS):
        fig = draw(part)
        png = OUT_DIR / f"{part['name']}.png"
        fig.savefig(png, dpi=DPI, facecolor="white")
        fig.savefig(OUT_DIR / f"{part['name']}.pdf", facecolor="white", metadata=PDF_METADATA)
        plt.close(fig)
        angle = photo_version(png, OUT_DIR / f"{part['name']}.photo.jpg", seed=1000 + i)
        expected = OUT_DIR / f"{part['name']}.expected.json"
        expected.write_text(json.dumps(expected_answer(part), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"{part['name']}: png, pdf, photo (rotated {angle:+.1f}°), expected.json")
    gost_like = [(p, draw_gost) for p in GOST_PARTS] + [(p, draw_holdout) for p in HOLDOUT_PARTS]
    for j, (part, draw_part) in enumerate(gost_like, start=len(PARTS)):
        fig = draw_part(part)
        png = OUT_DIR / f"{part['name']}.png"
        fig.savefig(png, dpi=DPI, facecolor="white")
        fig.savefig(OUT_DIR / f"{part['name']}.pdf", facecolor="white", metadata=PDF_METADATA)
        plt.close(fig)
        angle = photo_version(png, OUT_DIR / f"{part['name']}.photo.jpg", seed=1000 + j)
        expected = OUT_DIR / f"{part['name']}.expected.json"
        expected.write_text(json.dumps(expected_gost(part), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"{part['name']}: png, pdf, photo (rotated {angle:+.1f}°), expected.json")
    for name in LOWRES_OF:
        width, height = lowres_copy(name)
        print(f"{name}_lowres: png {width}x{height}, expected.json")


if __name__ == "__main__":
    main()
