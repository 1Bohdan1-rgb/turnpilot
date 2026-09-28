"""General (unspecified) tolerance note: kept as written, applied on the review screen, scored by grade."""

import importlib.util
import io
import json
import sys
from pathlib import Path

import pytest
from conftest import FIXTURES, FakeClient, feature, make_response, part

from turnpilot import planner
from turnpilot.drawing_reader import SYSTEM_PROMPT, parse_response
from turnpilot.extraction_schema import RECORD_PART_TOOL

NOTE = "Неуказанные предельные отклонения размеров: H14, h14, ±IT14/2"


@pytest.mark.parametrize("note, grade", [
    ("H14, h14, ±IT14/2", 14), (NOTE, 14), ("h12, H12, ±IT12/2", 12), ("±IT13/2", 13),
    ("ISO 2768-m", None), ("", None), (None, None),
])
def test_general_tolerance_grade(note, grade):
    assert planner.general_tolerance_grade(note) == grade


@pytest.mark.parametrize("feature_type, tolerance", [
    ("bore", "H14"), ("od_turn", "h14"), ("taper", "h14"), ("groove", "h14"),
    ("fillet", "±IT14/2"), ("chamfer", "±IT14/2"), ("thread", None), ("face", None),
])
def test_general_tolerance_for(feature_type, tolerance):
    assert planner.general_tolerance_for(feature_type, 14) == tolerance


def test_schema_and_prompt():
    schema = RECORD_PART_TOOL["input_schema"]
    assert schema["properties"]["general_tolerance"]["type"] == "string"  # not nullable: "" means none
    assert "general_tolerance" in schema["required"]
    assert "goes into general_tolerance as written" in SYSTEM_PROMPT.replace("\n", " ")


def test_empty_note_is_none():
    raw = part([feature("od_turn", 20, length=14)])
    raw["general_tolerance"] = ""
    assert parse_response(make_response(raw)).general_tolerance is None


def test_review_applies_the_note_to_sizes_without_their_own(app, client):
    raw = part([
        feature("od_turn", 20, length=14), feature("od_turn", 25, length=30, tolerance="±0.1"),
        feature("bore", 16, length=16), feature("thread", 14, length=28, pitch=2, tolerance="7H"),
        {**feature("chamfer", 14, length=2), "location": "internal", "face": "right"},
    ], overall_length=44)
    raw["general_tolerance"] = NOTE
    app.config["ANTHROPIC_CLIENT"] = FakeClient(make_response(raw))
    png = (FIXTURES / "01_stepped_shaft.png").read_bytes()
    client.post("/jobs/upload", data={"drawing": (io.BytesIO(png), "vtulka.png")}, content_type="multipart/form-data")
    page = client.get("/extractions/1/review").data.decode()
    assert f"General tolerances on the drawing: {NOTE}" in page
    assert 'name="f0-tolerance" value="h14"' in page       # shaft
    assert 'name="f1-tolerance" value="±0.1"' in page      # own tolerance wins
    assert 'name="f2-tolerance" value="H14"' in page       # hole
    assert 'name="f3-tolerance" value="7H"' in page        # thread class untouched
    assert 'name="f4-tolerance" value="±IT14/2"' in page   # the rest
    assert page.count(f'title="From the general tolerance note: {NOTE}">general</span>') == 3


def _eval():
    spec = importlib.util.spec_from_file_location("eval_extraction", Path(__file__).parent.parent / "tools" / "eval_extraction.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["eval_extraction"] = module
    spec.loader.exec_module(module)
    return module


def test_scorer_compares_the_general_tolerance_grade():
    eval_extraction = _eval()
    answer = json.loads((FIXTURES / "01_stepped_shaft.expected.json").read_text(encoding="utf-8"))
    for f in answer["features"]:
        f["confidence"] = 0.9
    answer["general_tolerance"] = "ISO 2768-m"  # expected has none; no IT grade either way
    result = eval_extraction.run_one(FakeClient(make_response(answer)), "m", "01_stepped_shaft", "png")
    assert result.scores["general_tolerance"].pct == 100
    answer["general_tolerance"] = "h14"  # an invented grade
    result = eval_extraction.run_one(FakeClient(make_response(answer)), "m", "01_stepped_shaft", "png")
    assert result.scores["general_tolerance"].pct == 0
