"""The job's full turret set-up from the library (services.turret_setup_proposal): by the planner's own rules, a tool
that is in the turret and fits stays, empty positions first, the operations' order, the library's checked tools only,
and what the library cannot give said in words."""
from gcode_jobs import make_job, pin_features, ready_machine

from turnpilot import services
from turnpilot.models import CuttingDataRow, Machine, Tool, db

NEW = {"name": "Lathe 2", "max_rpm": "3000", "power_kw": "7.5", "max_diameter": "250", "turret_positions": "12"}


def lathe2(client):
    client.post("/machines", data=NEW)
    return db.session.execute(db.select(Machine).filter_by(name="Lathe 2")).scalar_one()


def tool(code):
    return db.session.execute(db.select(Tool).filter_by(insert_code=code)).scalar_one()


def pin_on(machine):
    job = make_job(pin_features(), machine=machine, approve=False)
    services.calculate_operations(job, machine)
    db.session.commit()
    return job


def test_an_empty_turret_gets_a_full_set_up_in_the_operations_order(app, client):
    lathe1 = ready_machine()
    machine = lathe2(client)
    job = pin_on(machine)
    assert all(op.tool_id is None for op in job.current_operations)
    proposal = services.turret_setup_proposal(job, machine)
    assert [(r.position, r.install.insert_code, r.remove) for r in proposal.rows] == [
        (1, "SCMT120408-PM", None), (2, "CNMG120408-PM", None), (3, "DNMG150604-PF", None),
        (4, "N123G2-0300-0003-GM", None), (5, "QD-NG-0300-0002-CM", None)]
    assert all(r.also_on == [lathe1.name] for r in proposal.rows)
    assert proposal.rows[0].reasons == ["No facing tool in the turret. Install one to machine this feature."]
    assert proposal.missing == [] and proposal.unplaced == []
    assert all(s.tool is None for s in machine.slots)  # nothing written


def test_a_tool_in_the_turret_that_fits_stays(app, client):
    ready_machine()
    machine = lathe2(client)
    machine.slots[6].tool = tool("SCMT120408-PM")  # T7
    db.session.commit()
    job = pin_on(machine)
    proposal = services.turret_setup_proposal(job, machine)
    assert [(r.position, r.install.insert_code) for r in proposal.rows] == [
        (1, "CNMG120408-PM"), (2, "DNMG150604-PF"), (3, "N123G2-0300-0003-GM"), (4, "QD-NG-0300-0002-CM")]


def test_a_taken_position_is_used_only_when_its_tool_is_not_used_by_the_job(app, client):
    ready_machine()
    machine = lathe2(client)
    drills = [t for t in db.session.execute(db.select(Tool).filter_by(type="drilling", is_retired=False)).scalars()]
    for slot in machine.slots:
        slot.tool = drills[slot.position % len(drills)]
    machine.slots[1].tool = tool("SCMT120408-PM")  # T2: facing, used by the pin
    db.session.commit()
    job = pin_on(machine)
    proposal = services.turret_setup_proposal(job, machine)
    assert [r.position for r in proposal.rows] == [1, 3, 4, 5]
    assert all(r.remove is not None and r.remove.type == "drilling" for r in proposal.rows)


def test_a_full_turret_of_the_machine_proposes_nothing(app):
    machine = ready_machine()
    job = pin_on(machine)
    assert not services.turret_setup_proposal(job, machine).needed


def test_what_the_library_cannot_give_is_said(app, client):
    ready_machine()
    machine = lathe2(client)
    for row in db.session.execute(db.select(CuttingDataRow)).scalars():
        if "QD-NG" in (row.insert_code or ""):
            row.status = "read"  # the parting insert's catalogue row not confirmed
    db.session.commit()
    job = pin_on(machine)
    proposal = services.turret_setup_proposal(job, machine)
    assert "QD-NG-0300-0002-CM" not in [r.install.insert_code for r in proposal.rows]
    assert proposal.missing == [
        "Parting 3 mm: in the library, but its cutting data are not from a confirmed catalogue row for P1.2: not "
        f"proposed for parting of parting (row {next(f.id for f in job.active_features if f.type == 'parting')})"]
    tool("QD-NG-0300-0002-CM").is_retired = True
    db.session.commit()
    proposal = services.turret_setup_proposal(job, machine)
    assert proposal.missing[0].startswith("the library has no parting tool for ISO P checked by the catalogue for "
                                          "P1.2: parting (row ")
