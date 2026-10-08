"""Machine data on the Machine page: every field with its source; nothing guessed."""
import pytest

from turnpilot import services
from turnpilot.machine_spec import FIELDS_BY_NAME, MACHINE_FIELDS, SOURCE_OPERATOR, format_value, parse_value
from turnpilot.models import Tool, db

PROFILE = dict(action="profile", name="Lathe 1")


def _machine():
    db.session.expire_all()
    return services.get_machine()


@pytest.mark.parametrize("name, raw, value", [
    ("max_rpm", "4000", 4000), ("power_kw", "11,5", 11.5), ("coolant", "yes", True), ("coolant", "no", False),
    ("coolant", "", None), ("control", " Fanuc 0i-TF ", "Fanuc 0i-TF"), ("min_rpm", None, None),
])
def test_parse_value(name, raw, value):
    assert parse_value(FIELDS_BY_NAME[name], raw) == value


@pytest.mark.parametrize("name, raw", [("max_rpm", "40.5"), ("power_kw", "-1"), ("coolant", "maybe"), ("min_rpm", "x")])
def test_parse_value_rejects(name, raw):
    with pytest.raises(ValueError):
        parse_value(FIELDS_BY_NAME[name], raw)


def test_format_value():
    assert format_value(FIELDS_BY_NAME["power_kw"], 11.0) == "11"
    assert format_value(FIELDS_BY_NAME["live_tooling"], False) == "no"
    assert format_value(FIELDS_BY_NAME["c_axis"], None) == ""


def test_seed_machine_new_fields_are_not_guessed(app):
    machine = _machine()
    assert machine.turret_positions == 12 == len(machine.slots)
    for name in ("min_rpm", "power_s6_kw", "max_turning_length", "max_bar_diameter", "max_z_feed", "coolant",
                 "coolant_pressure_bar", "live_tooling", "c_axis", "control"):
        assert getattr(machine, name) is None, name
    assert machine.sources == []


def test_machine_page_shows_every_field_with_its_source(client):
    page = client.get("/machine").get_data(as_text=True)
    for field in MACHINE_FIELDS:
        assert f'name="{field.name}"' in page, field.name
    assert "seed ·" in page and "entered by the operator" not in page  # seed values have no recorded source


def test_a_changed_value_is_recorded_as_the_operators(client):
    client.post("/machine", data={**PROFILE, "min_rpm": "30", "coolant": "yes", "control": "Fanuc 0i-TF",
                                  "max_rpm": "4000"})
    machine = _machine()
    assert (machine.min_rpm, machine.coolant, machine.control) == (30, True, "Fanuc 0i-TF")
    sources = {s.field: s for s in machine.sources}
    assert set(sources) == {"min_rpm", "coolant", "control"}  # max_rpm did not change: no source recorded
    assert sources["coolant"].source == SOURCE_OPERATOR and sources["coolant"].value == "yes"
    assert "entered by the operator" in client.get("/machine").get_data(as_text=True)


def test_a_field_the_form_does_not_send_is_kept(client):
    client.post("/machine", data={**PROFILE, "min_rpm": "30"})
    client.post("/machine", data={**PROFILE, "max_turning_length": "500"})
    assert (_machine().min_rpm, _machine().max_turning_length) == (30, 500)


def test_clearing_a_required_field_is_refused(client):
    response = client.post("/machine", data={**PROFILE, "max_rpm": ""}, follow_redirects=True)
    assert "Max spindle speed is required" in response.get_data(as_text=True)
    assert _machine().max_rpm == 4000


def test_min_above_max_is_refused(client):
    response = client.post("/machine", data={**PROFILE, "min_rpm": "5000"}, follow_redirects=True)
    assert "Min spindle speed is above the max" in response.get_data(as_text=True)
    assert _machine().min_rpm is None


def test_turret_positions_grow_and_shrink(client):
    client.post("/machine", data={**PROFILE, "turret_positions": "16"})
    machine = _machine()
    assert machine.turret_positions == 16 and [s.position for s in machine.slots] == list(range(1, 17))
    client.post("/machine", data={**PROFILE, "turret_positions": "14"})
    assert [s.position for s in _machine().slots] == list(range(1, 15))


def test_turret_cannot_shrink_over_a_tool(client):
    response = client.post("/machine", data={**PROFILE, "turret_positions": "8"}, follow_redirects=True)
    text = response.get_data(as_text=True)
    assert "T9, T10, T11, T12 hold tools: take them off before reducing the turret to 8" in text
    machine = _machine()
    assert machine.turret_positions == 12 and len(machine.slots) == 12
    assert db.session.execute(db.select(Tool).filter_by(name="Drill 860-GM Ø10")).scalar_one() is not None
