"""Scoring logic of tools/eval_extraction.py, with a fake client (the real eval is not run in pytest)."""
import pytest

import importlib.util
import json
import sys
from pathlib import Path

from conftest import FIXTURES, FakeClient, make_response

spec = importlib.util.spec_from_file_location(
    "eval_extraction", Path(__file__).parent.parent / "tools" / "eval_extraction.py"
)
eval_extraction = importlib.util.module_from_spec(spec)
sys.modules["eval_extraction"] = eval_extraction  # dataclasses look their module up here
spec.loader.exec_module(eval_extraction)


def _answer(name, **changes):
    data = json.loads((FIXTURES / f"{name}.expected.json").read_text(encoding="utf-8"))
    for f in data["features"]:
        f["confidence"] = 0.9
    data.update(changes)
    return data


def test_perfect_answer_scores_100():
    client = FakeClient(make_response(_answer("02_threaded_shaft")))
    result = eval_extraction.run_one(client, "test-model", "02_threaded_shaft", "photo")
    assert result.error is None and result.group == "photo"
    assert all(result.scores[m].pct == 100 for m in eval_extraction.METRICS)
    assert result.missing == result.extra == 0


def test_mistakes_are_counted():
    answer = _answer("04_fitted_shaft", material="AlMg1SiCu")  # alias of Aluminium 6061: still correct
    answer["features"][1]["tolerance"] = "h7"  # expected h6
    answer["features"][0]["ra"] = 3.2  # invented Ra
    del answer["features"][2]  # missing Ø25 f7 section
    answer["features"].append({**answer["features"][0], "type": "bore"})  # extra feature
    result = eval_extraction.run_one(FakeClient(make_response(answer)), "m", "04_fitted_shaft", "png")

    assert result.scores["material"].pct == 100
    assert result.missing == 1 and result.extra == 1
    assert result.scores["tolerance"].correct == result.scores["tolerance"].total - 2  # h7 + missing
    assert result.scores["ra"].correct == result.scores["ra"].total - 2  # invented + missing
    assert any("tolerance 'h7'" in m for m in result.mismatches)


def test_api_error_counts_as_wrong():
    class Broken:
        class messages:
            @staticmethod
            def stream(**kwargs):
                raise RuntimeError("boom")

    result = eval_extraction.run_one(Broken(), "m", "01_stepped_shaft", "pdf")
    assert "boom" in result.error
    assert result.scores["diameter"].correct == 0 and result.scores["diameter"].total == 3


def test_summary_separates_clean_and_photo():
    client = FakeClient(make_response(_answer("01_stepped_shaft")))
    results = [eval_extraction.run_one(client, "m", "01_stepped_shaft", v) for v in ("png", "pdf", "photo")]
    table = eval_extraction.summary_table(results)
    assert "clean (2 runs)" in table and "photo (1 runs)" in table


def test_null_is_correct_when_value_is_not_on_the_drawing():
    """05_bushing_ambiguous has no bore length: null is right, a guessed 45 is wrong."""
    answer = _answer("05_bushing_ambiguous")
    bore = next(f for f in answer["features"] if f["type"] == "bore")
    assert bore["length"] is None

    result = eval_extraction.run_one(FakeClient(make_response(answer)), "m", "05_bushing_ambiguous", "png")
    assert result.scores["length"].pct == 100

    bore["length"] = 45  # the model guesses the through length
    result = eval_extraction.run_one(FakeClient(make_response(answer)), "m", "05_bushing_ambiguous", "png")
    assert result.scores["length"].correct == result.scores["length"].total - 1
    assert any("bore Ø30: length 45" in m for m in result.mismatches)


def test_report_has_known_limitations(tmp_path, monkeypatch):
    monkeypatch.setattr(eval_extraction, "RESULTS_MD", tmp_path / "eval_results.md")
    result = eval_extraction.run_one(FakeClient(make_response(_answer("01_stepped_shaft"))), "m", "01_stepped_shaft", "png")
    eval_extraction.write_markdown([result], "m")
    text = (tmp_path / "eval_results.md").read_text(encoding="utf-8")
    assert "## Known limitations" in text and "Synthetic test set" in text


def test_real_drawings_run_once_in_their_own_group(tmp_path, monkeypatch):
    monkeypatch.setattr(eval_extraction, "FIXTURES", tmp_path)
    (tmp_path / "real_01.jpg").write_bytes((FIXTURES / "01_stepped_shaft.photo.jpg").read_bytes())
    (tmp_path / "real_01.expected.json").write_text(
        (FIXTURES / "01_stepped_shaft.expected.json").read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_path / "real_02.expected.json").write_text("{}", encoding="utf-8")  # no drawing file: skipped
    runs = eval_extraction.planned_runs(["real_01", "real_02"], ["png", "pdf", "photo"])
    assert runs == [("real_01", "real")]

    result = eval_extraction.run_one(FakeClient(make_response(_answer("01_stepped_shaft"))), "m", "real_01", "real")
    assert result.group == "real" and result.error is None
    assert "real (1 runs)" in eval_extraction.summary_table([result])


def test_new_field_metrics_are_scored():
    answer = _answer("02_threaded_shaft", general_ra=3.2, overall_length=88)  # both wrong
    thread = next(f for f in answer["features"] if f["type"] == "thread")
    thread["pitch"] = 2.0  # wrong pitch
    result = eval_extraction.run_one(FakeClient(make_response(answer)), "m", "02_threaded_shaft", "png")
    assert result.scores["pitch"].correct == result.scores["pitch"].total - 1
    assert result.scores["general_ra"].pct == 0 and result.scores["overall_length"].pct == 0
    assert result.scores["start_diameter"].pct == 100 and result.scores["radius"].pct == 100
    assert any("general_ra 3.2 (expected None)" in m for m in result.mismatches)


def test_repeat_table_shows_each_run_mean_and_spread():
    good = _answer("01_stepped_shaft")
    bad = _answer("01_stepped_shaft")
    bad["features"][0]["diameter"] = 41  # one of three diameters wrong
    results = [
        eval_extraction.run_one(FakeClient(make_response(good)), "m", "01_stepped_shaft", "png", run=1),
        eval_extraction.run_one(FakeClient(make_response(bad)), "m", "01_stepped_shaft", "png", run=2),
    ]
    table = eval_extraction.repeat_table(results)
    lines = table.splitlines()
    assert lines[2].startswith("| 1 | 100% (3/3)") and lines[3].startswith("| 2 | 67% (2/3)")
    assert lines[4].startswith("| **mean** | 83%")
    assert lines[5].startswith("| min–max | 67–100%")


def test_report_path_can_be_changed(tmp_path):
    result = eval_extraction.run_one(FakeClient(make_response(_answer("01_stepped_shaft"))), "m", "01_stepped_shaft", "png")
    path = eval_extraction.write_markdown([result, eval_extraction.RunResult("x", "png", "clean", run=2)], "m",
                                          tmp_path / "real.md")
    assert path == tmp_path / "real.md" and "## Run to run" in path.read_text(encoding="utf-8")


def _real_like_runs():
    """Two scored runs of 01_stepped_shaft: run 2 has a null length and a wrong diameter."""
    good = _answer("01_stepped_shaft")
    bad = _answer("01_stepped_shaft")
    bad["features"][0]["length"] = None
    bad["features"][1]["diameter"] = 31
    runs = [
        eval_extraction.run_one(FakeClient(make_response(good)), "m", "01_stepped_shaft", "png", run=1),
        eval_extraction.run_one(FakeClient(make_response(bad)), "m", "01_stepped_shaft", "png", run=2),
    ]
    runs[0].tokens, runs[1].tokens = (100, 20000), (100, 30000)
    runs[0].seconds, runs[1].seconds = 200, 300
    return runs


def test_null_lengths_are_counted_and_scored_as_errors():
    runs = _real_like_runs()
    assert runs[0].null_lengths == 0 and runs[1].null_lengths == 1
    assert runs[1].scores["length"].correct == runs[1].scores["length"].total - 1
    assert runs[0].geometry == 0 and runs[1].geometry == 1  # the null length breaks the length sum
    assert runs[0].conflicts == 0


def test_saved_runs_are_rescored_with_the_current_scorer(tmp_path, monkeypatch):
    runs = _real_like_runs()
    saved = eval_extraction.save_runs(runs, "m", tmp_path / "old.runs.json")
    rescored = eval_extraction.rescore_saved(saved)
    assert [r.scores["length"].pct for r in rescored] == [r.scores["length"].pct for r in runs]
    assert [r.tokens for r in rescored] == [r.tokens for r in runs]

    # a changed expected answer changes the score of the very same responses
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    changed = _answer("01_stepped_shaft")
    changed["features"][1]["diameter"] = 31
    (fixtures / "01_stepped_shaft.expected.json").write_text(__import__("json").dumps(changed), encoding="utf-8")
    monkeypatch.setattr(eval_extraction, "FIXTURES", fixtures)
    again = eval_extraction.rescore_saved(saved)
    assert again[1].scores["diameter"].pct == 100 and again[0].scores["diameter"].pct < 100


def test_comparison_from_saved_runs_and_from_an_old_report(tmp_path):
    runs = _real_like_runs()
    report = eval_extraction.write_markdown(runs, "m", tmp_path / "old.md")

    old = eval_extraction.comparison_rows_from_report(report, "features")
    assert [(r["Run"], r["Null lengths"], r["Output tokens"], r["Geometry warnings"]) for r in old] == [
        (1, 0, 20000, None), (2, 1, 30000, None),
    ]
    new = eval_extraction.comparison_rows("dimensions_first", runs)
    table = eval_extraction.comparison_table(old + new)
    assert "| features | 2 | 67% | 1 | 0 | n/a | n/a | 67% |" in table
    # read back from the report's rounded percentages (100%, 67%): mean 83.5 -> 84%
    assert "| **features** | **mean** | 84% | 0.5 | 0 | n/a | n/a | 84% |" in table
    assert "| **dimensions_first** | **mean** | 83% | 0.5 | 0 | 0 | 0.5 | 83% |" in table
    assert "25,000" in table and "| 250 |" in table


def test_dimensions_first_conflicts_are_counted():
    import copy
    from test_dimensions_first import REAL_SHAFT

    raw = copy.deepcopy(REAL_SHAFT)
    raw["dimensions"].append({"value": 35, "tolerance": None, "kind": "chain", "from": 3, "to": 4, "section": None})
    fake = FakeClient(make_response(raw, tool_name="record_dimensions"))
    result = eval_extraction.run_one(fake, "m", "01_stepped_shaft", "png", mode="dimensions_first")
    assert result.conflicts == 1 and result.mode == "dimensions_first"


def test_eval_stops_after_the_first_client_error():
    import anthropic
    import httpx2

    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    message = "Schemas contains too many parameters with union types (22 ...) (limit: 16 parameters with unions)."
    error = anthropic.BadRequestError(
        message, response=httpx2.Response(400, request=request), body={"error": {"message": message}}
    )

    class Rejecting:
        class messages:
            calls = 0

            @classmethod
            def stream(cls, **kwargs):
                cls.calls += 1
                raise error

    runs = [("01_stepped_shaft", "png", k) for k in (1, 2, 3)]
    results = eval_extraction.run_all(Rejecting(), "m", runs, "features")
    assert len(results) == 1 and Rejecting.messages.calls == 1
    assert results[0].status_code == 400
    assert "limit: 16 parameters with unions" in results[0].error  # full text, not cut off


def test_eval_keeps_going_after_a_parse_error():
    fake = FakeClient(make_response(None, stop_reason="end_turn", text="no tool call"))
    runs = [("01_stepped_shaft", "png", k) for k in (1, 2)]
    results = eval_extraction.run_all(fake, "m", runs, "features")
    assert len(results) == 2 and all(r.error for r in results)


def test_compare_with_saved_runs_keeps_only_the_selected_drawings(tmp_path, monkeypatch):
    runs = [
        eval_extraction.run_one(FakeClient(make_response(_answer(name))), "m", name, "png", run=1)
        for name in ("01_stepped_shaft", "04_fitted_shaft")
    ]
    saved = eval_extraction.save_runs(runs, "m", tmp_path / "old.runs.json")
    monkeypatch.setattr(eval_extraction, "run_all", lambda client, model, planned, mode: [
        eval_extraction.run_one(FakeClient(make_response(_answer("01_stepped_shaft"))), "m", "01_stepped_shaft",
                                "png", run=1, mode=mode)
    ])
    monkeypatch.setattr(eval_extraction.drawing_reader, "make_client", lambda: None)
    out = tmp_path / "report.md"
    eval_extraction.main(["--only", "01_stepped_shaft", "--variants", "png", "--groups", "clean", "--yes",
                          "--output", str(out), "--compare", "saved", str(saved)])
    table = out.read_text(encoding="utf-8").split("## Comparison of reading modes")[1]
    assert table.count("| saved | 1 |") == 1  # 04_fitted_shaft is not mixed in


@pytest.mark.parametrize("expected, predicted, ok", [
    ("Р6М5Ф3", "Круг Р6М5Ф3 -II-а ГОСТ 19265-73; 29-В1-ГОСТ 2590-2006", True),  # grade inside the text
    ("Steel 45 (C45)", "C45", True),  # alias
    ("Aluminium 6061", "EN AW-6061", True),
    ("Р6М5Ф3", "Р18", False),
    ("AISI 304", "AISI 316", False),
    (None, None, True),
    (None, "Steel 45", False),
    ("Steel 45 (C45)", None, False),
])
def test_material_matches(expected, predicted, ok):
    assert eval_extraction.material_matches(expected, predicted) is ok


# --- a section with shoulders on both sides: groove or od_turn -------------------------------

from turnpilot.extraction_schema import DrawingData  # noqa: E402


def _part(*features):
    keys = ("type", "diameter", "length", "start_diameter")
    return DrawingData(part_type="turned", features=[dict(zip(keys, f)) for f in features])


def _fitting(recess_type="groove"):
    """Ø10 L3.5 | recess Ø8 L8 | Ø13 L3.5 | relief Ø8 L1.5 | Ø10 L8 (the fitting from real_03)."""
    start = 10 if recess_type == "groove" else None
    return _part(
        ("od_turn", 10, 3.5), (recess_type, 8, 8, start), ("od_turn", 13, 3.5),
        (recess_type, 8, 1.5, start), ("od_turn", 10, 8),
    )


def _scores(expected, predicted):
    result = eval_extraction.RunResult("x", "png", "clean")
    eval_extraction.score(expected, predicted, result)
    return result


def test_recess_sections_have_shoulders_on_both_sides():
    part = _fitting()
    recesses = eval_extraction.recess_sections(part.features)
    assert recesses == {id(part.features[1]), id(part.features[3])}


def test_recess_read_as_od_turn_counts():
    result = _scores(_fitting("groove"), _fitting("od_turn"))
    assert result.missing == result.extra == 0
    for metric in ("diameter", "start_diameter", "length"):
        assert result.scores[metric].pct == 100, metric


def test_od_turn_recess_read_as_groove_counts():
    result = _scores(_fitting("od_turn"), _fitting("groove"))
    assert result.missing == result.extra == 0
    assert result.scores["length"].pct == 100


def test_recess_matched_by_length_and_position():
    # the model swaps the two Ø8 types: each expected recess still gets the section of its own length
    predicted = _part(
        ("od_turn", 10, 3.5), ("od_turn", 8, 8), ("od_turn", 13, 3.5), ("groove", 8, 1.5, 10), ("od_turn", 10, 8),
    )
    result = _scores(_fitting(), predicted)
    assert result.scores["length"].pct == 100
    assert result.missing == result.extra == 0


def test_groove_at_the_end_of_a_step_is_not_interchangeable():
    # a thread relief next to one larger section only: an od_turn is not accepted for it
    expected = _part(("od_turn", 30, 60), ("od_turn", 20, 27), ("groove", 17, 3, 20))
    predicted = _part(("od_turn", 30, 60), ("od_turn", 20, 27), ("od_turn", 17, 3))
    result = _scores(expected, predicted)
    assert result.missing == 1 and result.extra == 1


def test_type_mismatch_is_counted_and_reported(tmp_path):
    result = _scores(_fitting("groove"), _fitting("od_turn"))
    assert result.type_mismatch == 2
    assert "groove Ø8: read as od_turn (type mismatch, counted as a match)" in result.mismatches
    assert _scores(_fitting(), _fitting()).type_mismatch == 0
    path = tmp_path / "report.md"
    eval_extraction.write_markdown([result], "test-model", path=path)
    text = path.read_text(encoding="utf-8")
    assert "| Missing | Extra | Type mismatch | Errors |" in text
    assert "| 0 | 0 | 2 | 0 |" in text
