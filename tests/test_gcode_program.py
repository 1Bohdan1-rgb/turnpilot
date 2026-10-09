"""Roadmap stage 6, commit 3: the profile from the part zero, facing, roughing and the finishing contour."""
import pytest
from gcode_jobs import chamfer, make_job, pin_features, ready_machine

from turnpilot import services
from turnpilot.gcode import profile as gp
from turnpilot.gcode.program import Feed, Rapid, Spindle, ascii_text
from turnpilot.models import Feature


def fd(id_, type_, **kw):
    return gp.FeatureData(id=id_, type=type_, **kw)


SHAFT = [fd(1, "od_turn", diameter=20, length=15), fd(2, "chamfer", length=1, face="left"),
         fd(3, "groove", diameter=16, start_diameter=20, length=3), fd(4, "od_turn", diameter=36, length=27),
         fd(5, "thread", diameter=36, length=27, pitch=1.5), fd(6, "chamfer", length=1.5, face="right"),
         fd(7, "taper", start_diameter=36, diameter=40, length=5)]


def test_profile_from_the_free_end_on_the_left():
    p = gp.build(SHAFT, "left")
    assert [(s.kind, s.z_free, s.z_chuck) for s in p.sections] == [
        ("od_turn", 0, -15), ("groove", -15, -18), ("od_turn", -18, -45), ("taper", -45, -50)]
    assert p.length == 50
    thread = p.sections[2]
    assert (thread.pitch, thread.turned_d, thread.chamfer_chuck) == (1.5, 35.85, 1.5)
    assert p.turned(0) == 9 and p.turned(-0.5) == 9.5 and p.turned(-10) == 10
    assert p.turned(-16) == 10 and p.final(-16) == 8  # the groove: bridged / its bottom
    assert p.turned(-15) == 10 and p.final(-15) == 8  # at a step: the smaller radius
    assert p.final(-30) == pytest.approx(35.85 / 2 - 0.613 * 1.5)  # the thread's root
    assert p.turned(-47.5) == 19
    assert p.section_of(5) is thread  # the thread row belongs to its cylinder


def test_profile_from_the_free_end_on_the_right():
    p = gp.build(SHAFT, "right")
    assert [(s.kind, s.d_free, s.d_chuck) for s in p.sections] == [
        ("taper", 40, 36), ("od_turn", 36, 36), ("groove", 16, 16), ("od_turn", 20, 20)]
    thread = p.sections[1]
    assert (thread.chamfer_free, thread.chamfer_chuck) == (1.5, 0)  # the drawing's right face is the free side now
    assert p.turned(0) == 20 and p.turned(-50) == 9  # the taper's Ø40 end; the Ø20 chamfer at the chuck side


def test_fillets_are_not_modelled_and_said_so():
    p = gp.build([fd(1, "od_turn", diameter=20, length=10), fd(2, "fillet", radius=2),
                  fd(3, "od_turn", diameter=30, length=10)], "left")
    assert p.warnings == ["transition R2 (row 2): not in the profile (cut as a sharp corner)"]


def blocks(app_job, machine):
    readiness, program = services.gcode_program(app_job, machine)
    assert readiness.ok, readiness.blockers
    return program


def test_facing_roughing_finishing_of_a_pin(app):
    machine = ready_machine()
    job = make_job()
    program = blocks(job, machine)
    titles = [b.title for b in program.blocks]
    assert titles == ["FACE Z0, 1 MM STOCK", "ROUGH D30 1 X AP 0.6 FROM D32", "ROUGH D24 1 X AP 2.6 FROM D30",
                      "ROUGH D20 1 X AP 1.6 FROM D24", "FINISH D20, D24, D30", "GROOVE D16 W3, INSERT 3, 1 PLUNGE(S)",
                      "PART OFF AT Z-63, INSERT 3"]
    face, r30, r24, r20, finish = program.blocks[:5]
    assert face.commands[0] == Spindle("css", 380, "M04", 3500)
    assert Feed(x=-0.8, f=0.25) in face.commands  # past the axis by the operator's 0.4 per side
    assert [c.z for c in face.commands if isinstance(c, Rapid) and c.x is None] == [0.5, 0.0, 3.0]  # two passes
    # roughing from the chuck side first; each pass from Z+clearance, stopping 0.4 (the finishing ap) short
    assert r30.commands[2:6] == [Rapid(x=30.8), Feed(z=-63.0, f=0.3), Feed(x=31.8), Rapid(z=3.0)]
    assert r20.commands[2:4] == [Rapid(x=20.8), Feed(z=-17.6, f=0.3)]  # over the groove (bridged at Ø20)
    moves = [(c.x, c.z) for c in finish.commands if isinstance(c, Feed)]
    assert moves[:4] == [(18.0, 0.0), (20.0, -1.0), (20.0, -15.0), (20.0, -18.0)]  # chamfer along its line
    assert (28.0, -38.0) in moves and (30.0, -39.0) in moves and moves[-2] == (30.0, -63.0)
    assert Rapid(x=14.0) in finish.commands  # the chamfer's line at Z+2
    assert "no nose radius compensation" in finish.warnings[0]
    assert program.warnings == ["coolant not known for this machine: M08 not written; the catalogue's Vc are with "
                                "coolant: check"]
    assert program.skipped == {}


def test_the_same_pin_drawn_the_other_way_round(app):
    machine = ready_machine()
    reversed_features = [Feature(type="face"), Feature(type="od_turn", diameter=30, length=25),
                         chamfer(30, 1, "right"), Feature(type="od_turn", diameter=24, length=20),
                         Feature(type="groove", diameter=16, start_diameter=20, length=3),
                         Feature(type="od_turn", diameter=20, length=15), chamfer(20, 1, "right"),
                         Feature(type="parting")]
    left = make_job(name="left")
    right = make_job(reversed_features, free_end="right", name="right")
    finish = lambda job: [c for c in blocks(job, machine).blocks[-1].commands]  # noqa: E731
    assert finish(left) == finish(right)


def test_coolant_written_when_the_machine_has_it(app):
    from turnpilot.gcode.program import Coolant
    machine = ready_machine(coolant=True)
    program = blocks(make_job(), machine)
    assert all(b.commands[2] == Coolant(True) and b.commands[-1] == Coolant(False) for b in program.blocks)
    assert program.warnings == []


def test_a_section_between_larger_ones_is_not_roughed(app):
    machine = ready_machine()
    features = [Feature(type="od_turn", diameter=30, length=10), Feature(type="od_turn", diameter=20, length=10),
                Feature(type="od_turn", diameter=30, length=10)]
    job = make_job(features)
    program = blocks(job, machine)
    pit = next(op for op in job.current_operations if op.tool_type == "turning_rough"
               and op.feature.diameter == 20)
    assert program.skipped[pit.id].startswith("a larger diameter between this section and the free end")


def test_finishing_split_at_a_section_without_a_finishing_pass(app):
    machine = ready_machine()
    features = [Feature(type="od_turn", diameter=20, length=10),
                Feature(type="taper", start_diameter=20, diameter=26, length=6),
                Feature(type="od_turn", diameter=26, length=10), Feature(type="od_turn", diameter=30, length=10)]
    job = make_job(features)
    program = blocks(job, machine)
    finishes = [b for b in program.blocks if b.tool_type == "turning_finish"]
    assert [b.title for b in finishes] == ["FINISH D20", "FINISH D26, D30"]
    second = finishes[1].commands
    # entry beside the taper (not finished: its allowance is assumed): above it, then down onto the face
    assert second[2:6] == [Rapid(x=27.8), Rapid(z=-13.6), Feed(x=26.0, f=0.15), Feed(z=-16.0)]


def test_comments_are_ascii():
    assert ascii_text("Завіса 36 (палець) Ø22×1,5") == "ZAVISA 36 PALETS D22X1,5"
