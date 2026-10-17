"""Machines placed on the shop plan (7.2): a 100 mm step, a rotation, the operator's name and the time, every place
kept; a footprint that runs into a wall, a column, a walkway or another machine is kept with a warning."""
import io
import json
from pathlib import Path

from gcode_jobs import ready_machine
from test_dxf_upload import no_api  # noqa: F401 (a fixture: no API call)

from turnpilot import services
from turnpilot.models import Machine, db

EXAMPLE = Path(__file__).resolve().parent.parent / "docs" / "shop_plan_example.json"
NEW = {"name": "Lathe 2", "max_rpm": "3000", "power_kw": "7.5", "max_diameter": "250", "turret_positions": "8"}


def upload_plan(client):
    client.post("/shop/plan", data={"plan": (io.BytesIO(EXAMPLE.read_bytes()), "shop.json"), "uploaded_by": "Bohdan"},
                content_type="multipart/form-data")


def place(client, machine, x, y, rotation=0, name="Bohdan"):
    return client.post(f"/shop/machines/{machine.id}/place", headers={"X-Requested-With": "fetch"},
                       data={"x_mm": x, "y_mm": y, "rotation": rotation, "placed_by": name})


def machines_on(page):
    start = page.index("const M = ") + len("const M = ")
    return {m["name"]: m for m in json.loads(page[start:page.index(";\n", start)])}


def lathe(machine, length=4400, width=2200):
    machine.machine_kind, machine.footprint_length_mm, machine.footprint_width_mm = "cnc_lathe", length, width
    db.session.commit()
    return machine


def test_a_place_needs_a_plan_and_a_name_snaps_to_100_mm_and_is_kept(app, client):
    machine = ready_machine()
    assert "No shop plan yet" in place(client, machine, 9000, 6000).get_json()["error"]
    upload_plan(client)
    assert place(client, machine, 9000, 6000, name="").get_json()["error"] == "The operator's name is required"
    assert "outside the plan" in place(client, machine, 80000, 6000).get_json()["error"]
    assert place(client, machine, 9000, 6000, rotation=45).get_json()["error"] == "Rotation: 0, 90, 180 or 270"
    assert place(client, machine, 9037, 5962).get_json() == {"ok": True}
    place(client, machine, 9037, 5962, rotation=90, name="Ivan")
    assert [(p.x_mm, p.y_mm, p.rotation, p.placed_by) for p in machine.placements] == [
        (9000, 6000, 0, "Bohdan"), (9000, 6000, 90, "Ivan")]
    m = machines_on(client.get("/shop").get_data(as_text=True))[machine.name]
    assert (m["x"], m["y"], m["rotation"], m["placed_by"]) == (9000, 6000, 90, "Ivan")


def test_running_into_a_wall_a_column_a_walkway_or_a_machine_is_kept_with_a_warning(app, client):
    machine = lathe(ready_machine())
    upload_plan(client)
    client.post("/machines", data=NEW)
    other = lathe(db.session.execute(db.select(Machine).filter_by(name="Lathe 2")).scalar_one())
    place(client, machine, 9000, 6000)  # free: the turning bay between the columns
    assert machines_on(client.get("/shop").get_data(as_text=True))[machine.name]["conflicts"] == []
    place(client, machine, 300, 6000)  # across the outer wall at X0
    assert machines_on(client.get("/shop").get_data(as_text=True))[machine.name]["conflicts"] == [
        "crosses a wall (walls[0])"]
    place(client, machine, 6000, 12400)  # on the column of axis 2, over the main walkway
    found = machines_on(client.get("/shop").get_data(as_text=True))[machine.name]["conflicts"]
    assert "stands on a column at (6000, 12400)" in found and "stands on a walkway (MAIN WALKWAY 2.4 m)" in found
    place(client, machine, 9000, 6000)
    place(client, other, 10000, 6500)
    on_plan = machines_on(client.get("/shop").get_data(as_text=True))
    assert on_plan[machine.name]["conflicts"] == ["overlaps Lathe 2"]
    assert on_plan["Lathe 2"]["conflicts"] == [f"overlaps {machine.name}"]
    assert (machine.placement.x_mm, other.placement.x_mm) == (9000, 10000)  # both places kept


def test_a_machine_without_a_footprint_is_placed_but_not_checked(app, client):
    machine = ready_machine()
    upload_plan(client)
    place(client, machine, 300, 6000)  # where a footprint would cross the wall
    m = machines_on(client.get("/shop").get_data(as_text=True))[machine.name]
    assert (m["x"], m["length"], m["conflicts"]) == (300, None, [])


def test_upload_drawing_from_the_card_plans_the_new_job_for_that_machine(app, client, no_api, tmp_path):
    """The card's "Upload drawing" opens the upload page for the machine; the job confirmed from it is planned for it
    (a DXF here: no API call)."""
    import dxf_drawings as dd
    from test_dxf_upload import _confirm_form, _extractions

    from turnpilot.models import Job

    ready_machine()
    client.post("/machines", data=NEW)
    lathe2 = db.session.execute(db.select(Machine).filter_by(name="Lathe 2")).scalar_one()
    assert f"/jobs/upload?machine_id=${{id}}" in client.get("/shop").get_data(as_text=True)
    page = client.get(f"/jobs/upload?machine_id={lathe2.id}").get_data(as_text=True)
    assert "The new job will be planned for <strong>Lathe 2</strong>" in page
    data = dd.write(tmp_path / "sheet.dxf", dd.draw_pin).read_bytes()
    response = client.post("/jobs/upload", data={"drawing": (io.BytesIO(data), "pin.dxf"), "machine_id": lathe2.id},
                           content_type="multipart/form-data")
    assert response.location.endswith(f"machine_id={lathe2.id}")
    (pin,) = _extractions()
    assert "planned for <strong>Lathe 2</strong>" in client.get(response.location).get_data(as_text=True)
    client.post(f"/extractions/{pin.id}/confirm", data={**_confirm_form(pin), "machine_id": lathe2.id})
    job = db.session.execute(db.select(Job)).scalar_one()
    assert job.machine is lathe2 and services.job_machine(job) is lathe2


def test_the_page_offers_dragging_from_the_list_and_x_y_in_the_card(app, client):
    """A machine not placed yet is dragged from the list by pointer events (mouse or finger, not HTML5 drag and drop,
    which phones lack) or placed by X / Y in its card; the card's light buttons have a visible text colour."""
    ready_machine()
    upload_plan(client)
    page = client.get("/shop").get_data(as_text=True)
    assert "function dragFromList(" in page and 'draggable="true"' not in page
    assert 'id="sf-place"' in page and "Place on the plan" in page and "give X / Y in its card" in page
    css = (Path(__file__).resolve().parent.parent / "turnpilot" / "static" / "style.css").read_text(encoding="utf-8")
    assert ".shop-floor a.sf-button.ghost { color:var(--sf-ink); }" in css


def test_the_plan_has_a_scale_fits_the_window_and_fades_covered_labels(app, client):
    """+ / − / Fit (the whole shop on opening), Ctrl + wheel to the cursor, the background dragged pans; the plan and the
    card fit the window's height; a label under a machine fades; a drop outside the plan is cancelled."""
    ready_machine()
    upload_plan(client)
    page = client.get("/shop").get_data(as_text=True)
    for piece in ('id="sf-zoom-in"', 'id="sf-zoom-out"', 'id="sf-fit"', "if (!e.ctrlKey) return;",
                  "function fitHeight()", "function dimCoveredLabels()", "Dropped outside the plan"):
        assert piece in page, piece
    css = (Path(__file__).resolve().parent.parent / "turnpilot" / "static" / "style.css").read_text(encoding="utf-8")
    assert "main:has(.shop-floor) { max-width:none; }" in css and ".p-zone-label.covered { opacity:.2; }" in css
