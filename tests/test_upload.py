"""Upload -> review -> confirm with a mocked API client."""

import io
import json
import os

import pytest
from conftest import FIXTURES, FakeClient, feature, make_response, part

from turnpilot.models import DrawingExtraction, Job, Operation, db

PNG = (FIXTURES / "02_threaded_shaft.png").read_bytes()

THREADED_SHAFT = part(
    [
        feature("od_turn", 30, length=60, ra=1.6),
        feature("od_turn", 20, length=30, confidence=0.5),  # low confidence
        feature("groove", 17, start_diameter=20, length=3),
        feature("chamfer", 20, length=1),
        feature("thread", 20, length=25, tolerance="6g", pitch=1.5),
    ],
    overall_length=90,
    quantity=50,
    warnings=["Groove width is small and hard to read."],
)


def _use_model(app, tool_input, **kwargs):
    fake = FakeClient(make_response(tool_input, **kwargs))
    app.config["ANTHROPIC_CLIENT"] = fake
    return fake


def _upload(client, data=PNG, filename="shaft.png"):
    return client.post("/jobs/upload", data={"drawing": (io.BytesIO(data), filename)},
                       content_type="multipart/form-data")


def _extractions():
    return db.session.execute(db.select(DrawingExtraction)).scalars().all()


def _review_form(**overrides):
    """What the review page posts for THREADED_SHAFT, with the suggested blank."""
    form = {
        "name": "Threaded shaft", "material_id": "1", "quantity": "50",
        "blank_diameter": "32", "blank_length": "95", "feature_count": "5",
        "add_face": "1", "add_parting": "1",
    }
    for i, f in enumerate(THREADED_SHAFT["features"]):
        form[f"f{i}-include"] = "1"
        for key, value in f.items():
            form[f"f{i}-{key}"] = "" if value is None else str(value)
    form.update(overrides)
    return form


# --- rejected uploads ------------------------------------------------------------------

@pytest.mark.parametrize(
    "filename, data, message",
    [
        ("notes.txt", b"just text", b"Unsupported file type"),
        ("drawing.gif", b"GIF89a....", b"Unsupported file type"),
        ("drawing.png", b"this is not a png", b"not a valid .png file"),
        ("drawing.pdf", PNG, b"not a valid .pdf file"),
        ("drawing.png", b"", b"empty"),
    ],
    ids=["txt", "gif", "fake-png", "png-as-pdf", "empty"],
)
def test_wrong_files_are_rejected(app, client, filename, data, message):
    fake = _use_model(app, THREADED_SHAFT)
    resp = _upload(client, data, filename)
    assert resp.status_code == 302
    assert message in client.get("/jobs/upload").data
    assert _extractions() == []
    assert fake.messages.calls == []  # nothing was sent to the API


def test_too_large_file_is_rejected(app, client):
    app.config["MAX_CONTENT_LENGTH"] = 50_000
    fake = _use_model(app, THREADED_SHAFT)
    resp = _upload(client, PNG)
    assert resp.status_code == 302
    assert b"too large" in client.get("/jobs/upload").data
    assert fake.messages.calls == []


def test_filename_is_sanitized(app, client):
    _use_model(app, THREADED_SHAFT)
    _upload(client, PNG, "../../etc/pass wd.png")
    (extraction,) = _extractions()
    assert extraction.original_filename == "etc_pass_wd.png"
    assert "/" not in extraction.stored_filename and ".." not in extraction.stored_filename


# --- successful upload and audit trail -----------------------------------------------

def test_upload_stores_original_and_raw_response(app, client):
    _use_model(app, THREADED_SHAFT)
    resp = _upload(client)
    (extraction,) = _extractions()
    assert resp.headers["Location"].endswith(f"/extractions/{extraction.id}/review")
    assert extraction.status == "extracted"
    assert extraction.model == "test-model"

    folder = os.path.join(app.instance_path, "drawings")
    with open(os.path.join(folder, extraction.stored_filename), "rb") as f:
        assert f.read() == PNG
    assert os.path.exists(os.path.join(folder, extraction.sent_filename))

    raw = json.loads(extraction.raw_response)
    assert raw["content"][0]["input"] == THREADED_SHAFT
    assert raw["usage"]["output_tokens"] == 400
    assert json.loads(extraction.parsed)["overall_length"] == 90


def test_failed_extraction_is_recorded(app, client):
    _use_model(app, None, stop_reason="end_turn", text="Sorry, unreadable.")
    _upload(client)
    (extraction,) = _extractions()
    assert extraction.status == "failed"
    assert "no record_part call" in extraction.error
    assert json.loads(extraction.raw_response)["content"][0]["text"] == "Sorry, unreadable."
    assert b"could not be read" in client.get("/jobs/upload").data
    assert db.session.execute(db.select(Job)).first() is None


def test_api_error_is_recorded(app, client):
    class Broken:
        class messages:
            @staticmethod
            def create(**kwargs):
                raise ConnectionError("network down")

    app.config["ANTHROPIC_CLIENT"] = Broken()
    _upload(client)
    (extraction,) = _extractions()
    assert extraction.status == "failed" and "network down" in extraction.error


# --- review screen -----------------------------------------------------------------------

def test_review_highlights_low_confidence_and_shows_warnings(app, client):
    _use_model(app, THREADED_SHAFT)
    _upload(client)
    page = client.get("/extractions/1/review").data.decode()
    assert page.count('class="low-confidence"') == 1
    assert "Groove width is small and hard to read." in page
    assert 'value="Threaded shaft"' not in page  # name comes from the file name
    assert "selected>Steel 45 (C45)" in page  # material matched from the title block


def test_review_suggests_blank_when_missing(app, client):
    _use_model(app, THREADED_SHAFT)
    _upload(client)
    page = client.get("/extractions/1/review").data.decode()
    # largest Ø30 + 2 mm = 32 -> bar 32; length 90 + 2 facing + 3 parting (seed tool width) = 95
    assert 'name="blank_diameter" type="number" step="any" min="0" value="32.0"' in page
    assert 'name="blank_length" type="number" step="any" min="0" value="95.0"' in page
    assert page.count("badge-suggested") == 2


def test_review_uses_blank_from_drawing(app, client):
    _use_model(app, {**THREADED_SHAFT, "blank_diameter": 36, "blank_length": 100})
    _upload(client)
    page = client.get("/extractions/1/review").data.decode()
    assert 'value="36.0"' in page and 'value="100.0"' in page
    assert "badge-suggested" not in page


def test_review_shows_grinding_warning(app, client):
    shaft = part([feature("od_turn", 30, length=40, tolerance="h5"), feature("od_turn", 25, length=30, ra=0.4),
                  feature("od_turn", 20, length=20, tolerance="h7", ra=0.8)])
    _use_model(app, shaft)
    _upload(client)
    page = client.get("/extractions/1/review").data.decode()
    assert page.count("may require grinding — not guaranteed by turning") == 2


# --- confirm -----------------------------------------------------------------------------

def test_confirm_creates_job_with_features(app, client):
    _use_model(app, THREADED_SHAFT)
    _upload(client)
    form = _review_form(**{"f3-include": ""})  # leave the chamfer out
    resp = client.post("/extractions/1/confirm", data=form)
    job = db.session.execute(db.select(Job)).scalar_one()
    assert resp.headers["Location"].endswith(f"/jobs/{job.id}")
    assert (job.name, job.quantity, job.blank_diameter, job.blank_length) == ("Threaded shaft", 50, 32, 95)
    assert [f.type for f in job.features] == ["face", "od_turn", "od_turn", "groove", "thread", "parting"]
    assert job.features[2].confidence == 0.5
    assert job.features[4].pitch == 1.5

    extraction = db.session.get(DrawingExtraction, 1)
    assert extraction.status == "confirmed" and extraction.job_id == job.id and extraction.confirmed_at

    client.post(f"/jobs/{job.id}/calculate")
    ops = db.session.execute(db.select(Operation)).scalars().all()
    assert {op.tool_type for op in ops} >= {"facing", "turning_rough", "grooving", "threading", "parting"}


def test_no_job_before_confirm(app, client):
    _use_model(app, THREADED_SHAFT)
    _upload(client)
    client.get("/extractions/1/review")
    assert db.session.execute(db.select(Job)).first() is None


def test_confirm_with_invalid_values_keeps_the_form(app, client):
    _use_model(app, THREADED_SHAFT)
    _upload(client)
    resp = client.post("/extractions/1/confirm", data=_review_form(**{"f2-start_diameter": "15"}))
    assert resp.status_code == 400
    page = resp.data.decode()
    assert "Feature 3: Groove start diameter must be larger" in page
    assert 'value="15"' in page  # the user's input is kept
    assert db.session.execute(db.select(Job)).first() is None
    assert db.session.get(DrawingExtraction, 1).status == "extracted"


def test_confirm_twice_does_not_duplicate(app, client):
    _use_model(app, THREADED_SHAFT)
    _upload(client)
    client.post("/extractions/1/confirm", data=_review_form())
    client.post("/extractions/1/confirm", data=_review_form())
    assert len(db.session.execute(db.select(Job)).scalars().all()) == 1


def test_drawing_files_are_served(app, client):
    _use_model(app, THREADED_SHAFT)
    _upload(client)
    assert client.get("/extractions/1/drawing/original").data == PNG
    assert client.get("/extractions/1/drawing/sent").status_code == 200
    assert client.get("/extractions/1/drawing/other").status_code == 404


# --- Ra that may belong to the bore ---------------------------------------------------

BORE_RA_WARNING = "Ra may belong to the bore — check"

BUSHING = part(
    [
        feature("od_turn", 50, length=45, tolerance="±0.05", ra=1.6),  # model put the bore's Ra here
        feature("bore", 30, length=45, tolerance="H7"),
        feature("chamfer", 50, length=1),
    ],
    material="AISI 304",
    blank_diameter=55,
    blank_length=50,
    overall_length=45,
    quantity=10,
)


def _review_page(app, client, tool_input):
    _use_model(app, tool_input)
    _upload(client)
    return client.get("/extractions/1/review").data.decode()


def test_bore_ra_warning_on_both_rows(app, client):
    page = _review_page(app, client, BUSHING)
    assert page.count(BORE_RA_WARNING) == 2  # the OD row with Ra and the bore row without


def test_no_bore_ra_warning_when_bore_has_ra(app, client):
    bushing = {**BUSHING, "features": [dict(f) for f in BUSHING["features"]]}
    bushing["features"][1]["ra"] = 1.6
    assert BORE_RA_WARNING not in _review_page(app, client, bushing)


def test_no_bore_ra_warning_without_bore(app, client):
    assert BORE_RA_WARNING not in _review_page(app, client, THREADED_SHAFT)  # OD with Ra, no bore


def test_no_bore_ra_warning_when_od_has_no_ra(app, client):
    bushing = {**BUSHING, "features": [dict(f) for f in BUSHING["features"]]}
    bushing["features"][0]["ra"] = None
    assert BORE_RA_WARNING not in _review_page(app, client, bushing)


def test_bore_ra_warning_ignores_excluded_rows(app, client):
    _use_model(app, BUSHING)
    _upload(client)
    form = {
        "name": "Bushing", "material_id": "2", "quantity": "10", "blank_diameter": "55", "blank_length": "50",
        "feature_count": "3", "f0-include": "1", "f2-include": "1",  # bore row unticked
    }
    for i, f in enumerate(BUSHING["features"]):
        for key, value in f.items():
            form[f"f{i}-{key}"] = "" if value is None else str(value)
    form["name"] = ""  # validation error, so the review page is shown again from the form
    page = client.post("/extractions/1/confirm", data=form).data.decode()
    assert BORE_RA_WARNING not in page


# --- facing / parting are not duplicated --------------------------------------------------

def _face_rows():
    return part(
        [feature("face"), feature("od_turn", 30, length=60), feature("parting")],
        overall_length=60,
    )


def test_face_from_drawing_unticks_the_checkbox(app, client):
    page = _review_page(app, client, _face_rows())
    assert 'name="add_face"' in page
    assert 'name="add_face" value="1" checked' not in page
    assert 'name="add_parting" value="1" checked' not in page


def test_face_is_not_duplicated_on_confirm(app, client):
    _use_model(app, _face_rows())
    _upload(client)
    form = {
        "name": "Shaft", "material_id": "1", "quantity": "1", "blank_diameter": "32", "blank_length": "65",
        "feature_count": "3", "add_face": "1", "add_parting": "1",  # ticked although the rows have them
    }
    for i, f in enumerate(_face_rows()["features"]):
        form[f"f{i}-include"] = "1"
        for key, value in f.items():
            form[f"f{i}-{key}"] = "" if value is None else str(value)
    client.post("/extractions/1/confirm", data=form)
    job = db.session.execute(db.select(Job)).scalar_one()
    types = [f.type for f in job.features]
    assert types.count("face") == 1 and types.count("parting") == 1
    assert types == ["face", "od_turn", "parting"]


def test_checkbox_adds_face_when_drawing_has_none(app, client):
    _use_model(app, THREADED_SHAFT)
    _upload(client)
    client.post("/extractions/1/confirm", data=_review_form())
    job = db.session.execute(db.select(Job)).scalar_one()
    assert [f.type for f in job.features].count("face") == 1
