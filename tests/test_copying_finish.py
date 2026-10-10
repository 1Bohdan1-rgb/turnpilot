"""A concave arc steeper than the finishing tool's RMPX is finished by a tool whose RMPX covers it (its own contour
block); with no such tool the default stays and the simulation says what is not cut."""
import pytest
from gcode_jobs import make_job, ready_machine

from turnpilot import services
from turnpilot.gcode import fanuc
from turnpilot.models import Feature, Tool, db
from turnpilot.planner import FeatureSpec, concave_arc_angle


def tool(code):
    return db.session.execute(db.select(Tool).filter_by(insert_code=code)).scalar_one()


def test_the_angle_of_a_concave_arc():
    nd012 = FeatureSpec(1, "arc", start_diameter=38.5, diameter=38.5, length=15, radius=12.5, arc_convex=False)
    assert concave_arc_angle(nd012) == pytest.approx(36.87, abs=0.01)  # НД 012's R12.5
    assert concave_arc_angle(FeatureSpec(1, "arc", start_diameter=20, diameter=30, length=10, radius=20,
                                         arc_convex=True)) is None
    assert concave_arc_angle(FeatureSpec(1, "od_turn", diameter=20, length=10)) is None


def dip_job():
    """A short concave R1.7 dip (36° at its rims, 0.32 deep) between two Ø30 sections."""
    return make_job([Feature(type="face"), Feature(type="od_turn", diameter=30, length=10),
                     Feature(type="arc", start_diameter=30, diameter=30, length=2, radius=1.7, arc_convex=False),
                     Feature(type="od_turn", diameter=30, length=10)], blank=32, length=30, stickout=35)


@pytest.fixture
def machine(app):
    machine = ready_machine()
    tool("DNMG150604-PF").max_ramp_angle = 27.0
    vnmg = tool("VNMG160404-PF")
    vnmg.max_ramp_angle = 44.0
    machine.slots[9].tool = vnmg  # T10 (above T4: T4 stays the first finishing tool), in this test's database only
    db.session.commit()
    return machine


def test_the_arc_goes_to_the_vnmg(machine):
    job = dip_job()
    finish = {op.feature.type: op for op in job.current_operations if op.tool_type == "turning_finish"}
    assert finish["arc"].turret_position == 10 and finish["od_turn"].turret_position == 4
    assert "finished by T10 (RMPX 44°): the concave arc goes down at up to 36° towards the chuck, T4 RMPX 27°" \
           in finish["arc"].note


def test_one_contour_block_per_tool(machine):
    job = dip_job()
    readiness, program = services.gcode_program(job, machine)
    assert readiness.ok, readiness.blockers
    finishing = [(b.tool_position, b.title) for b in program.blocks if b.tool_type == "turning_finish"]
    assert [p for p, _ in finishing] == [4, 10, 4]
    text = fanuc.render(program)
    result = services.gcode_simulation(job, machine, text)
    assert result.ok and result.complete, (result.errors, result.incomplete)


def test_without_a_tool_that_may_go_down_the_default_stays(app):
    machine = ready_machine()
    tool("DNMG150604-PF").max_ramp_angle = 27.0
    db.session.commit()
    job = dip_job()
    arc = next(op for op in job.current_operations if op.tool_type == "turning_finish" and op.feature.type == "arc")
    assert arc.turret_position == 4 and "finished by" not in (arc.note or "")
