"""Arcs G02/G03, commit 6: nose radius compensation G42 in the finishing contour, when the finishing tool's rε and
tip direction T are both known; the offset page's R and T in the header and in the operator's checklist."""
from gcode_jobs import make_job, ready_machine
from test_gcode_arc_program import sphere_job
from test_gcode_page import CHECKS, generate

from turnpilot import services
from turnpilot.gcode import fanuc
from turnpilot.models import Feature, Tool, db


def finishing_tool(tip_direction=3, **changes):
    tool = db.session.execute(db.select(Tool).filter_by(insert_code="DNMG150604-PF")).scalar_one()
    tool.tip_direction = tip_direction
    for name, value in changes.items():
        setattr(tool, name, value)
    db.session.commit()
    return tool


def program(job, machine):
    readiness, prog = services.gcode_program(job, machine)
    assert readiness.ok, readiness.blockers
    text = fanuc.render(prog)
    return prog, text, services.gcode_simulation(job, machine, text)


def finishing_lines(text):
    lines = text.splitlines()
    start = next(i for i, line in enumerate(lines) if line == "T0404")
    return lines[start:lines.index("M01", start)]


def test_the_finishing_contour_with_g42(app):
    machine = ready_machine()
    finishing_tool()
    prog, text, result = program(make_job(), machine)
    assert prog.compensation == [(4, "Finish turning DNMG (P)", 0.4, 3)]
    assert "(TOOL TIP: IMAGINARY POINT)\n(FINISHING CONTOUR: NOSE RADIUS COMPENSATION G42)" in text
    assert "(OFFSET PAGE T04 FINISH TURNING DNMG P: R0.4 T3)" in text
    assert "NO NOSE RADIUS COMPENSATION" not in text
    lines = finishing_lines(text)
    # G42 on the move onto the contour (the chamfer's line), G40 on the move off it
    assert lines[lines.index("G00 Z2.") + 1:][:2] == ["G00 G42 X14.", "G01 X18. Z0. F0.15"]
    assert lines[-2:] == ["G01 G40 X34.", "G00 Z3."]
    assert sum("G42" in line for line in lines) == 1 and sum("G40" in line for line in lines) == 1
    assert sum("G42" in line for line in text.splitlines() if not line.startswith("(")) == 1  # roughing: none
    finish = next(b for b in prog.blocks if b.tool_type == "turning_finish")
    assert not any("no nose radius compensation" in w for w in finish.warnings)  # chamfers as drawn: no check
    assert "nose radius compensation G42: the contour as drawn (offset R0.4 T3)" in finish.notes
    assert result.ok and result.complete, (result.errors, result.incomplete)


def test_without_t_no_compensation_and_the_checks_stay(app):
    machine = ready_machine()
    finishing_tool(tip_direction=None)
    prog, text, result = program(make_job(), machine)
    assert prog.compensation == []
    assert "(TOOL TIP: IMAGINARY POINT, NO NOSE RADIUS COMPENSATION)" in text
    assert "G42" not in text and "G01 G40" not in text
    finish = next(b for b in prog.blocks if b.tool_type == "turning_finish")
    assert any(w.startswith("no nose radius compensation: 45° chamfers come out about 0.166 mm") for w in
               finish.warnings)


def test_without_a_nose_radius_no_compensation(app):
    machine = ready_machine()
    finishing_tool(insert_code="DNMG-PF")  # no rε in the code and none from the operator
    prog, text, _ = program(make_job(), machine)
    assert prog.compensation == [] and "G42" not in text


def test_the_spherical_end_is_entered_along_the_face(app):
    machine = ready_machine()
    finishing_tool()
    prog, text, result = program(sphere_job(), machine)
    lines = finishing_lines(text)
    # down beside the face (2 × (clearance 1 + rε 0.4)), onto Z0, then the start-up along the face to X0 Z0: the
    # nose ends it on the axis, square to the sphere; no X below the axis
    entry = lines.index("G00 Z2.")
    assert lines[entry:entry + 5] == ["G00 Z2.", "G00 X2.8", "G01 Z0. F0.15", "G01 G42 X0.",
                                      "G03 X20. Z-10. I0. K-10. F0.15"]
    assert lines[entry + 5] == "G01 X20. Z-15. F0.15"  # no zero-length move after the arc
    assert not any("X-" in line for line in lines)
    finish = next(b for b in prog.blocks if b.tool_type == "turning_finish")
    assert not any("no nose radius compensation" in w for w in finish.warnings)
    assert result.ok and result.complete, (result.errors, result.incomplete)


def test_a_contour_broken_by_a_descent_cancels_and_starts_again(app):
    machine = ready_machine()
    finishing_tool()
    job = make_job([Feature(type="od_turn", diameter=30, length=10),
                    Feature(type="arc", start_diameter=30, diameter=30, length=17.32, radius=10, arc_convex=False),
                    Feature(type="od_turn", diameter=30, length=10)], blank=32, length=50, stickout=50)
    _, text, _ = program(job, machine)
    lines = finishing_lines(text)
    g42 = [line for line in lines if "G42" in line]
    g40 = [line for line in lines if "G40" in line]
    assert len(g42) == 2 and len(g40) == 2  # off at the arc it may not go down into, on again after it
    assert lines.index(g40[0]) < lines.index(g42[1])


def test_the_checklist_asks_for_the_offset_page(app, client):
    machine = ready_machine()
    finishing_tool()
    record = generate(client, make_job())
    items = dict(services.gcode_checklist(record))
    assert "T04 R0.4 T3" in items["nose_compensation"]
    page = client.get(f"/gcode/{record.id}").get_data(as_text=True)
    assert 'name="check-nose_compensation"' in page
    response = client.post(f"/gcode/{record.id}/ready", data={**CHECKS, "confirmed_by": "Operator I."},
                           follow_redirects=True)
    assert "not every item of the checklist is ticked" in response.get_data(as_text=True)
    client.post(f"/gcode/{record.id}/ready",
                data={**CHECKS, "check-nose_compensation": "1", "confirmed_by": "Operator I."})
    assert record.status == "ready"


def test_the_checklist_without_compensation_is_the_usual_one(app, client):
    ready_machine()
    record = generate(client, make_job())
    assert services.gcode_checklist(record) == services.GCODE_CHECKLIST


def test_fanuc_reads_g41_g42_back():
    parsed = fanuc.parse("%\nG00 G42 X14.\nG01 G41 X0.\nG01 G40 X34.\nM30\n%\n")
    assert parsed.ok, parsed.errors
