"""Orders (stage 7.1): a job, a quantity, a machine; operations and time from the job's program for that machine;
the operator records parts done and operations done by hand, with a name. Nothing goes to or from the machine."""
from gcode_jobs import make_job, ready_machine

from turnpilot import services
from turnpilot.models import Feature, Machine, Order, db

NEW = {"name": "Lathe 2", "max_rpm": "3000", "power_kw": "7.5", "max_diameter": "250", "turret_positions": "8"}


def stud(machine):
    return make_job([Feature(type="face"), Feature(type="od_turn", diameter=20, length=24), Feature(type="parting")],
                    blank=22, length=29, stickout=35, machine=machine)


def test_an_order_takes_the_program_for_its_machine_and_counts_the_time_left(app, client):
    machine = ready_machine()
    job = stud(machine)
    _, record = services.generate_gcode(job, machine)
    response = client.post(f"/jobs/{job.id}/orders", data={"machine_id": machine.id, "quantity": "50"},
                           follow_redirects=True)
    assert "name is required" in response.get_data(as_text=True)
    client.post(f"/jobs/{job.id}/orders", data={"machine_id": machine.id, "quantity": "50", "created_by": "Bohdan"})
    order = db.session.execute(db.select(Order)).scalar_one()
    assert (order.program, order.quantity, order.done, order.created_by) == (record, 50, 0, "Bohdan")
    progress = services.order_progress(order)
    assert progress["blocks"] and not progress["complete"]  # no rapid rates / tool change time: incomplete
    assert progress["left_s"] == 50 * progress["part_s"]
    client.post(f"/orders/{order.id}/done", data={"done": "20", "decided_by": "Bohdan"})
    first = progress["blocks"][0]
    client.post(f"/orders/{order.id}/mark", data={"block": first["number"], "title": first["title"], "done": "1",
                                                   "decided_by": "Ivan"})
    progress = services.order_progress(order)
    assert order.done == 20 and progress["left_s"] == 30 * progress["part_s"]
    assert progress["blocks"][0]["done"] and order.events[-1].kind == "done"
    page = client.get(f"/orders/{order.id}").get_data(as_text=True)
    assert "time incomplete" in page and "done 20 of 50" in page
    assert client.post(f"/orders/{order.id}/done", data={"done": "51", "decided_by": "Bohdan"},
                       follow_redirects=True).get_data(as_text=True).count("Parts done: 0 to 50") == 1


def test_one_job_on_two_machines_and_one_running_order_per_machine(app, client):
    machine = ready_machine()
    job = stud(machine)
    services.generate_gcode(job, machine)
    client.post("/machines", data=NEW)
    lathe2 = db.session.execute(db.select(Machine).filter_by(name="Lathe 2")).scalar_one()
    a = services.create_order(job, machine, 10, "Bohdan")
    b = services.create_order(job, lathe2, 5, "Bohdan")
    c = services.create_order(job, machine, 3, "Bohdan")
    assert b.program is None and "No program made for Lathe 2" in client.get(f"/orders/{b.id}").get_data(as_text=True)
    services.make_order_current(a, "Bohdan")
    services.make_order_current(b, "Bohdan")
    services.make_order_current(c, "Bohdan")
    assert (a.is_current, b.is_current, c.is_current) == (False, True, True)
    services.close_order(c, "Bohdan")
    assert (c.status, c.is_current) == ("closed", False)
