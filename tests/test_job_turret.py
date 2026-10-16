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
