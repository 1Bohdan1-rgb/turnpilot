"""The simulation as an SVG drawing: Z across (the chuck on the left, Z0 on the right), the radius up from the
axis. The blank, the jaws, the stock the program leaves, the finished profile, and every move (rapids dashed,
feeds solid, thread passes apart); the moves of lines with an error are marked. Colours come from CSS classes.
"""
from __future__ import annotations

from html import escape

WIDTH = 1000  # px of the drawing (it scales to the page)


def render(segments, final_stock, z_top, dz, profile_points, stock_radius, stickout, chuck_safety,
           error_lines=(), tools=None) -> str:
    tools = tools or {}
    max_r = stock_radius + 4
    z_min, z_max = -stickout - 8, z_top + 6
    scale = (WIDTH - 40) / (z_max - z_min)
    height = int((max_r + 3) * scale) + 30
    base = height - 20  # the axis

    def px(z):
        return round(20 + (z - z_min) * scale, 2)

    def py(r):
        return round(base - r * scale, 2)

    def x_r(x):  # a diameter written in the program -> a radius on the drawing (past the axis: below it)
        return min(max(x, -4.0), 2 * max_r) / 2

    parts = [f'<svg class="gcode-sim" viewBox="0 0 {WIDTH} {height}" role="img" '
             f'aria-label="Simulation of the program: the part and the tool moves">']
    parts.append('<defs><pattern id="jaw-hatch" width="6" height="6" patternUnits="userSpaceOnUse" '
                 'patternTransform="rotate(45)"><line x1="0" y1="0" x2="0" y2="6" class="hatch"/></pattern></defs>')
    # the jaws and the safety distance
    jaw_z = -stickout
    parts.append(f'<rect class="jaws" x="{px(z_min)}" y="{py(stock_radius + 3)}" width="{px(jaw_z) - px(z_min)}" '
                 f'height="{py(stock_radius) - py(stock_radius + 3) + (base - py(stock_radius)) * 0.35}"/>')
    safety = jaw_z + chuck_safety
    parts.append(f'<line class="safety" x1="{px(safety)}" y1="{py(max_r)}" x2="{px(safety)}" y2="{base}"/>')
    parts.append(f'<text class="label" x="{px(z_min) + 4}" y="{py(stock_radius + 3) - 4}">jaws</text>')
    # the blank, and the stock the program leaves
    parts.append(f'<rect class="blank" x="{px(jaw_z)}" y="{py(stock_radius)}" width="{px(z_top) - px(jaw_z)}" '
                 f'height="{base - py(stock_radius)}"/>')
    if final_stock:
        pts = [f"{px(z_top)},{base}"]
        for i, r in enumerate(final_stock):
            z = z_top - i * dz
            pts.append(f"{px(z)},{py(r)}")
        pts.append(f"{px(z_top - (len(final_stock) - 1) * dz)},{base}")
        parts.append(f'<polygon class="stock" points="{" ".join(pts)}"/>')
    if profile_points:
        pts = " ".join(f"{px(z)},{py(r)}" for z, r in profile_points)
        parts.append(f'<polyline class="profile" points="{pts}"/>')
    # the axis, Z0
    parts.append(f'<line class="axis" x1="{px(z_min)}" y1="{base}" x2="{px(z_max)}" y2="{base}"/>')
    parts.append(f'<line class="zero" x1="{px(0)}" y1="{py(max_r)}" x2="{px(0)}" y2="{base + 6}"/>')
    parts.append(f'<text class="label" x="{px(0) + 3}" y="{py(max_r) + 10}">Z0</text>')
    parts.append(f'<text class="label" x="{px(z_max) - 30}" y="{base - 4}">X0</text>')
    # the moves
    errors = set(error_lines)
    for i, segment in enumerate(segments):
        kind, x0, z0, x1, z1, line, tool = segment[:7]
        arc = segment[7] if len(segment) > 7 else None
        if z0 > z_max + 40 or x0 > 1e4:  # from the reference point: drawn from the edge of the view
            z0, x0 = min(z0, z_max), min(x0, 2 * max_r)
        cls = {"rapid": "rapid", "feed": "feed", "thread": "thread", "arc": "feed"}[kind] + \
            (" err" if line in errors else "")
        title = escape(f"line {line}, T{tool:02d} {tools.get(tool, '')}: {kind}" if tool else f"line {line}: {kind}")
        if arc:  # the radius up on the drawing turns a counter-clockwise arc (G03) clockwise on screen
            _cz, _cr, radius, clockwise = arc
            rad = round(radius * scale, 2)
            parts.append(f'<path class="mv {cls}" data-i="{i}" data-line="{line}" fill="none" '
                         f'd="M {px(z0)} {py(x_r(x0))} A {rad} {rad} 0 0 {0 if clockwise else 1} {px(z1)} '
                         f'{py(x_r(x1))}"><title>{title}</title></path>')
        else:
            parts.append(f'<line class="mv {cls}" data-i="{i}" data-line="{line}" x1="{px(z0)}" y1="{py(x_r(x0))}" '
                         f'x2="{px(z1)}" y2="{py(x_r(x1))}"><title>{title}</title></line>')
        if line in errors:
            parts.append(f'<circle class="errmark" cx="{px(z1)}" cy="{py(x_r(x1))}" r="4"><title>{title}</title>'
                         f'</circle>')
    parts.append("</svg>")
    return "".join(parts)
