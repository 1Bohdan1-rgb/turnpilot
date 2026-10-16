"""The operator skips an operation (e.g. no centring before the self-centring 860-GM drill): it blocks nothing and
is left out of the program; the skip goes on to the same operation of a new calculation."""
from gcode_jobs import make_job, ready_machine

from turnpilot import services
from turnpilot.models import Feature, db

NOTE = "860-GM: 140° point, self-centring; no centring (operator's decision, catalogue file section 6)"


def stud(machine):
    """03057789-2-01: M20 × 24, a blind Ø10 × 5 bore (T12 Ø10 drill); the turret has no centre drill."""
    job = make_job([Feature(type="face"), Feature(type="od_turn", diameter=20, length=24),
                    Feature(type="bore", diameter=10, length=5, ra=6.3), Feature(type="parting")],
                   blank=22, length=29, stickout=35, approve=False, machine=machine)
    for op in job.current_operations:
        if op.tool_id:
            op.status = "approved"
    db.session.commit()
    return job


def centring(job):
    return next(op for op in job.current_operations if op.tool_type == "centre_drilling")


def test_the_centring_blocks_until_skipped(app, client):
    machine = ready_machine()
    job = stud(machine)
    op = centring(job)
    assert op.tool_id is None and op.status == "proposed"
    readiness, _ = services.gcode_program(job, machine)
    assert any("not approved" in b for b in readiness.blockers)
    response = client.post(f"/operations/{op.id}/skip", data={"skipped_by": "Bohdan"}, follow_redirects=True)
    assert "name and a note are required" in response.get_data(as_text=True) and op.status == "proposed"
    client.post(f"/operations/{op.id}/skip", data={"skipped_by": "Bohdan", "note": NOTE})
    assert (op.status, op.skipped_by, op.skipped_note) == ("skipped", "Bohdan", NOTE)
    assert op.edits[-1].new_value == "skipped (Bohdan)"
    readiness, program = services.gcode_program(job, machine)
    assert readiness.ok, readiness.blockers
    assert readiness.skipped[op.id] == f"skipped by the operator (Bohdan): {NOTE}"
    from turnpilot.gcode import fanuc
    text = fanuc.render(program)
    assert f"OPERATION {op.id}: SKIPPED BY THE OPERATOR BOHDAN" in text.replace(")\n(", " ")
    page = client.get(f"/jobs/{job.id}/operations").get_data(as_text=True)
    assert "skipped by Bohdan" in page and "Unskip" in page


def test_the_skip_goes_on_to_a_new_calculation_and_can_be_undone(app, client):
    machine = ready_machine()
    job = stud(machine)
    services.skip_operation(centring(job), "Bohdan", NOTE)
    services.calculate_operations(job, machine)
    op = centring(job)
    assert (op.status, op.skipped_note, op.skip_carried_from) == ("skipped", NOTE, 1)
    assert "(carried from v1)" in client.get(f"/jobs/{job.id}/operations").get_data(as_text=True)
    client.post(f"/operations/{op.id}/unskip", data={"skipped_by": "Bohdan"})
    assert (op.status, op.skipped_by) == ("proposed", None)
