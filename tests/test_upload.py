"""Upload -> review -> confirm with a mocked API client."""

import io
import json
import os
import re

import pytest
from conftest import FIXTURES, FakeClient, feature, make_response, part

from turnpilot.models import DrawingExtraction, Job, Operation, db

PNG = (FIXTURES / "02_threaded_shaft.png").read_bytes()

THREADED_SHAFT = part(
    [
        feature("od_turn", 30, length=60, ra=1.6),
        feature("od_turn", 20, length=27, confidence=0.5),  # low confidence; the 3 mm groove is its own section
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
            def stream(**kwargs):
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


# --- not a lathe part --------------------------------------------------------------------

NOT_TURNED_BANNER = "This does not look like a lathe part"


def _squash(html):
    """Collapse whitespace so assertions do not depend on template line breaks."""
    return re.sub(r"\s+", " ", html)

FORK = part(
    [feature("bore", 12, length=10, confidence=0.4)],
    material="Steel 45 (C45)",
    part_type="not_turned",
    warnings=["The part is a flat fork with holes, not a body of revolution."],
)


def _fork_form(**overrides):
    form = {
        "name": "Fork", "material_id": "1", "quantity": "1", "blank_diameter": "40", "blank_length": "60",
        "feature_count": "1", "f0-include": "1", "add_face": "1", "add_parting": "1",
    }
    for key, value in FORK["features"][0].items():
        form[f"f0-{key}"] = "" if value is None else str(value)
    form.update(overrides)
    return form


@pytest.mark.parametrize("part_type", ["not_turned", "unclear"])
def test_banner_and_disabled_confirm_for_non_lathe_part(app, client, part_type):
    page = _review_page(app, client, {**FORK, "part_type": part_type})
    assert NOT_TURNED_BANNER in page
    assert 'name="override_part_type"' in page
    assert "I understand, create anyway" in page
    assert 'id="confirm-button" disabled>Confirm' in _squash(page)


def test_no_banner_for_turned_part(app, client):
    page = _review_page(app, client, THREADED_SHAFT)
    assert NOT_TURNED_BANNER not in page
    assert 'name="override_part_type"' not in page
    assert 'id="confirm-button" >Confirm' in _squash(page)  # not disabled


def test_confirm_blocked_without_override(app, client):
    _use_model(app, FORK)
    _upload(client)
    resp = client.post("/extractions/1/confirm", data=_fork_form())
    assert resp.status_code == 400
    assert NOT_TURNED_BANNER in resp.data.decode()
    assert db.session.execute(db.select(Job)).first() is None
    assert db.session.get(DrawingExtraction, 1).status == "extracted"


def test_confirm_allowed_with_override(app, client):
    _use_model(app, FORK)
    _upload(client)
    resp = client.post("/extractions/1/confirm", data=_fork_form(override_part_type="1"))
    job = db.session.execute(db.select(Job)).scalar_one()
    assert resp.headers["Location"].endswith(f"/jobs/{job.id}")
    extraction = db.session.get(DrawingExtraction, 1)
    assert extraction.status == "confirmed"
    assert json.loads(extraction.parsed)["part_type"] == "not_turned"  # the override stays auditable


def test_override_checkbox_kept_after_validation_error(app, client):
    _use_model(app, FORK)
    _upload(client)
    resp = client.post("/extractions/1/confirm", data=_fork_form(override_part_type="1", name=""))
    page = resp.data.decode()
    assert resp.status_code == 400
    assert 'id="override-part-type" checked> I understand' in _squash(page)


# --- real drawing rules on the review screen -------------------------------------------------

GOST_SHAFT = part(
    [
        feature("od_turn", 80, length=40.1, tolerance="+0.5/-1.3"),
        feature("fillet", radius=10, tolerance="±0.05"),
        feature("od_turn", 60, length=40, tolerance="±0.05", confidence=0.8),
        feature("taper", 55, start_diameter=60, length=30),
        feature("groove", 44, start_diameter=48, length=4),
        feature("od_turn", 48, length=16),
        feature("thread", 48, length=16, tolerance="6g", pitch=1.5),
        feature("chamfer", 48, length=1.5),
    ],
    overall_length=140.1,
    quantity=1,
    general_ra=3.2,
    warnings=["Ø60: length derived from chain dimensions"],
)


def test_review_shows_taper_and_fillet(app, client):
    page = _review_page(app, client, GOST_SHAFT)
    assert 'selected>taper' in page and 'selected>fillet' in page
    assert 'name="f1-radius" type="number" step="any" min="0" value="10.0"' in page
    assert 'name="f3-start_diameter" type="number" step="any" min="0" value="60.0"' in page


def test_general_ra_fills_features_without_own_mark(app, client):
    shaft = {**GOST_SHAFT, "features": [dict(f) for f in GOST_SHAFT["features"]]}
    shaft["features"][2]["ra"] = 1.6  # own mark on Ø60
    page = _review_page(app, client, shaft)
    assert "General roughness on the drawing: Ra 3.2" in page
    assert 'name="f2-ra" type="number" step="any" min="0" value="1.6"' in page  # own mark kept
    assert 'name="f0-ra" type="number" step="any" min="0" value="3.2"' in page  # general applied
    # one badge per row without its own Ra (all but Ø60) + one in the legend above the table
    assert page.count('<span class="badge badge-general">general</span>') == (len(shaft["features"]) - 1) + 1


def test_bore_ra_check_uses_own_marks_not_general_ra(app, client):
    bushing = {**BUSHING, "general_ra": 6.3}
    page = _review_page(app, client, bushing)
    assert page.count(BORE_RA_WARNING) == 2  # the bore gets general Ra, but the check runs on own marks


def test_confirm_keeps_taper_and_fillet_and_plans_them_as_manual(app, client):
    _use_model(app, GOST_SHAFT)
    _upload(client)
    form = {
        "name": "GOST shaft", "material_id": "1", "quantity": "1", "blank_diameter": "85", "blank_length": "146",
        "feature_count": str(len(GOST_SHAFT["features"])), "add_face": "1", "add_parting": "1",
    }
    for i, f in enumerate(GOST_SHAFT["features"]):
        form[f"f{i}-include"] = "1"
        for key, value in f.items():
            form[f"f{i}-{key}"] = "" if value is None else str(value)
    client.post("/extractions/1/confirm", data=form)
    job = db.session.execute(db.select(Job)).scalar_one()
    taper = next(f for f in job.features if f.type == "taper")
    fillet = next(f for f in job.features if f.type == "fillet")
    assert (taper.start_diameter, taper.diameter, taper.length) == (60, 55, 30)
    assert fillet.radius == 10

    page = client.post(f"/jobs/{job.id}/calculate", follow_redirects=True).data.decode()
    assert page.count("⚠ manual operation") == 2


# --- no repeated API calls ------------------------------------------------------------------

from datetime import datetime, timedelta, timezone  # noqa: E402

from turnpilot import services  # noqa: E402


def test_same_file_is_not_sent_twice(app, client):
    fake = _use_model(app, THREADED_SHAFT)
    _upload(client, PNG, "shaft.png")
    resp = _upload(client, PNG, "shaft_copy.png")
    assert len(fake.messages.calls) == 1

    first, second = _extractions()
    assert second.cached_from_id == first.id and second.is_cached
    assert second.status == "extracted" and second.parsed == first.parsed
    assert second.original_filename == "shaft_copy.png"
    assert second.stored_filename == first.stored_filename  # the file is not stored twice
    assert resp.headers["Location"].endswith(f"/extractions/{second.id}/review")

    page = client.get(f"/extractions/{second.id}/review").data.decode()
    assert "badge-cached" in page and "no new API call was made" in page
    assert "Read again" in page
    assert "badge-cached" in client.get("/jobs/upload").data.decode()


def test_cache_after_confirm_allows_a_second_job(app, client):
    fake = _use_model(app, THREADED_SHAFT)
    _upload(client)
    client.post("/extractions/1/confirm", data=_review_form())
    _upload(client)  # same drawing, new order
    assert len(fake.messages.calls) == 1
    client.post("/extractions/2/confirm", data=_review_form(name="Second batch"))
    assert len(db.session.execute(db.select(Job)).scalars().all()) == 2


def test_cached_copy_points_to_the_original(app, client):
    _use_model(app, THREADED_SHAFT)
    for _ in range(3):
        _upload(client)
    first, second, third = _extractions()
    assert second.cached_from_id == first.id and third.cached_from_id == first.id


def test_other_model_is_not_cached(app, client):
    fake = _use_model(app, THREADED_SHAFT)
    _upload(client)
    app.config["ANTHROPIC_MODEL"] = "other-model"
    _upload(client)
    assert len(fake.messages.calls) == 2
    assert _extractions()[1].cached_from_id is None


def test_other_file_is_not_cached(app, client):
    fake = _use_model(app, THREADED_SHAFT)
    _upload(client, PNG)
    _upload(client, (FIXTURES / "01_stepped_shaft.png").read_bytes(), "other.png")
    assert len(fake.messages.calls) == 2


def test_failed_reading_is_not_cached(app, client):
    fake = _use_model(app, None, stop_reason="end_turn", text="Unreadable.")
    _upload(client)
    fake.messages.response = make_response(THREADED_SHAFT)
    _upload(client)
    assert len(fake.messages.calls) == 2
    assert [e.status for e in _extractions()] == ["failed", "extracted"]


def test_read_again_makes_a_new_call(app, client):
    fake = _use_model(app, THREADED_SHAFT)
    _upload(client)
    _upload(client)  # cached
    resp = client.post("/extractions/2/read-again")
    assert len(fake.messages.calls) == 2
    fresh = _extractions()[-1]
    assert fresh.id == 3 and fresh.cached_from_id is None and fresh.status == "extracted"
    assert resp.headers["Location"].endswith("/extractions/3/review")
    # the fresh result becomes the one reused next time
    _upload(client)
    assert _extractions()[-1].cached_from_id == 3


def test_upload_while_same_file_is_being_read_is_rejected(app, client):
    fake = _use_model(app, THREADED_SHAFT)
    in_flight = services.DrawingExtraction(
        original_filename="shaft.png", stored_filename="x.png", file_type="png", size_bytes=len(PNG),
        sha256=services.hashlib.sha256(PNG).hexdigest(), model="test-model", status="pending",
        prompt_version=services.drawing_reader.prompt_version(),
    )
    db.session.add(in_flight)
    db.session.commit()

    _upload(client)
    assert fake.messages.calls == []
    assert b"already being read" in client.get("/jobs/upload").data
    assert len(_extractions()) == 1


def test_stale_pending_reading_does_not_block(app, client):
    fake = _use_model(app, THREADED_SHAFT)
    stale = services.DrawingExtraction(
        original_filename="shaft.png", stored_filename="x.png", file_type="png", size_bytes=len(PNG),
        sha256=services.hashlib.sha256(PNG).hexdigest(), model="test-model", status="pending",
        prompt_version=services.drawing_reader.prompt_version(),
        created_at=datetime.now(timezone.utc) - timedelta(seconds=services.IN_FLIGHT_SECONDS + 60),
    )
    db.session.add(stale)
    db.session.commit()
    _upload(client)
    assert len(fake.messages.calls) == 1


def test_read_buttons_are_blocked_after_click(app, client):
    page = _squash(client.get("/jobs/upload").data.decode())
    assert 'id="read-button"' in page
    assert "button.disabled = true;" in page and 'button.textContent = "Reading…";' in page

    _use_model(app, THREADED_SHAFT)
    _upload(client)
    _upload(client)
    review = _squash(client.get("/extractions/2/review").data.decode())
    assert 'class="js-reading">Read again' in review
    assert "button.disabled = true;" in review


# --- prompt version in the cache key ----------------------------------------------------------

import copy  # noqa: E402

from turnpilot import drawing_reader  # noqa: E402


def test_extraction_records_prompt_version(app, client):
    _use_model(app, THREADED_SHAFT)
    _upload(client)
    _upload(client)
    first, cached = _extractions()
    assert first.prompt_version == drawing_reader.prompt_version()
    assert cached.prompt_version == first.prompt_version


def test_changed_prompt_is_not_served_from_cache(app, client, monkeypatch):
    fake = _use_model(app, THREADED_SHAFT)
    _upload(client)
    old_version = drawing_reader.prompt_version()

    monkeypatch.setattr(drawing_reader, "SYSTEM_PROMPT", drawing_reader.SYSTEM_PROMPT + "\n- A new rule.")
    assert drawing_reader.prompt_version() != old_version
    _upload(client)

    assert len(fake.messages.calls) == 2
    first, second = _extractions()
    assert second.cached_from_id is None and second.prompt_version != first.prompt_version
    assert fake.messages.calls[1]["system"].endswith("- A new rule.")

    _upload(client)  # same new prompt again: served from the new result
    assert len(fake.messages.calls) == 2
    assert _extractions()[-1].cached_from_id == second.id


def test_changed_schema_is_not_served_from_cache(app, client, monkeypatch):
    fake = _use_model(app, THREADED_SHAFT)
    _upload(client)
    tool = copy.deepcopy(drawing_reader.RECORD_PART_TOOL)
    tool["input_schema"]["properties"]["material"]["description"] += " Also check the notes."
    monkeypatch.setattr(drawing_reader, "RECORD_PART_TOOL", tool)
    _upload(client)
    assert len(fake.messages.calls) == 2
    assert _extractions()[1].cached_from_id is None


def test_prompt_version_is_stable():
    assert drawing_reader.prompt_version() == drawing_reader.prompt_version()
    assert len(drawing_reader.prompt_version()) == 16


# --- response cut off at max_tokens -------------------------------------------------------------

def test_too_complex_drawing_shows_clear_message(app, client):
    usage = {"input_tokens": 4273, "output_tokens": 64000, "output_tokens_details": {"thinking_tokens": 64000}}
    fake = _use_model(app, None, stop_reason="max_tokens", usage=usage)
    resp = _upload(client)
    assert resp.status_code == 302
    page = client.get("/jobs/upload").data.decode()
    assert "Drawing too complex, try again" in page
    assert "The drawing could not be read: Drawing too complex" not in page  # shown as is

    (extraction,) = _extractions()
    assert extraction.status == "failed"
    assert json.loads(extraction.raw_response)["usage"]["output_tokens_details"]["thinking_tokens"] == 64000

    # a failed reading is not cached: trying again makes a new call
    fake.messages.response = make_response(THREADED_SHAFT)
    _upload(client)
    assert len(fake.messages.calls) == 2 and _extractions()[-1].status == "extracted"


# --- geometry check on the review screen --------------------------------------------------------

def test_review_shows_geometry_warnings(app, client):
    shaft = {**GOST_SHAFT, "features": [dict(f) for f in GOST_SHAFT["features"]]}
    shaft["features"][5]["length"] = 20  # od_turn Ø48: groove width counted twice
    shaft["features"][6]["length"] = 25  # thread longer than the Ø48 section
    page = _review_page(app, client, shaft)
    assert "Geometry check" in page
    assert "section lengths sum to 144.1, overall length is 140.1" in page
    assert "thread Ø48 is 25 long, longer than its section (20)" in page


def test_review_without_geometry_problems(app, client):
    assert "Geometry check" not in _review_page(app, client, GOST_SHAFT)


def test_geometry_check_uses_edited_rows(app, client):
    shaft = {**GOST_SHAFT, "features": [dict(f) for f in GOST_SHAFT["features"]]}
    shaft["features"][5]["length"] = 20  # model mistake: sum 144.1
    shaft["features"][6]["length"] = 20
    _use_model(app, shaft)
    _upload(client)
    assert "Geometry check" in client.get("/extractions/1/review").data.decode()
    form = {"name": "", "material_id": "1", "quantity": "1", "blank_diameter": "85", "blank_length": "146",
            "feature_count": str(len(shaft["features"]))}
    for i, f in enumerate(shaft["features"]):
        form[f"f{i}-include"] = "1"
        for key, value in f.items():
            form[f"f{i}-{key}"] = "" if value is None else str(value)
    form["f5-length"] = "16"
    form["f6-length"] = "16"
    page = client.post("/extractions/1/confirm", data=form).data.decode()  # name missing: page shown again
    assert "Geometry check" not in page
