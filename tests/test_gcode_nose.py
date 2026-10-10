"""Arcs G02/G03, commit 7: the simulation follows the nose circle of a turning insert whose rε and tip direction T
are known (with and without compensation), and catches the errors of G41 / G42."""
import pytest
from gcode_jobs import make_job, ready_machine
from test_gcode_arc_program import sphere_job
from test_gcode_compensation import finishing_tool, program

from turnpilot import services
from turnpilot.gcode import nose


def messages(result):
    return [m for _, m in result.errors]


def simulate(job, machine, text):
    return services.gcode_simulation(job, machine, text)


def replace(text, old, new):
    assert old in text, old
    return text.replace(old, new, 1)


@pytest.fixture
def pin(app):
    machine = ready_machine()
    finishing_tool()
    job = make_job()
    _, text, result = program(job, machine)
    return job, machine, text, result


@pytest.fixture
def sphere(app):
    machine = ready_machine()
    finishing_tool()
    job = sphere_job()
    _, text, result = program(job, machine)
    return job, machine, text, result


def test_the_compensated_pin_is_complete_with_its_inner_corner_named(pin):
    _, _, _, result = pin
    assert result.ok and result.complete, (result.errors, result.incomplete)
    assert (0, "the inner corner at Z-38. D24. keeps the nose radius R0.4 (up to 0.40 mm per side): the drawn corner "
               "is sharp, check") in result.warnings


def test_without_compensation_the_nose_leaves_the_chamfers_full(pin):
    job, machine, text, _ = pin
    result = simulate(job, machine, text.replace(" G42", "").replace(" G40", ""))
    assert result.ok, result.errors
    # 1×45° chamfers: rε 0.4 leaves 0.23 mm (per side, along X) at their middle, as the generator's CHECK said
    assert "PART NOT COMPLETE: material left from Z-0.01 to Z-0.98 (up to 0.23 mm per side): operations not in the " \
           "program, or manual" in result.incomplete
    assert any(m.startswith("PART NOT COMPLETE: material left from Z-38.01 to Z-38.98 (up to 0.23")
               for m in result.incomplete)


def test_a_tool_without_t_is_still_a_point(app):
    machine = ready_machine()
    finishing_tool(tip_direction=None)
    job = make_job()
    _, text, result = program(job, machine)
    assert result.ok and result.complete  # as before commit 7: the tip point on the contour
    assert not any("inner corner" in m for _, m in result.warnings)


def test_the_sphere_is_complete_and_never_below_the_axis(sphere):
    _, _, _, result = sphere
    assert result.ok and result.complete, (result.errors, result.incomplete)


def test_the_nose_centre_across_the_axis(sphere):
    # G42 a block earlier: the move down the face is compensated, so at X0 Z0 the nose turns round the corner
    # under the axis before it goes up the sphere
    job, machine, text, _ = sphere
    bad = replace(text, "G00 X2.8\nG01 Z0. F0.15\nG01 G42 X0.", "G00 G42 X2.8\nG01 Z0. F0.15\nG01 X0.")
    found = messages(simulate(job, machine, bad))
    assert any(m.startswith("the nose centre goes below the axis (X-") for m in found), found
    assert any(m.startswith("cuts below the finished profile") for m in found)


def test_a_cut_into_the_part_by_the_nose(pin):
    # T0: the tool was touched off at the nose centre, so without compensation the centre runs on the contour and
    # the nose cuts rε into the part (as a tip point it would only touch it)
    job, machine, text, _ = pin
    finishing_tool(tip_direction=0)
    found = messages(simulate(job, machine, text.replace(" G42", "").replace(" G40", "")))
    assert "cuts below the finished profile at Z-20. (D23.2 < D24.)" in found


def test_compensation_errors(pin):
    job, machine, text, _ = pin

    def found(old, new):
        return messages(simulate(job, machine, replace(text, old, new)))

    assert "G42 on G02: the start-up needs a G00 / G01 move" in found(
        "G00 G42 X14.\nG01 X18. Z0. F0.15", "G00 X14.\nG02 G42 X18. Z0. R2. F0.15")
    assert "G40 on G03: the cancel needs a G00 / G01 move" in found(
        "G01 G40 X34.", "G03 G40 X34. Z-65. R2.")
    assert "G41 while G42 is on: the side changes only after G40" in found(
        "G01 X24. Z-38. F0.15", "G01 G41 X24. Z-38. F0.15")
    missing = found("G01 G40 X34.", "G01 X34.")
    assert "a tool change under nose radius compensation: G40 first" in missing
    assert "G28 under nose radius compensation: G40 first" in missing
    assert "two blocks without a move under nose radius compensation: the control cannot see the next move (it may " \
           "cut into the part)" in found("G01 X24. Z-18. F0.15", "G01 X24. Z-18. F0.15\nG96 S400\nM08")
    assert "G42 without a move: write it on the G00 / G01 move onto the contour" in found(
        "G00 G42 X14.", "G42\nG00 X14.")


def test_compensation_with_a_tool_without_t(pin):
    job, machine, text, _ = pin
    finishing_tool(tip_direction=None)
    found = messages(simulate(job, machine, text))
    assert "G42 with a tool that has no nose radius and tip direction T, or is not a turning tool" in found


def test_a_tip_direction_the_simulation_does_not_know(pin):
    job, machine, text, _ = pin
    finishing_tool(tip_direction=8)
    found = messages(simulate(job, machine, text))
    assert "tip direction T8: only T0 to T4 and T9 are simulated, the nose of this tool is not checked" in found


def test_the_tip_and_the_centre():
    assert nose.centre_of_tip(0.0, 10.0, 0.4, 3) == (0.4, 10.4)  # an outside tool: the centre above, to Z+
    assert nose.centre_of_tip(0.0, 10.0, 0.4, 2) == (0.4, 9.6)  # a boring bar: below, to Z+
    assert nose.centre_of_tip(0.0, 10.0, 0.4, 0) == (0.0, 10.0)


def test_corners_of_a_compensated_path():
    moves = [nose.Move(1, "rapid", (2.0, 20.0), (2.0, 10.0)),  # start-up
             nose.Move(2, "feed", (2.0, 10.0), (-10.0, 10.0)),  # along Ø20 towards the chuck
             nose.Move(3, "feed", (-10.0, 10.0), (-10.0, 15.0)),  # up the shoulder: an inner corner
             nose.Move(4, "feed", (-10.0, 15.0), (-20.0, 15.0)),  # along Ø30: an outer corner before it
             nose.Move(5, "feed", (-20.0, 15.0), (-20.0, 17.0))]  # cancel
    paths, errors = nose.compensated_paths(moves, "G42", 0.4, 3, cancelled=True)
    assert errors == []
    assert paths[1] == [(2.4, 20.4), (2.0, 10.4)]  # type A: square to the next move at its start
    assert paths[2][-1] == pytest.approx((-9.6, 10.4))  # the inner corner: the two offsets' intersection
    assert paths[3][:2] == [pytest.approx((-9.6, 10.4)), pytest.approx((-9.6, 15.0))]  # up the shoulder
    arc = paths[3][2:]  # then round the outer corner Ø30 / shoulder on an arc of rε
    assert arc[-1] == pytest.approx((-10.0, 15.4)) and len(arc) == 90
    assert all(abs((z + 10) ** 2 + (r - 15) ** 2 - 0.16) < 1e-9 for z, r in arc)
    assert paths[4][0] == pytest.approx((-10.0, 15.4))
    assert paths[5] == [pytest.approx((-20.0, 15.4)), (-19.6, 17.4)]  # the cancel: to the tip's centre


def test_a_slot_narrower_than_the_nose_is_an_interference():
    moves = [nose.Move(1, "rapid", (2.0, 20.0), (2.0, 10.0)),
             nose.Move(2, "feed", (2.0, 10.0), (0.0, 10.0)),
             nose.Move(3, "feed", (0.0, 10.0), (0.0, 8.0)),  # down into a slot 0.5 wide
             nose.Move(4, "feed", (0.0, 8.0), (-0.5, 8.0)),
             nose.Move(5, "feed", (-0.5, 8.0), (-0.5, 10.0)),
             nose.Move(6, "feed", (-0.5, 10.0), (-0.5, 12.0))]
    _, errors = nose.compensated_paths(moves, "G42", 0.4, 3, cancelled=True)
    assert errors and errors[0][0] == 5 and "does not fit" in errors[0][1]


def test_a_concave_arc_smaller_than_the_nose_is_an_interference():
    moves = [nose.Move(1, "rapid", (2.0, 20.0), (2.0, 10.0)),
             nose.Move(2, "feed", (2.0, 10.0), (0.0, 10.0)),
             nose.Move(3, "arc", (0.0, 10.0), (-0.6, 10.0), (-0.3, 10.0, 0.3, True)),  # a groove R0.3 into Ø20
             nose.Move(4, "feed", (-0.6, 10.0), (-5.0, 10.0)),
             nose.Move(5, "feed", (-5.0, 10.0), (-5.0, 12.0))]
    _, errors = nose.compensated_paths(moves, "G42", 0.4, 3, cancelled=True)
    assert (3, "the arc R0.3 is smaller than the nose radius 0.4 on the tool's side: the nose does not fit (an "
               "interference)") in errors
