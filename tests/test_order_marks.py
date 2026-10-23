"""The operator's marks on an order's operations count only on the same operation: after "Take the latest program"
a mark whose block N is another operation now is cancelled with the reason (history kept)."""
from gcode_jobs import make_job, ready_machine

from turnpilot import services
from turnpilot.models import db


def _blocks(order):
    return {b["number"]: b for b in services.order_progress(order)["blocks"]}


def test_a_mark_on_an_operation_that_changed_is_cancelled(client):
    machine = ready_machine()
    job = make_job(face_stock=1.0)
    services.generate_gcode(job, machine)
    order = services.create_order(job, machine, 10, "Bohdan")
    first, second = sorted(_blocks(order))[:2]
    services.mark_order_block(order, first, True, "Bohdan")
    services.mark_order_block(order, second, True, "Bohdan")
    old_title = _blocks(order)[first]["title"]
    job.face_stock_mm = 2.0  # the facing block's title changes (its stock); the next block's stays
    db.session.commit()
    services.generate_gcode(job, machine)
    response = client.post(f"/orders/{order.id}/program", data={"decided_by": "Ivan"}, follow_redirects=True)
    blocks = _blocks(order)
    assert blocks[first]["title"] != old_title and not blocks[first]["done"]
    assert blocks[second]["done"]  # the same operation: the mark stays
    assert f"Marks cancelled, since their operation is another one in it now: N{first}." in response.get_data(as_text=True)
    last = order.last_marks[first]
    assert (last.done, last.decided_by, last.title) == (False, "Ivan", old_title[:200])
    assert [e.kind for e in order.events][-2:] == ["program", "marks_cancelled"]


def test_a_mark_made_before_on_another_operation_does_not_count(client):
    """Marks left from before this rule: the progress and the 3D view check the title too."""
    machine = ready_machine()
    job = make_job(face_stock=1.0)
    services.generate_gcode(job, machine)
    order = services.create_order(job, machine, 10, "Bohdan")
    first = min(_blocks(order))
    services.mark_order_block(order, first, True, "Bohdan")
    job.face_stock_mm = 2.0
    db.session.commit()
    _, newer = services.generate_gcode(job, machine)
    order.program = newer  # taken without the check (as an older version did)
    db.session.commit()
    assert not _blocks(order)[first]["done"]
    assert services.order_part3d(order)["done"] == 0
