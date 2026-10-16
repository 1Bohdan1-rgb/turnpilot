"""Roadmap stage 6, commit 4: grooving, threading (G92 per pass), drilling on the axis (G97, pecks), parting."""
from datetime import datetime

import pytest
from gcode_jobs import chamfer, make_job, ready_machine

from turnpilot import services
from turnpilot.gcode.program import Comment, Feed, Rapid, Spindle, ThreadPass
from turnpilot.models import Feature, db
from turnpilot.planner import thread_depth, thread_infeed_plan


def program_of(job, machine):
    readiness, program = services.gcode_program(job, machine)
    assert readiness.ok, readiness.blockers
    return program


def block(program, tool_type):
    return next(b for b in program.blocks if b.tool_type == tool_type)


def test_one_plunge_groove(app):
    machine = ready_machine()
    program = program_of(make_job(), machine)
    groove = block(program, "grooving")
    assert groove.title == "GROOVE D16 W3, INSERT 3, 1 PLUNGE"
    assert groove.commands[2:6] == [Rapid(x=26.8), Rapid(z=-15.0), Feed(x=16.0, f=0.07), Rapid(x=26.8)]


@pytest.mark.parametrize("reference, first, last", [("toward Z0", -15.0, -21.0), ("toward chuck", -18.0, -24.0)])
def test_wide_groove_plunges_by_the_touched_off_corner(app, reference, first, last):
    machine = ready_machine(groove_reference=reference)
    features = [Feature(type="face"), Feature(type="od_turn", diameter=20, length=15),
                Feature(type="groove", diameter=16, start_diameter=20, length=9),
                Feature(type="od_turn", diameter=24, length=20), Feature(type="parting")]
    groove = block(program_of(make_job(features), machine), "grooving")
    plunges = [c.z for c in groove.commands if isinstance(c, Rapid) and c.z is not None and c.x is None]
    assert plunges[0] == first and plunges[-1] == last and len(plunges) == 4  # 1 + ceil(6 / 2.4)
    assert f"corner {reference}" in groove.warnings[0]


def thread_job():
    return [Feature(type="face"), Feature(type="od_turn", diameter=24, length=27), chamfer(24, 1.5, "left"),
            Feature(type="thread", diameter=24, length=27, pitch=1.5, location="external"),
            Feature(type="groove", diameter=20.5, start_diameter=24, length=3),
            Feature(type="od_turn", diameter=30, length=20), Feature(type="parting")]


def test_thread_in_g97_with_one_g92_line_per_pass(app):
    machine = ready_machine()
    job = make_job(thread_job())
    thread = block(program_of(job, machine), "threading")
    op = next(o for o in job.current_operations if o.tool_type == "threading")
    assert thread.commands[0] == Spindle("rpm", op.n, "M04", 3500)
    assert thread.commands[2:4] == [Rapid(z=6.0), Rapid(x=25.85)]  # run-in 6, clearance 1 per side
    infeed, method = thread_infeed_plan(1.5, 0.05, 0.2)
    assert method == "catalogue"
    passes = [c for c in thread.commands if isinstance(c, ThreadPass)]
    assert len(passes) == len(infeed) == op.passes == 7  # C77: 6 cuts and the spring pass
    assert passes[0] == ThreadPass(round(23.85 - 2 * infeed[0], 3), -27.0, 1.5)
    assert passes[-1].x == passes[-2].x == round(23.85 - 2 * thread_depth(1.5), 3)  # the spring pass at full depth
    assert "check n·P" in thread.warnings[-1]


def test_thread_ending_at_a_shoulder_is_flagged(app):
    machine = ready_machine(max_thread_feed=2000)
    features = [Feature(type="od_turn", diameter=24, length=27),
                Feature(type="thread", diameter=24, length=27, pitch=1.5, location="external"),
                Feature(type="od_turn", diameter=30, length=20)]
    thread = block(program_of(make_job(features), machine), "threading")
    assert thread.warnings == ["the thread ends at a shoulder without a relief groove: G92 pulls out at the thread "
                               "end: check"]


def drilled_job(mode):
    features = [Feature(type="face"), Feature(type="bore", diameter=10, length=20, tolerance="H12", depth_mode=mode,
                                              depth_decided_by="Bohdan", depth_decided_at=datetime(2026, 10, 16)),
                Feature(type="od_turn", diameter=30, length=40), Feature(type="parting")]
    return make_job(features)


def test_drilling_on_the_axis_in_g97_with_pecks(app):
    machine = ready_machine(peck_depth_mm=8)
    job = drilled_job("tip")
    drill = block(program_of(job, machine), "drilling")
    op = next(o for o in job.current_operations if o.tool_type == "drilling")
    assert drill.commands[0] == Comment("DEPTH 20 TO THE DRILL'S POINT: FULL D10 18.18 DEEP, BOHDAN 2026-10-16")
    assert drill.commands[1] == Spindle("rpm", op.n, "M04", 3500)  # never G96 on X0
    assert drill.commands[3:] == [
        Rapid(x=0.0), Rapid(z=2.0),
        Feed(z=-8.0, f=op.f), Rapid(z=2.0),
        Rapid(z=-6.0), Feed(z=-16.0, f=op.f), Rapid(z=2.0),
        Rapid(z=-14.0), Feed(z=-20.0, f=op.f), Rapid(z=2.0),
        Rapid(z=3.0), Rapid(x=34.0)]
    assert not any("full Ø stops" in w for w in drill.warnings)


def test_drilling_to_the_full_diameter_goes_the_point_deeper(app):
    """T12 860-GM Ø10, 140° (SRT B26): the point is 1.82 long, so the full Ø reaches 20 with the point at Z-21.82."""
    machine = ready_machine(peck_depth_mm=8)
    job = drilled_job("full_diameter")
    drill = block(program_of(job, machine), "drilling")
    op = next(o for o in job.current_operations if o.tool_type == "drilling")
    assert drill.commands[0] == Comment("DEPTH 20 TO THE FULL D10: POINT TO Z-21.82 (140 DEG POINT 1.82), "
                                        "BOHDAN 2026-10-16")
    assert Feed(z=-21.82, f=op.f) in drill.commands and Feed(z=-20.0, f=op.f) not in drill.commands


def test_a_bore_without_the_depth_decision_blocks_the_program(app, client):
    machine = ready_machine()
    job = drilled_job(None)
    feature = next(f for f in job.active_features if f.type == "bore")
    feature.depth_decided_by = feature.depth_decided_at = None
    db.session.commit()
    readiness, program = services.gcode_program(job, machine)
    assert program is None and readiness.blockers == [
        "bore Ø10 × 20: the depth to the full Ø or to the drill's point: the operator's choice on the job page"]
    page = client.get(f"/jobs/{job.id}").get_data(as_text=True)
    assert "to the full Ø the point goes to Z-21.82; to the point Z-20, the full Ø 18.18 deep" in page
    client.post(f"/jobs/{job.id}/features/{feature.id}/depth", data={"depth_mode": "full_diameter"})
    assert feature.depth_mode is None  # the name is required
    client.post(f"/jobs/{job.id}/features/{feature.id}/depth",
                data={"depth_mode": "full_diameter", "decided_by": "Bohdan"})
    assert (feature.depth_mode, feature.depth_decided_by) == ("full_diameter", "Bohdan")
    assert services.gcode_program(job, machine)[0].ok


def test_the_full_diameter_needs_the_point_angle(app):
    machine = ready_machine()
    job = drilled_job("full_diameter")
    drill = next(o for o in job.current_operations if o.tool_type == "drilling").tool
    drill.point_angle_deg = None
    db.session.commit()
    assert (f"bore Ø10 × 20: the depth to the full Ø needs the point angle of {drill.name} (tool data)"
            in services.gcode_program(job, machine)[0].blockers)


def test_parting_to_the_axis_warns_about_g96(app):
    machine = ready_machine()
    parting = block(program_of(make_job(), machine), "parting")
    assert parting.commands[2] == Comment("PARTING PAST THE AXIS TO X-0.5 IN G96: SPINDLE RUNS UP TO G50 S3500 - "
                                          "OPERATOR MAY SWITCH TO G97")
    assert parting.commands[3:5] == [Rapid(z=-63.0), Feed(x=-0.5, f=0.1)]  # the operator's 0.25 per side
    assert parting.warnings[0].startswith("parting to the axis in G96")


def test_parting_by_the_chuck_side_corner(app):
    machine = ready_machine(groove_reference="toward chuck")
    parting = block(program_of(make_job(), machine), "parting")
    assert Rapid(z=-66.0) in parting.commands  # the 3 mm insert's chuck-side corner
