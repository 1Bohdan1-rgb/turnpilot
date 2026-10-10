"""НД 012's hollow: a concave R12.5 between Ø38.5 ends (36.9° at its rims), behind a larger Ø: roughed by copying
along concentric arcs with a tool that may go down at that angle, finished by one too."""
import pytest
from gcode_jobs import make_job, ready_machine

from turnpilot import services
from turnpilot.gcode import fanuc
from turnpilot.models import Feature, Tool, db


def tool(code):
    return db.session.execute(db.select(Tool).filter_by(insert_code=code)).scalar_one()


def hollow_job():
    return make_job([Feature(type="face"), Feature(type="od_turn", diameter=38.5, length=15),
                     Feature(type="arc", start_diameter=38.5, diameter=38.5, length=15, radius=12.5, arc_convex=False),
                     Feature(type="od_turn", diameter=45, length=5), Feature(type="parting")],
                    blank=50, length=40, stickout=45)


def install(machine, position, code, rmpx=44.0, tip=3):
    t = tool(code)
    t.max_ramp_angle, t.tip_direction = rmpx, tip
    machine.slots[position - 1].tool = t  # in this test's database only


@pytest.fixture
def machine(app):
    machine = ready_machine()
    tool("DNMG150604-PF").max_ramp_angle = 27.0
    db.session.commit()
    return machine


def ops(job, kind):
    return next(op for op in job.current_operations if op.feature.type == "arc" and op.tool_type == kind)


def program(job, machine):
    readiness, prog = services.gcode_program(job, machine)
    assert readiness.ok, readiness.blockers
    text = fanuc.render(prog)
    return prog, text, services.gcode_simulation(job, machine, text)


def test_roughed_by_the_pm_and_finished_by_the_pf(machine):
    install(machine, 10, "VNMG160408-PM")
    install(machine, 11, "VNMG160404-PF")
    db.session.commit()
    job = hollow_job()
    rough, finish = ops(job, "turning_rough"), ops(job, "turning_finish")
    assert (rough.turret_position, finish.turret_position) == (10, 11)
    assert "concave arc: copy roughing along arcs concentric to it" in rough.note
    prog, text, result = program(job, machine)
    block = next(b for b in prog.blocks if b.title.startswith("COPY ROUGH ARC R12.5"))
    assert block.tool_position == 10
    assert result.ok and result.complete, (result.errors, result.incomplete)


def test_only_the_pf_roughs_it_too_with_a_warning(machine):
    install(machine, 11, "VNMG160404-PF")
    db.session.commit()
    job = hollow_job()
    rough = ops(job, "turning_rough")
    assert rough.turret_position == 11
    assert f"roughed with the finishing copying insert T11 (no roughing tool may go down at 37°): {rough.passes} " \
           f"passes of ap {rough.ap:g}" in rough.warning
    prog, text, result = program(job, machine)
    assert result.ok and result.complete, (result.errors, result.incomplete)


def test_without_a_copying_tool_it_is_an_error(machine):
    job = hollow_job()
    prog, text, result = program(job, machine)
    assert any("goes down at up to 36° towards the chuck" in m and "VNMG16 in DVJNR" in m for _, m in result.errors)


def test_copy_roughing_needs_the_tip_direction(machine):
    # the passes are placed for the nose centre: without T they cannot be
    install(machine, 10, "VNMG160408-PM", tip=None)
    install(machine, 11, "VNMG160404-PF")
    db.session.commit()
    job = hollow_job()
    prog, text, result = program(job, machine)
    assert prog.skipped[ops(job, "turning_rough").id] == (
        "copy roughing needs the tool's nose radius and tip direction T (the passes are placed for the nose centre): "
        "not generated")
    assert any("above the tool's ap max" in m for _, m in result.errors)  # the finishing pass meets it all
