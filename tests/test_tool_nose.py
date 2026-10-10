"""Arcs G02/G03, commit 5: a tool's nose radius rε (the insert code's, or the operator's) and its tip direction T."""
from gcode_jobs import make_job, ready_machine

from turnpilot import planner, services
from turnpilot.models import Tool, db


def finishing_tool():
    return db.session.execute(db.select(Tool).filter_by(insert_code="DNMG150604-PF")).scalar_one()


def form(tool, **changes):
    data = {"name": tool.name, "iso_group": "P", "vc_min": tool.vc_min, "vc_max": tool.vc_max, "f_min": tool.f_min,
            "f_max": tool.f_max, "ap_min": tool.ap_min, "ap_max": tool.ap_max}
    data.update(changes)
    return data


def test_nose_radius_from_the_insert_code():
    assert planner.insert_nose_radius("DNMG150604-PF") == 0.4
    assert planner.insert_nose_radius("CNMG120408-PM") == 0.8
    assert planner.insert_nose_radius("CNMG 12 04 08-PM") == 0.8  # written with spaces
    assert planner.insert_nose_radius("CNMG 120408") == 0.8
    assert planner.insert_nose_radius("CCMT09T304-PF") == 0.4  # thickness T3
    assert planner.insert_nose_radius("CCMT 09 T3 04-PM") == 0.4
    assert planner.insert_nose_radius("CNMG 12 04 00") is None  # a sharp corner: no rε
    assert planner.insert_nose_radius("N123G2 0300 0003-GM") is None  # spaces do not make a grooving code an insert
    assert planner.insert_nose_radius("860.1-0600-016A0-GM") is None  # a drill
    assert planner.insert_nose_radius("N123G2-0300-0003-GM") is None  # a grooving insert: no rε in its code


def test_the_operator_sets_rε_and_t(app, client):
    tool = finishing_tool()
    client.post(f"/tools/{tool.id}/edit", data=form(tool, nose_radius="0.8", tip_direction="3"))
    assert (tool.nose_radius, tool.tip_direction) == (0.8, 3)
    assert "T3" in client.get("/machine").get_data(as_text=True)
    response = client.post(f"/tools/{tool.id}/edit", data=form(tool, tip_direction="12"), follow_redirects=True)
    assert "Tip direction T: 0 to 9" in response.get_data(as_text=True)


def test_the_operators_rε_wins_over_the_insert_code(app):
    machine = ready_machine()
    tool = finishing_tool()
    job = make_job()
    _, _, _, ops = services.gcode_inputs(job, machine)
    finish = next(op for op in ops if op.tool_type == "turning_finish")
    assert (finish.nose_radius, finish.tip_direction) == (0.4, None)
    tool.nose_radius, tool.tip_direction = 0.8, 3
    db.session.commit()
    _, _, _, ops = services.gcode_inputs(job, machine)
    finish = next(op for op in ops if op.tool_type == "turning_finish")
    assert (finish.nose_radius, finish.tip_direction) == (0.8, 3)
    assert planner.tool_nose_radius(services.tool_spec(tool)) == 0.8  # the finishing feed from Ra takes it too
