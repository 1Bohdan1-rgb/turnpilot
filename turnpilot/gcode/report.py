"""The simulation's messages for the page: one row per reason, with the lines' range and the values' ranges."""
from __future__ import annotations

import re

NUMBER = re.compile(r"-?\d+(?:\.\d*)?")  # "43." is one number too (Fanuc's whole mm)


def _span(values: list[str]) -> str:
    numbers = [float(v) for v in values]
    low, high = min(numbers), max(numbers)
    if low == high:
        return values[0]
    return f"{values[numbers.index(low)]}…{values[numbers.index(high)]}"


def group(entries) -> list[dict]:
    """[(line, message)] -> [{"line", "last", "count", "message"}]: messages that differ only in their numbers are one
    reason; its numbers become "min…max" where they differ. In the order the reasons first appear."""
    groups: dict[str, dict] = {}
    for line, message in entries:
        key = NUMBER.sub("#", message)
        g = groups.setdefault(key, {"lines": [], "values": [], "parts": NUMBER.split(message)})
        g["lines"].append(line)
        g["values"].append(NUMBER.findall(message))
    rows = []
    for g in groups.values():
        slots = list(zip(*g["values"]))
        text = g["parts"][0] + "".join(_span(list(v)) + part for v, part in zip(slots, g["parts"][1:]))
        lines = sorted({line for line in g["lines"] if line})
        rows.append({"line": lines[0] if lines else None, "last": lines[-1] if lines else None,
                     "count": len(g["lines"]), "message": text})
    return rows
