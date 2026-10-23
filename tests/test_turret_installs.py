"""The operator at the machine marks each tool of the job's turret set-up "installed, X / Z offsets measured"; until every
one is marked, a program of the job is not ready to run."""
from gcode_jobs import make_job, ready_machine
from test_gcode_page import CHECKS, generate

from turnpilot import services
from turnpilot.models import Tool, db

ALL_CHECKS = {**CHECKS, "check-turret_setup": "1", "confirmed_by": "Operator I."}


def tool(code):
    return db.session.execute(db.select(Tool).filter_by(insert_code=code)).scalar_one()


def pin_with_a_change(machine):
    """The pin, with T12 (the Ø10 drill, not used by it) taking the 2 mm grooving insert for this job."""
    job = make_job()
    services.change_job_turret(job, machine, 12, tool("N123E2-0200-0002-GM"), "Bohdan")
    db.session.commit()
    return job


def test_ready_to_run_waits_for_installed_and_offsets_measured(app, client):
    machine = ready_machine()
    job = pin_with_a_change(machine)
    (change,) = services.not_installed(job, machine)
    record = generate(client, job)
    page = client.get(f"/gcode/{record.id}").get_data(as_text=True)
    assert "Turret set-up at the machine" in page and "Installed, X / Z offsets measured" in page
    assert "the turret set-up is not marked installed with its offsets measured:\n    T12" in page
    response = client.post(f"/gcode/{record.id}/ready", data=ALL_CHECKS, follow_redirects=True)
    assert ("Not ready to run: the job&#39;s turret set-up is not marked installed with its X / Z offsets measured: "
            "T12 Grooving 2 mm") in response.get_data(as_text=True)
    back = f"/gcode/{record.id}#installs"
    response = client.post(f"/jobs/{job.id}/turret/installed", data={"change_id": change.id, "back": back},
                           follow_redirects=True)
    assert "The operator&#39;s name is required: installed, X / Z offsets measured" in response.get_data(as_text=True)
    client.post(f"/jobs/{job.id}/turret/installed", data={"change_id": change.id, "decided_by": "Ivan", "back": back})
    assert change.install.installed_by == "Ivan" and services.not_installed(job, machine) == []
    response = client.post(f"/gcode/{record.id}/ready", data=ALL_CHECKS, follow_redirects=True)
    page = response.get_data(as_text=True)
    assert "<strong>Ready to run</strong>, confirmed by Operator I" in page  # the banner, once: no flash as well
    assert page.count("confirmed by Operator I") == 1


def test_all_at_once_and_a_mark_cancelled(app, client):
    machine = ready_machine()
    job = pin_with_a_change(machine)
    services.change_job_turret(job, machine, 11, tool("VNMG160404-PF"), "Bohdan")
    db.session.commit()
    page = client.get(f"/jobs/{job.id}").get_data(as_text=True)
    assert "All installed, X / Z offsets measured" in page
    client.post(f"/jobs/{job.id}/turret/installed", data={"decided_by": "Ivan"})
    installs = services.turret_installs(job, machine)
    assert [(c.position, c.install.installed_by) for c in installs] == [(11, "Ivan"), (12, "Ivan")]
    mark = installs[0].install
    client.post(f"/jobs/{job.id}/turret/installed/{mark.id}/undo", data={"decided_by": "Petro"})
    assert (mark.undone_by, installs[0].install) == ("Petro", None)
    assert [c.position for c in services.not_installed(job, machine)] == [11]
    assert len(installs[0].installs) == 1  # kept, never deleted


def test_the_offset_page_values_for_a_g42_tool(app, client):
    machine = ready_machine()
    vnmg = tool("VNMG160404-PF")
    vnmg.tip_direction = 3
    db.session.commit()
    job = make_job()
    services.change_job_turret(job, machine, 11, vnmg, "Bohdan")
    db.session.commit()
    assert services.offset_details(vnmg, 11) == {"t_word": "T1111", "offset_page": "R0.4 T3"}
    assert services.offset_details(tool("N123E2-0200-0002-GM"), 12)["offset_page"] is None
    assert "offset page R0.4 T3 (G42)" in client.get(f"/jobs/{job.id}").get_data(as_text=True)


def test_the_order_panel_on_the_shop_page_shows_the_marks(app, client):
    machine = ready_machine()
    job = pin_with_a_change(machine)
    generate(client, job)
    order = services.create_order(job, machine, 10, "Bohdan")
    services.make_order_current(order, "Bohdan")
    panel = client.get(f"/shop/machines/{machine.id}?fragment=1").get_data(as_text=True)
    assert "Turret set-up at the machine" in panel and f'formaction="/jobs/{job.id}/turret/installed"' in panel
