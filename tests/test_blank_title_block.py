"""A blank named in the title block ("Круг 29", "29-В1-ГОСТ 2590-2006") is used for the blank diameter."""

import io

import pytest
from conftest import FIXTURES, FakeClient, feature, make_response, part

from turnpilot.drawing_reader import SYSTEM_PROMPT
from turnpilot.services import blank_from_title_block

REAL_02_MATERIAL = "Круг Р6М5Ф3 -II-а ГОСТ 19265-73; 29-В1-ГОСТ 2590-2006"  # as the model returned it


@pytest.mark.parametrize(
    "text, diameter",
    [
        (REAL_02_MATERIAL, 29),  # GOST 2590 size designation after the grade
        ("Круг 29 ГОСТ 2590-2006", 29),
        ("Круг Ø32", 32),
        ("круг 24,5", 24.5),
        ("bar Ø55 x 50", 55),
        ("round bar 40", 40),
        ("Круг Р6М5Ф3 ГОСТ 19265-73", None),  # no size: the 6 of Р6М5Ф3 must not be taken
        ("Круг 6М5", None),
        ("Steel 45 (C45)", None),
        (None, None),
    ],
)
def test_blank_from_title_block(text, diameter):
    assert blank_from_title_block(text) == diameter


def test_prompts_mention_the_title_block_blank():
    from turnpilot.dimensions_first import SYSTEM_PROMPT as DIMENSIONS_PROMPT

    for prompt in (SYSTEM_PROMPT, DIMENSIONS_PROMPT):
        assert "29-В1-ГОСТ 2590-2006" in prompt.replace("\n", " ")


def _review(app, client, tool_input):
    app.config["ANTHROPIC_CLIENT"] = FakeClient(make_response(tool_input))
    png = (FIXTURES / "01_stepped_shaft.png").read_bytes()
    client.post("/jobs/upload", data={"drawing": (io.BytesIO(png), "vtulka.png")}, content_type="multipart/form-data")
    return client.get("/extractions/1/review").data.decode()


BUSHING = [feature("od_turn", 20, length=14, tolerance="h14"), feature("od_turn", 25, length=30, tolerance="h14")]


def test_review_takes_the_blank_from_the_title_block(app, client):
    page = _review(app, client, part(BUSHING, material=REAL_02_MATERIAL, overall_length=44))
    assert 'name="blank_diameter" type="number" step="any" min="0" value="29.0"' in page
    assert ">title block</span>" in page
    assert page.count("badge-suggested") == 2  # "title block" on Ø, "suggested" on the length only


def test_blank_from_the_drawing_wins(app, client):
    page = _review(app, client, part(BUSHING, material=REAL_02_MATERIAL, blank_diameter=30, overall_length=44))
    assert 'name="blank_diameter" type="number" step="any" min="0" value="30.0"' in page
    assert ">title block</span>" not in page


def test_without_title_block_blank_the_suggestion_stays(app, client):
    page = _review(app, client, part(BUSHING, material="Steel 45 (C45)", overall_length=44))
    assert 'name="blank_diameter" type="number" step="any" min="0" value="28.0"' in page  # Ø25 + 2 -> bar 28
    assert ">title block</span>" not in page
