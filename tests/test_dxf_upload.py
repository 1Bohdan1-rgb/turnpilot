"""Upload of a KOMPAS DXF: read by the code, one extraction per part, no API call."""
import io
import json

import pytest
from conftest import FIXTURES

import dxf_drawings as dd
from turnpilot.drawing_reader import detect_file_type
from turnpilot.models import DrawingExtraction, db


class NoApi:
    """Fails the test if anything tries to call the model."""

    def __getattr__(self, name):
        raise AssertionError("a DXF must not reach the API")


@pytest.fixture
def no_api(app):
    app.config["ANTHROPIC_CLIENT"] = NoApi()


def _upload(client, data, filename="Завіса.dxf"):
    return client.post("/jobs/upload", data={"drawing": (io.BytesIO(data), filename)},
                       content_type="multipart/form-data")


def _extractions():
    return db.session.execute(db.select(DrawingExtraction).order_by(DrawingExtraction.id)).scalars().all()


def _dxf_bytes(tmp_path, *drawers):
    return dd.write(tmp_path / "sheet.dxf", *drawers).read_bytes()


def test_detect_dxf(tmp_path):
    assert detect_file_type(_dxf_bytes(tmp_path, dd.draw_shaft)) == "dxf"
    assert detect_file_type(b"999\r\nKOMPAS\r\n  0\r\nSECTION\r\n  2\r\nHEADER\r\n") == "dxf"
    assert detect_file_type(b"AutoCAD Binary DXF\r\n\x1a\x00") == "dxf"
    assert detect_file_type(b"SECTION but not a DXF") is None
    assert detect_file_type(b"%PDF-1.7") == "pdf"


def test_upload_two_parts(client, no_api, tmp_path):
    response = _upload(client, _dxf_bytes(tmp_path, dd.draw_shaft, dd.draw_pin))
    first, second = _extractions()
    assert response.status_code == 302 and response.location.endswith(f"/extractions/{first.id}/review")
    for n, extraction in enumerate((first, second), start=1):
        assert (extraction.status, extraction.file_type, extraction.read_mode, extraction.dxf_part) == (
            "extracted", "dxf", "dxf", n)
        assert extraction.model is None and extraction.sent_filename is None
        assert extraction.prompt_version.startswith("dxf:") and extraction.original_filename == "Завіса.dxf"
    assert first.stored_filename == second.stored_filename
    assert json.loads(first.parsed)["overall_length"] == 50
    assert json.loads(second.binding)["label"] == "Part 2 of 2: Ø28 × 59, 6 sections"


def test_upload_not_kompas(client, no_api):
    import ezdxf
    doc = ezdxf.new()
    doc.modelspace().add_line((0, 0), (10, 0))
    stream = io.StringIO()
    doc.write(stream)
    response = _upload(client, stream.getvalue().encode("utf-8"), "plain.dxf")
    (extraction,) = _extractions()
    assert extraction.status == "failed" and "Only KOMPAS-3D DXF" in extraction.error
    assert response.status_code == 302 and response.location.endswith("/jobs/upload")
    page = client.get("/jobs/upload").get_data(as_text=True)
    assert "Only KOMPAS-3D DXF" in page


def test_upload_png_named_dxf(client, no_api):
    _upload(client, (FIXTURES / "02_threaded_shaft.png").read_bytes(), "shaft.dxf")
    assert _extractions() == []
    assert "not a valid .dxf file" in client.get("/jobs/upload").get_data(as_text=True)


def _confirm_form(extraction, **overrides):
    """What the review page posts for a DXF part: its rows as read, a material and the suggested blank."""
    data = json.loads(extraction.parsed)
    form = {"name": "Pin", "material_id": "1", "quantity": "1", "blank_diameter": "45", "blank_length": "80",
            "blank_shape": "round", "feature_count": str(len(data["features"])), "add_face": "1", "add_parting": "1"}
    for i, f in enumerate(data["features"]):
        form[f"f{i}-include"] = "1"
        form[f"f{i}-type"] = f["type"]
        for key in ("diameter", "start_diameter", "length", "tolerance", "pitch", "radius"):
            if f.get(key) is not None:
                form[f"f{i}-{key}"] = str(f[key])
        form[f"f{i}-position"] = " ".join(p for p in (f.get("location"), f.get("face")) if p)
    form.update(overrides)
    return form


def test_review_page_of_a_dxf_part(client, no_api, tmp_path):
    _upload(client, _dxf_bytes(tmp_path, dd.draw_shaft, dd.draw_pin))
    first, second = _extractions()
    page = client.get(f"/extractions/{first.id}/review").get_data(as_text=True)
    assert "read from the DXF geometry by the code" in page and "Image sent to the model" not in page
    assert "2 parts on this sheet" in page and f"/extractions/{second.id}/review" in page
    assert "Dimensions → sections" in page and "inner profile: not read" in page
    assert "geometry 30 ≠ text 30.2" in page  # in the binding table and under the groove row
    assert "from Ø40h12" in page and 'value="Завіса (part 1)"' in page
    assert "DXF warnings" in page and "Ø10H11 is on the inner profile" in page
    assert "Read again" not in page


def test_confirm_each_part_as_its_own_job(client, no_api, tmp_path):
    from turnpilot.models import Feature, Job, Operation
    _upload(client, _dxf_bytes(tmp_path, dd.draw_shaft, dd.draw_pin))
    shaft, pin = _extractions()
    response = client.post(f"/extractions/{pin.id}/confirm", data=_confirm_form(pin))
    job = db.session.execute(db.select(Job)).scalar_one()
    assert response.location.endswith(f"/jobs/{job.id}") and pin.job_id == job.id and pin.status == "confirmed"
    types = [f.type for f in job.active_features]
    assert types == ["face", "od_turn", "taper", "od_turn", "od_turn", "fillet", "od_turn", "arc", "parting"]
    arc = db.session.execute(db.select(Feature).filter_by(type="arc")).scalar_one()
    assert (arc.start_diameter, arc.radius, arc.length, arc.diameter) == (18, 9, 9, None)
    client.post(f"/jobs/{job.id}/calculate")
    manual = db.session.execute(db.select(Operation).filter_by(tool_type="manual")).scalars().all()
    assert manual == []  # a taper and an arc are turned; with the order along the axis a fillet goes with its section

    # the other part is still to be reviewed, and its page links to the job of this one
    page = client.get(f"/extractions/{shaft.id}/review").get_data(as_text=True)
    assert f"/jobs/{job.id}" in page and "(job created)" in page
    assert shaft.status == "extracted"


def test_od_under_a_dxf_thread_is_turned(client, no_api, tmp_path):
    from turnpilot.models import Job, Operation
    from turnpilot.planner import THREAD_MAJOR_NOTE
    _upload(client, _dxf_bytes(tmp_path, dd.draw_shaft))
    (shaft,) = _extractions()
    client.post(f"/extractions/{shaft.id}/confirm", data=_confirm_form(shaft, name="Shaft"))
    job = db.session.execute(db.select(Job)).scalar_one()
    client.post(f"/jobs/{job.id}/calculate")
    finish = db.session.execute(db.select(Operation).filter_by(tool_type="turning_finish")).scalars().all()
    assert any(f"{THREAD_MAJOR_NOTE}: Ø35.85 (nominal Ø36)" in (op.note or "") for op in finish)


def test_axial_order_known_from_a_dxf_until_a_feature_is_added(client, no_api, tmp_path):
    from turnpilot.models import Job
    _upload(client, _dxf_bytes(tmp_path, dd.draw_pin))
    (pin,) = _extractions()
    client.post(f"/extractions/{pin.id}/confirm", data=_confirm_form(pin))
    job = db.session.execute(db.select(Job)).scalar_one()
    assert job.axial_order_known
    client.post(f"/jobs/{job.id}/features/{job.active_features[1].id}/delete")
    assert job.axial_order_known  # deleting keeps the order
    client.post(f"/jobs/{job.id}/features", data=dict(type="od_turn", diameter=20, length=5))
    assert not job.axial_order_known  # an added feature is at the end of the list


def test_dxf_job_is_roughed_from_the_neighbouring_sections(client, no_api, tmp_path):
    from turnpilot.models import Job, Operation
    from turnpilot.planner import TWO_SIDES_NOTE
    _upload(client, _dxf_bytes(tmp_path, dd.draw_pin))
    (pin,) = _extractions()
    client.post(f"/extractions/{pin.id}/confirm", data=_confirm_form(pin))  # blank Ø45
    job = db.session.execute(db.select(Job)).scalar_one()

    def rough():
        ops = db.session.execute(db.select(Operation).filter_by(tool_type="turning_rough", is_archived=False)).scalars()
        return {op.feature.diameter: op for op in ops if op.feature.type != "arc"}

    client.post(f"/jobs/{job.id}/calculate")
    by_diameter = rough()
    # Ø28 is the largest: from the bar; Ø24, Ø16 and Ø18 lie on both sides of it and start from Ø28
    assert {d: op.ref_diameter for d, op in by_diameter.items()} == {24: 28.8, 16: 28.8, 28: 45, 18: 28.8}  # Ø28 + 2 × 0.4
    assert TWO_SIDES_NOTE in by_diameter[28].note

    client.post(f"/jobs/{job.id}/features", data=dict(type="od_turn", diameter=10, length=2))  # order lost
    client.post(f"/jobs/{job.id}/calculate")
    assert {op.ref_diameter for op in rough().values()} == {45}
