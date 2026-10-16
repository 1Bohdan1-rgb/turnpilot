"""A job's own turret set-up: changes over the machine's turret for that job only."""
from gcode_jobs import make_job, ready_machine

from turnpilot import services
from turnpilot.models import Feature, JobTurretChange, Tool, db


def tool(code):
    return db.session.execute(db.select(Tool).filter_by(insert_code=code)).scalar_one()


def change(job, machine, position, code, name="Bohdan"):
    slot = next(s for s in machine.slots if s.position == position)
    c = JobTurretChange(job=job, position=position, tool=tool(code) if code else None, machine_tool=slot.tool,
                        decided_by=name)
    db.session.add(c)
    db.session.commit()
    return c


def bore_job():
    """03057789-2-01: M20 × 24 with a blind Ø10 × 5 bore."""
    return make_job([Feature(type="face"), Feature(type="od_turn", diameter=20, length=24),
                     Feature(type="bore", diameter=10, length=5, ra=6.3), Feature(type="parting")],
                    blank=22, length=29, stickout=35, approve=False)


def test_the_job_turret_is_the_machines_with_its_changes(app):
    machine = ready_machine()
    job, other = bore_job(), bore_job()
    change(job, machine, 12, "VNMG160404-PF")
    assert next(s for s in services.job_turret(job, machine) if s.position == 12).tool.insert_code == "VNMG160404-PF"
    assert next(s for s in machine.slots if s.position == 12).tool.insert_code == "860.1-1000-029A0-GM"
    assert next(s for s in services.job_turret(other, machine) if s.position == 12).tool.insert_code == \
        "860.1-1000-029A0-GM"


def test_the_calculation_takes_the_job_turret(app):
    machine = ready_machine()
    job = bore_job()
    services.calculate_operations(job, machine)  # T12 Ø10 drill: drilled to size
    drill = next(op for op in job.current_operations if op.tool_type == "drilling")
    assert (drill.turret_position, drill.ref_diameter) == (12, 10)
    change(job, machine, 12, "VNMG160404-PF")
    services.calculate_operations(job, machine)  # without it: Ø8 and boring (DMIN not set)
    drill = next(op for op in job.current_operations if op.tool_type == "drilling")
    assert drill.ref_diameter == 8
    assert any(op.tool_type == "boring" for op in job.current_operations)


def test_an_undone_change_is_kept_but_not_used(app):
    machine = ready_machine()
    job = bore_job()
    c = change(job, machine, 12, "VNMG160404-PF")
    from datetime import datetime, timezone
    c.removed_by, c.removed_at = "Bohdan", datetime.now(timezone.utc)
    db.session.commit()
    assert next(s for s in services.job_turret(job, machine) if s.position == 12).change is None
    assert len(job.turret_changes) == 1


def test_the_simulation_knows_the_job_turret(app):
    machine = ready_machine()
    job = bore_job()
    change(job, machine, 3, "VNMG160408-PM")
    from turnpilot.gcode import simulate
    seen = {}
    real = simulate.simulate

    def spy(text, data):
        seen.update(data.tools)
        return real(text, data)

    simulate.simulate = spy
    try:
        services.gcode_simulation(job, machine, "%\nM30\n%\n")
    finally:
        simulate.simulate = real
    assert seen[3].insert_code == "VNMG160408-PM"


def test_the_job_page_changes_and_undoes(app, client):
    machine = ready_machine()
    job = bore_job()
    vnmg = tool("VNMG160404-PF")
    response = client.post(f"/jobs/{job.id}/turret", data={"position": "12", "tool_id": str(vnmg.id)},
                           follow_redirects=True)
    assert "The operator&#39;s name is required" in response.get_data(as_text=True) and not job.turret_changes
    client.post(f"/jobs/{job.id}/turret", data={"position": "12", "tool_id": str(vnmg.id), "decided_by": "Bohdan",
                                                 "note": "for the R12.5"})
    (c,) = job.turret_changes
    assert (c.position, c.tool.insert_code, c.machine_tool.insert_code, c.decided_by, c.note) == (
        12, "VNMG160404-PF", "860.1-1000-029A0-GM", "Bohdan", "for the R12.5")
    page = client.get(f"/jobs/{job.id}").get_data(as_text=True)
    assert "Drill 860-GM Ø10 → Copy finishing VNMG (P)" in page and "Bohdan" in page
    # a second change of the same position undoes the first
    client.post(f"/jobs/{job.id}/turret", data={"position": "12", "tool_id": "", "decided_by": "Bohdan"})
    assert [x.active for x in job.turret_changes] == [False, True]
    assert next(s for s in services.job_turret(job, machine) if s.position == 12).tool is None
    client.post(f"/jobs/{job.id}/turret/{job.turret_changes[1].id}/undo", data={"decided_by": "Bohdan"})
    assert all(not x.active for x in job.turret_changes)
    assert "Undone changes (2)" in client.get(f"/jobs/{job.id}").get_data(as_text=True)


def test_a_drill_of_the_bore_size_is_suggested_on_a_free_position(app, client):
    # the Ø10 drill not in the turret (T12 holds a VNMG): the job does not use T3 (CNMG M/N, ISO M/N) -> T3
    machine = ready_machine()
    slot12 = next(s for s in machine.slots if s.position == 12)
    slot12.tool = tool("VNMG160404-PF")
    db.session.commit()
    job = bore_job()
    services.calculate_operations(job, machine)
    (s,) = services.turret_suggestions(job, machine)
    assert (s.tool.insert_code, s.reason) == ("860.1-1000-029A0-GM", "the bore Ø10 drilled to size in one operation")
    used = {op.turret_position for op in job.current_operations if op.tool_id}
    assert s.position not in used
    page = client.get(f"/jobs/{job.id}").get_data(as_text=True)
    assert "Suggested for this job" in page and "the bore Ø10 drilled to size in one operation" in page
    client.post(f"/jobs/{job.id}/turret", data={"position": str(s.position), "tool_id": str(s.tool.id),
                                                 "decided_by": "Bohdan", "note": s.reason})
    services.calculate_operations(job, machine)
    drill = next(op for op in job.current_operations if op.tool_type == "drilling")
    assert (drill.turret_position, drill.ref_diameter) == (s.position, 10)
    assert services.turret_suggestions(job, machine) == []


def test_a_copying_tool_is_suggested_for_a_steep_concave_arc(app):
    machine = ready_machine()
    tool("DNMG150604-PF").max_ramp_angle = 27.0
    vnmg = tool("VNMG160404-PF")
    vnmg.max_ramp_angle = 44.0
    db.session.commit()
    from gcode_jobs import make_job
    job = make_job([Feature(type="face"), Feature(type="od_turn", diameter=30, length=10),
                    Feature(type="arc", start_diameter=30, diameter=30, length=2, radius=1.7, arc_convex=False),
                    Feature(type="od_turn", diameter=30, length=10)], blank=32, length=30, stickout=35, approve=False)
    suggestions = services.turret_suggestions(job, machine)
    assert [(s.tool.insert_code, s.reason) for s in suggestions] == [
        ("VNMG160404-PF", "the concave R1.7 goes down at 36°: a tool with RMPX ≥ 36°")]


def test_the_program_names_the_set_up_and_is_out_of_date_after_a_change(app, client):
    from gcode_jobs import make_job as approved_job
    machine = ready_machine()
    job = approved_job()  # the pin: no bore, so T12 (the Ø10 drill) is not used
    change(job, machine, 12, "VNMG160404-PF")
    services.calculate_operations(job, machine)
    for op in job.current_operations:
        op.status = "approved"
    db.session.commit()
    _, record = services.generate_gcode(job, machine)
    assert "(TURRET SET-UP FOR THIS JOB NOT THE MACHINE'S:)\n(T12: DRILL 860-GM D10 -> COPY FINISHING VNMG P BOHDAN," \
           in record.text
    items = dict(services.gcode_checklist(record))
    assert items["turret_setup"].startswith("the turret is set up for this job: T12: Drill 860-GM Ø10 -> Copy "
                                            "finishing VNMG (P) (Bohdan, ")
    assert "Turret set-up for this job" in client.get(f"/gcode/{record.id}").get_data(as_text=True)
    assert services.gcode_stale(record, machine) == []
    services.undo_job_turret_change(job.turret_changes[0], "Bohdan")
    assert "the job's turret set-up changed after this program" in services.gcode_stale(record, machine)
