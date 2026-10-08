"""Cutting data from tool makers' catalogues: matching a tool to its confirmed catalogue rows.

Pure helpers: no Flask, no database queries. A row is anything with CuttingDataRow's attributes.
"""
from __future__ import annotations

import json
import re

from .planner import format_vc_points, parse_vc_points

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


GEOMETRY_FIELDS = ("ap_min", "ap_rec", "ap_max", "f_min", "f_rec", "f_max")
GRADE_FIELDS = ("vc_min", "vc_max", "vc_points", "coolant")


def values_from_form(form, prefix: str, kind: str) -> dict:
    """A row's values from a form (fields "<prefix><name>"), checked: positive numbers, min <= rec <= max, Vc(f)
    points in the planner's format. Raises ValueError."""
    values = {}
    for name in GEOMETRY_FIELDS if kind == "geometry" else ("vc_min", "vc_max"):
        raw = (form.get(prefix + name) or "").strip().replace(",", ".")
        try:
            values[name] = float(raw) if raw else None
        except ValueError:
            raise ValueError(f"{name}: '{raw}' is not a number") from None
        if values[name] is not None and values[name] <= 0:
            raise ValueError(f"{name} must be greater than zero")
    if kind == "grade_vc":
        try:
            points = parse_vc_points(form.get(prefix + "vc_points"))
        except ValueError as e:
            raise ValueError(f"Vc(f): {e} (write e.g. 0.1:455, 0.4:305, 0.8:215, or one Vc)") from None
        values["vc_points"] = format_vc_points(points) or None
        values["coolant"] = {"yes": True, "no": False}.get(form.get(prefix + "coolant"))
        if not values["vc_points"] and values["vc_min"] is None and values["vc_max"] is None:
            raise ValueError("no Vc given")
    elif all(v is None for v in values.values()):
        raise ValueError("no ap or f given")
    for name in ("ap", "f", "vc"):
        chain = [values.get(f"{name}_{k}") for k in ("min", "rec", "max")]
        chain = [v for v in chain if v is not None]
        if chain != sorted(chain):
            raise ValueError(f"{name}: min / rec / max are not in order")
    return values


def changes(row, values: dict) -> list[str]:
    """"ap_rec 3 → 2" for every value that differs from the row's."""
    return [f"{k} {_show(getattr(row, k))} → {_show(v)}" for k, v in values.items() if getattr(row, k) != v]


def _show(value) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "yes" if value else "no"
    return f"{value:g}" if isinstance(value, float) else str(value)


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


# --- the planner's cutting data from the confirmed rows ---------------------------------------------------

NO_GROUP_WARNING = ("{material} has no catalogue group (Machine page, Materials): cutting data are the tool's own "
                    "values, not from a confirmed catalogue table (check)")
NO_APPLICATION_WARNING = "no catalogue cutting data for {type} tools: the tool's own values (check)"
NO_GEOMETRY_WARNING = ("no confirmed catalogue ap / f for {code} in {group}: the tool's own values (check)")
NO_GRADE_WARNING = ("no confirmed catalogue Vc for {grade} ({application}) in {group}: the tool's own values "
                    "(check)")


def _source(row, what: str) -> str:
    page = f" p. {row.page}" if row.page else ""
    graph = ", from a graph" if row.from_graph else ""
    by = "entered by the operator" if row.origin == "operator" else "confirmed"
    when = f" {row.confirmed_at:%Y-%m-%d}" if row.confirmed_at else ""
    return f"{what}: {row.catalogue}{page}{graph} ({by}{when})"


def catalogue_values(tool_type: str, insert_code: str | None, grade: str | None, material_name: str,
                     material_group: str | None, rows) -> dict:
    """What the confirmed rows give a tool for a material, as ToolSpec fields: ap / f from the insert's geometry
    row, Vc from the grade's row, and the note naming the catalogue and pages (catalogue_note). What they do not
    give stays the tool's own value, and catalogue_warning asks to check it. rows: the confirmed rows."""
    if not material_group:
        return {"catalogue_warning": NO_GROUP_WARNING.format(material=material_name)}
    application = application_for(tool_type)
    if application is None:
        return {"catalogue_warning": NO_APPLICATION_WARNING.format(type=tool_type)}
    values, sources, warnings = {}, [], []
    geometry = geometry_row(rows, insert_code, material_group)
    if geometry is None:
        warnings.append(NO_GEOMETRY_WARNING.format(code=insert_code or "a tool without an insert code",
                                                   group=material_group))
    else:
        values.update({k: getattr(geometry, k) for k in ("ap_min", "ap_rec", "ap_max", "f_min", "f_rec", "f_max")
                       if getattr(geometry, k) is not None})
        sources.append(_source(geometry, "ap / f"))
    grade_vc = grade_row(rows, grade, tool_type, material_group)
    if grade_vc is None:
        warnings.append(NO_GRADE_WARNING.format(grade=grade or "no grade", application=application,
                                                group=material_group))
    else:
        points = parse_vc_points(grade_vc.vc_points)
        if points:
            values["vc_points"] = points
        speeds = [vc for _, vc in points]
        for key, given, fallback in (("vc_min", grade_vc.vc_min, min(speeds, default=None)),
                                     ("vc_max", grade_vc.vc_max, max(speeds, default=None))):
            if (given if given is not None else fallback) is not None:
                values[key] = given if given is not None else fallback
        sources.append(_source(grade_vc, "Vc"))
    if sources:
        values["catalogue_note"] = f"cutting data for {material_group}: " + "; ".join(sources)
    if warnings:
        values["catalogue_warning"] = "; ".join(warnings)
    else:
        values["source"] = None  # every number is from the catalogue rows: the tool's own source is not used
    return values
