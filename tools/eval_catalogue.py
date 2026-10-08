"""Measure the catalogue reading (turnpilot/catalogue_reader.py) against a hand-typed catalogue file.
Uses the REAL API and costs money: one call per --read. Without --yes it shows the estimate and asks first.

The expected answer is a hand-typed file (docs/turnpilot_catalog_*.md), parsed as the app imports it. Only its
rows whose pages were read are expected; values the file marks as read off a graph are expected NOT to be taken.

    python tools/eval_catalogue.py --read TT.pdf "301=A278, 302=A279, ..." --read SRT.pdf "B70, B71"
        --expected docs/turnpilot_catalog_P1.2.md --output instance/eval_catalogue_p12.md [--yes] [--estimate]
    python tools/eval_catalogue.py --rescore instance/eval_catalogue_p12.runs.json --output ...

--read PDF PAGES: PAGES are PDF page numbers or printed labels (when the PDF has labels); "301=A283" reads PDF
page 301 and takes it as the catalogue's page A283 (to know which expected rows are on it).
Run with ANTHROPIC_BASE_URL unset.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from turnpilot import catalogue_file, catalogue_reader  # noqa: E402
from turnpilot.cutting_data import group_matches, normalize_code  # noqa: E402
from turnpilot.planner import parse_vc_points  # noqa: E402

PRICES = {  # $ per million tokens (input, output), Anthropic first-party API
    "claude-sonnet-5": (2.0, 10.0), "claude-sonnet-5-5": (2.0, 10.0), "claude-opus-5-5": (4.0, 20.0),
    "claude-opus-5": (5.0, 25.0), "claude-haiku-5-5": (0.10, 0.50),
}
GEOMETRY = ("ap_min", "ap_rec", "ap_max", "f_min", "f_rec", "f_max")
LABEL = re.compile(r"[A-H]\d+")


def expand_pages(text: str | None) -> set[str]:
    """"A278–A279, A283" -> {"A278", "A279", "A283"}."""
    out = set()
    for part in (text or "").split(","):
        m = re.fullmatch(r"\s*([A-H])(\d+)\s*[–-]\s*[A-H]?(\d+)\s*", part)
        if m:
            out.update(f"{m.group(1)}{n}" for n in range(int(m.group(2)), int(m.group(3)) + 1))
        elif LABEL.fullmatch(part.strip()):
            out.add(part.strip())
    return out


def resolve(pdf: str, spec: str) -> tuple[list[int], dict[int, str]]:
    """PDF page numbers to read and their catalogue labels."""
    pages = catalogue_reader.read_pages(pdf)
    numbers, labels = [], {}
    for item in (i.strip() for i in spec.split(",")):
        if not item:
            continue
        if "=" in item:
            number, label = (x.strip() for x in item.split("=", 1))
            numbers.append(int(number))
            labels[int(number)] = label
            continue
        for number in catalogue_reader.resolve_pages(item, pages):
            numbers.append(number)
            labels[number] = pages["labels"][number - 1] or item
    return sorted(set(numbers)), labels


def expected_rows(path: str, labels: set[str]) -> list[dict]:
    rows = catalogue_file.parse_hand_typed(Path(path).read_text(encoding="utf-8"), Path(path).name)
    return [r for r in rows if expand_pages(r.get("page")) & labels]


def codes_of(rows) -> str:
    return "\n".join(dict.fromkeys(r.get("insert_code") or r.get("grade") for r in rows))


def numbers_of(row) -> dict:
    """The numbers a row gives: ap / f by name, Vc points by their feed, vc_min / vc_max."""
    out = {k: row.get(k) for k in GEOMETRY + ("vc_min", "vc_max") if row.get(k) is not None}
    for f, vc in parse_vc_points(row.get("vc_points")):
        out[f"Vc at f {f:g}" if f else "Vc rec"] = vc
    return out


def same_thing(expected, got) -> bool:
    if expected["kind"] != got["kind"] or not group_matches(got["material_group"], expected["material_group"]):
        return False
    if expected["kind"] == "geometry":
        return normalize_code(expected["insert_code"]) == normalize_code(got["insert_code"])
    return (normalize_code(expected["grade"]) == normalize_code(got["grade"])
            and expected["application"] == got["application"])


def score(expected: list[dict], taken: list[dict], not_taken: list[dict]) -> dict:
    result = {"right": 0, "total": 0, "wrong": [], "missing": [], "extra": [], "graph": [], "rows": []}
    used = set()
    for exp in expected:
        name = exp.get("insert_code") or f"{exp['grade']} {exp['application']}"
        if exp["from_graph"]:
            taken_graph = [g for g in taken if same_thing(exp, g)]
            result["graph"].append((name, "taken (should not be)" if taken_graph else "not taken (right)"))
            used.update(id(g) for g in taken_graph)
            continue
        got = next((g for g in taken if id(g) not in used and same_thing(exp, g)), None)
        want = numbers_of(exp)
        result["total"] += len(want)
        if got is None:
            result["missing"].append((name, len(want)))
            result["rows"].append((name, 0, len(want), "missing"))
            continue
        used.add(id(got))
        have = numbers_of(got)
        right = sum(1 for k, v in want.items() if k in have and abs(have[k] - v) < 1e-9)
        result["right"] += right
        for k, v in want.items():
            if not (k in have and abs(have[k] - v) < 1e-9):
                result["wrong"].append((name, k, v, have.get(k), "; ".join(got["checks"]) or "SILENT"))
        result["rows"].append((name, right, len(want), "; ".join(got["checks"])))
    result["extra"] = [(g.get("insert_code") or f"{g['grade']} {g['application']}", g["material_group"],
                        numbers_of(g)) for g in taken if id(g) not in used]
    result["not_taken"] = [(item["row"].get("insert_code") or item["row"].get("grade"), item["reason"])
                           for item in not_taken]
    return result


def cost(model, input_tokens, output_tokens) -> float | None:
    price = PRICES.get(model)
    return None if price is None else input_tokens / 1e6 * price[0] + output_tokens / 1e6 * price[1]


def report(runs: list[dict], expected_file: str) -> str:
    lines = [f"# Catalogue reading eval ({datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC)", "",
             f"Expected answer: `{expected_file}` (hand-typed; rows on the read pages only).", ""]
    total_right = total = silent = 0
    for run in runs:
        labels = {int(k): v for k, v in run["labels"].items()}
        expected = expected_rows(expected_file, set(labels.values()))
        texts = catalogue_reader.read_pages(run["pdf"])["texts"]
        taken, not_taken = catalogue_reader.check_reading(
            run["tool_input"], {n: texts[n - 1] for n in run["pages"]}, run["groups"], run["codes"])
        s = score(expected, taken, not_taken)
        total_right, total = total_right + s["right"], total + s["total"]
        silent += sum(1 for w in s["wrong"] if w[4] == "SILENT")
        usage = run.get("usage") or {}
        lines += [f"## {Path(run['pdf']).name}: pages " + ", ".join(f"{labels.get(p, p)} (PDF {p})" for p in run["pages"]), "",
                  f"Model {run['model']}, prompt {run['prompt_version']}; input {usage.get('input_tokens')} / "
                  f"output {usage.get('output_tokens')} tokens.", "",
                  f"**Numbers right: {s['right']} / {s['total']}**", "",
                  "| Row | Right | Of | Code checks |", "|---|---|---|---|"]
        lines += [f"| {n} | {r} | {t} | {c} |" for n, r, t, c in s["rows"]]
        if s["wrong"]:
            lines += ["", "Wrong numbers (expected → read; the code's check, or SILENT):", ""]
            lines += [f"- {n} {k}: {v:g} → {'—' if g is None else f'{g:g}'} ({c})" for n, k, v, g, c in s["wrong"]]
        if s["missing"]:
            lines += ["", "Rows not read: " + ", ".join(f"{n} ({k} numbers)" for n, k in s["missing"])]
        if s["graph"]:
            lines += ["", "Graph values (expected not taken): " + ", ".join(f"{n}: {r}" for n, r in s["graph"])]
        if s["extra"]:
            lines += ["", "Taken rows not in the expected answer (not scored):", ""]
            lines += [f"- {n} ({g}): {v}" for n, g, v in s["extra"]]
        if s["not_taken"]:
            lines += ["", "Not taken by the code:", ""] + [f"- {n}: {r}" for n, r in s["not_taken"]]
        lines.append("")
    share = f"{100 * total_right / total:.1f}%" if total else "—"
    lines[4:4] = [f"**All: {total_right} / {total} numbers right ({share}); wrong numbers without a code check: "
                  f"{silent}.**", ""]
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--read", nargs=2, action="append", metavar=("PDF", "PAGES"), default=[])
    parser.add_argument("--expected", default="docs/turnpilot_catalog_P1.2.md")
    parser.add_argument("--groups", default="P1.2")
    parser.add_argument("--model", default=None, help="default: ANTHROPIC_MODEL (.env), as the app")
    parser.add_argument("--output", required=True, help="the report (.md); the runs go next to it (.runs.json)")
    parser.add_argument("--estimate", action="store_true", help="show the calls, tokens and cost; no call")
    parser.add_argument("--yes", action="store_true", help="do not ask for confirmation")
    parser.add_argument("--rescore", metavar="RUNS_JSON", help="score saved runs again (no call)")
    args = parser.parse_args(argv)
    output = Path(args.output)

    if args.rescore:
        runs = json.loads(Path(args.rescore).read_text(encoding="utf-8"))
        output.write_text(report(runs, args.expected), encoding="utf-8", newline="\n")
        print(f"Report: {output}")
        return 0

    import os

    from dotenv import load_dotenv
    load_dotenv()
    model = args.model or os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5")
    plans = []
    for pdf, spec in args.read:
        pages, labels = resolve(pdf, spec)
        expected = expected_rows(args.expected, set(labels.values()))
        codes = codes_of(expected)
        est = catalogue_reader.estimate(pdf, pages, args.groups, codes)
        plans.append(dict(pdf=pdf, pages=pages, labels=labels, codes=codes, estimate=est, expected=len(expected)))
    calls = len(plans)
    input_tokens = sum(p["estimate"]["input_tokens"] for p in plans)
    output_max = sum(p["estimate"]["max_output_tokens"] for p in plans)
    print(f"Model {model}; prompt {catalogue_reader.prompt_version()}; {calls} call(s):")
    for p in plans:
        print(f"  {Path(p['pdf']).name}: {len(p['pages'])} page(s) {p['labels']}, {p['expected']} expected row(s), "
              f"~{p['estimate']['input_tokens']} input tokens")
    low, high = cost(model, input_tokens, 0), cost(model, input_tokens, output_max)
    price = f"${low:.2f} (input) to ${high:.2f} (if every call used all {output_max // calls} output tokens)" \
        if low is not None else "price of this model not known to the tool"
    print(f"Total: ~{input_tokens} input tokens, up to {output_max} output tokens; {price}.")
    if args.estimate:
        return 0
    if not args.yes and input("Continue? [y/N] ").strip().lower() != "y":
        return 1

    from turnpilot.drawing_reader import make_client
    client = make_client()
    runs = []
    for p in plans:
        tool_input, raw = catalogue_reader.read_catalogue(p["pdf"], p["pages"], args.groups, p["codes"], client, model)
        with open(p["pdf"], "rb") as f:
            sha = hashlib.sha256(f.read()).hexdigest()
        runs.append(dict(pdf=p["pdf"], sha256=sha, pages=p["pages"], labels={str(k): v for k, v in p["labels"].items()},
                         groups=args.groups, codes=p["codes"], model=model,
                         prompt_version=catalogue_reader.prompt_version(), tool_input=tool_input,
                         usage=raw.get("usage"), at=datetime.now(timezone.utc).isoformat()))
        usage = raw.get("usage") or {}
        print(f"  {Path(p['pdf']).name}: input {usage.get('input_tokens')}, output {usage.get('output_tokens')}")
    runs_path = output.with_suffix(".runs.json")
    runs_path.write_text(json.dumps(runs, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
    output.write_text(report(runs, args.expected), encoding="utf-8", newline="\n")
    print(f"Report: {output}; runs: {runs_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
