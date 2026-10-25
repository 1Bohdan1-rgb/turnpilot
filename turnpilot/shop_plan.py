"""The shop's plan (stage 7.2): walls, openings, columns, walkways, zones, storage, fixtures and labels, in mm, from a
JSON file the operator uploads (an example: docs/shop_plan_example.json). X runs to the right, Y down the sheet, from
the plan's top left corner. Nothing is drawn by hand or guessed: an element the file does not give is not there.
"""
from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Wall(_Strict):
    points: list[tuple[float, float]] = Field(min_length=2)  # mm, along the wall's centre line
    thickness_mm: float = Field(gt=0)
    closed: bool = False
    height_mm: float | None = Field(default=None, gt=0)  # optional: the 3D view; else the plan's wall_height_mm


class Opening(_Strict):
    """A door, a roller door, an exit or a window in a wall: from (x, y) along the wall, width_mm long."""
    kind: Literal["door", "roller_door", "exit", "window"]
    x_mm: float
    y_mm: float
    width_mm: float = Field(gt=0)
    along: Literal["x", "y"]  # the wall it is in runs along X or along Y
    label: str | None = None


class Column(_Strict):
    x_mm: float  # centre
    y_mm: float
    size_mm: float = Field(gt=0)


class Area(_Strict):
    x_mm: float  # top left corner
    y_mm: float
    width_mm: float = Field(gt=0)
    height_mm: float = Field(gt=0)
    label: str | None = None


class Label(_Strict):
    x_mm: float
    y_mm: float
    text: str = Field(min_length=1, max_length=80)
    rotate: Literal[0, 90] = 0


class Plan(_Strict):
    name: str = Field(min_length=1, max_length=100)
    width_mm: float = Field(gt=0)
    height_mm: float = Field(gt=0)
    wall_height_mm: float | None = Field(default=None, gt=0)  # optional: the walls' height (3D view)
    walls: list[Wall] = []
    openings: list[Opening] = []
    columns: list[Column] = []
    walkways: list[Area] = []  # kept free: a machine over one is warned about
    zones: list[Area] = []  # dashed areas (an operator zone, a goods-in bay)
    storage: list[Area] = []  # racks, shelving (hatched)
    fixtures: list[Area] = []  # benches, washers, desks (solid)
    labels: list[Label] = []

    @model_validator(mode="after")
    def _inside(self):
        """Every point lies on the sheet (0..width, 0..height)."""
        def check(where, x, y):
            if not (0 <= x <= self.width_mm and 0 <= y <= self.height_mm):
                raise ValueError(f"{where}: ({x:g}, {y:g}) is outside the plan 0..{self.width_mm:g} × "
                                 f"0..{self.height_mm:g} mm")
        for i, w in enumerate(self.walls):
            for x, y in w.points:
                check(f"walls[{i}]", x, y)
        for name in ("openings", "columns", "labels"):
            for i, e in enumerate(getattr(self, name)):
                check(f"{name}[{i}]", e.x_mm, e.y_mm)
        for name in ("walkways", "zones", "storage", "fixtures"):
            for i, a in enumerate(getattr(self, name)):
                check(f"{name}[{i}]", a.x_mm, a.y_mm)
                check(f"{name}[{i}]", a.x_mm + a.width_mm, a.y_mm + a.height_mm)
        return self


class PlanError(ValueError):
    pass


def parse(text: str) -> Plan:
    """The plan from the file's text; PlanError names what is wrong (the path to the field)."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise PlanError(f"not JSON: line {e.lineno}, column {e.colno}: {e.msg}") from None
    try:
        return Plan.model_validate(data)
    except ValidationError as e:
        problems = []
        for err in e.errors()[:10]:
            path = ".".join(str(p) for p in err["loc"])
            problems.append(f"{path}: {err['msg']}" if path else err["msg"])
        raise PlanError("; ".join(problems)) from None


# --- a machine's footprint on the plan and what it runs into ------------------------------------------------------

SNAP_MM = 100  # a machine is placed on a 100 mm step


def footprint_rect(x, y, rotation, length, width):
    """(x0, y0, x1, y1) of a footprint centred at (x, y): length along X at 0° / 180°, along Y at 90° / 270°."""
    dx, dy = (length, width) if rotation in (0, 180) else (width, length)
    return x - dx / 2, y - dy / 2, x + dx / 2, y + dy / 2


def _overlap(a, b) -> bool:
    """Two rectangles share an area (touching edges do not count)."""
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def _segment_hits(rect, p, q) -> bool:
    """The segment p-q crosses the rectangle's inside (Liang-Barsky)."""
    x0, y0, x1, y1 = rect
    (px, py), (qx, qy) = p, q
    dx, dy = qx - px, qy - py
    t0, t1 = 0.0, 1.0
    for edge_p, edge_q in ((-dx, px - x0), (dx, x1 - px), (-dy, py - y0), (dy, y1 - py)):
        if edge_p == 0:
            if edge_q <= 0:
                return False
            continue
        t = edge_q / edge_p
        if edge_p < 0:
            t0 = max(t0, t)
        else:
            t1 = min(t1, t)
        if t0 >= t1:
            return False
    return True


def conflicts(plan: Plan, rect, others) -> list[str]:
    """What a footprint runs into: walls (with their thickness), columns, walkways and the other machines' footprints
    (others: [(name, rect)]). A warning each; the place is kept (the operator decides)."""
    out = []
    for i, w in enumerate(plan.walls):
        half = w.thickness_mm / 2
        grown = (rect[0] - half, rect[1] - half, rect[2] + half, rect[3] + half)
        points = list(w.points) + ([w.points[0]] if w.closed else [])
        if any(_segment_hits(grown, p, q) for p, q in zip(points, points[1:])):
            out.append(f"crosses a wall (walls[{i}])")
    for c in plan.columns:
        h = c.size_mm / 2
        if _overlap(rect, (c.x_mm - h, c.y_mm - h, c.x_mm + h, c.y_mm + h)):
            out.append(f"stands on a column at ({c.x_mm:g}, {c.y_mm:g})")
    for a in plan.walkways:
        if _overlap(rect, (a.x_mm, a.y_mm, a.x_mm + a.width_mm, a.y_mm + a.height_mm)):
            out.append(f"stands on a walkway{' (' + a.label + ')' if a.label else ''}")
    for name, other in others:
        if _overlap(rect, other):
            out.append(f"overlaps {name}")
    return out
