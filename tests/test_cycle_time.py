"""The cycle time estimate from a program's text: cutting feeds (G96 with the diameter), rapids per axis, G92 passes,
dwells and tool changes; the machine's rapid traverse and tool change time from the passport or the operator."""
import json

from turnpilot import passport_reader


def test_the_rapid_and_tool_change_fields_are_read_from_the_passport(app, client):
    schema = json.dumps(passport_reader.RECORD_MACHINE_TOOL)
    assert all(name in schema for name in ("rapid_x_m_min", "rapid_z_m_min", "tool_change_s"))
    page = client.get("/machine").get_data(as_text=True)
    assert "Rapid traverse X" in page and "Rapid traverse Z" in page and "Tool change time" in page


import math

import pytest

from turnpilot.gcode.cycle_time import estimate


def program(*body):
    return "\n".join(["%", "O0001 (TEST)", "G21 G18 G40 G99", *body, "M30", "%"]) + "\n"


TOOL = ["G28 U0.", "G28 W0.", "T0101"]


def test_g97_turning_and_rapids_per_axis():
    # 20 mm at f 0.2, n 1000: 20 / (0.2 · 1000 / 60) = 6 s; rapids at 12 m/min = 200 mm/s
    text = program("N1 (T01 TURN)", *TOOL, "G97 S1000 M03", "G00 X24. Z4.", "G00 X20. Z2.",
                   "G01 Z-18. F0.2", "G00 X24.", "M01")
    t = estimate(text, 12, 12, 2.0)
    (op,) = t.operations
    assert op.number == 1 and op.cut_s == pytest.approx(6.0)
    # X24 Z4 -> X20 Z2: max(2 / 200, 2 / 200); X20 -> X24 at Z-18: 2 / 200 (the approach from G28 is not counted)
    assert op.rapid_s == pytest.approx(0.02) and (op.rapid_x_mm, op.rapid_z_mm) == (4.0, 2.0)
    assert (op.tool_changes, op.tool_change_s) == (1, 2.0) and t.complete
    assert t.total_s == pytest.approx(8.02)


def test_g96_facing_takes_n_from_the_diameter_up_to_g50():
    # X40 -> X0 at f 0.1, Vc 100, G50 S2000: n = 1000·Vc/(π·D) above Dc = 15.915, 2000 below it
    text = program("N1 (T01 FACE)", *TOOL, "G50 S2000", "G96 S100 M03", "G00 X40. Z4.", "G00 Z0.",
                   "G01 X0. F0.1", "M01")
    dc = 1000 * 100 / (math.pi * 2000)
    expected = 60 * math.pi / (2 * 0.1 * 1000 * 100) * (40 ** 2 - dc ** 2) / 2 + (dc / 2) / (0.1 * 2000 / 60)
    assert estimate(text, 12, 12, 2.0).operations[0].cut_s == pytest.approx(expected, rel=2e-3)


def test_g92_passes_dwell_and_unknown_machine_values():
    # 3 passes of 25 mm (Z5 -> Z-20) at n 1000, P 1.5: 1 s each; a 0.5 s dwell
    text = program("N2 (T07 THREAD)", *TOOL, "G97 S1000 M03", "G00 X24. Z5.", "G92 X19.5 Z-20. F1.5",
                   "G92 X19. Z-20. F1.5", "G92 X18.5 Z-20. F1.5", "G04 P500", "G00 X24.", "M01")
    t = estimate(text, None, None, None)
    (op,) = t.operations
    assert op.thread_s == pytest.approx(3.0) and op.dwell_s == 0.5
    assert op.rapid_s is None and op.tool_change_s is None and not t.complete
    assert op.rapid_z_mm == pytest.approx(3 * 25)  # back along Z after each pass (the thread goes forward)
    assert t.total_s == pytest.approx(3.5)


def test_an_arc_is_cut_along_its_length():
    # a quarter circle R10 (X0 Z0 -> X20 Z-10, centre X0 Z-10) at f 0.1, n 1000: 15.708 / (0.1 · 1000 / 60) s
    text = program("N1 (T04 ARC)", *TOOL, "G97 S1000 M03", "G00 X0. Z2.", "G01 Z0. F0.1",
                   "G03 X20. Z-10. I0. K-10. F0.1", "M01")
    assert estimate(text, 12, 12, 2.0).operations[0].cut_s == pytest.approx(
        2 / (0.1 * 1000 / 60) + (math.pi * 10 / 2) / (0.1 * 1000 / 60), rel=1e-3)
