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
    client = FakeClient(make_response(to_wire(REAL_SHAFT), tool_name=TOOL_NAME))
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


# --- wire format: no nullable fields, 0 / "" / -1 mean "not on the drawing" ----------------------

from turnpilot.dimensions_first import from_wire, parse_tool_input  # noqa: E402
from turnpilot.extraction_schema import RECORD_PART_TOOL  # noqa: E402


def to_wire(data):
    """Encode None the way the model sends it."""
    def enc(key, value):
        if value is not None:
            return value
        if key in ("from", "to", "start", "end"):
            return -1
        if key in ("material", "tolerance"):
            return ""
        return 0

    out = {k: enc(k, v) for k, v in data.items() if k not in ("sections", "overlays", "dimensions")}
    for key in ("sections", "overlays", "dimensions"):
        out[key] = [{k: enc(k, v) for k, v in item.items()} for item in data[key]]
    return out


def test_wire_markers_become_null():
    wire = to_wire(REAL_SHAFT)
    assert wire["material"] == "" and wire["quantity"] == 0
    assert wire["dimensions"][-1]["from"] == -1 and wire["sections"][0]["radius"] == 0
    decoded = from_wire(wire)
    assert decoded["material"] is None and decoded["quantity"] is None
    assert decoded["sections"][0]["radius"] is None and decoded["overlays"][1]["diameter"] is None
    assert decoded["dimensions"][-1]["from"] is None and decoded["dimensions"][-1]["to"] is None


def test_boundary_zero_is_kept():
    decoded = from_wire(to_wire(REAL_SHAFT))
    assert decoded["dimensions"][0]["from"] == 0  # the left end face is a real boundary


def test_zero_deviation_tolerance_is_not_lost():
    wire = to_wire(REAL_SHAFT)
    wire["sections"][2]["tolerance"] = "0/-0.021"
    wire["dimensions"][3]["tolerance"] = "0/-0.1"
    data = parse_tool_input(wire)
    assert data.features[2].tolerance == "0/-0.021"
    assert from_wire(wire)["dimensions"][3]["tolerance"] == "0/-0.1"


def test_wire_input_gives_the_same_part():
    assert parse_tool_input(to_wire(REAL_SHAFT)) == to_drawing_data(DimensionsData.model_validate(REAL_SHAFT))


def _union_params(node):
    count = 0
    if isinstance(node, dict):
        if "anyOf" in node or isinstance(node.get("type"), list):
            count += 1
        count += sum(_union_params(v) for v in node.values())
    elif isinstance(node, list):
        count += sum(_union_params(v) for v in node)
    return count


@pytest.mark.parametrize("tool", [RECORD_PART_TOOL, RECORD_DIMENSIONS_TOOL], ids=lambda t: t["name"])
def test_strict_schemas_stay_within_the_union_limit(tool):
    # The API rejects strict schemas with more than 16 nullable/union parameters ("limit: 16
    # parameters with unions"; 22 was rejected). Keep a margin: 13 is what record_part has.
    assert tool["strict"] is True
    assert _union_params(tool["input_schema"]) <= 13


def test_dimensions_schema_has_no_nullable_fields():
    assert _union_params(RECORD_DIMENSIONS_TOOL["input_schema"]) == 0


# --- lengths computed by subtraction are flagged -------------------------------------------------

def test_derived_lengths_are_flagged():
    data = to_drawing_data(DimensionsData.model_validate(REAL_SHAFT))
    flags = [(f.type, f.diameter, f.length_derived) for f in data.features]
    assert flags == [
        ("od_turn", 80, True),     # 140.1 - 100
        ("fillet", None, False),   # no length (radius)
        ("od_turn", 60, True),     # 90 - 30 - 20
        ("taper", 55, False),      # the 30 chain dimension
        ("groove", 44, False),     # the 4 chain dimension
        ("od_turn", 48, True),     # baseline 20 minus the groove 4
        ("thread", 48, True),      # over the Ø48 section
        ("chamfer", 48, False),
    ]


HOLDOUT = {  # 07_holdout transcribed as written: baselines from the LEFT face, groove width 3
    "part_type": "turned", "material": "Aluminium 6061", "blank_diameter": None, "blank_length": None,
    "quantity": 25, "general_ra": 3.2,
    "sections": [
        section("od_turn", 36, tolerance="0/-0.016"),
        section("od_turn", 45, tolerance="±0.1"),
        section("taper", 38, start_diameter=45, tolerance="±0.2"),
        section("groove", 30, start_diameter=34, tolerance="±0.3"),
        section("od_turn", 34),
    ],
    "overlays": [overlay("thread", 5, 34, tolerance="6g", pitch=1.5), overlay("chamfer", 5, size=1)],
    "dimensions": [
        dim(25, "baseline", 0, 1), dim(55, "baseline", 0, 2), dim(75, "baseline", 0, 3),
        dim(100, "overall", 0, 5), dim(3, "chain", 3, 4, "±0.1"),
    ],
    "warnings": [],
}


def test_holdout_baselines_from_the_left_face():
    data = to_drawing_data(DimensionsData.model_validate(HOLDOUT))
    got = [(f.type, f.diameter, f.length, f.length_derived) for f in data.features]
    assert got == [
        ("od_turn", 36, 25, False), ("od_turn", 45, 30, True), ("taper", 38, 20, True),
        ("groove", 30, 3, False), ("od_turn", 34, 22, True), ("thread", 34, 22, True), ("chamfer", 34, 1, False),
    ]
    expected = DrawingData.model_validate_json((FIXTURES / "07_holdout.expected.json").read_text(encoding="utf-8"))
    want = [(f.type, f.diameter, f.start_diameter, f.length, f.tolerance, f.pitch) for f in expected.features]
    assert [(f.type, f.diameter, f.start_diameter, f.length, f.tolerance, f.pitch) for f in data.features] == want
    assert geometry_warnings(data.features, data.overall_length) == []


@pytest.mark.parametrize("phrase", [
    "may span several sections, including grooves",
    "following its extension lines to the part",
    "not a separate cylinder",
])
def test_dimensions_prompt_has_the_general_rules(phrase):
    from turnpilot.dimensions_first import SYSTEM_PROMPT

    assert phrase in SYSTEM_PROMPT.replace("\n", " ")


# --- ambiguous binding of a face dimension next to a groove ---------------------------------------

from turnpilot.dimensions_first import AMBIGUOUS_BINDING_PREFIX, ambiguous_face_bindings  # noqa: E402


def _with_dimension(data, old_span, new_span, kind=None):
    changed = copy.deepcopy(data)
    for d in changed["dimensions"]:
        if (d["from"], d["to"]) == old_span:
            d["from"], d["to"] = new_span
            if kind:
                d["kind"] = kind
    return changed


def _ambiguity_warnings(data):
    return [w for w in data.warnings if w.startswith(AMBIGUOUS_BINDING_PREFIX)]


def test_correct_face_dimension_over_the_groove_is_not_flagged():
    data = to_drawing_data(DimensionsData.model_validate(REAL_SHAFT))  # 20 from 4 to 6: groove included
    assert _ambiguity_warnings(data) == []
    assert not any(f.length_ambiguous for f in data.features)


def test_face_dimension_on_the_section_next_to_a_groove_is_flagged():
    # the typical misreading: 20 bound to the Ø48 section only (5 to 6); the groove width 4 still fits
    wrong = _with_dimension(REAL_SHAFT, (4, 6), (5, 6), kind="chain")
    data = to_drawing_data(DimensionsData.model_validate(wrong))
    (warning,) = _ambiguity_warnings(data)
    assert "dimension 20 (boundaries 5–6) may also span the groove (4–6)" in warning
    flagged = [(f.type, f.diameter) for f in data.features if f.length_ambiguous]
    assert flagged == [("od_turn", 48), ("thread", 48)]
    # only a warning: the recorded reading is kept
    assert next(f for f in data.features if f.type == "od_turn" and f.diameter == 48).length == 20


def test_left_face_dimension_next_to_a_groove_is_flagged():
    # a part with the groove near the left face: 10 from the left face may or may not include the groove
    part = {
        "part_type": "turned", "material": None, "blank_diameter": None, "blank_length": None,
        "quantity": None, "general_ra": None,
        "sections": [section("od_turn", 20), section("groove", 16, start_diameter=20), section("od_turn", 30)],
        "overlays": [], "warnings": [],
        "dimensions": [dim(10, "baseline", 0, 1), dim(3, "chain", 1, 2), dim(50, "overall", 0, 3)],
    }
    (warning,) = _ambiguity_warnings(to_drawing_data(DimensionsData.model_validate(part)))
    assert "dimension 10 (boundaries 0–1) may also span the groove (0–2)" in warning


def test_holdout_reading_has_no_ambiguity():
    assert _ambiguity_warnings(to_drawing_data(DimensionsData.model_validate(HOLDOUT))) == []


def test_alternative_with_more_conflicts_is_not_flagged():
    # A part where 20 really is the last section: an extra baseline 120.1 to boundary 5 pins it.
    # Moving 20 across the groove would contradict that baseline, so the reading is not ambiguous.
    part = _with_dimension(REAL_SHAFT, (4, 6), (5, 6), kind="chain")
    part["dimensions"].append(dim(120.1, "baseline", 0, 5))
    data = DimensionsData.model_validate(part)
    assert solve_boundaries(7, data.dimensions).warnings == []  # the recorded reading is consistent
    assert ambiguous_face_bindings(data) == []


def test_alternative_that_removes_a_conflict_is_flagged():
    # The recorded reading conflicts with a baseline to the groove's left boundary; the alternative
    # (20 including the groove) removes the conflict, so it is at least as plausible.
    part = _with_dimension(REAL_SHAFT, (4, 6), (5, 6), kind="chain")
    part["dimensions"].append(dim(120.1, "baseline", 0, 4))
    assert len(ambiguous_face_bindings(DimensionsData.model_validate(part))) == 1


def test_inner_dimension_next_to_a_groove_is_not_flagged():
    # 30 (taper, boundaries 3-4) touches the groove but not an end face: the drawing is unambiguous
    assert ambiguous_face_bindings(DimensionsData.model_validate(REAL_SHAFT)) == []


# --- rules carried over from the features prompt --------------------------------------------------

@pytest.mark.parametrize("phrase", [
    "A bore marked THRU (through) runs the full part",
    "start 0 and end the last boundary",
    "neither THRU nor a length dimension gets start and end -1",
    "belongs to the surface the leader's arrow touches",
    "do not copy a value to another one",
])
def test_dimensions_prompt_has_the_rules_from_the_features_prompt(phrase):
    from turnpilot.dimensions_first import SYSTEM_PROMPT

    assert phrase in SYSTEM_PROMPT.replace("\n", " ")


BUSHING = {  # 03_bushing transcribed as the prompt asks: a THRU bore from boundary 0 to the last one
    "part_type": "turned", "material": "AISI 304", "blank_diameter": 55, "blank_length": 50, "quantity": 10,
    "general_ra": None,
    "sections": [section("od_turn", 50, tolerance="±0.05")],
    "overlays": [
        {"type": "bore", "section": None, "diameter": 30, "tolerance": "H7", "ra": 1.6, "pitch": None,
         "size": None, "start": 0, "end": 1, "confidence": 0.9},
        overlay("chamfer", 1, size=1),
    ],
    "dimensions": [dim(45, "overall", 0, 1)],
    "warnings": [],
}


def test_thru_bore_gets_the_overall_length():
    data = to_drawing_data(DimensionsData.model_validate(BUSHING))
    bore = next(f for f in data.features if f.type == "bore")
    assert (bore.diameter, bore.length, bore.tolerance, bore.ra) == (30, 45, "H7", 1.6)
    expected = DrawingData.model_validate_json((FIXTURES / "03_bushing.expected.json").read_text(encoding="utf-8"))
    want = next(f for f in expected.features if f.type == "bore")
    assert (bore.diameter, bore.length, bore.tolerance, bore.ra) == (want.diameter, want.length, want.tolerance, want.ra)


def test_bore_without_thru_or_length_stays_null():
    part = copy.deepcopy(BUSHING)
    part["overlays"][0].update(start=None, end=None)
    bore = next(f for f in to_drawing_data(DimensionsData.model_validate(part)).features if f.type == "bore")
    assert bore.length is None
