"""Decimal commas in tolerances (GOST drawings: "±0,05") become points everywhere in the app."""

import io

import pytest
from conftest import FIXTURES, FakeClient, feature, make_response, part

from turnpilot import planner, services
from turnpilot.drawing_reader import parse_response
from turnpilot.extraction_schema import normalize_tolerance
from turnpilot.models import Feature, Job, Material, db


@pytest.mark.parametrize(
    "written, normalized",
    [
        ("±0,05", "±0.05"),
        ("+0,1/-0,05", "+0.1/-0.05"),
        ("0/-0,021", "0/-0.021"),
        ("+0,5/-1,3", "+0.5/-1.3"),
        ("±0.05", "±0.05"),
        ("h7", "h7"),
        ("js12", "js12"),
        ("6g", "6g"),
        ("  ±0,105 ", "±0.105"),
        ("h6, f7", "h6, f7"),  # a comma that is not between digits stays
        ("", None),
        (None, None),
    ],
)
def test_normalize_tolerance(written, normalized):
    assert normalize_tolerance(written) == normalized


def test_features_mode_output_is_normalized():
    data = parse_response(make_response(part([feature("od_turn", 60, length=40, tolerance="±0,05")])))
    assert data.features[0].tolerance == "±0.05"


def test_dimensions_first_output_is_normalized():
    from test_dimensions_first import REAL_SHAFT, to_wire

    from turnpilot.dimensions_first import parse_tool_input

    wire = to_wire(REAL_SHAFT)
    wire["sections"][0]["tolerance"] = "+0,5/-1,3"
    wire["sections"][2]["tolerance"] = "0/-0,021"
    data = parse_tool_input(wire)
    assert data.features[0].tolerance == "+0.5/-1.3"
    assert data.features[2].tolerance == "0/-0.021"  # zero deviation kept, comma converted


def test_review_screen_shows_points(app, client):
    app.config["ANTHROPIC_CLIENT"] = FakeClient(make_response(
        part([feature("od_turn", 60, length=40, tolerance="±0,05")], overall_length=40)
    ))
    png = (FIXTURES / "01_stepped_shaft.png").read_bytes()
    client.post("/jobs/upload", data={"drawing": (io.BytesIO(png), "shaft.png")}, content_type="multipart/form-data")
    page = client.get("/extractions/1/review").data.decode()
    assert 'name="f0-tolerance" value="±0.05"' in page
    assert "±0,05" not in page


def test_manual_feature_form_normalizes(app, client):
    client.post("/jobs", data=dict(name="Shaft", material_id=1, quantity=1, blank_diameter=60, blank_length=100))
    client.post("/jobs/1/features", data=dict(type="od_turn", diameter=40, length=50, tolerance="0/-0,021"))
    assert db.session.execute(db.select(Feature)).scalar_one().tolerance == "0/-0.021"


def test_planner_gets_points_even_for_old_rows(app):
    material = db.session.execute(db.select(Material).filter_by(iso_group="P")).scalar_one()
    job = Job(name="Old", material=material, quantity=1, blank_diameter=60, blank_length=100)
    job.features.append(Feature(type="od_turn", diameter=40, length=50, tolerance="±0,004"))  # saved before the fix
    db.session.add(job)
    db.session.commit()
    spec = services.job_spec(job)
    assert spec.features[0].tolerance == "±0.004"
    finish = next(op for op in planner.plan_job(spec, services.turret_entries(services.get_machine()), 4000)
                  if op.mode == "finish")
    assert planner.GRINDING_WARNING in finish.warnings  # ±0.004 on Ø40 is within IT5
