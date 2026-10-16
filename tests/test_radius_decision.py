"""An arc drawn with another radius than its dimension: the operator takes the dimension, the geometry or their own
value (checked: a circle of it must go through the section's ends), with a note; until then the simulation errs."""
from gcode_jobs import make_job, ready_machine
from markupsafe import escape

from turnpilot import services
from turnpilot.gcode import fanuc
from turnpilot.models import Feature, db


def conflict_job():
    """Ø30 L10, then a convex arc Ø30 -> Ø20 over 8 (free end on the left): dimension R20.46, drawn R51.61."""
    return make_job([Feature(type="face"), Feature(type="od_turn", diameter=30, length=10),
                     Feature(type="arc", start_diameter=30, diameter=20, length=8, radius=20.46, arc_convex=True,
                             drawn_radius=51.61), Feature(type="parting")], blank=32, length=30, stickout=35)


def arc(job):
    return next(f for f in job.features if f.type == "arc")


def simulate(job, machine):
    _, program = services.gcode_program(job, machine)
    return services.gcode_simulation(job, machine, fanuc.render(program))


def test_the_conflict_on_the_job_page_and_the_error_until_decided(app, client):
    machine = ready_machine()
    job = conflict_job()
    page = client.get(f"/jobs/{job.id}").get_data(as_text=True)
    assert "Z-10.0 to Z-18.0 (D30.0 to D20.0): dimension R20.46 ≠ geometry R51.61" in page
    assert "Take the dimension R20.46" in page and "Take the geometry R51.61" in page and "Take my value" in page
    assert (0, "Z-10 to Z-18 (D30 to D20): arc drawn R51.61 ≠ dimensioned R20.46: choose the radius on the job page "
               "(the operator's decision)") in simulate(job, machine).errors


def test_take_the_dimension_with_a_note(app, client):
    machine = ready_machine()
    job = conflict_job()
    f = arc(job)
    client.post(f"/jobs/{job.id}/features/{f.id}/radius", data={"radius": "dimension", "note": "уточнено в конструктора", "decided_by": "Operator I."})
    assert (f.radius, f.drawn_radius, f.radius_source) == (20.46, 20.46, "dimension")
    assert (f.radius_dimension, f.radius_geometry, f.radius_note) == (20.46, 51.61, "уточнено в конструктора")
    assert f.radius_decided_at is not None
    assert "R20.46: the operator's decision" in client.get(f"/jobs/{job.id}").get_data(as_text=True)
    services.calculate_operations(job, machine)
    for op in job.current_operations:
        op.status = "approved"
    db.session.commit()
    assert not any("operator's decision" in m for _, m in simulate(job, machine).errors)


def test_own_value_checked_against_the_ends(app, client):
    ready_machine()
    job = conflict_job()
    f = arc(job)
    # the ends Z-10 D30 and Z-18 D20: the chord is 9.434, so R4 cannot go through them
    response = client.post(f"/jobs/{job.id}/features/{f.id}/radius", data={"radius": "own", "own_radius": "4", "decided_by": "Operator I."},
                           follow_redirects=True)
    text = response.get_data(as_text=True)
    assert "R4 not saved: no circle R4 through its ends" in text and "is R4.717 (half the chord)" in text
    assert (f.radius, f.radius_source) == (20.46, None)
    client.post(f"/jobs/{job.id}/features/{f.id}/radius", data={"radius": "own", "own_radius": "30", "decided_by": "Operator I."})
    assert (f.radius, f.drawn_radius, f.radius_source, f.radius_note) == (30.0, 30.0, "operator", None)


def test_take_the_geometry(app, client):
    ready_machine()
    job = conflict_job()
    f = arc(job)
    client.post(f"/jobs/{job.id}/features/{f.id}/radius", data={"radius": "drawn", "decided_by": "Operator I."})
    assert (f.radius, f.radius_source, f.radius_dimension) == (51.61, "drawn", 20.46)


def test_the_name_is_required(app, client):
    ready_machine()
    job = conflict_job()
    f = arc(job)
    response = client.post(f"/jobs/{job.id}/features/{f.id}/radius", data={"radius": "dimension"},
                           follow_redirects=True)
    assert "The operator&#39;s name is required" in response.get_data(as_text=True) and f.radius_source is None


def test_change_the_decision(app, client):
    machine = ready_machine()
    job = conflict_job()
    f = arc(job)
    client.post(f"/jobs/{job.id}/features/{f.id}/radius", data={"radius": "dimension", "decided_by": "Operator I."})
    services.calculate_operations(job, machine)
    for op in job.current_operations:
        op.status = "approved"
    db.session.commit()
    _, program = services.gcode_program(job, machine)
    record = services.generate_gcode(job, machine)[1]
    page = client.get(f"/jobs/{job.id}").get_data(as_text=True)
    assert "Change the decision" in page and "Take the geometry R51.61" in page
    response = client.post(f"/jobs/{job.id}/features/{f.id}/radius",
                           data={"radius": "drawn", "note": "уточнено в конструктора", "decided_by": "Operator P."},
                           follow_redirects=True)
    assert "Arc radius changed to R51.61 (the geometry); the job was calculated again" in         response.get_data(as_text=True)
    assert (f.radius, f.radius_source, f.radius_dimension, f.radius_geometry) == (51.61, "drawn", 20.46, 51.61)
    history = [(d.radius, d.source, d.decided_by, d.note) for d in f.radius_decisions]
    assert history == [(20.46, "dimension", "Operator I.", None), (51.61, "drawn", "Operator P.",
                                                                    "уточнено в конструктора")]
    ops = job.current_operations
    arc_ops = [op for op in ops if op.feature_id == f.id]
    assert arc_ops and all(op.status == "proposed" for op in arc_ops)  # the arc's numbers changed
    assert any(op.status == "approved" for op in ops if op.feature.type == "face")  # unchanged: kept
    reasons = services.gcode_stale(record, machine)
    assert "the job was calculated again after this program" in reasons
    assert any(r.startswith("the decision on the arc's radius was changed to R51.61 by Operator P.") for r in reasons)
    page = client.get(f"/jobs/{job.id}").get_data(as_text=True)
    assert "Operator I." in page and "Operator P." in page  # both decisions in the history


def test_a_convex_arc_above_its_neighbour_is_shown_before_the_choice(app, client):
    """НД 012: Ø35 -> Ø38.5 over Z-35..-45, convex: R20.46 tops out at D38.62, above the Ø38.5 it meets; R51.608 not."""
    ready_machine()
    job = make_job([Feature(type="face"), Feature(type="od_turn", diameter=35, length=35),
                    Feature(type="arc", start_diameter=35, diameter=38.5, length=10, radius=20.46, arc_convex=True,
                            drawn_radius=51.608),
                    Feature(type="od_turn", diameter=38.5, length=10), Feature(type="parting")],
                   blank=40, length=60, stickout=65)
    top = "R20.46: the arc's top D38.62 at Z-43.42 is above D38.5 at Z-45 (the neighbouring section's Ø there): check"
    assert services.arc_top_warnings(job) == {arc(job).id: {"dimension": top}}
    assert str(escape(top)) in client.get(f"/jobs/{job.id}").get_data(as_text=True)
    response = client.post(f"/jobs/{job.id}/features/{arc(job).id}/radius",
                           data={"radius": "dimension", "decided_by": "Bohdan"}, follow_redirects=True)
    assert response.get_data(as_text=True).count(str(escape(top))) == 3  # the flash, the decision, the choice to change it
