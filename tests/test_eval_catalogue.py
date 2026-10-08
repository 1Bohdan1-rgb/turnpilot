"""tools/eval_catalogue.py without API calls: the estimate and the scoring of saved runs."""
import json
import sys
from pathlib import Path

import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import eval_catalogue  # noqa: E402

EXPECTED = """# Test для сталі P1.2 (з охолодженням)
## 1. Turning
| Роль | Пластина | Сплав | ap, мм | f, мм/об | Vc, м/хв при f 0.1 / 0.4 / 0.8 | Source |
|---|---|---|---|---|---|---|
| a | CNMG 12 04 08-PM | GC4325 | 0.5 – 3 – 5.5 | 0.15 – 0.3 – 0.5 | 455 / 305 / 215 | TT A283 (ap/f), A279 (Vc) |
| b | DNMG 15 06 04-PF | GC4315 | 0.25 – 0.4 – 1.5 | 0.07 – 0.15 – 0.3 | 510 / 365 / 265 | TT A285 (ap/f), A278 (Vc) |
## 3. Grooving
| Роль | Пластина | Сплав | f, мм/об | Vc, м/хв (f 0.05 → 0.5) | Source |
|---|---|---|---|---|---|
| Канавка | N123G2-0300-0003-GM | GC4325 | ~0.07 стартова (діапазон ~0.04–0.14) | 315 → 140 | TT B139 (f), B130 (Vc) |
"""
PAGES = ["Contents", "CNMG 12 04 08-PM 0.5 3 5.5 0.15 0.3 0.5 N123G2-0300-0003-GM", "GC4325 P1.2 0.1 0.4 0.8 455 305 215"]


def geometry(**kw):
    row = dict(kind="geometry", insert_code="CNMG 12 04 08-PM", grade="", application="none", material_group="P",
               ap_min=0.5, ap_rec=3, ap_max=5.5, f_min=0.15, f_rec=0.3, f_max=0.5, vc_min=None, vc_max=None,
               vc_points=[], coolant="not_stated", from_graph=False, pdf_page=2, page_label="", quote="CNMG 12 04 08-PM")
    row.update(kw)
    return row


def files(tmp_path):
    doc = pymupdf.open()
    for text in PAGES:
        doc.new_page().insert_text((50, 72), text)
    pdf = tmp_path / "tt.pdf"
    doc.save(pdf)
    expected = tmp_path / "expected.md"
    expected.write_text(EXPECTED, encoding="utf-8")
    return str(pdf), str(expected)


def test_estimate_makes_no_call(tmp_path, capsys, monkeypatch):
    pdf, expected = files(tmp_path)
    monkeypatch.setattr("turnpilot.drawing_reader.make_client", lambda: (_ for _ in ()).throw(AssertionError))
    code = eval_catalogue.main(["--read", pdf, "2=A283, 3=A279", "--expected", expected, "--model", "claude-sonnet-5",
                                "--output", str(tmp_path / "r.md"), "--estimate"])
    out = capsys.readouterr().out
    assert code == 0 and "1 call(s)" in out and "2 expected row(s)" in out and "$" in out
    assert not (tmp_path / "r.md").exists()


def test_scoring_of_a_saved_run(tmp_path):
    pdf, expected = files(tmp_path)
    grade = dict(geometry(), kind="grade_vc", insert_code="", grade="GC4325", application="turning",
                 material_group="P1.2", ap_min=None, ap_rec=None, ap_max=None, f_min=None, f_rec=None, f_max=None,
                 vc_points=[{"f": 0.1, "vc": 455}, {"f": 0.4, "vc": 215}, {"f": 0.8, "vc": 215}], pdf_page=3,
                 quote="GC4325 P1.2")
    graph = geometry(insert_code="N123G2-0300-0003-GM", from_graph=True, ap_min=None, ap_rec=None, ap_max=None,
                     f_min=0.04, f_rec=0.07, f_max=0.14)
    run = dict(pdf=pdf, pages=[2, 3], labels={"2": "A283", "3": "A279"}, groups="P1.2", model="m",
               codes="CNMG120408-PM\nGC4325\nN123G2-0300-0003-GM", prompt_version="catalogue:x",
               tool_input={"rows": [geometry(), grade, graph]}, usage={"input_tokens": 1, "output_tokens": 2})
    runs = tmp_path / "r.runs.json"
    runs.write_text(json.dumps([run]), encoding="utf-8")
    eval_catalogue.main(["--rescore", str(runs), "--expected", expected, "--output", str(tmp_path / "r.md")])
    text = (tmp_path / "r.md").read_text(encoding="utf-8")
    # expected on A283 / A279: CNMG geometry (6) and GC4325 turning (3 points); DNMG is on A285 / A278: not expected
    assert "**All: 8 / 9 numbers right (88.9%); wrong numbers without a code check: 1.**" in text
    # 215 is written on the page (in another column): the code cannot tell, so the wrong number is silent
    assert "GC4325 turning Vc at f 0.4: 305 → 215 (SILENT)" in text
    assert "N123G2-0300-0003-GM: not taken (right)" not in text  # its pages B139 / B130 were not read
    assert "read from a graph" in text  # the code did not take the graph row


def test_pages_of_a_row():
    assert eval_catalogue.expand_pages("A278–A279, A283 (ap/f)") == {"A278", "A279"}
    assert eval_catalogue.expand_pages("A278–A279, A283") == {"A278", "A279", "A283"}
