"""The stock after each operation, for the 3D view: the simulation's own grid (gcode/simulate.py, the radius per z
every 0.01 mm) at the end of every block N, nothing else: the outer radius, and the hole's (0: solid) when a block
has one. Flats are not in it.

A state is a polyline (z, r) from the stock's end face (z_top) to the jaws, simplified (Douglas-Peucker) to within
SIMPLIFY_MM of the grid; "inner" the hole's polyline the same way, only in a state that has a hole.
"""
from __future__ import annotations

SIMPLIFY_MM = 0.01


def profile_after(result, k: int) -> list[float]:
    """The stock's radius per grid z after the first k blocks of the program: k = 0 the bar, k = len(blocks) the stock
    the program leaves (result.final_stock)."""
    if not 0 <= k <= len(result.blocks):
        raise IndexError(f"block {k}: the program has {len(result.blocks)} blocks")
    return list(result.initial_stock) if k == 0 else list(result.blocks[k - 1].stock)


def simplify(points: list[tuple[float, float]], tol: float = SIMPLIFY_MM) -> list[tuple[float, float]]:
    """Douglas-Peucker: the fewest points whose polyline stays within tol of every point."""
    if len(points) < 3:
        return list(points)
    # the inner points of a run of the same radius say nothing
    pts = [points[0]] + [p for i, p in enumerate(points[1:-1], start=1)
                         if not (points[i - 1][1] == p[1] == points[i + 1][1])] + [points[-1]]
    keep = [False] * len(pts)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        a, b = stack.pop()
        (z0, r0), (z1, r1) = pts[a], pts[b]
        dz, dr = z1 - z0, r1 - r0
        length = (dz * dz + dr * dr) ** 0.5
        far, worst = -1.0, None
        for i in range(a + 1, b):
            z, r = pts[i]
            d = abs(dz * (r0 - r) - (z0 - z) * dr) / length if length else abs(r - r0)
            if d > far:
                far, worst = d, i
        if worst is not None and far > tol:
            keep[worst] = True
            stack += [(a, worst), (worst, b)]
    return [p for p, k in zip(pts, keep) if k]


def polyline(stock: list[float], z_top: float, dz: float) -> list[list[float]]:
    return [[round(z, 3), round(r, 3)] for z, r in simplify([(z_top - i * dz, r) for i, r in enumerate(stock)])]


def states(result, dz: float) -> list[dict]:
    """[{n, title, points}]: the bar (n None), then the stock at the end of every block in program order."""
    out = [{"n": None, "title": "BAR", "points": polyline(result.initial_stock, result.z_top, dz)}]
    for block in result.blocks:
        state = {"n": block.number, "title": block.title, "points": polyline(block.stock, result.z_top, dz)}
        if any(r > 0 for r in block.bore):
            # the hole only where there is material around it (cut off beyond the part: none)
            hole = [min(r, s) for r, s in zip(block.bore, block.stock)]
            state["inner"] = polyline(hole, result.z_top, dz)
        out.append(state)
    return out
