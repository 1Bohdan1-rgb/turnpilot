"""The machine page: copy the turret from another machine, with the operator's name and the history; programs and
calculations made before the copy are out of date."""
import json

from gcode_jobs import make_job, ready_machine
from test_gcode_page import generate

from turnpilot import services
from turnpilot.models import Machine, MachineTurretCopy, db

NEW = {"name": "Lathe 2", "max_rpm": "3000", "power_kw": "7.5", "max_diameter": "250", "turret_positions": "12"}


def lathe2(client, positions="12"):
    client.post("/machines", data={**NEW, "turret_positions": positions})
    return db.session.execute(db.select(Machine).filter_by(name="Lathe 2")).scalar_one()


def test_copy_the_turret_from_another_machine(app, client):
    lathe1 = ready_machine()
    machine = lathe2(client)
    page = client.get(f"/machines/{machine.id}").get_data(as_text=True)
    assert "Copy the turret from the other machine" in page
    response = client.post(f"/machines/{machine.id}", data={"action": "copy_turret", "from_machine_id": lathe1.id},
                           follow_redirects=True)
    assert "The operator&#39;s name is required to copy a turret" in response.get_data(as_text=True)
    assert all(s.tool is None for s in machine.slots)
    client.post(f"/machines/{machine.id}", data={"action": "copy_turret", "from_machine_id": lathe1.id,
                                                 "copied_by": "Bohdan"})
    assert [s.tool_id for s in machine.slots] == [s.tool_id for s in lathe1.slots]
    (copy,) = db.session.execute(db.select(MachineTurretCopy)).scalars().all()
    assert (copy.copied_by, copy.from_machine_id) == ("Bohdan", lathe1.id)
    assert set(json.loads(copy.before).values()) == {None}
    page = client.get(f"/machines/{machine.id}").get_data(as_text=True)
    assert "Turret copies (1)" in page and "T1 empty → Facing SCMT" in page


def test_a_smaller_turret_is_refused_naming_the_positions(app, client):
    lathe1 = ready_machine()
    machine = lathe2(client, positions="8")
    response = client.post(f"/machines/{machine.id}", data={"action": "copy_turret", "from_machine_id": lathe1.id,
                                                            "copied_by": "Bohdan"}, follow_redirects=True)
    text = response.get_data(as_text=True)
    assert "Lathe 1 has tools at positions Lathe 2 lacks: T9 " in text and "T12 " in text
    assert all(s.tool is None for s in machine.slots)


def test_a_copy_makes_earlier_programs_and_calculations_out_of_date(app, client):
    lathe1 = ready_machine()
    job = make_job()
    record = generate(client, job)
    machine = lathe2(client)
    services.copy_turret(lathe1, machine, "Bohdan")  # Lathe 1 takes the empty turret of Lathe 2
    stale = services.gcode_stale(record, lathe1)
    assert any(r.startswith("the machine's turret was copied from Lathe 2 by Bohdan on ") for r in stale)
    blockers = services.gcode_readiness(job, lathe1).blockers
    assert blockers[0].startswith("Lathe 1's turret was copied from Lathe 2 by Bohdan on ")
    assert blockers[0].endswith("after the calculation: calculate the job again")
