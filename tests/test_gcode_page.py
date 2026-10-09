"""Roadmap stage 6, commit 7: the program page, the operator's check, "Ready to run", the download."""
import json

from gcode_jobs import make_job, ready_machine

from turnpilot import services
from turnpilot.models import GcodeProgram, db

CHECKS = {f"check-{key}": "1" for key, _ in services.GCODE_CHECKLIST}


def generate(client, job):
    response = client.post(f"/jobs/{job.id}/gcode")
    record = db.session.execute(db.select(GcodeProgram).order_by(GcodeProgram.id.desc())).scalars().first()
    assert response.location.endswith(f"/gcode/{record.id}")
    return record


def test_job_page_without_a_program(app, client):
    job = make_job(approve=False)
    page = client.get(f"/jobs/{job.id}/gcode").get_data(as_text=True)
    assert "No program yet" in page and "operations not approved yet" in page and "Generate" not in page
    response = client.post(f"/jobs/{job.id}/gcode", follow_redirects=True)
    assert "No program:" in response.get_data(as_text=True)
    assert db.session.execute(db.select(GcodeProgram)).first() is None


def test_ready_to_run_and_download(app, client):
    machine = ready_machine()
    job = make_job()
    assert "G-code" in client.get(f"/jobs/{job.id}").get_data(as_text=True)
    record = generate(client, job)
    page = client.get(f"/gcode/{record.id}").get_data(as_text=True)
    assert '<svg class="gcode-sim"' in page and "found no error" in page and 'id="L1"' in page
    assert "DEMO values" not in page
    assert client.get(f"/gcode/{record.id}/download").status_code == 403  # not ready yet
    response = client.post(f"/gcode/{record.id}/ready", data={**CHECKS, "check-tools": ""}, follow_redirects=True)
    assert "not every item of the checklist is ticked" in response.get_data(as_text=True)
    response = client.post(f"/gcode/{record.id}/ready", data=CHECKS, follow_redirects=True)
    assert "the operator&#39;s name is required" in response.get_data(as_text=True)
    response = client.post(f"/gcode/{record.id}/ready", data={**CHECKS, "confirmed_by": "Operator I."},
                           follow_redirects=True)
    assert "Ready to run, confirmed by Operator I." in response.get_data(as_text=True)
    assert record.status == "ready" and json.loads(record.checklist)["spindle"] is True
    download = client.get(f"/gcode/{record.id}/download")
    assert download.status_code == 200 and download.get_data(as_text=True) == record.text
    assert f'filename="O{job.id:04d}_job{job.id}_v{record.id}.nc"' in download.headers["Content-Disposition"]
    # a new calculation makes it out of date: no download any more
    services.calculate_operations(job, machine)
    assert client.get(f"/gcode/{record.id}/download").status_code == 403
    assert "the job was calculated again after this program" in client.get(f"/gcode/{record.id}").get_data(as_text=True)


def test_demo_values_block_ready_to_run(app, client):
    machine = ready_machine()
    job = make_job()
    for name in ("max_rpm", "spindle_right_hand", "clearance_x"):
        machine.source_of(name).source = "demo"
    job.gcode_setup_demo = True
    db.session.commit()
    record = generate(client, job)
    page = client.get(f"/gcode/{record.id}").get_data(as_text=True)
    assert "DEMO values, not from the machine's passport or the operator:" in page
    assert "Max spindle speed 3500 rpm" in page and "Stick-out from the jaws 75 mm" in page
    response = client.post(f"/gcode/{record.id}/ready", data={**CHECKS, "confirmed_by": "Operator I."},
                           follow_redirects=True)
    assert "Not ready to run: DEMO values are used: Max spindle speed" in response.get_data(as_text=True)
    assert record.status == "simulated"
    # the operator saves the values (the same numbers): they become the operator's; the old program is out of date
    client.post("/machine", data={"action": "programming", **{f.name: getattr(machine, f.name) or "" for f in
                                                                services.machine_spec.PROGRAMMING_FIELDS}})
    assert machine.source_of("spindle_right_hand").source == "operator"
    client.post("/machine", data={"action": "profile", "name": machine.name, "max_rpm": "3500",
                                  "power_kw": "11", "max_diameter": "300", "turret_positions": "12"})
    assert machine.source_of("max_rpm").source == "operator"
    client.post(f"/jobs/{job.id}/gcode-setup", data={"free_end": "left", "stickout_mm": "75", "face_stock_mm": "1"})
    assert not job.gcode_setup_demo
    assert "values changed since this program" in client.get(f"/gcode/{record.id}").get_data(as_text=True)
    fresh = generate(client, job)
    client.post(f"/gcode/{fresh.id}/ready", data={**CHECKS, "confirmed_by": "Operator I."})
    assert fresh.status == "ready"


def test_errors_and_an_incomplete_part_block_ready_to_run(app, client):
    machine = ready_machine()
    job = make_job()
    record = generate(client, job)
    sim = json.loads(record.simulation)
    sim["errors"] = [[30, "G00 through material"]]
    sim["incomplete"] = ["PART NOT COMPLETE: material left from Z-10 to Z-16"]
    record.simulation = json.dumps(sim)
    db.session.commit()
    page = client.get(f"/gcode/{record.id}").get_data(as_text=True)
    assert "PART NOT COMPLETE." in page and '<a href="#L30">line 30</a>: G00 through material' in page
    response = client.post(f"/gcode/{record.id}/ready", data={**CHECKS, "confirmed_by": "Operator I."},
                           follow_redirects=True)
    text = response.get_data(as_text=True)
    assert "the simulation found errors; the part is not complete" in text and record.status == "simulated"


def test_demo_command(app):
    from turnpilot.commands import gcode_demo_command
    job = make_job(stickout=None, face_stock=None)
    runner = app.test_cli_runner()
    result = runner.invoke(gcode_demo_command, ["--job", str(job.id)])
    assert "DEMO values set" in result.output
    machine = services.get_machine()
    assert machine.source_of("clearance_x").source == "demo" and machine.max_rpm == 3500
    assert job.gcode_setup_demo and job.stickout_mm == 76 and job.face_stock_mm == 1  # part 63 + 3 + 5 + 5
    services.set_machine_value(machine, "clearance_x", 1.5, "operator")
    runner.invoke(gcode_demo_command, [])
    assert machine.clearance_x == 1.5  # the operator's value is kept
