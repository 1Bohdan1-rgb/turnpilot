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


def test_truncated_response_says_too_complex_and_logs_usage(caplog):
    usage = {"input_tokens": 4273, "output_tokens": 64000, "output_tokens_details": {"thinking_tokens": 63100}}
    response = make_response(None, stop_reason="max_tokens", usage=usage)
    with caplog.at_level("WARNING", logger="turnpilot.drawing_reader"):
        with pytest.raises(ExtractionError) as err:
            parse_response(response)
    message = str(err.value)
    assert message.startswith("Drawing too complex, try again")
    assert "64000 output tokens (thinking: 63100)" in message
    assert err.value.raw["usage"]["output_tokens"] == 64000  # kept for the audit log
    (record,) = caplog.records
    assert "max_tokens" in record.getMessage() and '"thinking_tokens": 63100' in record.getMessage()


def test_request_streams_with_a_large_output_budget():
    client = FakeClient(make_response(VALID))
    extract_drawing(_png_image(), client=client)
    (call,) = client.messages.calls
    assert call["max_tokens"] == drawing_reader.MAX_OUTPUT_TOKENS >= 2 * 16000
    assert drawing_reader.MAX_OUTPUT_TOKENS <= 128000  # the model's output limit


def test_prompt_asks_for_short_warnings():
    assert "one sentence each" in drawing_reader.SYSTEM_PROMPT


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
    # rendered as large as both limits allow: an A4 sheet is bound by the visual token limit
    assert drawing_reader.within_limits(image.width, image.height)
    assert drawing_reader.visual_tokens(image.width, image.height) > 0.95 * drawing_reader.MAX_VISUAL_TOKENS


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
    assert result.data.warnings[:len(image.notes)] == image.notes  # image notes come first
    assert any(w.startswith("The PDF has 2 pages") for w in result.data.warnings)


# --- fixtures ------------------------------------------------------------------------

@pytest.mark.parametrize("path", sorted(FIXTURES.glob("*.expected.json")), ids=lambda p: p.name)
def test_expected_files_match_the_schema(path):
    data = DrawingData.model_validate_json(path.read_text(encoding="utf-8"))
    assert data.features
    name = path.name.removesuffix(".expected.json")
    if name.startswith("real_"):
        assert any((FIXTURES / f"{name}{s}").exists() for s in (".png", ".jpg", ".jpeg", ".pdf"))
    elif name.endswith("_lowres"):  # a downscaled PNG of a generated drawing, same expected answer
        assert (FIXTURES / f"{name}.png").exists()
        original = FIXTURES / f"{name.removesuffix('_lowres')}.expected.json"
        assert path.read_text(encoding="utf-8") == original.read_text(encoding="utf-8")
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
        "A taper never replaces the cylinder next to it",
        "separate od_turn with its own length and tolerance",
    ],
)
def test_prompt_contains_drawing_rules(phrase):
    assert phrase in SYSTEM_PROMPT


# --- thread class check (in code, not in the prompt) -------------------------------------------

@pytest.mark.parametrize("value", ["6g", "6h", "4h6h", "6e", "8g", "6H", "7H", "5H6H", "6G"])
def test_valid_thread_class_is_kept(value):
    data = parse_response(make_response(part([feature("thread", 48, length=16, pitch=1.5, tolerance=value)])))
    assert data.features[0].tolerance == value
    assert not any("thread class unclear" in w for w in data.warnings)


@pytest.mark.parametrize("value", ["69", "M48x1.5-6g", "h6", "6", "g6", "6g6H", "10g", "±0.1", "6 g"])
def test_unclear_thread_class_is_cleared_with_warning(value):
    data = parse_response(make_response(part([feature("thread", 48, length=16, pitch=1.5, tolerance=value)])))
    assert data.features[0].tolerance is None
    assert f"thread class unclear: '{value}'" in data.warnings


def test_thread_class_check_only_touches_threads():
    data = parse_response(make_response(part([feature("od_turn", 48, length=16, tolerance="h6")])))
    assert data.features[0].tolerance == "h6" and data.warnings == []


# --- visual token limit ---------------------------------------------------------------------------

def test_visual_tokens():
    assert drawing_reader.visual_tokens(28, 28) == 1
    assert drawing_reader.visual_tokens(29, 28) == 2
    assert drawing_reader.visual_tokens(1169, 858) == 42 * 31
    assert drawing_reader.visual_tokens(2572, 1818) == 92 * 65  # 5980: over the limit


def _png(width, height):
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, width, height), False)
    pix.clear_with(255)
    return pix.tobytes("png")


def test_a4_sheet_is_shrunk_to_the_token_limit():
    image = prepare_image(_png(2572, 1818), "png")  # long edge fine, 5980 tokens: too many
    assert drawing_reader.visual_tokens(image.width, image.height) <= drawing_reader.MAX_VISUAL_TOKENS
    assert drawing_reader.visual_tokens(image.width, image.height) > 0.95 * drawing_reader.MAX_VISUAL_TOKENS
    assert abs(image.width / image.height - 2572 / 1818) < 0.01  # aspect ratio kept
    assert "4784 visual tokens" in image.notes[0]


def test_square_image_is_bound_by_tokens_not_by_the_long_edge():
    image = prepare_image(_png(3000, 3000), "png")
    assert image.width == image.height and image.width < drawing_reader.MAX_LONG_EDGE_PX
    assert drawing_reader.within_limits(image.width, image.height)


def test_long_narrow_image_is_bound_by_the_long_edge():
    image = prepare_image(_png(5000, 400), "png")
    assert max(image.width, image.height) == drawing_reader.MAX_LONG_EDGE_PX


def test_image_within_both_limits_is_unchanged():
    data = _png(2000, 1400)  # 72 x 50 = 3600 tokens
    image = prepare_image(data, "png")
    assert image.data == data and image.notes == []


@pytest.mark.parametrize("width, height", [(2572, 1818), (1818, 2572), (4000, 3000), (2576, 2576), (10000, 200)])
def test_fit_size_is_within_limits_and_never_enlarges(width, height):
    w, h = drawing_reader.fit_size(width, height)
    assert drawing_reader.within_limits(w, h)
    assert w <= width and h <= height
