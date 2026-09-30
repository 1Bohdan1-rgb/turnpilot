"""The model's numbers checked against the numbers written on the drawing (turnpilot/number_check.py)."""
from turnpilot.extraction_schema import DrawingData
from turnpilot.number_check import check_numbers, length_formulas
from turnpilot.pdf_text import DrawingNumber


def _drawing(*items):
    """(kind, value[, text]) -> DrawingNumber list."""
    return [DrawingNumber(item[0], item[1], item[2] if len(item) > 2 else str(item[1]), 0, 0) for item in items]


# a bushing like real_02: only 14, 16 and 44 are written as lengths; 30 and 28 are computed
BUSHING = _drawing(("linear", 14), ("linear", 16), ("linear", 44), ("diameter", 20), ("diameter", 25),
                   ("diameter", 16), ("thread", 14, "M14-7H"), ("chamfer", 2), ("roughness", 20),
                   ("roughness", 40))


def _bushing(l20, l25, bore, thread):
    return DrawingData(part_type="turned", overall_length=44, general_ra=40, general_ra_param="Rz", features=[
        {"type": "od_turn", "diameter": 20, "length": l20},
        {"type": "od_turn", "diameter": 25, "length": l25},
        {"type": "bore", "diameter": 16, "length": bore},
        {"type": "thread", "diameter": 14, "length": thread, "tolerance": "7H", "ra": 20, "ra_param": "Rz"},
        {"type": "chamfer", "diameter": 14, "length": 2, "location": "internal", "face": "right"},
    ])


def _check(data, drawing):
    return check_numbers(data.features, data.overall_length, data.general_ra, drawing)


def test_right_reading_passes_and_computed_lengths_are_marked():
    result = _check(_bushing(14, 30, 16, 28), BUSHING)
    assert result.warnings == []
    assert result.computed == {1: "44 − 14", 3: "44 − 16"}
    assert result.computed[1] != "14 + 16"  # differences first: 30 is also 14 + 16


def test_mirrored_reading_is_not_caught():
    # the known limitation: the same numbers on the other sections pass (that is stage 2)
    result = _check(_bushing(16, 28, 14, 30), BUSHING)
    assert result.warnings == []


def test_number_not_on_the_drawing_is_reported():
    result = _check(_bushing(14, 30, 18, 28), BUSHING)  # 18: neither written nor 44 ± 14/16
    assert "bore Ø16: length 18 is not on the drawing: check" in result.warnings


def test_unused_number_is_reported():
    data = _bushing(14, 30, 16, 28)
    data.features = [f for f in data.features if f.type != "chamfer"]
    assert _check(data, BUSHING).warnings == ["chamfer 2 on the drawing is not used: check"]


def test_coarse_pitch_of_a_thread_without_pitch_is_on_the_drawing():
    data = _bushing(14, 30, 16, 28)  # the validator fills pitch 2 (ISO coarse, marked assumed)
    assert data.features[3].pitch == 2 and data.features[3].pitch_assumed
    data.features[3].pitch_assumed = False  # as in a hand-written expected answer
    assert _check(data, BUSHING).warnings == []


def test_computed_length_uses_its_operands_even_if_the_value_is_written_elsewhere():
    # like real_03: the recess L8 = 11.5 − 3.5 while 8 is also the thread length on the drawing
    drawing = _drawing(("linear", 11.5), ("linear", 3.5), ("linear", 8), ("linear", 15))
    data = DrawingData(part_type="turned", overall_length=15, features=[
        {"type": "od_turn", "diameter": 10, "length": 3.5},
        {"type": "groove", "diameter": 8, "start_diameter": 10, "length": 8, "length_derived": True},
        {"type": "od_turn", "diameter": 12, "length": 8},
    ])
    drawing += _drawing(("diameter", 10), ("diameter", 8), ("diameter", 12))
    assert _check(data, drawing).warnings == []
    data.features[1].length_derived = False  # not marked: 11.5 looks unused
    assert _check(data, drawing).warnings == ["length 11.5 on the drawing is not used: check"]


def test_no_drawing_text_no_check():
    result = _check(_bushing(14, 30, 16, 28), None)
    assert result.warnings == [] and result.computed == {}


def test_length_formulas_count_the_values_the_rule_lets_through():
    formulas = length_formulas([14, 16, 44])
    assert sorted(formulas) == [2, 28, 30, 58, 60]  # 30 twice: 44 − 14 and 14 + 16
    assert formulas[30] == ["44 − 14", "14 + 16"]


# --- on the review screen ------------------------------------------------------------------------------

import io  # noqa: E402

from conftest import FIXTURES, FakeClient, feature, make_response, part  # noqa: E402


def _review(app, client, filename, features):
    app.config["ANTHROPIC_CLIENT"] = FakeClient(make_response(part(features, overall_length=90)))
    data = (FIXTURES / filename).read_bytes()
    client.post("/jobs/upload", data={"drawing": (io.BytesIO(data), filename)}, content_type="multipart/form-data")
    return client.get("/extractions/1/review").data.decode()


THREADED_SHAFT = [  # 02_threaded_shaft as drawn: 60, 30, 90, groove 3 x Ø17, M20x1.5 L25, 1x45°, Ra 1.6
    feature("od_turn", 30, length=60, ra=1.6), feature("od_turn", 20, length=27),
    feature("groove", 17, start_diameter=20, length=3), feature("thread", 20, length=25, pitch=1.5),
    dict(feature("chamfer", 20, length=1), location="external", face="right"),
]


def test_review_of_a_cad_pdf_marks_computed_lengths(app, client):
    page = _review(app, client, "02_threaded_shaft.pdf", THREADED_SHAFT)
    assert "computed as 30 − 3 from its dimensions" in page  # Ø20 L27: 30 minus the groove
    assert "is not on the drawing" not in page and "on the drawing is not used" not in page


def test_review_of_a_cad_pdf_reports_numbers_that_do_not_match(app, client):
    wrong = [dict(f) for f in THREADED_SHAFT]
    wrong[0]["length"] = 66  # not on the drawing, nor a difference / sum of two lengths on it
    page = _review(app, client, "02_threaded_shaft.pdf", wrong)
    assert "od_turn Ø30: length 66 is not on the drawing: check" in page
    assert "length 60 on the drawing is not used: check" in page


def test_a_coincidence_can_hide_a_wrong_number(app, client):
    # the limitation of the difference / sum rule: a wrong 65 is 90 − 25, so it passes as computed
    wrong = [dict(f) for f in THREADED_SHAFT]
    wrong[0]["length"] = 65
    page = _review(app, client, "02_threaded_shaft.pdf", wrong)
    assert "computed as 90 − 25" in page and "is not on the drawing" not in page
    assert "length 60 on the drawing is not used: check" in page  # the unused 60 still shows it


def test_review_of_an_image_has_no_number_check(app, client):
    wrong = [dict(f) for f in THREADED_SHAFT]
    wrong[0]["length"] = 66
    page = _review(app, client, "02_threaded_shaft.png", wrong)
    assert "is not on the drawing" not in page and "computed as" not in page
