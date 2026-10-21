"""The shop page (stages 7.1, 7.2): the plan from its file, the machines on it with the operator's status and the
order running; everything by hand, nothing goes to or from the machines."""
import io
import json
from pathlib import Path

from gcode_jobs import make_job, ready_machine

from turnpilot import services
from turnpilot.models import Feature, MachinePlacement, db

EXAMPLE = Path(__file__).resolve().parent.parent / "docs" / "shop_plan_example.json"


def upload_plan(client):
    client.post("/shop/plan", data={"plan": (io.BytesIO(EXAMPLE.read_bytes()), "shop.json"), "uploaded_by": "Bohdan"},
                content_type="multipart/form-data")


def machines_on(page):
    start = page.index("const M = ") + len("const M = ")
    return json.loads(page[start:page.index(";\n", start)])


def test_without_a_plan_the_page_says_so_and_lists_the_machines(app, client):
    machine = ready_machine()
    page = client.get("/shop").get_data(as_text=True)
    assert "No shop plan yet" in page and "<svg class=\"sf-plan\"" not in page
    (m,) = machines_on(page)
    assert (m["name"], m["x"], m["status"]) == (machine.name, None, "free")


def test_the_plan_and_a_machine_with_its_status_and_order(app, client):
    machine = ready_machine()
    upload_plan(client)
    job = make_job([Feature(type="face"), Feature(type="od_turn", diameter=20, length=24), Feature(type="parting")],
                   blank=22, length=29, stickout=35, machine=machine)
    services.generate_gcode(job, machine)
    order = services.create_order(job, machine, 50, "Bohdan")
    services.make_order_current(order, "Bohdan")
    services.set_order_done(order, 1, "Bohdan")
    services.set_machine_status(machine, "running", "Bohdan")
    db.session.add(MachinePlacement(machine_id=machine.id, x_mm=4000, y_mm=6800, rotation=0, placed_by="Bohdan"))
    db.session.commit()
    page = client.get("/shop").get_data(as_text=True)
    assert 'viewBox="-1500 -1500 75000 42200"' in page and "MAIN WALKWAY 2.4 m" in page and "p-column" in page
    (m,) = machines_on(page)
    assert (m["x"], m["y"], m["status"], m["status_by"], m["kind"], m["length"]) == (4000, 6800, "running", "Bohdan",
                                                                                      None, None)
    o = m["order"]
    assert (o["id"], o["done"], o["quantity"], o["incomplete"]) == (order.id, 1, 50, True)
    assert o["left"].count(":") == 2
    services.set_machine_status(machine, "alarm", "Bohdan", "door")
    (m,) = machines_on(client.get("/shop").get_data(as_text=True))
    assert (m["status"], m["alarm"], m["status_note"]) == ("stopped", True, "door")


def test_the_status_panel_of_7_1_still_opens_from_the_card(app, client):
    machine = ready_machine()
    panel = client.get(f"/shop/machines/{machine.id}?fragment=1").get_data(as_text=True)
    assert "<html" not in panel and "Set the status" in panel and "Place on the plan" not in panel
    client.post(f"/shop/machines/{machine.id}/status", data={"status": "running", "decided_by": "Bohdan"})
    assert machine.status.status == "running"


def test_the_card_shows_the_queue_and_the_time_left_of_all_orders(app, client):
    """Lathe 1: the stud running, НД 012-like part next; the queue lists every open order not running and the time
    left of all of them (incomplete while the passport's values are missing; an order without a program is named)."""
    machine = ready_machine()
    stud = make_job([Feature(type="face"), Feature(type="od_turn", diameter=20, length=24), Feature(type="parting")],
                    blank=22, length=29, stickout=35, machine=machine, name="Stud")
    pin = make_job([Feature(type="face"), Feature(type="od_turn", diameter=30, length=40), Feature(type="parting")],
                   blank=32, length=45, stickout=50, machine=machine, name="Pin")
    other = make_job([Feature(type="face"), Feature(type="od_turn", diameter=24, length=10)], blank=26, length=15,
                     stickout=20, machine=machine, name="Collar")
    services.generate_gcode(stud, machine)
    services.generate_gcode(pin, machine)
    running = services.create_order(stud, machine, 50, "Bohdan")
    services.make_order_current(running, "Bohdan")
    services.set_order_done(running, 10, "Bohdan")
    queued = services.create_order(pin, machine, 40, "Bohdan")
    services.create_order(other, machine, 5, "Bohdan")  # no program made for it
    (m,) = machines_on(client.get("/shop").get_data(as_text=True))
    assert [(q["job"], q["quantity"], q["no_program"]) for q in m["queue"]] == [("Pin", 40, False), ("Collar", 5, True)]
    left = services.order_progress(running)["left_s"] + services.order_progress(queued)["left_s"]
    assert m["all_left"] == services._clock(left) and m["all_incomplete"] and m["all_without_program"] == 1
    page = client.get("/shop").get_data(as_text=True)
    assert "<dt>Next</dt>" in page and "All orders left" in page
