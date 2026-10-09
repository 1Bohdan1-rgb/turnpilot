"""Roadmap stage 6, commit 6: the simulation reads the printed text and catches what must not reach the machine."""
import pytest
from gcode_jobs import make_job, ready_machine

from turnpilot import services
from turnpilot.gcode import fanuc
from turnpilot.models import Feature


@pytest.fixture
def pin(app):
    machine = ready_machine()
    job = make_job()
    readiness, program = services.gcode_program(job, machine)
    return job, machine, fanuc.render(program)


def run(pin, text=None):
    job, machine, original = pin
    return services.gcode_simulation(job, machine, text if text is not None else original)


def replace(text, old, new, count=1):
    assert old in text, old
    return text.replace(old, new, count)


def messages(result):
    return [m for _, m in result.errors]


def test_the_generated_pin_is_clean(pin):
    result = run(pin)
    assert result.ok, result.errors
    assert [m for _, m in result.warnings] == [
        "parting to the axis in G96: the spindle runs up to G50 S3500; the operator may switch to G97"]
    assert result.leftover == []
    kinds = {s.kind for s in result.segments}
    assert kinds == {"rapid", "feed"}


def test_a_rapid_into_the_material(pin):
    text = replace(pin[2], "G00 X30.8\nG01 Z-63. F0.3", "G00 X30.8\nG00 Z-63.\nG01 Z-63. F0.3")
    assert "G00 through material" in messages(run(pin, text))


def test_a_rapid_whose_axes_may_move_one_after_the_other(pin):
    # from Z3. over the part: straight to X30. Z-40. would cut the corner of the Ø32 bar
    text = replace(pin[2], "G00 X30.8\nG01 Z-63. F0.3", "G00 X30.8 Z-40.\nG01 Z-63. F0.3")
    assert any(m.startswith("G00 through material") for m in messages(run(pin, text)))


def test_a_cut_below_the_finished_profile(pin):
    text = replace(pin[2], "G01 X20. Z-15. F0.15", "G01 X19. Z-15. F0.15")
    assert any(m.startswith("cuts below the finished profile at Z-") and "(D19." in m for m in messages(run(pin, text)))


def test_the_jaws(pin):
    text = replace(pin[2], "G01 Z-63. F0.3", "G01 Z-71. F0.3")  # jaws at Z-75, safety 5
    assert "closer than 5 mm to the jaws (Z-71., jaws at Z-75.)" in messages(run(pin, text))


def test_below_the_axis(pin):
    text = replace(pin[2], "G01 X0. F0.1", "G01 X-2. F0.1")  # parting past the axis
    assert "X-2. below the axis" in messages(run(pin, text))
    assert "X-0.8 below the axis" not in messages(run(pin))  # facing past it by the operator's overshoot


def test_spindle_rules(pin):
    text = replace(pin[2], "G50 S3500\nG96 S380 M04", "G96 S380 M04")
    assert "G96 without G50 (no spindle limit)" in messages(run(pin, text))
    text = replace(pin[2], "G50 S3500", "G50 S9000")
    assert "G50 S9000 above the machine's max 3500" in messages(run(pin, text))
    text = replace(pin[2], "G96 S380 M04", "G96 S380 M03")
    assert "M03: the operator's direction for a right-hand tool is M04" in messages(run(pin, text))
    text = replace(pin[2], "G96 S380 M04", "G96 S380")
    assert "a cutting move with the spindle stopped" in messages(run(pin, text))


def test_a_tool_change_away_from_the_reference_point(pin):
    text = replace(pin[2], "G28 U0.\nG28 W0.\nT0202", "T0202")
    assert "tool change away from the reference point (G28 U0. then G28 W0. first)" in messages(run(pin, text))


def test_a_value_without_a_decimal_point_stops_the_simulation(pin):
    result = run(pin, replace(pin[2], "G00 X30.8", "G00 X308"))
    assert any(m == "X308 without a decimal point (Fanuc reads it in µm)" for m in messages(result))
    assert result.segments == []


def test_material_left_where_an_operation_is_not_in_the_program(app):
    machine = ready_machine()
    features = [Feature(type="face"), Feature(type="od_turn", diameter=20, length=10),
                Feature(type="taper", start_diameter=20, diameter=26, length=6),
                Feature(type="od_turn", diameter=26, length=10), Feature(type="parting")]
    job = make_job(features, blank=30, length=30)
    readiness, program = services.gcode_program(job, machine)
    result = services.gcode_simulation(job, machine, fanuc.render(program))
    assert result.ok, result.errors
    (z0, z1, excess), = result.leftover  # the taper: manual in the planner, roughed only to its allowance
    assert z0 == pytest.approx(-10.01) and z1 > -16.01 and excess > 0.4


def test_drilling_in_g96_and_threading_in_g96(app):
    machine = ready_machine()
    features = [Feature(type="face"), Feature(type="bore", diameter=10, length=20, tolerance="H12"),
                Feature(type="od_turn", diameter=24, length=27),
                Feature(type="thread", diameter=24, length=27, pitch=1.5, location="external"),
                Feature(type="groove", diameter=20.5, start_diameter=24, length=3),
                Feature(type="od_turn", diameter=30, length=20), Feature(type="parting")]
    job = make_job(features)
    text = fanuc.render(services.gcode_program(job, machine)[1])
    clean = services.gcode_simulation(job, machine, text)
    assert clean.ok, clean.errors
    assert any(m.startswith("n·P") for _, m in clean.warnings)
    op = next(o for o in job.current_operations if o.tool_type == "drilling")
    drill = replace(text, f"G97 S{op.n} M04", "G96 S125 M04")
    assert "G96 while drilling on the axis: the spindle runs up to G50 (use G97)" in messages(
        services.gcode_simulation(job, machine, drill))
    thread = next(o for o in job.current_operations if o.tool_type == "threading")
    wrong = replace(text, f"G97 S{thread.n} M04", "G96 S195 M04")
    assert "G92 outside G97 (a thread needs a fixed spindle speed)" in messages(
        services.gcode_simulation(job, machine, wrong))


def test_threading_above_the_machine_limit(app):
    machine = ready_machine(max_thread_feed=1000)
    features = [Feature(type="od_turn", diameter=24, length=27),
                Feature(type="thread", diameter=24, length=27, pitch=1.5, location="external"),
                Feature(type="groove", diameter=20.5, start_diameter=24, length=3),
                Feature(type="od_turn", diameter=30, length=20)]
    job = make_job(features)
    op = next(o for o in job.current_operations if o.tool_type == "threading")
    text = fanuc.render(services.gcode_program(job, machine)[1])
    assert services.gcode_simulation(job, machine, text).ok  # the planner kept n·P within 1000
    fast = replace(text, f"G97 S{op.n} M04", "G97 S1000 M04")
    assert "n·P 1500 mm/min above the machine's threading limit 1000" in messages(
        services.gcode_simulation(job, machine, fast))
