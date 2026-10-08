"""The operator confirms the passport's values: only ticked ones reach the machine, each with its page and quote."""
import html
import io

import pytest
from conftest import FakeClient, make_response

from passport_pdfs import make_passport
from test_passport_reading import GOOD, answer
from turnpilot import services
from turnpilot.machine_spec import SOURCE_NOT_IN_PASSPORT, SOURCE_OPERATOR, SOURCE_PASSPORT
from turnpilot.models import MachineDocument, db


@pytest.fixture
def read_document(app, client, tmp_path):
    def read(tool_input=GOOD):
        app.config["ANTHROPIC_CLIENT"] = FakeClient(make_response(tool_input, tool_name="record_machine"))
        data = make_passport(tmp_path / "passport.pdf").read_bytes()
        client.post("/machine/passport", data={"passport": (io.BytesIO(data), "passport.pdf")},
                    content_type="multipart/form-data")
        document = db.session.execute(db.select(MachineDocument).order_by(MachineDocument.id.desc())).scalars().first()
        client.post(f"/machine/passport/{document.id}", data={"page": ["3", "5"]})
        response = client.post(f"/machine/passport/{document.id}/read", data={"paid": "1"})
        assert response.location.endswith(f"/machine/passport/{document.id}/review")
        return document
    return read


def _machine():
    db.session.expire_all()
    return services.get_machine()


def test_review_shows_values_pages_quotes_and_not_in_passport(client, read_document):
    document = read_document()
    page = html.unescape(client.get(f"/machine/passport/{document.id}/review").get_data(as_text=True))
    assert 'name="value-min_rpm" value="30"' in page and "Spindle speed range" in page and "✅" in page
    assert page.count("not in passport") >= 4  # efficiency, threading limit, driven tools, C axis
    assert 'name="confirm-drive_efficiency"' not in page  # nothing to confirm when not in the passport
    assert "also use it as the" in page and "threading limit" in page


def test_only_ticked_values_reach_the_machine_with_page_and_quote(client, read_document):
    document = read_document()
    client.post(f"/machine/passport/{document.id}/confirm", data={
        "confirm-min_rpm": "1", "value-min_rpm": "30",
        "confirm-coolant": "1", "value-coolant": "yes",
        "value-max_turning_length": "500",  # not ticked
    })
    machine = _machine()
    assert (machine.min_rpm, machine.coolant, machine.max_turning_length) == (30, True, None)
    source = machine.source_of("min_rpm")
    assert (source.source, source.page, source.document_id) == (SOURCE_PASSPORT, 3, document.id)
    assert "Spindle speed range" in source.quote
    assert machine.source_of("max_turning_length") is None
    assert db.session.get(MachineDocument, document.id).status == "confirmed"
    assert "passport p. 3" in client.get("/machine").get_data(as_text=True)


def test_a_changed_value_is_the_operators(client, read_document):
    document = read_document()
    client.post(f"/machine/passport/{document.id}/confirm", data={"confirm-max_rpm": "1", "value-max_rpm": "3500"})
    machine = _machine()
    assert machine.max_rpm == 3500 and machine.source_of("max_rpm").source == SOURCE_OPERATOR


def test_not_in_passport_is_recorded_and_the_value_kept(client, read_document):
    client.post("/machine", data={"action": "profile", "name": "Lathe 1", "drive_efficiency": "0.8"})
    document = read_document()
    client.post(f"/machine/passport/{document.id}/confirm", data={})
    machine = _machine()
    assert machine.source_of("live_tooling").source == SOURCE_NOT_IN_PASSPORT and machine.live_tooling is None
    # the operator's own value is not overwritten by "not in passport"
    assert machine.drive_efficiency == 0.8 and machine.source_of("drive_efficiency").source == SOURCE_OPERATOR


def test_the_z_feed_as_threading_limit_only_on_the_operators_tick(client, read_document):
    document = read_document()
    client.post(f"/machine/passport/{document.id}/confirm", data={"confirm-max_z_feed": "1", "value-max_z_feed": "5000"})
    machine = _machine()
    assert machine.max_z_feed == 5000 and machine.max_thread_feed is None
    client.post(f"/machine/passport/{document.id}/confirm", data={
        "confirm-max_z_feed": "1", "value-max_z_feed": "5000", "thread-limit": "1"})
    machine = _machine()
    assert machine.max_thread_feed == 5000
    source = machine.source_of("max_thread_feed")
    assert source.source == SOURCE_OPERATOR and source.quote.startswith("max Z feed from the passport")


def test_a_conflict_writes_nothing(client, read_document):
    document = read_document(answer(turret_positions=("8", 3, "Turret, number of positions ............................ 12"),
                                    min_rpm=("30", 3, "Spindle speed range, rpm ............................... 30 - 4000")))
    response = client.post(f"/machine/passport/{document.id}/confirm", data={
        "confirm-turret_positions": "1", "value-turret_positions": "8",
        "confirm-min_rpm": "1", "value-min_rpm": "30"}, follow_redirects=True)
    assert "hold tools: take them off before reducing the turret to 8" in response.get_data(as_text=True)
    machine = _machine()
    assert machine.turret_positions == 12 and machine.min_rpm is None and machine.sources == []


def test_confirmed_values_reach_the_planner(client, read_document):
    from turnpilot.models import Job, Material, Operation
    from turnpilot.planner import NO_COOLANT_WARNING
    document = read_document(answer(coolant=("no", 3, "Coolant pump pressure, bar ............................. 2")))
    client.post(f"/machine/passport/{document.id}/confirm", data={"confirm-coolant": "1", "value-coolant": "no"})
    material = db.session.execute(db.select(Material).filter_by(iso_group="P")).scalar_one()
    client.post("/jobs", data=dict(name="Shaft", material_id=material.id, quantity=1, blank_diameter=60,
                                   blank_length=120))
    job = db.session.execute(db.select(Job)).scalar_one()
    client.post(f"/jobs/{job.id}/features", data=dict(type="od_turn", diameter=50, length=20))
    client.post(f"/jobs/{job.id}/calculate")
    rough = db.session.execute(db.select(Operation).filter_by(tool_type="turning_rough")).scalar_one()
    assert NO_COOLANT_WARNING in rough.warning
