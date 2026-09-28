"""Where a chamfer is (outside / inside, which end face): read, reviewed, planned and scored."""

import importlib.util
import io
import sys
from pathlib import Path

import pytest
from conftest import FIXTURES, FakeClient, feature, make_response, part

from turnpilot import planner
from turnpilot.drawing_reader import SYSTEM_PROMPT, parse_response
from turnpilot.extraction_schema import RECORD_PART_TOOL
from turnpilot.models import Job, db
from turnpilot.planner import FeatureSpec, JobSpec


def chamfer(diameter, location, face, size=2):
    f = feature("chamfer", diameter, length=size)
    f.update(location=location, face=face)
    return f


def test_schema_and_prompt():
    item = RECORD_PART_TOOL["input_schema"]["properties"]["features"]["items"]
    assert item["properties"]["location"]["enum"] == ["external", "internal", "none"]
    assert item["properties"]["face"]["enum"] == ["left", "right", "none"]
    assert {"location", "face"} <= set(item["required"])
    assert "internal (entrance of a bore or internal thread)" in SYSTEM_PROMPT.replace("\n", " ")


def test_position_is_parsed_and_none_means_none():
    data = parse_response(make_response(part([
        chamfer(14, "internal", "right"),
        {**feature("od_turn", 25, length=30), "location": "none", "face": "none"},
        {**feature("od_turn", 20, length=14), "location": "external", "face": "left"},  # not a chamfer: dropped
    ])))
    assert (data.features[0].location, data.features[0].face) == ("internal", "right")
    assert (data.features[1].location, data.features[1].face) == (None, None)
    assert (data.features[2].location, data.features[2].face) == (None, None)


def test_internal_chamfer_is_planned_with_a_boring_tool(app):
    from turnpilot import services

    turret = services.turret_entries(services.get_machine())
    job = JobSpec("P", 29, 50, (
        FeatureSpec(1, "od_turn", diameter=25, length=30),
        FeatureSpec(2, "chamfer", diameter=14, length=2, location="internal"),
    ))
    (chamfer_op,) = [op for op in planner.plan_job(job, turret, 4000) if op.feature_id == 2]
    assert chamfer_op.tool_type == "boring"


def test_internal_chamfer_joins_the_bore_not_the_od():
    features = [
        FeatureSpec(1, "od_turn", diameter=16, length=10),
        FeatureSpec(2, "bore", diameter=16, length=16),
        FeatureSpec(3, "chamfer", diameter=16, length=1, location="internal"),
    ]
    assert planner.match_chamfers(features)[id(features[2])] is features[1]
    features[2] = FeatureSpec(3, "chamfer", diameter=16, length=1, location="external")
    assert planner.match_chamfers(features)[id(features[2])] is features[0]


def test_review_and_confirm_keep_the_position(app, client):
    app.config["ANTHROPIC_CLIENT"] = FakeClient(make_response(part([
        feature("od_turn", 25, length=30), chamfer(14, "internal", "right"),
    ], overall_length=30)))
    png = (FIXTURES / "01_stepped_shaft.png").read_bytes()
    client.post("/jobs/upload", data={"drawing": (io.BytesIO(png), "vtulka.png")}, content_type="multipart/form-data")
    page = client.get("/extractions/1/review").data.decode()
    assert "<option selected>internal right</option>" in page

    form = {"name": "Vtulka", "material_id": "1", "quantity": "1", "blank_diameter": "29", "blank_length": "36",
            "feature_count": "2", "f0-include": "1", "f0-type": "od_turn", "f0-diameter": "25", "f0-length": "30",
            "f1-include": "1", "f1-type": "chamfer", "f1-diameter": "14", "f1-length": "2",
            "f1-chamfer_at": "internal right"}
    client.post("/extractions/1/confirm", data=form)
    stored = next(f for f in db.session.execute(db.select(Job)).scalar_one().features if f.type == "chamfer")
    assert (stored.location, stored.face) == ("internal", "right")


def test_bad_position_is_rejected(app, client):
    client.post("/jobs", data=dict(name="Shaft", material_id=1, quantity=1, blank_diameter=60, blank_length=100))
    resp = client.post("/jobs/1/features", data=dict(type="chamfer", diameter=40, length=1, chamfer_at="sideways"),
                       follow_redirects=True)
    assert b"Unknown chamfer position" in resp.data


def _eval_module():
    spec = importlib.util.spec_from_file_location("eval_extraction", Path(__file__).parent.parent / "tools" / "eval_extraction.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["eval_extraction"] = module
    spec.loader.exec_module(module)
    return module


def test_scorer_checks_the_chamfer_position():
    import json

    eval_extraction = _eval_module()
    answer = json.loads((FIXTURES / "02_threaded_shaft.expected.json").read_text(encoding="utf-8"))
    for f in answer["features"]:
        f["confidence"] = 0.9
    right = eval_extraction.run_one(FakeClient(make_response(answer)), "m", "02_threaded_shaft", "png")
    assert right.scores["chamfer_position"].pct == 100 and right.scores["chamfer_position"].total == 1

    next(f for f in answer["features"] if f["type"] == "chamfer")["location"] = "internal"
    wrong = eval_extraction.run_one(FakeClient(make_response(answer)), "m", "02_threaded_shaft", "png")
    assert wrong.scores["chamfer_position"].pct == 0
    assert any("chamfer at internal right (expected external right)" in m for m in wrong.mismatches)


def test_dimensions_first_carries_the_position():
    import copy

    from test_dimensions_first import REAL_SHAFT, to_wire

    from turnpilot.dimensions_first import parse_tool_input

    wire = to_wire(REAL_SHAFT)
    wire["overlays"][1].update(location="external", face="right")
    wire["overlays"][0].update(location="none", face="none")
    data = parse_tool_input(wire)
    ch = next(f for f in data.features if f.type == "chamfer")
    assert (ch.location, ch.face) == ("external", "right")
