"""Arcs G02/G03, commit 4: arcs in the plan and in the program: roughed in steps and along the arc, finished as
G02 / G03; an arc the insert cannot follow is left out and said so."""
import pytest
from gcode_jobs import make_job, ready_machine

from turnpilot import services
from turnpilot.gcode import fanuc
from turnpilot.gcode.program import ArcMove, Feed
from turnpilot.models import Feature, Tool, db
from turnpilot.planner import FeatureSpec, arc_lowest_diameter


def program(job, machine):
    readiness, prog = services.gcode_program(job, machine)
    assert readiness.ok, readiness.blockers
    text = fanuc.render(prog)
    return prog, text, services.gcode_simulation(job, machine, text)


def sphere_job(convex=True):
    """деталь 1's end: a sphere R10 from the axis to Ø20 at the free end (drawn on the right), Ø20 L5, Ø30 L10."""
    return make_job([Feature(type="face"), Feature(type="od_turn", diameter=30, length=10),
                     Feature(type="od_turn", diameter=20, length=5),
                     Feature(type="arc", start_diameter=20, length=10, radius=10, arc_convex=convex),
                     Feature(type="parting")], free_end="right", blank=32, length=40, stickout=40)


def test_lowest_diameter_of_an_arc():
    assert arc_lowest_diameter(FeatureSpec(1, "arc", start_diameter=20, length=10, radius=10, arc_convex=True)) == 0
    assert arc_lowest_diameter(FeatureSpec(1, "arc", start_diameter=30, diameter=30, length=17.32, radius=10,
                                           arc_convex=False)) == pytest.approx(20, abs=0.01)
    assert arc_lowest_diameter(FeatureSpec(1, "arc", start_diameter=30, diameter=20, length=8, radius=10)) is None


def test_a_spherical_end_roughed_along_the_arc_and_finished_with_g03(app):
    machine = ready_machine()
    job = sphere_job()
    prog, text, result = program(job, machine)
    rough = next(b for b in prog.blocks if b.title.startswith("ROUGH ARC R10"))
    arcs = [c for c in rough.commands if isinstance(c, ArcMove)]
    # along the arc 0.4 outside it: from Z+0.4 on the axis to Ø20.8 at Z-10, about the same centre
    assert arcs == [ArcMove(x=20.8, z=-10.0, i=0.0, k=-10.4, clockwise=False)]
    assert "arc: steps, then one pass along it 0.4 mm above it" in rough.notes
    finish = next(b for b in prog.blocks if b.tool_type == "turning_finish")
    assert ArcMove(x=20.0, z=-10.0, i=0.0, k=-10.0, clockwise=False, f=0.15) in finish.commands
    assert "G03 X20. Z-10. I0. K-10. F0.15" in text
    assert result.ok and result.complete, (result.errors, result.incomplete)


def test_a_concave_arc_the_inserts_may_not_go_down_into(app):
    machine = ready_machine()
    job = make_job([Feature(type="od_turn", diameter=30, length=10),
                    Feature(type="arc", start_diameter=30, diameter=30, length=17.32, radius=10, arc_convex=False),
                    Feature(type="od_turn", diameter=30, length=10)], blank=32, length=50, stickout=50)
    prog, text, result = program(job, machine)
    arc_rough = next(op for op in job.current_operations if op.feature.type == "arc" and op.rough_finish == "rough")
    assert "a larger diameter between this section and the free end" in prog.skipped[arc_rough.id]
    finish = next(b for b in prog.blocks if b.tool_type == "turning_finish")
    assert any("the arc R10: Z-10 to Z-27.32 goes down" in w and "not cut by this tool" in w for w in finish.warnings)
    assert not result.complete


def test_a_concave_fillet_smaller_than_the_nose_radius(app):
    machine = ready_machine()
    tool = db.session.execute(db.select(Tool).filter_by(insert_code="DNMG150604-PF")).scalar_one()
    tool.max_ramp_angle = 89
    db.session.commit()
    job = make_job([Feature(type="od_turn", diameter=20, length=10),
                    Feature(type="fillet", radius=0.2, face="right", arc_convex=False),
                    Feature(type="od_turn", diameter=30, length=10)], blank=32, length=30, stickout=30)
    prog, text, result = program(job, machine)
    finish = next(b for b in prog.blocks if b.tool_type == "turning_finish")
    assert "concave R0.2 is smaller than the nose radius 0.4: not cut" in finish.warnings


def test_an_arc_waiting_for_the_operators_radius_is_left_out(app):
    machine = ready_machine()
    job = make_job([Feature(type="od_turn", diameter=30, length=10),
                    Feature(type="arc", start_diameter=30, diameter=20, length=8, radius=20.46, arc_convex=True,
                            drawn_radius=51.61)], blank=32, length=30, stickout=30)
    prog, _, _ = program(job, machine)
    arc_ops = [op for op in job.current_operations if op.feature.type == "arc"]
    assert all("drawn R51.61 ≠ dimensioned R20.46" in prog.skipped[op.id] for op in arc_ops)
    assert not any(isinstance(c, ArcMove) for b in prog.blocks for c in b.commands)
    assert any(isinstance(c, Feed) for b in prog.blocks for c in b.commands)
