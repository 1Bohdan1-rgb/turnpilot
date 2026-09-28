"""Rz on drawings: shown and stored as written, converted to Ra (≈ Rz/4, GOST 2789) only for the planner."""

import io

import pytest
from conftest import FIXTURES, FakeClient, feature, make_response, part

from turnpilot import planner, services
from turnpilot.drawing_reader import SYSTEM_PROMPT, parse_response
from turnpilot.extraction_schema import RECORD_PART_TOOL
from turnpilot.models import Feature, Job, Material, db


def rz_feature(type_, diameter=None, **kwargs):
    f = feature(type_, diameter, **kwargs)
    f["ra_param"] = "Rz"
    return f


def with_params(features, general_ra=None, general_ra_param="Ra", **kwargs):
    for f in features:
        f.setdefault("ra_param", "Ra")
    data = part(features, general_ra=general_ra, **kwargs)
    data["general_ra_param"] = general_ra_param
    return data


def test_rz_to_ra():
    assert planner.rz_to_ra(20) == 5
    assert planner.rz_to_ra(40) == 10
    assert planner.rz_to_ra(3.2) == 0.8


def test_schema_and_prompt_ask_for_the_parameter():
    item = RECORD_PART_TOOL["input_schema"]["properties"]["features"]["items"]
    assert item["properties"]["ra_param"]["enum"] == ["Ra", "Rz"] and "ra_param" in item["required"]
    assert "general_ra_param" in RECORD_PART_TOOL["input_schema"]["required"]
    assert "never convert Rz to Ra" in SYSTEM_PROMPT.replace("\n", " ")


def test_rz_is_parsed_as_written():
    data = parse_response(make_response(with_params(
        [rz_feature("thread", 14, length=28, pitch=2, tolerance="7H", ra=20)], general_ra=40, general_ra_param="Rz",
    )))
    assert (data.features[0].ra, data.features[0].ra_param) == (20, "Rz")
    assert (data.general_ra, data.general_ra_param) == (40, "Rz")


def test_older_output_without_parameter_is_ra():
    data = parse_response(make_response(part([feature("od_turn", 30, length=10, ra=1.6)])))
    assert data.features[0].ra_param == "Ra" and data.general_ra_param == "Ra"


def _review(app, client, tool_input):
    app.config["ANTHROPIC_CLIENT"] = FakeClient(make_response(tool_input))
    png = (FIXTURES / "01_stepped_shaft.png").read_bytes()
    client.post("/jobs/upload", data={"drawing": (io.BytesIO(png), "vtulka.png")}, content_type="multipart/form-data")
    return client.get("/extractions/1/review").data.decode()


def test_review_shows_rz_as_written(app, client):
    page = _review(app, client, with_params(
        [feature("od_turn", 25, length=30), rz_feature("od_turn", 20, length=14, ra=20)],
        general_ra=40, general_ra_param="Rz", overall_length=44,
    ))
    assert "General roughness on the drawing: Rz 40" in page
    assert '<select name="f1-ra_param">' in page
    assert 'name="f1-ra_param">\n              <option >Ra</option><option selected>Rz</option>' in page
    assert 'name="f0-ra" type="number" step="any" min="0" value="40.0"' in page  # general Rz 40 applied
    assert 'name="f0-ra_param">\n              <option >Ra</option><option selected>Rz</option>' in page


def test_confirm_stores_rz_and_planner_gets_ra_with_warning(app, client):
    _review(app, client, with_params([rz_feature("od_turn", 25, length=30, ra=20)], overall_length=30))
    form = {"name": "Vtulka", "material_id": "1", "quantity": "1", "blank_diameter": "29", "blank_length": "36",
            "feature_count": "1", "f0-include": "1", "f0-type": "od_turn", "f0-diameter": "25", "f0-length": "30",
            "f0-ra": "20", "f0-ra_param": "Rz"}
    client.post("/extractions/1/confirm", data=form)
    job = db.session.execute(db.select(Job)).scalar_one()
    stored = job.features[0]
    assert (stored.ra, stored.ra_param) == (20, "Rz")

    spec = services.job_spec(job)
    assert spec.features[0].ra == 5 and spec.features[0].ra_from_rz == 20
    page = client.post(f"/jobs/{job.id}/calculate", follow_redirects=True).data.decode()
    assert "Ra 5 µm assumed from Rz 20 (Ra ≈ Rz/4, GOST 2789): check" in page
    assert "Rz 20" in client.get(f"/jobs/{job.id}").data.decode()


def test_manual_feature_with_rz(app, client):
    client.post("/jobs", data=dict(name="Shaft", material_id=1, quantity=1, blank_diameter=60, blank_length=100))
    client.post("/jobs/1/features", data=dict(type="od_turn", diameter=40, length=50, ra=6.3, ra_param="Rz"))
    f = db.session.execute(db.select(Feature)).scalar_one()
    assert (f.ra, f.ra_param) == (6.3, "Rz")
    resp = client.post("/jobs/1/features", data=dict(type="od_turn", diameter=30, length=10, ra=1, ra_param="Rq"),
                       follow_redirects=True)
    assert b"must be Ra or Rz" in resp.data


def test_grinding_check_uses_converted_rz(app):
    material = db.session.execute(db.select(Material).filter_by(iso_group="P")).scalar_one()
    job = Job(name="Fine", material=material, quantity=1, blank_diameter=60, blank_length=100)
    job.features.append(Feature(type="od_turn", diameter=40, length=50, ra=1.6, ra_param="Rz"))  # Ra 0.4
    db.session.add(job)
    db.session.commit()
    ops = planner.plan_job(services.job_spec(job), services.turret_entries(services.get_machine()), 4000)
    finish = next(op for op in ops if op.mode == "finish")
    assert planner.GRINDING_WARNING in finish.warnings
