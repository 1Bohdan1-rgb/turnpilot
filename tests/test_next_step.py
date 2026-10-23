"""The job's next step (services.next_step): one button on the job page, the process sheet and the G-code pages."""
from gcode_jobs import make_job, ready_machine

from turnpilot import i18n, services
from turnpilot.models import Job, Material, db


def test_every_step_label_has_its_ukrainian():
    assert [t for t in services.NEXT_STEP_LABELS.values() if t not in i18n.UK] == []


def test_a_job_without_features_asks_for_them(client):
    material = db.session.execute(db.select(Material)).scalars().first()
    job = Job(name="Empty", material=material, quantity=1, blank_diameter=30, blank_length=50)
    db.session.add(job)
    db.session.commit()
    step = services.next_step(job)
    assert step.key == "features" and step.anchor == "add-feature"
    page = client.get(f"/jobs/{job.id}").get_data(as_text=True)
    assert 'aria-label="Next step"' in page and f'href="/jobs/{job.id}#add-feature">Add the features →</a>' in page


def test_calculate_then_approve_then_gcode_then_ready_then_order(client):
    machine = ready_machine()
    job = make_job(approve=False)
    step = services.next_step(job)
    assert step.key == "approve" and step.values["n"] == len(job.current_operations)
    for op in job.current_operations:
        if op.tool_id:
            op.status = "approved"
    db.session.commit()
    step = services.next_step(job)
    assert step.key == "generate" and step.post and step.endpoint == "main.job_gcode"
    page = client.get(f"/jobs/{job.id}/operations").get_data(as_text=True)
    assert f'<form method="post" action="/jobs/{job.id}/gcode" class="next-step-form">' in page
    _, record = services.generate_gcode(job, machine)
    step = services.next_step(job)
    assert step.key in ("ready", "installed", "fix") and step.args == {"program_id": record.id}
    record.status = "ready"
    db.session.commit()
    assert services.next_step(job).key == "order"
    order = services.create_order(job, machine, 5, "Bohdan")
    step = services.next_step(job)
    assert step.key == "progress" and step.args == {"order_id": order.id}


def test_a_new_calculation_is_asked_for_after_the_tools_changed(client):
    ready_machine()
    job = make_job()
    tool = next(op.tool for op in job.current_operations if op.tool)
    tool.vc_max = tool.vc_max + 1
    db.session.commit()
    if job.tools_changed_since_calculation:
        step = services.next_step(job)
        assert step.key == "calculate" and step.post


def test_the_step_in_ukrainian(app):
    ready_machine()
    job = make_job(approve=False)
    page = app.test_client().get(f"/jobs/{job.id}").get_data(as_text=True)
    assert "Наступний крок" in page and "Затвердити операції (лишилося" in page
