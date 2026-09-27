"""The experimental "dimensions first" reading mode: solver, conversion, mode wiring. No API calls."""

import copy

import pytest
from conftest import FIXTURES, FakeClient, make_response

from turnpilot import drawing_reader
from turnpilot.dimensions_first import (
    RECORD_DIMENSIONS_TOOL,
    TOOL_NAME,
    Dimension,
    DimensionsData,
    solve_boundaries,
    to_drawing_data,
)
from turnpilot.drawing_reader import ExtractionError, extract_drawing, parse_response, prepare_image
from turnpilot.extraction_schema import DrawingData
from turnpilot.models import DrawingExtraction, db
from turnpilot.planner import geometry_warnings


def section(type_, diameter=None, start_diameter=None, tolerance=None, radius=None, ra=None, confidence=0.9):
    return {"type": type_, "diameter": diameter, "start_diameter": start_diameter, "tolerance": tolerance,
            "ra": ra, "radius": radius, "confidence": confidence}


def overlay(type_, section_no, diameter=None, tolerance=None, pitch=None, size=None, start=None, end=None):
    return {"type": type_, "section": section_no, "diameter": diameter, "tolerance": tolerance, "ra": None,
            "pitch": pitch, "size": size, "start": start, "end": end, "confidence": 0.9}


def dim(value, kind, from_=None, to=None, tolerance=None, section_no=None):
    return {"value": value, "tolerance": tolerance, "kind": kind, "from": from_, "to": to, "section": section_no}


# The real shaft transcribed as written: boundaries 0 (left face) .. 6 (right face).
# Baseline dimensions 140.1 / 100 / 90 / 20 from the right face, chain 30 and the groove width 4.
REAL_SHAFT = {
    "part_type": "turned", "material": None, "blank_diameter": None, "blank_length": None, "quantity": None,
    "general_ra": 3.2,
    "sections": [
        section("od_turn", 80, tolerance="+0.5/-1.3"),
        section("fillet", tolerance="±0.05", radius=10),
        section("od_turn", 60, tolerance="±0.05"),
        section("taper", 55, start_diameter=60, tolerance="±0.85"),
        section("groove", 44, start_diameter=48, tolerance="±0.85"),
        section("od_turn", 48),
    ],
    "overlays": [
        overlay("thread", 6, 48, tolerance="6g", pitch=1.5),
        overlay("chamfer", 6, size=1.5),
    ],
    "dimensions": [
        dim(140.1, "overall", 0, 6, "js12"),
        dim(100, "baseline", 1, 6, "js12"),
        dim(90, "baseline", 2, 6, "js12"),
        dim(30, "chain", 3, 4, "±0.105"),
        dim(20, "baseline", 4, 6, "js12"),
        dim(4, "chain", 4, 5, "±0.08"),
        dim(60, "diameter", tolerance="±0.05", section_no=3),
    ],
    "warnings": [],
}


def _dims(raw):
    return [Dimension.model_validate(d) for d in raw]


# --- solver -----------------------------------------------------------------------------------

def test_real_shaft_lengths_come_from_the_dimension_graph():
    data = to_drawing_data(DimensionsData.model_validate(REAL_SHAFT))
    lengths = [(f.type, f.diameter, f.length) for f in data.features]
    assert lengths == [
        ("od_turn", 80, 40.1), ("fillet", None, None), ("od_turn", 60, 40), ("taper", 55, 30),
        ("groove", 44, 4), ("od_turn", 48, 16), ("thread", 48, 16), ("chamfer", 48, 1.5),
    ]
    assert data.overall_length == 140.1 and data.general_ra == 3.2
    assert data.warnings == []
    assert geometry_warnings(data.features, data.overall_length) == []


def test_matches_the_machinist_checked_expected_answer():
    expected = DrawingData.model_validate_json(
        (FIXTURES / "real_01.expected.json").read_text(encoding="utf-8")
    ) if (FIXTURES / "real_01.expected.json").exists() else None
    if expected is None:
        pytest.skip("real_01.expected.json is local only")
    data = to_drawing_data(DimensionsData.model_validate(REAL_SHAFT))
    got = [(f.type, f.diameter, f.start_diameter, f.length, f.tolerance, f.pitch, f.radius) for f in data.features]
    want = [(f.type, f.diameter, f.start_diameter, f.length, f.tolerance, f.pitch, f.radius) for f in expected.features]
    assert got == want


def test_chain_dimensions():
    solution = solve_boundaries(4, _dims([dim(10, "chain", 0, 1), dim(20, "chain", 1, 2), dim(30, "chain", 2, 3)]))
    assert solution.positions == [0, 10, 30, 60] and solution.warnings == []


def test_boundaries_in_either_order():
    solution = solve_boundaries(3, _dims([dim(50, "overall", 2, 0), dim(20, "chain", 2, 1)]))
    assert solution.positions == [0, 30, 50]


def test_conflicting_dimensions_are_reported():
    solution = solve_boundaries(3, _dims([dim(50, "overall", 0, 2), dim(20, "chain", 0, 1), dim(25, "chain", 1, 2)]))
    assert len(solution.warnings) == 1
    assert "conflicts with the others by 5 mm" in solution.warnings[0]


def test_small_differences_are_not_conflicts():
    solution = solve_boundaries(3, _dims([dim(50, "overall", 0, 2), dim(20, "chain", 0, 1), dim(30.1, "chain", 1, 2)]))
    assert solution.warnings == []


def test_undetermined_sections_stay_null():
    raw = copy.deepcopy(REAL_SHAFT)
    raw["dimensions"] = [d for d in raw["dimensions"] if d["value"] not in (30, 90)]  # taper and Ø60 open
    data = to_drawing_data(DimensionsData.model_validate(raw))
    lengths = {(f.type, f.diameter): f.length for f in data.features}
    assert lengths[("od_turn", 60)] is None and lengths[("taper", 55)] is None
    assert lengths[("od_turn", 80)] == 40.1 and lengths[("od_turn", 48)] == 16


def test_invalid_boundaries_are_reported():
    solution = solve_boundaries(3, _dims([dim(10, "chain", 0, 7), dim(5, "chain", 1, 1), dim(9, "chain")]))
    assert len(solution.warnings) == 3


def test_diameter_dimension_fills_a_missing_diameter():
    raw = copy.deepcopy(REAL_SHAFT)
    raw["sections"][2]["diameter"] = None
    raw["sections"][2]["tolerance"] = None
    data = to_drawing_data(DimensionsData.model_validate(raw))
    assert (data.features[2].diameter, data.features[2].tolerance) == (60, "±0.05")


def test_thread_length_from_its_own_boundaries():
    raw = copy.deepcopy(REAL_SHAFT)
    raw["overlays"][0].update(start=5, end=6)
    assert to_drawing_data(DimensionsData.model_validate(raw)).features[6].length == 16


def test_thread_class_check_still_applies():
    raw = copy.deepcopy(REAL_SHAFT)
    raw["overlays"][0]["tolerance"] = "69"
    data = to_drawing_data(DimensionsData.model_validate(raw))
    assert data.features[6].tolerance is None
    assert "thread class unclear: '69'" in data.warnings


# --- mode wiring ------------------------------------------------------------------------------

def _image():
    return prepare_image((FIXTURES / "01_stepped_shaft.png").read_bytes(), "png")


def test_dimensions_mode_request_and_parsing():
    client = FakeClient(make_response(REAL_SHAFT, tool_name=TOOL_NAME))
    result = extract_drawing(_image(), client=client, mode="dimensions_first")
    (call,) = client.messages.calls
    assert call["tools"] == [RECORD_DIMENSIONS_TOOL] and RECORD_DIMENSIONS_TOOL["strict"] is True
    assert "Do not add up or subtract dimensions yourself" in call["system"]
    assert result.data.features[0].length == 40.1
    assert result.raw["content"][0]["input"]["dimensions"][0]["value"] == 140.1  # graph kept for audit


def test_dimensions_mode_needs_its_own_tool_call():
    with pytest.raises(ExtractionError, match="no record_dimensions call"):
        parse_response(make_response(REAL_SHAFT), mode="dimensions_first")  # a record_part call


def test_invalid_dimensions_output_is_rejected():
    raw = copy.deepcopy(REAL_SHAFT)
    raw["dimensions"][0]["kind"] = "radius"
    with pytest.raises(ExtractionError, match="failed validation"):
        parse_response(make_response(raw, tool_name=TOOL_NAME), mode="dimensions_first")


def test_unknown_mode():
    with pytest.raises(ValueError, match="Unknown reading mode"):
        drawing_reader.prompt_version("guess_everything")


def test_modes_have_different_prompt_versions():
    assert drawing_reader.prompt_version("dimensions_first") != drawing_reader.prompt_version("features")
    assert drawing_reader.prompt_version() == drawing_reader.prompt_version("features")  # default unchanged


def test_dimensions_schema_is_strict():
    schema = RECORD_DIMENSIONS_TOOL["input_schema"]

    def check(node):
        if node.get("type") == "object":
            assert node["additionalProperties"] is False
            assert set(node["required"]) == set(node["properties"])
            for child in node["properties"].values():
                check(child)
        if node.get("type") == "array":
            check(node["items"])

    check(schema)


def test_config_selects_the_mode(app, client):
    import io

    app.config["DRAWING_READ_MODE"] = "dimensions_first"
    fake = FakeClient(make_response(REAL_SHAFT, tool_name=TOOL_NAME))
    app.config["ANTHROPIC_CLIENT"] = fake
    png = (FIXTURES / "01_stepped_shaft.png").read_bytes()
    client.post("/jobs/upload", data={"drawing": (io.BytesIO(png), "shaft.png")}, content_type="multipart/form-data")
    extraction = db.session.execute(db.select(DrawingExtraction)).scalar_one()
    assert extraction.read_mode == "dimensions_first" and extraction.status == "extracted"
    assert extraction.prompt_version == drawing_reader.prompt_version("dimensions_first")
    assert fake.messages.calls[0]["tools"][0]["name"] == TOOL_NAME
    assert "mode dimensions_first" in client.get(f"/extractions/{extraction.id}/review").data.decode()

    # the features mode does not reuse the dimensions-first result for the same file
    app.config["DRAWING_READ_MODE"] = "features"
    fake.messages.response = make_response({
        "part_type": "turned", "material": None, "blank_diameter": None, "blank_length": None,
        "overall_length": None, "quantity": None, "general_ra": None, "features": [], "warnings": [],
    })
    client.post("/jobs/upload", data={"drawing": (io.BytesIO(png), "shaft.png")}, content_type="multipart/form-data")
    assert len(fake.messages.calls) == 2
