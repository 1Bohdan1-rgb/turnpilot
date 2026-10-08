"""Reading the machine passport with the model (a fake client: no paid call) and checking the values by code."""
import io

import pytest
from conftest import FakeClient, make_response

from passport_pdfs import make_passport
from turnpilot import drawing_reader, passport_reader
from turnpilot.machine_spec import MACHINE_FIELDS
from turnpilot.models import MachineDocument, db

SPEC = 3  # the technical data page of the synthetic passport
SCAN = 5


def answer(**fields):
    """The record_machine tool input: every field not found, except the ones given as (value, page, quote[, unit])."""
    out = {f.name: {"found": False, "value": "", "unit_as_written": "", "page": 0, "quote": ""} for f in MACHINE_FIELDS}
    for name, (value, page, quote, *unit) in fields.items():
        out[name] = {"found": True, "value": value, "unit_as_written": unit[0] if unit else "", "page": page,
                     "quote": quote}
    return out


GOOD = answer(
    max_rpm=("4000", SPEC, "Spindle speed range, rpm ............................... 30 - 4000"),
    min_rpm=("30", SPEC, "Spindle speed range, rpm ............................... 30 - 4000"),
    power_kw=("11", SPEC, "Main motor power S1 / S6, kW ........................... 11 / 15"),
    power_s6_kw=("15", SPEC, "Main motor power S1 / S6, kW ........................... 11 / 15"),
    max_diameter=("300", SPEC, "Max turning diameter over the carriage, mm ............ 300"),
    max_turning_length=("500", SPEC, "Max turning length, mm ................................ 500"),
    max_bar_diameter=("52", SPEC, "Spindle bore (bar capacity), mm ....................... 52"),
    turret_positions=("12", SPEC, "Turret, number of positions ............................ 12"),
    max_z_feed=("5000", SPEC, "Max feed Z, mm/min ..................................... 5000"),
    coolant=("yes", SPEC, "Coolant pump pressure, bar ............................. 2"),
    coolant_pressure_bar=("2", SPEC, "Coolant pump pressure, bar ............................. 2"),
    control=("Fanuc 0i-TF", SPEC, "CNC control ............................................ Fanuc 0i-TF"),
)


@pytest.fixture
def passport(tmp_path):
    return make_passport(tmp_path / "passport.pdf")


def _texts(path, pages):
    texts = passport_reader.page_texts(path)
    return {n: texts[n - 1] for n in pages}


def _values(tool_input, path, pages=(SPEC,)):
    return {v.field: v for v in passport_reader.check_reading(tool_input, _texts(path, pages))}


# --- the tool and the content sent -------------------------------------------------------------------

def test_tool_schema_is_strict_and_asks_for_page_and_quote():
    schema = passport_reader.RECORD_MACHINE_TOOL["input_schema"]
    assert passport_reader.RECORD_MACHINE_TOOL["strict"] and schema["additionalProperties"] is False
    assert set(schema["required"]) == {f.name for f in MACHINE_FIELDS}
    for prop in schema["properties"].values():
        assert prop["required"] == ["found", "value", "unit_as_written", "page", "quote"]
    assert passport_reader.prompt_version().startswith("passport:")
    assert drawing_reader.prompt_version("features") == "ae6963808bf03c12"  # the drawing prompt is untouched


def test_content_text_pages_as_text_scans_as_images(passport):
    content, estimate = passport_reader.build_content(passport, [SPEC, SCAN])
    assert content[0]["text"].startswith("=== Page 3 ===") and "Spindle speed range" in content[0]["text"]
    assert content[1]["text"] == "=== Page 5 (a scanned image) ===" and content[2]["type"] == "image"
    assert (estimate["calls"], estimate["text_pages"], estimate["image_pages"]) == (1, 1, 1)
    assert passport_reader.estimate(passport, [SPEC])["image_pages"] == 0  # the estimate renders no image


# --- checking by code ------------------------------------------------------------------------------

def test_values_stated_on_the_page_are_taken_and_checked(passport):
    values = _values(GOOD, passport)
    assert (values["max_rpm"].value, values["min_rpm"].value, values["power_kw"].value) == (4000, 30, 11.0)
    assert values["coolant"].value is True and values["control"].value == "Fanuc 0i-TF"
    for name in GOOD:
        if GOOD[name]["found"]:
            assert values[name].found and values[name].quote_checked and values[name].checks == [], name
    assert not values["drive_efficiency"].found and not values["max_thread_feed"].found  # not in the passport


def test_a_quote_not_on_the_page_gets_a_check(passport):
    values = _values(answer(max_rpm=("4000", SPEC, "Max spindle speed 4000 rpm")), passport)
    assert values["max_rpm"].found and not values["max_rpm"].quote_checked
    assert values["max_rpm"].checks == ["the quote is not on page 3"]


def test_a_value_not_in_its_quote_gets_a_check(passport):
    values = _values(answer(max_z_feed=("5", SPEC, "Max feed Z, mm/min ..................................... 5000",
                                        "m/min")), passport)
    assert values["max_z_feed"].checks == ["5 is not in the quote (written in m/min): converted or misread?"]


@pytest.mark.parametrize("item, reason", [
    (("4000", 2, "Spindle speed range, rpm 30 - 4000"), "no page among the picked ones or no quote: not taken"),
    (("4000", SPEC, ""), "no page among the picked ones or no quote: not taken"),
    (("fast", SPEC, "Spindle speed range, rpm ... 30 - 4000"), "value not readable"),
])
def test_without_a_page_quote_or_readable_value_it_is_not_taken(passport, item, reason):
    v = _values(answer(max_rpm=item), passport)["max_rpm"]
    assert not v.found and v.value is None and v.checks[0].startswith(reason)


def test_a_scanned_page_cannot_be_checked_by_code(passport):
    v = _values(answer(max_rpm=("4000", SCAN, "4000 rpm")), passport, pages=(SPEC, SCAN))["max_rpm"]
    assert v.found and v.checks == ["a scanned page: the code cannot check the quote"]


def test_reading_round_trip_as_json(passport):
    values = passport_reader.check_reading(GOOD, _texts(passport, [SPEC]))
    assert passport_reader.reading_from_json(passport_reader.reading_to_json(values)) == values


# --- through the app (fake client) --------------------------------------------------------------------

def _upload_and_pick(client, passport, pages=("3",)):
    client.post("/machine/passport", data={"passport": (io.BytesIO(passport.read_bytes()), "passport.pdf")},
                content_type="multipart/form-data")
    document = db.session.execute(db.select(MachineDocument)).scalar_one()
    client.post(f"/machine/passport/{document.id}", data={"page": list(pages)})
    return document


def test_the_reading_needs_the_operators_tick_and_shows_the_cost(app, client, passport):
    fake = FakeClient(make_response(GOOD, tool_name="record_machine"))
    app.config["ANTHROPIC_CLIENT"] = fake
    document = _upload_and_pick(client, passport)
    page = client.get(f"/machine/passport/{document.id}").get_data(as_text=True)
    assert "1 API call" in page and "as text" in page
    response = client.post(f"/machine/passport/{document.id}/read", data={}, follow_redirects=True)
    assert "Tick that you start a paid reading" in response.get_data(as_text=True)
    assert fake.messages.calls == []  # no call without the tick


def test_reading_sends_only_the_picked_pages_and_keeps_the_record(app, client, passport):
    fake = FakeClient(make_response(GOOD, tool_name="record_machine"))
    app.config["ANTHROPIC_CLIENT"] = fake
    document = _upload_and_pick(client, passport)
    client.post(f"/machine/passport/{document.id}/read", data={"paid": "1"})
    (call,) = fake.messages.calls
    sent = " ".join(block.get("text", "") for block in call["messages"][0]["content"])
    assert "=== Page 3 ===" in sent and "=== Page 1 ===" not in sent and "Safety" not in sent
    assert call["tools"][0]["name"] == "record_machine"
    db.session.expire_all()
    document = db.session.get(MachineDocument, document.id)
    assert (document.status, document.read_pages, document.model) == ("read", "3", "test-model")
    assert document.prompt_version == passport_reader.prompt_version()
    values = {v.field: v for v in passport_reader.reading_from_json(document.reading)}
    assert values["max_rpm"].value == 4000 and values["max_rpm"].quote_checked


def test_a_refusal_is_kept_as_failed(app, client, passport):
    app.config["ANTHROPIC_CLIENT"] = FakeClient(make_response(stop_reason="refusal"))
    document = _upload_and_pick(client, passport)
    response = client.post(f"/machine/passport/{document.id}/read", data={"paid": "1"}, follow_redirects=True)
    assert "The model declined to read the passport." in response.get_data(as_text=True)
    db.session.expire_all()
    assert db.session.get(MachineDocument, document.id).status == "failed"


def test_no_reading_without_picked_pages(app, client, passport):
    fake = FakeClient(make_response(GOOD, tool_name="record_machine"))
    app.config["ANTHROPIC_CLIENT"] = fake
    document = _upload_and_pick(client, passport, pages=())
    response = client.post(f"/machine/passport/{document.id}/read", data={"paid": "1"}, follow_redirects=True)
    assert "Pick the pages" in response.get_data(as_text=True) and fake.messages.calls == []
