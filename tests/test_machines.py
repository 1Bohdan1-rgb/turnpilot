"""Several machines (stage 7.1): each with its own page, turret, programming values and passport; a new one gets
only the name and the four required values the operator types (no default)."""
from turnpilot import services
from turnpilot.models import Machine, db

NEW = {"name": "Lathe 2", "max_rpm": "3000", "power_kw": "7.5", "max_diameter": "250", "turret_positions": "8"}


def test_add_a_machine_with_the_required_values_only(app, client):
    first = services.get_machine()
    response = client.post("/machines", data={**NEW, "power_kw": ""}, follow_redirects=True)
    assert "Required: Spindle power S1" in response.get_data(as_text=True)
    assert len(services.machines()) == 1
    response = client.post("/machines", data=NEW, follow_redirects=True)
    lathe2 = db.session.execute(db.select(Machine).filter_by(name="Lathe 2")).scalar_one()
    assert (lathe2.max_rpm, lathe2.power_kw, lathe2.max_diameter, len(lathe2.slots)) == (3000, 7.5, 250, 8)
    assert all(s.tool_id is None for s in lathe2.slots)
    assert lathe2.source_of("max_rpm").source == "operator" and lathe2.spindle_right_hand is None
    assert "Machine: Lathe 2" in response.get_data(as_text=True)
    assert services.get_machine() is first  # Lathe 1 stays the first
    assert client.post("/machines", data=NEW, follow_redirects=True).get_data(as_text=True).count(
        "A machine named Lathe 2 exists already") == 1


def test_each_machine_page_saves_its_own_values(app, client):
    client.post("/machines", data=NEW)
    first = services.get_machine()
    lathe2 = db.session.execute(db.select(Machine).filter_by(name="Lathe 2")).scalar_one()
    page = client.get("/machines").get_data(as_text=True)
    assert first.name in page and "Lathe 2" in page
    client.post(f"/machines/{lathe2.id}", data={"action": "programming", "clearance_x": "1.5"})
    assert lathe2.clearance_x == 1.5 and first.clearance_x != 1.5
    assert f"/machines/{lathe2.id}/passport" in client.get(f"/machines/{lathe2.id}").get_data(as_text=True)
    assert "Machine: " + first.name in client.get("/machine").get_data(as_text=True)


def test_a_job_moved_to_another_machine_is_calculated_and_programmed_for_it(app, client):
    from gcode_jobs import make_job, ready_machine

    from turnpilot.models import Feature

    lathe1 = ready_machine()
    job = make_job([Feature(type="face"), Feature(type="od_turn", diameter=20, length=24), Feature(type="parting")],
                   blank=22, length=29, stickout=35, machine=lathe1)
    assert services.job_machine(job) is lathe1
    readiness, record = services.generate_gcode(job, lathe1)
    assert readiness.ok and record.machine is lathe1
    client.post("/machines", data=NEW)
    lathe2 = db.session.execute(db.select(Machine).filter_by(name="Lathe 2")).scalar_one()
    client.post(f"/jobs/{job.id}/machine", data={"machine_id": lathe2.id})
    assert services.job_machine(job) is lathe2
    page = client.get(f"/jobs/{job.id}").get_data(as_text=True)
    assert "The current calculation was made for " + lathe1.name in page
    readiness, _ = services.gcode_program(job, lathe2)
    assert readiness.blockers[0] == f"the calculation was made for {lathe1.name}: calculate the job for Lathe 2"
    assert f"made for {lathe1.name}; the job is planned for Lathe 2 now" in services.gcode_stale(record, lathe1)


def test_a_turret_change_belongs_to_its_machine(app, client):
    from gcode_jobs import make_job, ready_machine

    from turnpilot.models import Feature, Tool

    lathe1 = ready_machine()
    job = make_job([Feature(type="face"), Feature(type="od_turn", diameter=20, length=24)], blank=22, length=29,
                   stickout=35, machine=lathe1)
    tool = db.session.execute(db.select(Tool).filter_by(is_retired=False)).scalars().first()
    empty = next(s.position for s in lathe1.slots if s.tool_id is None) if any(
        s.tool_id is None for s in lathe1.slots) else lathe1.slots[-1].position
    services.change_job_turret(job, lathe1, empty, tool, "Bohdan")
    client.post("/machines", data=NEW)
    lathe2 = db.session.execute(db.select(Machine).filter_by(name="Lathe 2")).scalar_one()
    assert next(s for s in services.job_turret(job, lathe1) if s.position == empty).tool is tool
    assert all(s.change is None for s in services.job_turret(job, lathe2))
