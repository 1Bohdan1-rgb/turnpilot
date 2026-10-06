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
