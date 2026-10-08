"""Cutting data from tool makers' catalogues: matching a tool to its confirmed catalogue rows.

Pure helpers: no Flask, no database queries. A row is anything with CuttingDataRow's attributes.
"""
from __future__ import annotations

import json
import re

# The catalogue's Vc tables are per application: a grade has other Vc for grooving than for turning.
APPLICATION_BY_TOOL_TYPE = {
    "facing": "turning",
    "turning_rough": "turning",
    "turning_finish": "turning",
    "boring": "turning",
    "boring_rough": "turning",
    "grooving": "grooving",
    "parting": "parting",
    "threading": "threading",
    "threading_internal": "threading",
    "drilling": "drilling",
}


def application_for(tool_type: str) -> str | None:
    """The catalogue application of a tool type; None: no catalogue cutting data (milling, centring, tapping)."""
    return APPLICATION_BY_TOOL_TYPE.get(tool_type)


def normalize_code(code: str | None) -> str:
    """"CNMG 12 04 08-PM" and "cnmg120408-pm" -> "CNMG120408-PM": codes compared without spaces and case."""
    return re.sub(r"\s+", "", code or "").upper()


def group_matches(row_group: str, material_group: str) -> bool:
    """A row's group covers the material: the same group ("P1.2"), or the row is for the whole ISO letter ("P")."""
    row_group, material_group = (row_group or "").strip().upper(), (material_group or "").strip().upper()
    return bool(row_group) and (row_group == material_group or row_group == material_group[:1] and len(row_group) == 1)


def _best(rows):
    """The most specific row: one for the material group before one for the whole ISO letter; then the newest."""
    return max(rows, key=lambda r: (len(r.material_group or ""), r.id or 0), default=None)


def geometry_row(rows, insert_code: str | None, material_group: str):
    code = normalize_code(insert_code)
    if not code:
        return None
    return _best([r for r in rows if r.kind == "geometry" and normalize_code(r.insert_code) == code
                  and group_matches(r.material_group, material_group)])


def grade_row(rows, grade: str | None, tool_type: str, material_group: str):
    application = application_for(tool_type)
    grade = normalize_code(grade)
    if not grade or application is None:
        return None
    return _best([r for r in rows if r.kind == "grade_vc" and normalize_code(r.grade) == grade
                   and r.application == application and group_matches(r.material_group, material_group)])


def row_checks(row) -> list[str]:
    return json.loads(row.checks) if row.checks else []


def row_key(row) -> tuple:
    """What a row is about: two rows with the same key give values for the same thing."""
    if row.kind == "geometry":
        return ("geometry", normalize_code(row.insert_code), (row.material_group or "").upper())
    return ("grade_vc", normalize_code(row.grade), row.application, (row.material_group or "").upper())


VALUE_FIELDS = ("ap_min", "ap_rec", "ap_max", "f_min", "f_rec", "f_max", "vc_min", "vc_max", "vc_points", "coolant")


def same_values(a, b) -> bool:
    return all(getattr(a, k) == getattr(b, k) for k in VALUE_FIELDS)


def _fmt(value) -> str:
    return "" if value is None else f"{value:g}"


def triple(lo, rec, hi) -> str:
    """"0.5 – 3 – 5.5"; a missing value is "·"."""
    if lo is None and rec is None and hi is None:
        return ""
    return " – ".join(_fmt(v) if v is not None else "·" for v in (lo, rec, hi))


def describe(row) -> str:
    """One line for notes and lists: what the row gives."""
    if row.kind == "geometry":
        parts = [f"ap {triple(row.ap_min, row.ap_rec, row.ap_max)}" if any(
            v is not None for v in (row.ap_min, row.ap_rec, row.ap_max)) else "",
                 f"f {triple(row.f_min, row.f_rec, row.f_max)}" if any(
            v is not None for v in (row.f_min, row.f_rec, row.f_max)) else ""]
        return "; ".join(p for p in parts if p)
    parts = []
    if row.vc_points:
        parts.append(f"Vc(f) {row.vc_points}")
    if row.vc_min is not None or row.vc_max is not None:
        parts.append(f"Vc {_fmt(row.vc_min)}–{_fmt(row.vc_max)}")
    return "; ".join(parts)
