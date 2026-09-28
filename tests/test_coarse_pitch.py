"""A metric thread without a pitch on the drawing gets the ISO 261 coarse pitch, marked for checking."""

import io

import pytest
from conftest import FIXTURES, FakeClient, feature, make_response, part

from turnpilot.drawing_reader import parse_response
from turnpilot.extraction_schema import DrawingData, coarse_pitch

M14 = feature("thread", 14, length=28, tolerance="7H")  # "M14-7H": no pitch written


@pytest.mark.parametrize("diameter, pitch", [(14, 2), (20, 2.5), (8, 1.25), (1.6, 0.35), (48, 5), (13, None), (None, None)])
def test_coarse_pitch_table(diameter, pitch):
    assert coarse_pitch(diameter) == pitch


def test_missing_pitch_is_filled_and_marked():
    data = parse_response(make_response(part([M14])))
    thread = data.features[0]
    assert thread.pitch == 2 and thread.pitch_assumed
    assert "thread M14: no pitch on the drawing, coarse pitch 2 assumed (ISO 261): check" in data.warnings


def test_written_pitch_is_kept():
    data = parse_response(make_response(part([feature("thread", 20, length=25, pitch=1.5, tolerance="6g")])))
    assert data.features[0].pitch == 1.5 and not data.features[0].pitch_assumed
    assert not any("coarse pitch" in w for w in data.warnings)


def test_non_standard_diameter_stays_without_pitch():
    data = parse_response(make_response(part([feature("thread", 13, length=10)])))
    assert data.features[0].pitch is None and not data.features[0].pitch_assumed


def test_reloading_stored_data_does_not_repeat_the_warning():
    data = parse_response(make_response(part([M14])))
    again = DrawingData.model_validate_json(data.model_dump_json())
    assert again.features[0].pitch_assumed
    assert sum("coarse pitch" in w for w in again.warnings) == 1


def test_dimensions_first_fills_the_pitch_too():
    import copy

    from test_dimensions_first import REAL_SHAFT

    from turnpilot.dimensions_first import DimensionsData, to_drawing_data

    raw = copy.deepcopy(REAL_SHAFT)
    raw["overlays"][0]["pitch"] = None  # thread M48 without a pitch
    thread = next(f for f in to_drawing_data(DimensionsData.model_validate(raw)).features if f.type == "thread")
    assert thread.pitch == 5 and thread.pitch_assumed


def test_review_marks_the_assumed_pitch(app, client):
    app.config["ANTHROPIC_CLIENT"] = FakeClient(make_response(part([M14])))
    png = (FIXTURES / "01_stepped_shaft.png").read_bytes()
    client.post("/jobs/upload", data={"drawing": (io.BytesIO(png), "vtulka.png")}, content_type="multipart/form-data")
    page = client.get("/extractions/1/review").data.decode()
    assert 'name="f0-pitch" type="number" step="any" min="0" value="2.0"' in page
    assert "the ISO 261 coarse pitch was filled in" in page
