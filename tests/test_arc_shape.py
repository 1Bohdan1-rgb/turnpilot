"""Arcs G02/G03, commit 1: an arc's or a fillet's shape (convex / concave) and a fillet's side, from the DXF geometry
(dxf_reader called as it is) or chosen by the operator."""
import io
import json

import dxf_drawings as dd
import pytest
from test_dxf_upload import NoApi, _confirm_form

from turnpilot import services
from turnpilot.models import DrawingExtraction, Feature, Job, db


@pytest.fixture
def pin_extraction(app, client, tmp_path):
    app.config["ANTHROPIC_CLIENT"] = NoApi()
    data = dd.write(tmp_path / "pin.dxf", dd.draw_pin).read_bytes()
    client.post("/jobs/upload", data={"drawing": (io.BytesIO(data), "pin.dxf")}, content_type="multipart/form-data")
    return db.session.execute(db.select(DrawingExtraction)).scalar_one()


def test_shapes_from_the_dxf_geometry(app, pin_extraction):
    shapes = services.dxf_arc_shapes(pin_extraction, app.instance_path)
    types = [f["type"] for f in json.loads(pin_extraction.parsed)["features"]]
    arc, fillet = types.index("arc"), types.index("fillet")
    assert shapes[arc] == {"arc_shape": "convex"}  # the spherical end R9
    assert shapes[fillet] == {"arc_shape": "convex", "face": "right"}  # Ø28's edge down to the Ø18 step


def test_review_page_and_confirm_keep_the_shapes(app, client, pin_extraction):
    page = client.get(f"/extractions/{pin_extraction.id}/review").get_data(as_text=True)
    assert page.count('<option value="convex" selected>convex</option>') == 2
    form = _confirm_form(pin_extraction)
    types = [f["type"] for f in json.loads(pin_extraction.parsed)["features"]]
    form[f"f{types.index('arc')}-arc_shape"] = "convex"
    form[f"f{types.index('fillet')}-arc_shape"] = "convex"
    form[f"f{types.index('fillet')}-position"] = "external right"
    client.post(f"/extractions/{pin_extraction.id}/confirm", data=form)
    job = db.session.execute(db.select(Job)).scalar_one()
    arc = next(f for f in job.features if f.type == "arc")
    fillet = next(f for f in job.features if f.type == "fillet")
    assert arc.arc_convex is True and (fillet.arc_convex, fillet.face) == (True, "right")


def test_a_concave_arc_from_the_geometry(app, client, tmp_path):
    def draw(sheet):  # Ø30 L10, a concave arc R10 from Ø30 down to Ø20 and up again (L ≈ 17.3), Ø30 L10
        s = sheet
        s.axis(-3, 41)
        s.vertical(0, 0, 15)
        s.line(0, 15, 10, 15)
        s.arc(18.66, 20, 10, 210, 330)
        s.line(27.32, 15, 37.32, 15)
        s.vertical(37.32, 0, 15)
        s.diameter(5, 30, f"{dd.DIA}30")
    app.config["ANTHROPIC_CLIENT"] = NoApi()
    data = dd.write(tmp_path / "groove.dxf", draw).read_bytes()
    client.post("/jobs/upload", data={"drawing": (io.BytesIO(data), "g.dxf")}, content_type="multipart/form-data")
    extraction = db.session.execute(db.select(DrawingExtraction)).scalar_one()
    shapes = services.dxf_arc_shapes(extraction, app.instance_path)
    assert {"arc_shape": "concave"} in shapes.values()


def test_operator_chooses_the_shape_by_hand(app, client):
    job = Job(name="J", material_id=1, quantity=1, blank_diameter=40, blank_length=80)
    db.session.add(job)
    db.session.commit()
    client.post(f"/jobs/{job.id}/features", data=dict(type="arc", start_diameter=30, diameter=20, length=8, radius=10,
                                                      arc_shape="concave"))
    client.post(f"/jobs/{job.id}/features", data=dict(type="fillet", radius=2, position="external left"))
    arc, fillet = db.session.execute(db.select(Feature).order_by(Feature.id)).scalars().all()
    assert arc.arc_convex is False and (fillet.arc_convex, fillet.face) == (None, "left")  # not chosen: unknown
    response = client.post(f"/jobs/{job.id}/features", data=dict(type="arc", radius=5, arc_shape="round"),
                           follow_redirects=True)
    assert "Arc shape: convex or concave" in response.get_data(as_text=True)
