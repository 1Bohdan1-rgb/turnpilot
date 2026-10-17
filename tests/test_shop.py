"""The shop page (stage 7.1): the machines on a plan, each with the operator's status and the order running; the
panel shows the order's operations and the time left. Everything by hand: nothing goes to or from the machines."""
from gcode_jobs import make_job, ready_machine

from turnpilot import services
from turnpilot.models import Feature, Machine, db

NEW = {"name": "Lathe 2", "max_rpm": "3000", "power_kw": "7.5", "max_diameter": "250", "turret_positions": "8"}


def test_place_status_and_the_running_order_on_the_plan(app, client):
    machine = ready_machine()
    job = make_job([Feature(type="face"), Feature(type="od_turn", diameter=20, length=24), Feature(type="parting")],
                   blank=22, length=29, stickout=35, machine=machine)
    services.generate_gcode(job, machine)
    client.post("/machines", data=NEW)
    lathe2 = db.session.execute(db.select(Machine).filter_by(name="Lathe 2")).scalar_one()
    page = client.get("/shop").get_data(as_text=True)
    assert "Not placed yet" in page and "no order running" in page
    client.post(f"/shop/machines/{machine.id}/place", data={"row": "1", "col": "1"})
    client.post(f"/shop/machines/{lathe2.id}/place", data={"row": "1", "col": "2"})
    client.post(f"/shop/machines/{lathe2.id}/place", data={"row": "1", "col": "1"})  # a taken cell: the two swap
    assert (lathe2.shop_row, lathe2.shop_col, machine.shop_col) == (1, 1, 2)
    response = client.post(f"/shop/machines/{machine.id}/place", data={"row": "25", "col": "1"},
                           headers={"X-Requested-With": "fetch"})
    assert response.status_code == 400 and "row 1 to 20" in response.get_json()["error"]
    assert client.post(f"/shop/machines/{machine.id}/status", data={"status": "running"},
                       follow_redirects=True).get_data(as_text=True).count("name is required") == 1
    client.post(f"/shop/machines/{machine.id}/status", data={"status": "running", "decided_by": "Bohdan"})
    client.post(f"/shop/machines/{machine.id}/status", data={"status": "alarm", "decided_by": "Bohdan",
                                                             "note": "door"})
    assert [s.status for s in machine.statuses] == ["running", "alarm"] and machine.status.note == "door"
    order = services.create_order(job, machine, 40, "Bohdan")
    services.make_order_current(order, "Bohdan")
    services.set_order_done(order, 10, "Bohdan")
    page = client.get("/shop").get_data(as_text=True)
    assert "status-alarm" in page and f"{job.name} · 10/40" in page and "(incomplete)" in page
    panel = client.get(f"/shop/machines/{machine.id}?fragment=1").get_data(as_text=True)
    assert "<html" not in panel and f"Order {order.id}" in panel and "time incomplete" in panel
    assert "done 10 of 40" in panel and "Set the status" in panel
