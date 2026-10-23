"""The stick-out hint from the drawing, from the jaws to the raw end face: the part's length + the stock beyond Z0 +
the parting insert's width + max(the machine's margin, the safety distance to the jaws); all the operator's values,
no default; taken only by the operator, with the name."""
import json

from gcode_jobs import make_job, ready_machine

from turnpilot import services
from turnpilot.models import db


def _length(job):
    return sum(f.length or 0 for f in job.active_features if f.type in services.PROFILE_TYPES)


def _width(job):
    return next(op.tool.insert_width for op in job.current_operations if op.tool_type == "parting")


def test_no_hint_without_the_margin(client):
    machine = ready_machine()
    machine.stickout_margin_mm = None
    db.session.commit()
    job = make_job()
    hint = services.stickout_hint(job, machine)
    assert hint["missing"] == ["the stick-out margin beyond the parting cut (Machine page, programming values)"]
    page = client.get(f"/jobs/{job.id}").get_data(as_text=True)
    assert "No hint: not known" in page and "Take " not in page.split('class="card stickout-hint"', 1)[1][:600]


def test_no_hint_without_the_stock_beyond_z0_or_the_safety_distance(client):
    machine = ready_machine()
    machine.stickout_margin_mm, machine.chuck_safety_mm = 2.0, None
    job = make_job()
    job.face_stock_mm = None
    db.session.commit()
    assert services.stickout_hint(job, machine)["missing"] == [
        "the stock beyond Z0 to face off (the job's G-code set-up)",
        "the safety distance to the chuck jaws (Machine page, programming values)"]


def test_the_safety_distance_when_the_margin_is_less(client):
    """НД 012's case: 114 + 2 + 3 + max(2, 5) = 124, and the page says the safety distance is taken."""
    machine = ready_machine()
    machine.stickout_margin_mm, machine.chuck_safety_mm = 2.0, 5.0
    job = make_job(face_stock=2.0)
    db.session.commit()
    hint = services.stickout_hint(job, machine)
    assert hint["clearance"] == 5.0 and hint["from_safety"]
    assert hint["value"] == round(_length(job) + 2.0 + _width(job) + 5.0, 3)
    page = client.get(f"/jobs/{job.id}").get_data(as_text=True)
    assert "The margin 2 mm is less than the safety distance to the jaws 5 mm: 5 mm is taken" in page
    machine.stickout_margin_mm = 8.0
    db.session.commit()
    hint = services.stickout_hint(job, machine)
    assert hint["clearance"] == 8.0 and not hint["from_safety"]


def test_the_hint_and_taking_it_with_the_name(client):
    machine = ready_machine()
    machine.stickout_margin_mm = 6.0
    db.session.commit()
    job = make_job(stickout=90)
    hint = services.stickout_hint(job, machine)
    width, stock, safety = _width(job), job.face_stock_mm, machine.chuck_safety_mm
    assert hint["value"] == round(_length(job) + stock + width + max(6.0, safety), 3)
    assert job.stickout_mm == 90  # shown only: nothing filled in
    page = client.get(f"/jobs/{job.id}").get_data(as_text=True)
    assert f"= {hint['value']:g} mm" in page
    response = client.post(f"/jobs/{job.id}/stickout-hint", data={"value": hint["value"]}, follow_redirects=True)
    assert "The operator&#39;s name is required" in response.get_data(as_text=True) and job.stickout_mm == 90
    client.post(f"/jobs/{job.id}/stickout-hint", data={"value": hint["value"] + 1, "decided_by": "Bohdan"})
    assert job.stickout_mm == 90  # a hint that changed since: refused
    client.post(f"/jobs/{job.id}/stickout-hint", data={"value": hint["value"], "decided_by": "Bohdan"})
    assert job.stickout_mm == hint["value"] and "Bohdan" in job.stickout_note
    assert job.stickout_note.startswith(f"{hint['length']:g} + {stock:g} + {width:g} + {hint['clearance']:g}")
    # typed by hand afterwards: the note goes
    client.post(f"/jobs/{job.id}/gcode-setup", data={"free_end": job.free_end, "stickout_mm": "80",
                                                       "face_stock_mm": job.face_stock_mm})
    assert job.stickout_mm == 80 and job.stickout_note is None


def test_the_margin_is_not_a_value_of_the_program(client):
    """It does not go into a program's settings: programs made before it was entered stay up to date."""
    machine = ready_machine()
    job = make_job()
    _, record = services.generate_gcode(job, machine)
    assert "stickout_margin_mm" not in {row["name"] for row in json.loads(record.settings)}
    machine.stickout_margin_mm = 5.0
    db.session.commit()
    assert services.gcode_stale(record, machine) == []
