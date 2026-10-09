"""Arcs G02/G03, commit 3: printing G02 / G03 with I / K, reading them back, simulating them, drawing them."""
import pytest
from gcode_jobs import make_job, ready_machine

from turnpilot import services
from turnpilot.gcode import fanuc
from turnpilot.gcode.program import ArcMove, arc_direction
from turnpilot.gcode.svg import render
from turnpilot.models import Feature


def test_direction_seen_with_z_right_and_x_up():
    # a convex sphere from its apex (Z0, X0) to Ø20 at Z-10, centre on the axis at Z-10: counter-clockwise
    assert arc_direction(0, 0, -10, 10, -10, 0) is False
    # a concave arc from Ø30 down and up again towards the chuck, centre above it: clockwise
    assert arc_direction(-10, 15, -27.32, 15, -18.66, 20) is True


def test_print_and_read_back():
    lines = fanuc._command(ArcMove(20, -10, 0, -10, clockwise=False, f=0.15))
    assert lines == ["G03 X20. Z-10. I0. K-10. F0.15"]
    assert fanuc._command(ArcMove(30, -27.32, 5, -8.66, clockwise=True)) == ["G02 X30. Z-27.32 I5. K-8.66"]
    parsed = fanuc.parse("%\nG03 X20. Z-10. I0. K-10. F0.15\nG02 X30. Z-5. R5.\n%\n")
    assert parsed.ok and parsed.lines[0].g == [3] and parsed.lines[0].words["K"] == -10
    assert (2, "K-10 without a decimal point (Fanuc reads it in µm)") in fanuc.parse("%\nG03 X20. Z-10. I0. K-10\n%\n").errors


def sphere_job():
    """Ø20 L5 then a convex sphere R10 at the free end (drawn on the right): Ø20.4 bar."""
    return make_job([Feature(type="od_turn", diameter=20, length=5),
                     Feature(type="arc", start_diameter=20, length=10, radius=10, arc_convex=True)],
                    free_end="right", blank=20.4, length=30, stickout=30, face_stock=0.0)


PROGRAM = """%
O0001 (SPHERE)
G21 G18 G40 G99
N1 (T04 FINISH)
G28 U0.
G28 W0.
T0404
G50 S3500
G96 S300 M04
G00 X22. Z2.
G00 X0.
G01 Z0. F0.15
{arc}
G01 Z-15.
G01 X22.
G00 Z2.
M05
G28 U0.
G28 W0.
M30
%
"""


@pytest.mark.parametrize("arc, below", [
    ("G03 X20. Z-10. I0. K-10.", False),
    ("G03 X20. Z-10. R10.", False),
    ("G02 X20. Z-10. I10. K0.", True),  # the wrong way round: through the sphere
])
def test_simulated_arcs(app, arc, below):
    machine = ready_machine()
    job = sphere_job()
    result = services.gcode_simulation(job, machine, PROGRAM.format(arc=arc))
    messages = [m for _, m in result.errors]
    assert any(m.startswith("cuts below the finished profile") for m in messages) is below
    assert not any("off its circle" in m for m in messages)
    arcs = [s for s in result.segments if s.kind == "arc"]
    assert len(arcs) == 1 and arcs[0].arc[2] == pytest.approx(10)


def test_an_arc_whose_end_is_off_its_circle(app):
    machine = ready_machine()
    result = services.gcode_simulation(sphere_job(), machine, PROGRAM.format(arc="G03 X20. Z-10. I0. K-10.5"))
    assert any(m == "the arc's end is off its circle: R10.500 at the start, R10.012 at the end"
               for _, m in result.errors)


def test_arcs_are_drawn_as_arcs():
    svg = render([["arc", 0, 0, 20, -10, 5, 4, [-10, 0, 10, False]], ["feed", 20, -10, 20, -15, 6, 4]], [], 0, 0.1,
                 [], 10.2, 30, 5)
    assert ' A ' in svg and '<path class="mv feed"' in svg and '<line class="mv feed"' in svg
