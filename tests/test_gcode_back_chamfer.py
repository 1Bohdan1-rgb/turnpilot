"""The operator's remarks on the Zavisa 36 pin: no finishing cut down towards the chuck steeper than the insert's
in-copying angle; the back chamfer with the parting insert's corner; parting past the axis; a dwell in grooves."""
from gcode_jobs import chamfer, make_job, ready_machine

from turnpilot import services
from turnpilot.gcode import fanuc
from turnpilot.gcode.program import Comment, Dwell, Feed, Rapid
from turnpilot.models import Feature, Tool, db


def back_chamfer_part(parting=True):
    features = [Feature(type="face"), Feature(type="od_turn", diameter=20, length=15),
                Feature(type="od_turn", diameter=30, length=25), chamfer(30, 2, "right")]
    return features + ([Feature(type="parting")] if parting else [])


def generate(job, machine):
    readiness, program = services.gcode_program(job, machine)
    assert readiness.ok, readiness.blockers
    text = fanuc.render(program)
    return program, text, services.gcode_simulation(job, machine, text)


def finish_tool(ramp):
    tool = db.session.execute(db.select(Tool).filter_by(insert_code="DNMG150604-PF")).scalar_one()
    tool.max_ramp_angle = ramp
    db.session.commit()


def test_the_back_chamfer_is_cut_with_the_parting_inserts_corner(app):
    machine = ready_machine()
    job = make_job(back_chamfer_part())
    program, text, result = generate(job, machine)
    finish = next(b for b in program.blocks if b.tool_type == "turning_finish")
    feeds = [(c.x, c.z) for c in finish.commands if isinstance(c, Feed)]
    assert (30.0, -38.0) in feeds and (26.0, -40.0) not in feeds  # stops where the chamfer starts
    assert "the tool's max in-copying angle is not set: not cut by this tool" in finish.warnings[0]
    parting = next(b for b in program.blocks if b.tool_type == "parting")
    assert parting.commands[2:11] == [
        Comment("BACK CHAMFER 2X45 WITH THE INSERT'S CORNER: SLOT FIRST, THEN ALONG THE CHAMFER"),
        Rapid(z=-40.0), Feed(x=26.0, f=0.1), Rapid(x=34.0),  # the slot down to the chamfer's foot
        Rapid(z=-37.0), Rapid(x=32.0), Feed(x=26.0, z=-40.0, f=0.1), Rapid(x=34.0),  # along its line from +1
        Comment("PARTING PAST THE AXIS TO X-0.5 IN G96: SPINDLE RUNS UP TO G50 S3500 - OPERATOR MAY SWITCH TO G97")]
    assert program.warnings == ["coolant not known for this machine: M08 not written; the catalogue's Vc are with "
                                "coolant: check"]
    assert result.ok and result.complete, (result.errors, result.incomplete)


def test_a_finishing_tool_allowed_to_go_down_cuts_the_chamfer_itself(app):
    machine = ready_machine()
    finish_tool(50)
    job = make_job(back_chamfer_part())
    program, text, result = generate(job, machine)
    finish = next(b for b in program.blocks if b.tool_type == "turning_finish")
    assert (26.0, -40.0) in [(c.x, c.z) for c in finish.commands if isinstance(c, Feed)]
    parting = next(b for b in program.blocks if b.tool_type == "parting")
    assert not any(isinstance(c, Comment) and "BACK CHAMFER" in c.text for c in parting.commands)
    assert result.ok and result.complete
    finish_tool(30)  # the same program read for a holder that allows 30° only
    result = services.gcode_simulation(job, machine, text)
    assert any(m == "goes down at 45° towards the chuck, above the tool's max in-copying angle 30°"
               for _, m in result.errors)


def test_without_a_parting_operation_the_chamfer_is_left_and_said_so(app):
    machine = ready_machine()
    job = make_job(back_chamfer_part(parting=False))
    program, text, result = generate(job, machine)
    assert any(w.startswith("Z-38 to Z-40 (D30 to D26, 45° down towards the chuck) is not cut") for w in program.warnings)
    assert not result.complete and result.incomplete[0].startswith("PART NOT COMPLETE")


def test_a_cut_down_in_material_without_the_angle_is_an_error(app):
    machine = ready_machine()
    finish_tool(50)
    job = make_job(back_chamfer_part())
    _, text, _ = generate(job, machine)
    finish_tool(None)
    result = services.gcode_simulation(job, machine, text)
    assert any(m == "goes down at 45° towards the chuck in material: the tool's max in-copying angle is not set"
               for _, m in result.errors)


def test_a_dwell_at_the_grooves_bottom(app):
    machine = ready_machine(groove_dwell_s=0.5)
    job = make_job()
    program, text, result = generate(job, machine)
    groove = next(b for b in program.blocks if b.tool_type == "grooving")
    assert Dwell(0.5) in groove.commands and "G04 P500" in text
    assert result.ok


def test_the_ramp_angle_on_the_tool_form(app, client):
    tool = db.session.execute(db.select(Tool).filter_by(insert_code="DNMG150604-PF")).scalar_one()
    page = client.get(f"/tools/{tool.id}/edit").get_data(as_text=True)
    assert 'name="max_ramp_angle"' in page
    form = {"name": tool.name, "iso_group": "P", "vc_min": tool.vc_min, "vc_max": tool.vc_max, "f_min": tool.f_min,
            "f_max": tool.f_max, "ap_min": tool.ap_min, "ap_max": tool.ap_max, "max_ramp_angle": "27"}
    client.post(f"/tools/{tool.id}/edit", data=form)
    assert db.session.get(Tool, tool.id).max_ramp_angle == 27
    response = client.post(f"/tools/{tool.id}/edit", data={**form, "max_ramp_angle": "95"}, follow_redirects=True)
    assert "below 90" in response.get_data(as_text=True)
