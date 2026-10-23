"""A machine set "running" with no order running on it is pointed out; closing the running order names the other
open ones and switches nothing by itself."""
from gcode_jobs import make_job, ready_machine

from turnpilot import services


def test_closing_the_running_order_names_the_others_and_the_machine_is_flagged(client):
    machine = ready_machine()
    job = make_job()
    services.generate_gcode(job, machine)
    first = services.create_order(job, machine, 10, "Bohdan")
    second = services.create_order(job, machine, 5, "Bohdan")
    services.make_order_current(first, "Bohdan")
    services.set_machine_status(machine, "running", "Bohdan")
    assert not services.running_without_order(machine)
    response = client.post(f"/orders/{first.id}/close", data={"decided_by": "Bohdan"}, follow_redirects=True)
    text = response.get_data(as_text=True)
    assert f"Order {first.id} was running on {machine.name}: no order runs there now. Open orders: {second.id} (" in text
    assert not second.is_current  # nothing switched by itself
    assert services.running_without_order(machine)
    panel = client.get(f"/shop/machines/{machine.id}?fragment=1").get_data(as_text=True)
    assert "but no order runs on this machine" in panel
    (card,) = [m for m in services.shop_machines() if m["id"] == machine.id]
    assert card["running_without_order"] is True
    services.make_order_current(second, "Bohdan")
    assert not services.running_without_order(machine)


def test_closing_an_order_that_was_not_running_says_nothing_more(client):
    machine = ready_machine()
    job = make_job()
    services.generate_gcode(job, machine)
    order = services.create_order(job, machine, 10, "Bohdan")
    services.create_order(job, machine, 5, "Bohdan")
    text = client.post(f"/orders/{order.id}/close", data={"decided_by": "Bohdan"}, follow_redirects=True).get_data(as_text=True)
    assert "no order runs there now" not in text
