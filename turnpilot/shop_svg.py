"""The shop's plan as SVG, in mm (viewBox = the plan's size with a margin); line widths in screen px
(non-scaling-stroke), colours from the page's CSS variables (light and dark theme). The machines are drawn over it by
the page's script from their data."""
from __future__ import annotations

from markupsafe import Markup, escape

from .shop_plan import Plan

MARGIN_MM = 1500  # around the sheet, for the dimensions


def _f(v: float) -> str:
    return f"{v:g}"


def view_box(plan: Plan) -> str:
    return f"{-MARGIN_MM} {-MARGIN_MM} {_f(plan.width_mm + 2 * MARGIN_MM)} {_f(plan.height_mm + 2 * MARGIN_MM)}"


def text_size(plan: Plan) -> float:
    """A label's height in mm: 1/80 of the plan's longer side (some 11 px on a plan 900 px wide)."""
    return round(max(plan.width_mm, plan.height_mm) / 80)


def plan_svg(plan: Plan) -> Markup:
    t = text_size(plan)
    out = [f'<rect x="0" y="0" width="{_f(plan.width_mm)}" height="{_f(plan.height_mm)}" class="p-sheet"/>']

    def area(a, cls, label_cls="p-label"):
        out.append(f'<rect x="{_f(a.x_mm)}" y="{_f(a.y_mm)}" width="{_f(a.width_mm)}" height="{_f(a.height_mm)}" '
                   f'class="{cls}" vector-effect="non-scaling-stroke"/>')
        if a.label:
            vertical = a.height_mm > 2 * a.width_mm
            cx, cy = a.x_mm + a.width_mm / 2, a.y_mm + a.height_mm / 2
            rot = f' transform="rotate(-90 {_f(cx)} {_f(cy)})"' if vertical else ""
            out.append(f'<text x="{_f(cx)}" y="{_f(cy + t / 3)}" font-size="{_f(t * .8)}" text-anchor="middle" '
                       f'class="{label_cls}"{rot}>{escape(a.label)}</text>')

    for a in plan.walkways:
        area(a, "p-walkway")
    for a in plan.zones:
        area(a, "p-zone")
    for a in plan.storage:
        area(a, "p-storage")
    for a in plan.fixtures:
        area(a, "p-fixture")
    for w in plan.walls:
        points = " ".join(f"{_f(x)},{_f(y)}" for x, y in w.points)
        tag = "polygon" if w.closed else "polyline"
        out.append(f'<{tag} points="{points}" class="p-wall" stroke-width="{_f(w.thickness_mm)}"/>')
    for o in plan.openings:  # a gap in the wall, then the opening's own sign
        x0, y0 = o.x_mm, o.y_mm
        x1, y1 = (x0 + o.width_mm, y0) if o.along == "x" else (x0, y0 + o.width_mm)
        out.append(f'<line x1="{_f(x0)}" y1="{_f(y0)}" x2="{_f(x1)}" y2="{_f(y1)}" class="p-gap" '
                   f'stroke-width="{_f(max(w.thickness_mm for w in plan.walls) * 1.6 if plan.walls else 400)}"/>')
        out.append(f'<line x1="{_f(x0)}" y1="{_f(y0)}" x2="{_f(x1)}" y2="{_f(y1)}" class="p-opening p-{o.kind}" '
                   f'vector-effect="non-scaling-stroke"/>')
        if o.label:
            cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
            rot = f' transform="rotate(-90 {_f(cx)} {_f(cy)})"' if o.along == "y" else ""
            out.append(f'<text x="{_f(cx)}" y="{_f(cy - t * .6)}" font-size="{_f(t * .75)}" text-anchor="middle" '
                       f'class="p-label"{rot}>{escape(o.label)}</text>')
    for c in plan.columns:
        h = c.size_mm / 2
        out.append(f'<rect x="{_f(c.x_mm - h)}" y="{_f(c.y_mm - h)}" width="{_f(c.size_mm)}" height="{_f(c.size_mm)}" '
                   f'class="p-column"/>')
    for label in plan.labels:
        rot = f' transform="rotate(-90 {_f(label.x_mm)} {_f(label.y_mm)})"' if label.rotate == 90 else ""
        out.append(f'<text x="{_f(label.x_mm)}" y="{_f(label.y_mm)}" font-size="{_f(t)}" class="p-zone-label"{rot}>'
                   f'{escape(label.text)}</text>')
    # overall dimensions
    out.append(f'<text x="{_f(plan.width_mm / 2)}" y="{_f(-MARGIN_MM / 3)}" font-size="{_f(t)}" text-anchor="middle" '
               f'class="p-label">{_f(plan.width_mm)}</text>')
    out.append(f'<text x="{_f(plan.width_mm + MARGIN_MM / 2)}" y="{_f(plan.height_mm / 2)}" font-size="{_f(t)}" '
               f'text-anchor="middle" class="p-label" transform="rotate(-90 {_f(plan.width_mm + MARGIN_MM / 2)} '
               f'{_f(plan.height_mm / 2)})">{_f(plan.height_mm)}</text>')
    return Markup("\n".join(out))
