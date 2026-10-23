"""The stick-out hint from the drawing: the part's length + the parting insert's width + the machine's margin (the
operator's programming value, no default); taken only by the operator, with the name."""
import json

from gcode_jobs import make_job, ready_machine

from turnpilot import services
from turnpilot.models import db


def _length(job):
    return sum(f.length or 0 for f in job.active_features if f.type in services.PROFILE_TYPES)


def test_no_hint_without_the_margin(client):
    machine = ready_machine()
    machine.stickout_margin_mm = None
    db.session.commit()
    job = make_job()
    hint = services.stickout_hint(job, machine)
    assert hint["missing"] == ["the stick-out margin beyond the parting cut (Machine page, programming values)"]
    page = client.get(f"/jobs/{job.id}").get_data(as_text=True)
    assert "No hint: not known" in page and "Take " not in page.split('class="card stickout-hint"', 1)[1][:600]


def test_the_hint_and_taking_it_with_the_name(client):
    machine = ready_machine()
    machine.stickout_margin_mm = 5.0
    db.session.commit()
    job = make_job(stickout=90)
    hint = services.stickout_hint(job, machine)
    width = next(op.tool.insert_width for op in job.current_operations if op.tool_type == "parting")
    assert hint == {"value": round(_length(job) + width + 5.0, 3), "length": _length(job), "parting": width,
                    "margin": 5.0}
    assert job.stickout_mm == 90  # shown only: nothing filled in
    page = client.get(f"/jobs/{job.id}").get_data(as_text=True)
    assert f"= {hint['value']:g} mm" in page
    response = client.post(f"/jobs/{job.id}/stickout-hint", data={"value": hint["value"]}, follow_redirects=True)
    assert "The operator&#39;s name is required" in response.get_data(as_text=True) and job.stickout_mm == 90
    client.post(f"/jobs/{job.id}/stickout-hint", data={"value": hint["value"] + 1, "decided_by": "Bohdan"})
    assert job.stickout_mm == 90  # a hint that changed since: refused
    client.post(f"/jobs/{job.id}/stickout-hint", data={"value": hint["value"], "decided_by": "Bohdan"})
    assert job.stickout_mm == hint["value"] and "Bohdan" in job.stickout_note
    assert job.stickout_note.startswith(f"{hint['length']:g} + {width:g} + 5 = {hint['value']:g} mm")
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
