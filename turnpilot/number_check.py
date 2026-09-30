"""Check the numbers the model read against the numbers written on a CAD PDF (see pdf_text).

Both ways: every number in the model's answer is on the drawing or marked as computed, and every
number on the drawing is used. A length that is not on the drawing but is the difference or sum of two
lengths that are (a section between two baseline dimensions: 44 − 14 = 30) is marked as computed, never
changed. Only the numbers are compared: whether a number belongs to the right section is not checked,
so an answer that uses the right numbers on the wrong sections passes.

Works on duck-typed features (DrawingData features or review-screen rows turned into objects).
"""

from __future__ import annotations

import itertools
import re
from dataclasses import dataclass, field

from .extraction_schema import coarse_pitch

TOLERANCE_MM = 0.01
LENGTH_TYPES = ("od_turn", "groove", "taper", "hex", "bore", "thread")
DIAMETER_KINDS = ("diameter", "thread", "across_flats")
KIND_LABELS = {"linear": "length", "diameter": "Ø", "thread": "M", "thread_pitch": "pitch", "chamfer": "chamfer",
               "roughness": "roughness", "radius": "R", "across_flats": "S"}


@dataclass
class NumberCheck:
    warnings: list[str] = field(default_factory=list)
    # index of a feature -> "44 − 14": its length is not on the drawing but computed from two that are
    computed: dict[int, str] = field(default_factory=dict)


def _same(a, b):
    return a is not None and b is not None and abs(a - b) <= TOLERANCE_MM


def length_formulas(linear: list[float], include_drawn: bool = False) -> dict[float, list[str]]:
    """Values that are the difference or the sum of two different lengths on the drawing, with their
    formulas (differences first: baseline dimensions are more common than chains).

    Without include_drawn only values that are not themselves on the drawing: these are the values the
    "difference / sum of two numbers" rule lets through.
    """
    values = sorted(set(round(v, 3) for v in linear))
    found: dict[float, list[str]] = {}
    for sign in ("−", "+"):
        for a, b in itertools.combinations(values, 2):
            value = round(b - a if sign == "−" else a + b, 3)
            if value <= 0 or (not include_drawn and any(_same(value, v) for v in values)):
                continue
            key = next((k for k in found if _same(k, value)), value)
            found.setdefault(key, []).append(f"{b:g} − {a:g}" if sign == "−" else f"{a:g} + {b:g}")
    return found


def check_numbers(features, overall_length, general_ra, drawing) -> NumberCheck:
    """drawing: pdf_text.DrawingNumber list (None or empty: nothing to check against)."""
    result = NumberCheck()
    if not drawing:
        return result
    pools: dict[str, list[float]] = {}
    for number in drawing:
        pools.setdefault(number.kind, []).append(number.value)
    # threads written without a pitch ("M10"): the ISO coarse pitch is meant
    coarse_threads = [n.value for n in drawing if n.kind == "thread" and not re.search(r"[x×]", n.text)]
    formulas = length_formulas(pools.get("linear", []), include_drawn=True)
    used: set[tuple[str, float]] = set()

    def find(value, kinds):
        for kind in kinds:
            for v in pools.get(kind, []):
                if _same(value, v):
                    used.add((kind, v))
                    return True
        return False

    def formula_for(value):
        return next((texts[0] for v, texts in formulas.items() if _same(value, v)), None)

    def use_operands(formula):
        for operand in re.split(r" [−+] ", formula):
            find(float(operand), ("linear",))

    def check(value, kinds, label, derivable=False, index=None, derived=False):
        if value is None:
            return
        if derived and derivable and formula_for(value):
            # marked as computed: its operands are used, even if the value is also written elsewhere
            use_operands(formula_for(value))
            find(value, kinds)
            return
        if find(value, kinds):
            return
        formula = formula_for(value) if derivable else None
        if formula:
            use_operands(formula)
            if index is not None:
                result.computed[index] = formula
            return
        if not derived:
            result.warnings.append(f"{label} {value:g} is not on the drawing: check")

    for i, f in enumerate(features):
        name = f"{f.type} Ø{f.diameter:g}" if getattr(f, "diameter", None) else f.type
        if f.type in ("od_turn", "groove", "taper", "bore", "thread") or (
            f.type == "hex" and getattr(f, "size_derived", None) != "diameter"
        ):
            check(f.diameter, DIAMETER_KINDS, f"{name}: Ø")
        if f.type in ("groove", "taper"):
            check(getattr(f, "start_diameter", None), DIAMETER_KINDS, f"{name}: start Ø")
        if f.type in LENGTH_TYPES:
            check(f.length, ("linear",), f"{name}: length", derivable=True, index=i,
                  derived=bool(getattr(f, "length_derived", False)))
        if f.type == "chamfer":
            check(f.length, ("chamfer",), f"{name}: chamfer")
        if f.type == "hex" and getattr(f, "size_derived", None) != "across_flats":
            check(getattr(f, "across_flats", None), ("across_flats", "diameter"), f"{name}: S")
        pitch = getattr(f, "pitch", None)
        if f.type == "thread" and pitch is not None and not getattr(f, "pitch_assumed", False):
            coarse = any(_same(f.diameter, d) and _same(pitch, coarse_pitch(d)) for d in coarse_threads)
            if not coarse:
                check(pitch, ("thread_pitch",), f"{name}: pitch")
        if f.type == "fillet":
            check(getattr(f, "radius", None), ("radius",), f"{name}: R")
        if getattr(f, "ra", None) is not None and not getattr(f, "ra_general", False):
            check(f.ra, ("roughness",), f"{name}: roughness")
    check(overall_length, ("linear",), "overall length")
    check(general_ra, ("roughness",), "general roughness")

    for kind, value in sorted({(n.kind, n.value) for n in drawing} - used):
        if kind == "thread" and any(_same(value, v) for k, v in used if k in ("diameter", "thread")):
            continue
        label = KIND_LABELS.get(kind, kind)
        text = f"{label}{value:g}" if label in ("Ø", "M", "R", "S") else f"{label} {value:g}"
        result.warnings.append(f"{text} on the drawing is not used: check")
    return result
