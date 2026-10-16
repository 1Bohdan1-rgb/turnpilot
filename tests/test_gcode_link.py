"""The G-code link is on the job page and the process sheet whatever the job's state: the G-code page says what
blocks a program."""
from gcode_jobs import make_job, ready_machine

from turnpilot.models import Feature, db


def test_the_gcode_link_is_always_shown(app, client):
    job = make_job([Feature(type="face"), Feature(type="od_turn", diameter=20, length=24)],
                   blank=22, length=29, stickout=35, machine=ready_machine())
    job.axial_order_known = False
    db.session.commit()
    link = f'href="/jobs/{job.id}/gcode"'
    assert link in client.get(f"/jobs/{job.id}").get_data(as_text=True)
    assert link in client.get(f"/jobs/{job.id}/operations").get_data(as_text=True)
    assert "order along the axis is not known" in client.get(f"/jobs/{job.id}/gcode").get_data(as_text=True)


def test_the_unconfirmed_data_note_only_when_an_operation_has_it(app, client):
    job = make_job([Feature(type="face"), Feature(type="od_turn", diameter=20, length=24)],
                   blank=22, length=29, stickout=35, machine=ready_machine())
    with_tool = [op for op in job.current_operations if op.tool_id]
    assert all(op.cutting_data_origin in ("catalogue", "operator") for op in with_tool)
    page = client.get(f"/jobs/{job.id}/operations").get_data(as_text=True)
    assert "own ranges" not in page and "placeholder tool ranges" not in page
    with_tool[0].cutting_data_origin = "tool"
    db.session.commit()
    page = client.get(f"/jobs/{job.id}/operations").get_data(as_text=True)
    assert f"1 operation(s) ({with_tool[0].sequence})" in page and "the tool's own ranges" in page
