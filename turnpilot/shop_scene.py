"""The shop in 3D (stage 7.2b): the plan's elements and the machines as simple shapes, in mm, for static/shop3d.js to
draw. Built from the same data as the 2D plan (shop_plan.Plan and services.shop_machines): statuses, alarms and
conflicts are the 2D page's, nothing is computed again.

Coordinates: X to the right and Y down the sheet as on the plan, the height H up from the floor. A shape is a box
(centre x, y, h; size along x, y, h) or a cylinder (centre, radius, length, axis "h" / "x" / "y"), with a role the
page colours (body, dark, glass, stone, status, accent).

Heights are not in a plan, and a machine's only when the operator or the passport gave it. Where they are not
given, the conditional heights below are the only place they come from, and the scene says which are conditional
(the page shows it): the wall height of the plan or a wall's own, else CONDITIONAL_WALL_HEIGHT_MM; a machine's
height, else its type's CONDITIONAL_MACHINE_HEIGHT_MM. Zones, storage and fixtures stay on the floor (outlines with
their labels); doors, roller doors, exits and windows are panels on their walls (the walls are not cut).
"""
from __future__ import annotations

import math

# CONDITIONAL (not from a drawing or a passport): the only heights the 3D view makes up, marked "conditional" there.
CONDITIONAL_WALL_HEIGHT_MM = 3000
CONDITIONAL_MACHINE_HEIGHT_MM = {"cnc_lathe": 1900, "manual_lathe": 1300, "mill": 2500, "saw": 1200, "cmm": 2200}
UNKNOWN_MACHINE_MM = 800  # a machine with no type or footprint: a grey frame of this size, not to scale (as in 2D)
STATUS_PLATE_MM = 30  # the status plate on the floor under a machine, its thickness
STATUS_MARGIN_MM = 300  # how far it shows round the footprint (seen from afar)
OPENING_HEIGHT_MM = {"door": 2100, "roller_door": None, "exit": 2100, "window": 1200}  # CONDITIONAL; None: wall high
WINDOW_SILL_MM = 900  # CONDITIONAL


def box(x, y, h, sx, sy, sh, role):
    return {"shape": "box", "x": x, "y": y, "h": h, "sx": sx, "sy": sy, "sh": sh, "role": role}


def cylinder(x, y, h, r, length, axis, role):
    return {"shape": "cylinder", "x": x, "y": y, "h": h, "r": r, "length": length, "axis": axis, "role": role}


# --- the machines' shapes: in the machine's own frame, x along its length (the spindle), y across, centre (0, 0) ------

def _cnc_lathe(L, W, H):
    return [box(0, 0, H * .45, L, W, H * .9, "body"),
            box(-L * .375, 0, H * .46, L * .25, W * .96, H * .92, "dark"),  # the headstock end
            box(L * .1, W / 2, H * .52, L * .5, W * .02, H * .44, "glass"),  # the door's window (the front: +y)
            box(L * .43, W / 2, H * .66, L * .1, W * .04, H * .34, "dark"),  # the control panel
            box(0, 0, H * .92, L, W, H * .04, "status")]  # the status band on top


def _manual_lathe(L, W, H):
    return [box(-L * .35, 0, H * .27, L * .12, W * .6, H * .54, "dark"),  # the stand's legs
            box(L * .35, 0, H * .27, L * .12, W * .6, H * .54, "dark"),
            box(0, 0, H * .61, L * .95, W * .45, H * .14, "body"),  # the bed
            box(-L * .38, 0, H * .78, L * .22, W * .8, H * .44, "body"),  # the headstock
            box(-L * .02, 0, H * .78, L * .14, W * .7, H * .2, "dark"),  # the carriage
            box(L * .38, 0, H * .76, L * .1, W * .4, H * .16, "body"),  # the tailstock
            box(-L * .38, 0, H * .99, L * .22, W * .8, H * .02, "status")]


def _mill(L, W, H):
    return [box(0, 0, H * .42, L, W, H * .84, "body"),
            box(L * .05, W / 2, H * .5, L * .55, W * .02, H * .42, "glass"),
            cylinder(-L * .32, -W * .1, H * .7, min(L, W) * .2, W * .12, "y", "dark"),  # the tool magazine
            box(0, -W * .15, H * .92, L * .28, W * .32, H * .16, "dark"),  # the spindle head
            box(L * .44, W / 2, H * .6, L * .1, W * .04, H * .32, "dark"),  # the control panel
            box(0, 0, H * .85, L, W, H * .02, "status")]


def _saw(L, W, H):
    base = L * .3
    return [box(-L / 2 + base / 2, 0, H * .3, base, W, H * .6, "body"),
            cylinder(-L / 2 + base / 2, 0, H * .78, H * .2, W * .08, "y", "dark"),  # the blade
            box(-L / 2 + base / 2, 0, H * .66, base * .9, W * .5, H * .12, "accent"),  # the arm
            box(base / 2, 0, H * .38, L - base, W * .4, H * .06, "body"),  # the roller conveyor
            box(base / 2, 0, H * .18, L - base, W * .1, H * .36, "dark"),
            box(-L / 2 + base / 2, 0, H * .61, base, W, H * .02, "status")]


def _cmm(L, W, H):
    return [box(0, 0, H * .175, L, W, H * .35, "stone"),  # the granite table
            box(0, -W * .45, H * .64, L * .06, W * .06, H * .62, "body"),  # the gantry's two posts
            box(0, W * .45, H * .64, L * .06, W * .06, H * .62, "body"),
            box(0, 0, H * .97, L * .08, W * .96, H * .06, "body"),  # its beam
            box(0, 0, H * .72, L * .05, W * .05, H * .44, "dark"),  # the probe's column
            box(0, 0, H * .355, L, W, H * .01, "status")]


SHAPES = {"cnc_lathe": _cnc_lathe, "manual_lathe": _manual_lathe, "mill": _mill, "saw": _saw, "cmm": _cmm}


def machine(m: dict) -> dict:
    """A placed machine (an item of services.shop_machines) as shapes in its own frame, with its place and turn."""
    known = bool(m.get("kind") in SHAPES and m.get("length") and m.get("width"))
    L, W = (m["length"], m["width"]) if known else (UNKNOWN_MACHINE_MM, UNKNOWN_MACHINE_MM)
    if m.get("height"):
        H, source = m["height"], "machine"
    elif known:
        H, source = CONDITIONAL_MACHINE_HEIGHT_MM[m["kind"]], "conditional"
    else:
        H, source = UNKNOWN_MACHINE_MM, "unknown"
    parts = SHAPES[m["kind"]](L, W, H) if known else []
    plate = box(0, 0, STATUS_PLATE_MM / 2, L + 2 * STATUS_MARGIN_MM, W + 2 * STATUS_MARGIN_MM, STATUS_PLATE_MM,
                "status")
    return {"id": m["id"], "name": m["name"], "kind": m.get("kind"), "known": known, "x": m["x"], "y": m["y"],
            "rotation": m.get("rotation") or 0, "length": L, "width": W, "height": H, "height_source": source,
            "status": m["status"], "alarm": bool(m.get("alarm")), "conflicts": list(m.get("conflicts") or []),
            "parts": [plate] + parts}


# --- the plan --------------------------------------------------------------------------------------------------------

def _wall_height(plan, wall):
    if getattr(wall, "height_mm", None):
        return wall.height_mm, False
    if getattr(plan, "wall_height_mm", None):
        return plan.wall_height_mm, False
    return CONDITIONAL_WALL_HEIGHT_MM, True


def _area(a, role):
    return {"x": a.x_mm, "y": a.y_mm, "w": a.width_mm, "d": a.height_mm, "label": a.label, "role": role}


def build(plan, machines: list[dict]) -> dict:
    """The scene: the floor, the walls (a box per segment), the openings' panels, the columns, the floor areas, the
    labels, and the placed machines; with what is conditional."""
    walls, conditional_walls = [], False
    for wall in plan.walls:
        height, conditional = _wall_height(plan, wall)
        conditional_walls |= conditional
        points = list(wall.points) + ([wall.points[0]] if wall.closed else [])
        for (x0, y0), (x1, y1) in zip(points, points[1:]):
            if math.hypot(x1 - x0, y1 - y0) < 1e-6:
                continue
            walls.append({"x0": x0, "y0": y0, "x1": x1, "y1": y1, "thickness": wall.thickness_mm, "height": height,
                          "conditional": conditional})
    top = max((w["height"] for w in walls), default=CONDITIONAL_WALL_HEIGHT_MM)
    openings = []
    for o in plan.openings:
        height = OPENING_HEIGHT_MM[o.kind] or top
        sill = WINDOW_SILL_MM if o.kind == "window" else 0.0
        openings.append({"kind": o.kind, "x": o.x_mm, "y": o.y_mm, "width": o.width_mm, "along": o.along,
                         "sill": sill, "height": min(height, top - sill), "label": o.label})
    columns_height = top
    scene = {
        "floor": {"w": plan.width_mm, "d": plan.height_mm},
        "walls": walls,
        "openings": openings,
        "columns": [{"x": c.x_mm, "y": c.y_mm, "size": c.size_mm, "height": columns_height} for c in plan.columns],
        "areas": ([_area(a, "walkway") for a in plan.walkways] + [_area(a, "zone") for a in plan.zones]
                  + [_area(a, "storage") for a in plan.storage] + [_area(a, "fixture") for a in plan.fixtures]),
        "labels": [{"x": lb.x_mm, "y": lb.y_mm, "text": lb.text} for lb in plan.labels],
        "machines": [machine(m) for m in machines if m.get("x") is not None and not m.get("removed")],
    }
    scene["conditional"] = {
        "walls": conditional_walls, "wall_height": CONDITIONAL_WALL_HEIGHT_MM if conditional_walls else None,
        "machines": [m["name"] for m in scene["machines"] if m["height_source"] == "conditional"],
        "openings": bool(openings),
    }
    return scene
