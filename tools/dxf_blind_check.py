"""Blind check of the DXF input (roadmap stage 3): the binding of turnpilot.dxf_reader against expected answers
written by hand BEFORE the run. This file only reads the expected answers and compares; the binding comes
unchanged from turnpilot.dxf_reader. No API calls.

    python tools/dxf_blind_check.py dxf_blind --report instance/dxf_blind_report.md
    python tools/dxf_blind_check.py dxf_blind --check-etalon      # expected answers only, no DXF is opened

The folder holds <name>.dxf with <name>.etalon.md each (template: docs/dxf_etalon_template.md). A run starts
by checking that the frozen files still have the md5 recorded in tools/dxf_blind_freeze.json, and stops if not.

Metric: the outer-profile dimensions of the expected answers bound to the right place, summed over all
parts. Rows marked "внутрішній" or "поза критерієм", and the dimensions listed under "Поза критерієм", are
reported apart. A sheet whose basic steps break (no axis, no profile, not readable) counts all its outer
dimensions as wrong. Check badges and "geometry ≠ text" flags are reported apart, outside the metric.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from turnpilot import dxf_input, dxf_reader  # noqa: E402
from turnpilot.dxf_reader import BIND_TOL, CYRILLIC, SPECIAL  # noqa: E402

FREEZE_FILE = ROOT / "tools" / "dxf_blind_freeze.json"
FROZEN_FILES = ("turnpilot/dxf_reader.py", "turnpilot/dxf_input.py", "tools/dxf_blind_check.py", "requirements.txt")
CRITERION = 0.90
SECTION_TYPES = ("od_turn", "groove", "taper", "arc", "thread", "stock")


# --- freeze ---------------------------------------------------------------------------------------------

def file_md5(path: Path) -> str:
    """md5 of the file with LF line ends, so a CRLF checkout gives the same number."""
    return hashlib.md5(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def freeze_manifest() -> dict:
    import ezdxf
    return {"files": {name: file_md5(ROOT / name) for name in FROZEN_FILES}, "ezdxf": ezdxf.__version__}


def check_freeze(manifest_path: Path = FREEZE_FILE) -> list[str]:
    """Differences from the frozen manifest; empty when the code is as frozen."""
    if not manifest_path.exists():
        return [f"{manifest_path.name} not found: the code is not frozen"]
    frozen, now = json.loads(manifest_path.read_text(encoding="utf-8")), freeze_manifest()
    problems = [f"{name}: md5 {now['files'][name]} ≠ frozen {md5}" for name, md5 in frozen["files"].items()
                if now["files"].get(name) != md5]
    if frozen.get("ezdxf") != now["ezdxf"]:
        problems.append(f"ezdxf {now['ezdxf']} ≠ frozen {frozen.get('ezdxf')}")
    return problems


# --- expected answer ------------------------------------------------------------------------------------

def norm_text(text: str) -> str:
    """Compare texts as written: comma / point, "−" / "-", "×" / "x", Cyrillic / Latin letters, spaces."""
    text = re.sub(r"\s*\(.*?\)\s*", "", text)  # "(лівий)", "(правий)"
    text = text.replace("−", "-").translate(CYRILLIC).replace("x", "×").replace("X", "×").replace(" ", "")
    for code, char in SPECIAL.items():
        text = text.replace(code, char)
    return text.replace(",", ".")


def _number(text: str) -> float | None:
    m = re.search(r"\d+(?:[.,]\d+)?", text or "")
    return float(m.group().replace(",", ".")) if m else None


@dataclass
class ExpectedSection:
    k: int
    kind: str
    size: str
    length: float | None


@dataclass
class ExpectedDim:
    text: str  # as written
    raw: str  # "до чого прив'язаний"


@dataclass
class ExpectedPart:
    title: str
    sections: list = field(default_factory=list)
    dims: list = field(default_factory=list)
    out_of_scope: list = field(default_factory=list)  # texts under "Поза критерієм"
    inner_sections: int = 0


def read_etalon(path: Path) -> list[ExpectedPart]:
    parts, block = [], None
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            parts.append(ExpectedPart(line[3:].strip()))
            block = None
            continue
        if line.startswith("### "):
            title = line.lower()
            block = ("sections" if "зовнішні" in title else "inner" if "внутрішні" in title else
                     "out" if "поза критерієм" in title else "dims" if "розміри" in title else None)
            continue
        if not parts or not block or not line.startswith("|") or set(line.replace("|", "").strip()) <= {"-", " "}:
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if cells[0] in ("№", "текст", "що"):
            continue
        if block == "sections":
            parts[-1].sections.append(ExpectedSection(int(cells[0]), cells[1], cells[2], _number(cells[3])))
        elif block == "inner":
            parts[-1].inner_sections += 1
        elif block == "out":
            parts[-1].out_of_scope.append(cells[1] if len(cells) > 1 and cells[1] else cells[0])
        else:
            parts[-1].dims.append(ExpectedDim(cells[0], cells[1] if len(cells) > 1 else ""))
    return parts


def machined(part: ExpectedPart) -> list[ExpectedSection]:
    return sorted((s for s in part.sections if s.kind != "stock" and s.length is not None), key=lambda s: s.k)


def target_kind(raw: str) -> str:
    """outer / inner / out (of scope), from the row's "до чого прив'язаний"."""
    low = raw.lower()
    if "внутрішн" in low:
        return "inner"
    if "поза критерієм" in low:
        return "out"
    return "outer"


def expected_target(raw: str, bx: dict, sx: dict):
    """The geometric target of an outer row (boundaries / sections placed in x)."""
    low = raw.lower()
    m = re.search(r"межі\s*(\d+)\s*[–-]\s*(\d+)", low)
    if m:
        return ("x", bx[int(m.group(1))], bx[int(m.group(2))])
    m = re.search(r"фаска на межі\s*(\d+)", low)
    if m:
        return ("chamfer_at", bx[int(m.group(1))])
    m = re.search(r"\br\b.*на межі\s*(\d+)", low) or re.search(r"перехід.*межі\s*(\d+)", low)
    if m:
        return ("fillet_at", bx[int(m.group(1))])
    m = re.search(r"всередині ділянки\s*(\d+)\s*до межі\s*(\d+)", low)
    if m:
        return ("inside", sx[int(m.group(1))], bx[int(m.group(2))])
    numbers = []
    for group in re.findall(r"ділянк\w*\s*([\d,\sабоі]+)", low):
        numbers += [int(n) for n in re.findall(r"\d+", group)]
    if not numbers:
        raise ValueError(f"cannot read the target '{raw}'")
    return ("section_any", [sx[k] for k in numbers])


def check_etalon(path: Path) -> list[str]:
    """Format problems of an expected answer (no DXF is opened)."""
    problems = []
    try:
        parts = read_etalon(path)
    except (ValueError, IndexError) as e:
        return [f"{path.name}: cannot read: {e}"]
    if not parts:
        return [f"{path.name}: no '## ' part"]
    for part in parts:
        where = f"{path.name} / {part.title}"
        for s in part.sections:
            if s.kind not in SECTION_TYPES:
                problems.append(f"{where}: section {s.k}: unknown type '{s.kind}' (one of {', '.join(SECTION_TYPES)})")
            if s.kind != "stock" and s.length is None:
                problems.append(f"{where}: section {s.k}: no length")
        numbers = [s.k for s in machined(part)]
        if numbers != list(range(1, len(numbers) + 1)):
            problems.append(f"{where}: machined sections must be numbered 1, 2, … left to right (are {numbers})")
        bx = {k: 0.0 for k in range(len(numbers) + 1)}
        sx = {k: (0.0, 0.0) for k in numbers}
        if not part.dims:
            problems.append(f"{where}: no dimension rows")
        for d in part.dims:
            if target_kind(d.raw) != "outer":
                continue
            try:
                expected_target(d.raw, bx, sx)
            except (ValueError, KeyError) as e:
                problems.append(f"{where}: '{d.text}' → '{d.raw}': {e if isinstance(e, ValueError) else 'no such boundary / section ' + str(e)}")
    return problems


# --- comparison -----------------------------------------------------------------------------------------

def etalon_geometry(part: ExpectedPart, ours):
    """x of the expected boundaries and sections: from the expected lengths, shifted to where most of them
    coincide with our boundaries (as in the prototype's control run)."""
    offsets, ranges = [0.0], {}
    for s in machined(part):
        ranges[s.k] = (offsets[-1], offsets[-1] + s.length)
        offsets.append(offsets[-1] + s.length)
    best = (-1, 0.0)
    for b in ours.boundaries:
        for off in offsets:
            shift = b - off
            hits = sum(any(abs(shift + o - x) <= BIND_TOL for x in ours.boundaries) for o in offsets)
            if hits > best[0]:
                best = (hits, shift)
    shift = best[1]
    return ({k: shift + off for k, off in enumerate(offsets)},
            {k: (shift + a, shift + b) for k, (a, b) in ranges.items()}, best[0], len(offsets))


def matches(ours, expected) -> bool:
    if ours is None:
        return False
    kind = expected[0]

    def close(a, b):
        return abs(a - b) <= BIND_TOL

    if kind == "x":
        return ours[0] == "x" and close(ours[1], expected[1]) and close(ours[2], expected[2])
    if kind == "chamfer_at":
        return ours[0] == "chamfer_at" and close(ours[1], expected[1])
    if kind == "fillet_at":
        return ours[0] == "fillet_at" and close(ours[1], expected[1])
    if kind == "inside":
        (lo, hi), end = expected[1], expected[2]
        if ours[0] != "x":
            return False
        a, b = ours[1], ours[2]
        return (close(b, end) and lo - BIND_TOL <= a <= hi + BIND_TOL) or (close(a, end) and lo - BIND_TOL <= b <= hi + BIND_TOL)
    if kind == "section_any":
        return ours[0] == "section" and any(lo - BIND_TOL <= ours[1] and ours[2] <= hi + BIND_TOL
                                            for lo, hi in expected[1])
    return False


@dataclass
class RowResult:
    part: str
    text: str
    expected: str
    kind: str  # outer / inner / out
    ours: str
    right: bool | None  # None: outside the metric
    flag_expected: bool = False
    flags: list = field(default_factory=list)


@dataclass
class SheetResult:
    name: str
    rows: list = field(default_factory=list)
    broken: str | None = None
    sections: list = field(default_factory=list)  # (part, expected, ours, same)
    badges: list = field(default_factory=list)  # (part, section, check, value right)
    extra: list = field(default_factory=list)  # our dimensions with no expected row

    @property
    def outer(self):
        return [r for r in self.rows if r.kind == "outer"]


def compare_sheet(dxf_path: Path, etalon_path: Path) -> SheetResult:
    name = dxf_path.stem
    expected_parts = read_etalon(etalon_path)
    result = SheetResult(name)

    def all_wrong(reason):
        result.broken = reason
        for part in expected_parts:
            out = {norm_text(t) for t in part.out_of_scope}
            for d in part.dims:
                kind = "out" if norm_text(d.text) in out else target_kind(d.raw)
                result.rows.append(RowResult(part.title, d.text, d.raw, kind, reason, False if kind == "outer" else None))
        return result

    try:
        reading = dxf_reader.read_dxf(str(dxf_path))
    except dxf_reader.DxfReadError as e:
        return all_wrong(f"basic step broke: {e}")
    ours = sorted(reading.parts, key=lambda p: -p.axis_y)  # top to bottom, as the expected parts are written
    if len(ours) != len(expected_parts):
        return all_wrong(f"basic step broke: {len(ours)} parts found, {len(expected_parts)} expected")
    for n, (part, mine) in enumerate(zip(expected_parts, ours), start=1):
        bx, sx, hits, total = etalon_geometry(part, mine)
        for s in machined(part):
            lo, hi = sx[s.k]
            over = [o for o in mine.sections if o.x0 >= lo - BIND_TOL and o.x1 <= hi + BIND_TOL]
            same = len(over) == 1 and abs(over[0].x0 - lo) <= BIND_TOL and abs(over[0].x1 - hi) <= BIND_TOL
            result.sections.append((part.title, f"{s.k} {s.kind} {s.size} L{s.length:g}",
                                    "; ".join(f"{o.index} {o.kind} {o.size()} L{o.length:g}" for o in over) or "none",
                                    same))
        # check badges: our rows (dxf_input) whose section coincides with an expected one
        index = reading.parts.index(mine) + 1
        rows = dxf_input.part_result(reading, mine, index)
        for feature, row in zip(rows.data.features, rows.report["rows"]):
            if feature.type not in ("od_turn", "groove", "taper", "arc") or row["section"] is None:
                continue
            ours_section = mine.sections[row["section"] - 1]
            for s in machined(part):
                lo, hi = sx[s.k]
                if abs(ours_section.x0 - lo) <= BIND_TOL and abs(ours_section.x1 - hi) <= BIND_TOL:
                    right = feature.length is not None and abs(feature.length - s.length) <= BIND_TOL
                    result.badges.append((part.title, s.k, bool(row["notes"]) or feature.length_derived, right))
        # dimensions: pair rows with our dimensions of the same text, agreeing pairs first
        dims = reading.dims_of(mine)
        out = {norm_text(t) for t in part.out_of_scope}
        kinds = ["out" if norm_text(d.text) in out else target_kind(d.raw) for d in part.dims]
        targets = [expected_target(d.raw, bx, sx) if k == "outer" else None for d, k in zip(part.dims, kinds)]
        chosen, used = [None] * len(part.dims), set()
        for i, d in enumerate(part.dims):
            if targets[i] is not None:
                hit = next((o for o in dims if id(o) not in used and norm_text(o.text) == norm_text(d.text)
                            and matches(o.target, targets[i])), None)
                if hit:
                    chosen[i] = hit
                    used.add(id(hit))
        for i, d in enumerate(part.dims):
            if chosen[i] is None:
                rest = next((o for o in dims if id(o) not in used and norm_text(o.text) == norm_text(d.text)), None)
                if rest:
                    chosen[i] = rest
                    used.add(id(rest))
        for i, d in enumerate(part.dims):
            dim = chosen[i]
            ours_text = "NOT FOUND" if dim is None else (dim.binding or "not bound")
            right = (dim is not None and matches(dim.target, targets[i])) if kinds[i] == "outer" else None
            result.rows.append(RowResult(part.title, d.text, d.raw, kinds[i], ours_text, right,
                                         "не збігається" in d.raw.lower(), list(dim.flags) if dim else []))
        result.extra += [f"{part.title}: {o.text} ({o.binding})" for o in dims if id(o) not in used]
    return result


# --- report ---------------------------------------------------------------------------------------------

def report(results: list[SheetResult], freeze: dict) -> str:
    outer = [r for s in results for r in s.outer]
    right = sum(r.right for r in outer)
    share = right / len(outer) if outer else 0.0
    verdict = ("DXF CONFIRMED as an input" if share >= CRITERION else "DXF STAYS A PROTOTYPE (no patching for these files)")
    lines = ["# DXF blind check", "",
             "## Freeze", "", f"ezdxf {freeze['ezdxf']}", "", "| file | md5 (LF) |", "|---|---|"]
    lines += [f"| {name} | {md5} |" for name, md5 in freeze["files"].items()]
    lines += ["", "## Result", "",
              f"**Outer-profile dimensions bound right: {right} of {len(outer)} ({share:.1%}). "
              f"Criterion ≥ {CRITERION:.0%}: {'MET' if share >= CRITERION else 'NOT MET'}. {verdict}.**", "",
              "| sheet / part | right | outer dimensions | share |", "|---|---|---|---|"]
    for s in results:
        for title in dict.fromkeys(r.part for r in s.rows):
            rows = [r for r in s.outer if r.part == title]
            ok = sum(r.right for r in rows)
            lines.append(f"| {s.name} / {title} | {ok} | {len(rows)} | {ok / len(rows):.0%} |" if rows
                         else f"| {s.name} / {title} | — | 0 | — |")
    for s in results:
        lines += ["", f"## {s.name}", ""]
        if s.broken:
            lines += [f"**{s.broken}**", ""]
        if s.sections:
            lines += ["| part | expected section | ours over its range | same |", "|---|---|---|---|"]
            lines += [f"| {p} | {e} | {o} | {'✅' if same else '≠'} |" for p, e, o, same in s.sections]
            lines.append("")
        lines += ["| part | text | expected | ours | result |", "|---|---|---|---|---|"]
        for r in s.rows:
            mark = {"inner": "inner: outside the metric", "out": "outside the criterion"}.get(r.kind) \
                or ("✅" if r.right else "❌")
            flags = f" [{'; '.join(r.flags)}]" if r.flags else ""
            lines.append(f"| {r.part} | {r.text} | {r.expected} | {r.ours}{flags} | {mark} |")
        if s.extra:
            lines += ["", "Our dimensions with no expected row: " + "; ".join(s.extra)]
    badges = [b for s in results for b in s.badges]
    checked = [b for b in badges if b[2]]
    lines += ["", "## Outside the metric", "",
              f"- Sections with a \"check\" badge: {len(checked)} of {len(badges)} matched sections; "
              f"their length right: {sum(b[3] for b in checked)} of {len(checked)}. "
              f"Without the badge, length right: {sum(b[3] for b in badges if not b[2])} of {len(badges) - len(checked)}.",
              f"- Inner dimensions: {sum(r.kind == 'inner' for s in results for r in s.rows)}; "
              f"outside the criterion: {sum(r.kind == 'out' for s in results for r in s.rows)}.",
              f"- \"Drawing ≠ dimension\" marked in the expected answers: "
              f"{sum(r.flag_expected for s in results for r in s.rows)}; flagged by the code among them: "
              f"{sum(r.flag_expected and bool(r.flags) for s in results for r in s.rows)}; flags elsewhere: "
              f"{sum(bool(r.flags) and not r.flag_expected for s in results for r in s.rows)}."]
    return "\n".join(lines) + "\n"


def sheets(folder: Path) -> list[tuple[Path, Path]]:
    pairs, missing = [], []
    for dxf in sorted(folder.glob("*.dxf")):
        etalon = dxf.with_suffix(".etalon.md")
        (pairs.append((dxf, etalon)) if etalon.exists() else missing.append(dxf.name))
    if missing:
        raise SystemExit("No expected answer for: " + ", ".join(missing))
    return pairs


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("folder", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--check-etalon", action="store_true", help="check the expected answers only (no DXF opened)")
    args = parser.parse_args(argv)
    if args.check_etalon:
        etalons = sorted(args.folder.glob("*.etalon.md"))
        problems = [p for e in etalons for p in check_etalon(e)]
        print(f"{len(etalons)} expected answer(s); " + ("format OK" if not problems else f"{len(problems)} problem(s):"))
        for p in problems:
            print("  " + p)
        return 1 if problems or not etalons else 0
    problems = check_freeze()
    if problems:
        print("STOP: the code is not as frozen:\n  " + "\n  ".join(problems))
        return 2
    pairs = sheets(args.folder)
    format_problems = [p for _, e in pairs for p in check_etalon(e)]
    if format_problems:
        print("STOP: expected answers with format problems:\n  " + "\n  ".join(format_problems))
        return 3
    text = report([compare_sheet(dxf, etalon) for dxf, etalon in pairs], freeze_manifest())
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(text, encoding="utf-8", newline="\n")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
