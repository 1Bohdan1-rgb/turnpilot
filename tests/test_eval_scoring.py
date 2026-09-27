"""Scoring logic of tools/eval_extraction.py, with a fake client (the real eval is not run in pytest)."""

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
            def create(**kwargs):
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
