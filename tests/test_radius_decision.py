"""An arc drawn with another radius than its dimension: the operator takes the dimension, the geometry or their own
value (checked: a circle of it must go through the section's ends), with a note; until then the simulation errs."""
from gcode_jobs import make_job, ready_machine

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
    client.post(f"/jobs/{job.id}/features/{f.id}/radius", data={"radius": "dimension", "note": "уточнено в конструктора"})
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
    response = client.post(f"/jobs/{job.id}/features/{f.id}/radius", data={"radius": "own", "own_radius": "4"},
                           follow_redirects=True)
    text = response.get_data(as_text=True)
    assert "R4 not saved: no circle R4 through its ends" in text and "is R4.717 (half the chord)" in text
    assert (f.radius, f.radius_source) == (20.46, None)
    client.post(f"/jobs/{job.id}/features/{f.id}/radius", data={"radius": "own", "own_radius": "30"})
    assert (f.radius, f.drawn_radius, f.radius_source, f.radius_note) == (30.0, 30.0, "operator", None)


def test_take_the_geometry(app, client):
    ready_machine()
    job = conflict_job()
    f = arc(job)
    client.post(f"/jobs/{job.id}/features/{f.id}/radius", data={"radius": "drawn"})
    assert (f.radius, f.radius_source, f.radius_dimension) == (51.61, "drawn", 20.46)
