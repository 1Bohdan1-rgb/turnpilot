"""drawing_reader with a mocked API client: no network, no cost."""

import pymupdf
import pytest
from conftest import FIXTURES, FakeClient, feature, make_response, part

from turnpilot import drawing_reader
from turnpilot.drawing_reader import ExtractionError, detect_file_type, extract_drawing, parse_response, prepare_image
from turnpilot.extraction_schema import RECORD_PART_TOOL, DrawingData

VALID = part(
    [
        feature("od_turn", 30, length=60, ra=1.6),
        feature("groove", 17, start_diameter=20, length=3, confidence=0.55),
        feature("thread", 20, length=25, tolerance="6g", pitch=1.5),
    ],
    overall_length=90,
    quantity=50,
)


def _png_image():
    return prepare_image((FIXTURES / "01_stepped_shaft.png").read_bytes(), "png")


# --- parsing the response -------------------------------------------------------

def test_parse_valid_response():
    data = parse_response(make_response(VALID))
    assert data.material == "Steel 45 (C45)"
    assert data.quantity == 50
    assert [f.type for f in data.features] == ["od_turn", "groove", "thread"]
    groove = data.features[1]
    assert (groove.diameter, groove.start_diameter, groove.length, groove.confidence) == (17, 20, 3, 0.55)
    assert data.features[2].pitch == 1.5


def test_missing_values_stay_null():
    data = parse_response(make_response(VALID))
    assert data.blank_diameter is None and data.blank_length is None
    od = data.features[0]
    assert od.tolerance is None and od.pitch is None and od.start_diameter is None


def test_empty_strings_become_null():
    raw = part([feature("od_turn", 30, length=60, tolerance="  ")], material="")
    data = parse_response(make_response(raw))
    assert data.material is None
    assert data.features[0].tolerance is None


def test_text_before_tool_call_is_ignored():
    data = parse_response(make_response(VALID, text="Reading the drawing..."))
    assert len(data.features) == 3


@pytest.mark.parametrize(
    "bad_feature, message",
    [
        (feature("keyway", 20), "type"),
        (feature("od_turn", -30, length=10), "diameter"),
        (feature("od_turn", 30, pitch=1.5), "pitch is only valid for threads"),
        (feature("groove", 20, start_diameter=18, length=3), "start_diameter must be larger"),
        (feature("od_turn", 30, confidence=1.7), "confidence"),
    ],
)
def test_invalid_values_are_rejected(bad_feature, message):
    with pytest.raises(ExtractionError) as err:
        parse_response(make_response(part([bad_feature])))
    assert message in str(err.value)
    assert err.value.raw["content"][0]["name"] == "record_part"  # raw response kept for the audit log


def test_unknown_field_is_rejected():
    raw = part([feature("od_turn", 30)])
    raw["surface_treatment"] = "anodize"
    with pytest.raises(ExtractionError):
        parse_response(make_response(raw))


def test_no_tool_call_is_an_error():
    with pytest.raises(ExtractionError, match="no record_part call"):
        parse_response(make_response(None, stop_reason="end_turn", text="I cannot read this."))


def test_refusal_is_an_error():
    with pytest.raises(ExtractionError, match="declined"):
        parse_response(make_response(None, stop_reason="refusal"))


def test_truncated_response_is_an_error():
    with pytest.raises(ExtractionError, match="max_tokens"):
        parse_response(make_response(VALID, stop_reason="max_tokens"))


# --- the request sent to the API -------------------------------------------------

def test_request_uses_strict_tool_and_image(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_MODEL", raising=False)
    client = FakeClient(make_response(VALID))
    result = extract_drawing(_png_image(), client=client)

    (call,) = client.messages.calls
    assert call["model"] == "claude-sonnet-5"  # default model
    assert call["tools"] == [RECORD_PART_TOOL]
    assert RECORD_PART_TOOL["strict"] is True
    assert call["tool_choice"] == {"type": "auto"}
    image_block, text_block = call["messages"][0]["content"]
    assert image_block["type"] == "image" and image_block["source"]["media_type"] == "image/png"
    assert text_block["type"] == "text"
    assert result.model == "claude-sonnet-5"
    assert result.raw["usage"]["input_tokens"] == 1500


def test_model_from_environment(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_MODEL", "claude-opus-5")
    client = FakeClient(make_response(VALID))
    assert extract_drawing(_png_image(), client=client).model == "claude-opus-5"
    assert client.messages.calls[0]["model"] == "claude-opus-5"


def test_schema_marks_every_field_required():
    schema = RECORD_PART_TOOL["input_schema"]
    assert set(schema["required"]) == set(schema["properties"])
    item = schema["properties"]["features"]["items"]
    assert set(item["required"]) == set(item["properties"])
    assert schema["additionalProperties"] is False and item["additionalProperties"] is False


# --- file handling -----------------------------------------------------------------

def test_detect_file_type():
    assert detect_file_type((FIXTURES / "01_stepped_shaft.png").read_bytes()) == "png"
    assert detect_file_type((FIXTURES / "01_stepped_shaft.photo.jpg").read_bytes()) == "jpeg"
    assert detect_file_type((FIXTURES / "01_stepped_shaft.pdf").read_bytes()) == "pdf"
    assert detect_file_type(b"hello, not a drawing") is None


def test_pdf_is_converted_to_png():
    image = prepare_image((FIXTURES / "02_threaded_shaft.pdf").read_bytes(), "pdf")
    assert image.media_type == "image/png"
    assert image.data.startswith(b"\x89PNG")
    assert max(image.width, image.height) == drawing_reader.MAX_LONG_EDGE_PX


def test_small_jpeg_is_sent_unchanged():
    data = (FIXTURES / "02_threaded_shaft.photo.jpg").read_bytes()
    image = prepare_image(data, "jpeg")
    assert image.data == data and image.media_type == "image/jpeg"


def test_large_image_is_downscaled():
    big = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 5000, 400), False)
    big.clear_with(255)
    image = prepare_image(big.tobytes("png"), "png")
    assert max(image.width, image.height) == drawing_reader.MAX_LONG_EDGE_PX
    assert any("downscaled" in n for n in image.notes)


def test_broken_pdf_is_an_error():
    with pytest.raises(ExtractionError, match="Cannot open"):
        prepare_image(b"%PDF-1.7 truncated garbage", "pdf")


def test_notes_become_warnings():
    image = _png_image()
    image.notes.append("The PDF has 2 pages; only page 1 was read.")
    result = extract_drawing(image, client=FakeClient(make_response(VALID)))
    assert result.data.warnings[0].startswith("The PDF has 2 pages")


# --- fixtures ------------------------------------------------------------------------

@pytest.mark.parametrize("path", sorted(FIXTURES.glob("*.expected.json")), ids=lambda p: p.name)
def test_expected_files_match_the_schema(path):
    data = DrawingData.model_validate_json(path.read_text(encoding="utf-8"))
    assert data.features
    name = path.name.removesuffix(".expected.json")
    if name.startswith("real_"):
        assert any((FIXTURES / f"{name}{s}").exists() for s in (".png", ".jpg", ".jpeg", ".pdf"))
    else:
        assert data.material
        for suffix in (".png", ".pdf", ".photo.jpg"):
            assert (FIXTURES / f"{name}{suffix}").exists()


# --- part type ----------------------------------------------------------------------------

def test_part_type_is_required_in_the_schema():
    schema = RECORD_PART_TOOL["input_schema"]
    assert "part_type" in schema["required"]
    assert schema["properties"]["part_type"]["enum"] == ["turned", "not_turned", "unclear"]


@pytest.mark.parametrize("value", ["turned", "not_turned", "unclear"])
def test_part_type_values(value):
    assert parse_response(make_response(part([], part_type=value))).part_type == value


def test_unknown_part_type_is_rejected():
    with pytest.raises(ExtractionError, match="part_type"):
        parse_response(make_response(part([], part_type="milled")))


def test_missing_part_type_is_rejected():
    raw = part([])
    del raw["part_type"]
    with pytest.raises(ExtractionError, match="part_type"):
        parse_response(make_response(raw))


# --- rules for real drawings: taper, fillet, general Ra, chain dimensions, thread relief ------

from turnpilot.drawing_reader import SYSTEM_PROMPT  # noqa: E402


def test_taper_and_fillet_are_parsed():
    raw = part([
        feature("taper", 55, start_diameter=60, length=30),
        feature("fillet", radius=10, confidence=0.7),
    ])
    taper, fillet = parse_response(make_response(raw)).features
    assert (taper.start_diameter, taper.diameter, taper.length) == (60, 55, 30)
    assert fillet.radius == 10 and fillet.diameter is None


def test_taper_start_may_be_smaller_than_end():
    raw = part([feature("taper", 60, start_diameter=55, length=30)])
    assert parse_response(make_response(raw)).features[0].start_diameter == 55


def test_radius_only_on_fillets():
    with pytest.raises(ExtractionError, match="radius is only valid for fillets"):
        parse_response(make_response(part([feature("od_turn", 30, radius=2)])))


def test_start_diameter_not_allowed_on_od_turn():
    with pytest.raises(ExtractionError, match="grooves and tapers"):
        parse_response(make_response(part([feature("od_turn", 30, start_diameter=40)])))


def test_general_ra_is_parsed():
    data = parse_response(make_response(part([feature("od_turn", 30, length=10)], general_ra=3.2)))
    assert data.general_ra == 3.2
    assert data.features[0].ra is None  # own marks only


def test_schema_has_new_fields():
    schema = RECORD_PART_TOOL["input_schema"]
    item = schema["properties"]["features"]["items"]
    assert {"taper", "fillet"} <= set(item["properties"]["type"]["enum"])
    assert "radius" in item["required"]
    assert "general_ra" in schema["required"]


@pytest.mark.parametrize(
    "phrase",
    [
        "top-right corner",  # general roughness
        "general_ra",
        "length derived from chain dimensions",
        "confidence 0.8",
        "thread relief",
        "minor diameter",
        "taper",
        "fillet",
    ],
)
def test_prompt_contains_drawing_rules(phrase):
    assert phrase in SYSTEM_PROMPT
