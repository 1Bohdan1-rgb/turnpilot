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
