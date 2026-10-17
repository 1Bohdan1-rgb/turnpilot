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
