"""The operator's decisions on one screen (decisions.operator_decisions): each with the program's proposal, why and its
source, or empty where it cannot be proposed; nothing written."""
from gcode_jobs import make_job, ready_machine

from turnpilot import decisions, services
from turnpilot.models import Feature, db


def nd012_like(**kw):
    """НД 012's arc: Ø35 -> Ø38.5 over 10, convex, dimension R20.46 (its top D38.62 rises above the Ø38.5 next to it),
    drawn R51.608."""
    return make_job([Feature(type="face"), Feature(type="od_turn", diameter=35, length=35),
                     Feature(type="arc", start_diameter=35, diameter=38.5, length=10, radius=20.46, arc_convex=True,
                             drawn_radius=51.608),
                     Feature(type="od_turn", diameter=38.5, length=10), Feature(type="parting")],
                    blank=40, length=60, stickout=65, **kw)


def by_kind(d, kind):
    return [x for x in d.items if x.kind == kind]


def test_an_arc_whose_top_rises_above_its_neighbour_gets_the_geometry(app):
    ready_machine()
    job = nd012_like()
    (arc,) = by_kind(decisions.operator_decisions(job), "arc_radius")
    assert arc.value == "drawn" and arc.decided is None and arc.source == "the drawing's geometry"
    assert arc.why.startswith("R20.46: the arc's top D38.62 at Z-43.42 is above D38.5 at Z-45")
    assert arc.why.endswith("R51.608 (the geometry) keeps the profile under it")
    assert [v for v, _ in arc.options] == ["dimension", "drawn"]


def test_an_arc_otherwise_gets_the_dimension_and_a_decision_shows_who(app):
    ready_machine()
    job = make_job([Feature(type="face"), Feature(type="od_turn", diameter=30, length=10),
                    Feature(type="arc", start_diameter=30, diameter=20, length=8, radius=20.46, arc_convex=True,
                            drawn_radius=51.61), Feature(type="parting")], blank=32, length=30, stickout=35)
    (arc,) = by_kind(decisions.operator_decisions(job), "arc_radius")
    assert (arc.value, arc.source) == ("dimension", "the dimension on the drawing")
    feature = db.session.get(Feature, arc.feature_id)
    services.decide_arc_radius(feature, "drawn", 51.61, "test", "Bohdan", services.job_machine(job))
    (arc,) = by_kind(decisions.operator_decisions(job), "arc_radius")
    assert arc.value == "drawn" and arc.decided.startswith("Bohdan, ")


def test_the_set_up_proposals_and_what_stays_empty(app):
    machine = ready_machine()
    job = nd012_like()
    job.free_end = job.stickout_mm = job.face_stock_mm = None
    db.session.commit()
    free, stock, stick = decisions.operator_decisions(job).setup
    assert (free.value, free.source) == ("left", "assumption (placeholder)")  # the largest Ø is the last section
    assert free.why == decisions.CHUCK_ASSUMPTION
    assert (stock.value, stock.source) == (2.0, "placeholder") and stock.why == decisions.FACE_STOCK_PLACEHOLDER
    assert stick.value is None and stick.empty and "the stick-out margin" in stick.why  # no margin: no hint
    services.set_machine_value(machine, "stickout_margin_mm", 2.0, "operator")
    db.session.commit()
    stick = decisions.operator_decisions(job).setup[2]
    assert (stick.value, stick.source) == (55 + 2 + 3 + 5, "the stick-out hint")  # the jaws' safety 5 > margin 2
    assert (job.free_end, job.stickout_mm, job.face_stock_mm) == (None, None, None)  # nothing written


def test_a_bore_depth_to_the_full_diameter_by_the_drafting_convention(app):
    machine = ready_machine()
    job = make_job([Feature(type="face"), Feature(type="od_turn", diameter=20, length=24),
                    Feature(type="bore", diameter=10, length=5, ra=6.3), Feature(type="parting")],
                   blank=22, length=29, stickout=35, approve=False, machine=machine)
    d = decisions.operator_decisions(job)
    (depth,) = by_kind(d, "bore_depth")
    drill = next(op.tool for op in job.current_operations if op.tool_type == "drilling")
    if drill.point_angle_deg:
        assert (depth.value, depth.why) == ("full_diameter", decisions.CONVENTION_FULL_DIAMETER)
    drill.point_angle_deg = None
    db.session.commit()
    (depth,) = by_kind(decisions.operator_decisions(job), "bore_depth")
    assert depth.value == "tip" and "has no point angle" in depth.why
    centring = next(r for r in d.operations if r.op.tool_type == "centre_drilling")
    assert centring.approve is False and centring.skip_note == decisions.CENTRING_SKIP_NOTE
    assert all(r.approve for r in d.operations if r.op.tool_id)
    assert all(r.key == decisions.op_key(r.op) for r in d.operations)


def test_a_shape_not_known_is_empty(app):
    ready_machine()
    job = nd012_like()
    arc = next(f for f in job.features if f.type == "arc")
    arc.arc_convex = None
    db.session.commit()
    (shape,) = by_kind(decisions.operator_decisions(job), "arc_shape")
    assert shape.empty and shape.required
    assert [s.key for s in decisions.operator_decisions(job).empty_required] == [f"shape-{arc.id}"]


# --- confirm all of it at once (decisions.apply_decisions) -----------------------------------------------------------

import json  # noqa: E402

import pytest  # noqa: E402


def open_job(machine):
    """The НД 012-like part with nothing decided: Z0, the stock and the stick-out empty, the arc not chosen."""
    services.set_machine_value(machine, "stickout_margin_mm", 2.0, "operator")
    job = nd012_like(approve=False)
    job.free_end = job.stickout_mm = job.face_stock_mm = None
    db.session.commit()
    return job


def test_confirm_all_writes_with_the_name_and_makes_the_program(app):
    machine = ready_machine()
    job = open_job(machine)
    d = decisions.operator_decisions(job)
    result = decisions.apply_decisions(job, decisions.proposal_form(d), "Bohdan")
    assert result.stopped is None, result.stopped
    arc = next(f for f in job.features if f.type == "arc")
    assert (arc.radius, arc.radius_source, arc.radius_decisions[-1].decided_by) == (51.608, "drawn", "Bohdan")
    assert (job.free_end, job.face_stock_mm, job.stickout_mm) == ("left", 2.0, 65.0)
    assert job.stickout_note.startswith("55 + 2 + 3 + 5 (safety) = 65 mm, Bohdan ")  # the hint, taken by name
    ops = job.current_operations
    assert all(op.status == "approved" for op in ops)
    assert all(op.edits[-1].new_value == "approved (Bohdan)" for op in ops)
    assert result.record is not None and result.record.job_id == job.id
    assert json.loads(result.record.simulation)["errors"] == []
    assert services.next_step(job).key in ("installed", "ready")


def test_nothing_is_written_without_the_name_or_a_value(app):
    machine = ready_machine()
    job = open_job(machine)
    form = decisions.proposal_form(decisions.operator_decisions(job))
    with pytest.raises(decisions.DecisionError, match="name is required"):
        decisions.apply_decisions(job, form, " ")
    del form["stickout"]
    with pytest.raises(decisions.DecisionError, match="Not decided yet: Stick-out"):
        decisions.apply_decisions(job, form, "Bohdan")
    arc = next(f for f in job.features if f.type == "arc")
    assert (job.free_end, job.stickout_mm, arc.radius_decisions) == (None, None, [])


def test_a_set_up_changed_since_the_screen_stops_there(app, client):
    from test_turret_setup import lathe2, pin_on

    ready_machine()
    machine = lathe2(client)
    job = pin_on(machine)
    job.machine_id = machine.id
    db.session.commit()
    d = decisions.operator_decisions(job)
    assert d.turret.rows
    form = decisions.proposal_form(d)
    result = decisions.apply_decisions(job, {**form, "setup_signature": "1:999"}, "Bohdan")
    assert result.stopped == "the turret set-up changed with your decisions: look at it again on this screen"
    assert all(s.tool is None for s in services.job_turret(job, machine))
    result = decisions.apply_decisions(job, form, "Bohdan")
    assert "turret set-up accepted" in result.done
    assert result.stopped.startswith("no program: ")  # Lathe 2 has no programming values: the machine's, not the job's
    assert all(op.status == "approved" for op in job.current_operations if op.tool_id)


def test_the_centring_is_skipped_with_its_reason_and_the_depth_decided(app):
    machine = ready_machine()
    job = make_job([Feature(type="face"), Feature(type="od_turn", diameter=20, length=24),
                    Feature(type="bore", diameter=10, length=5, ra=6.3), Feature(type="parting")],
                   blank=22, length=29, stickout=35, approve=False, machine=machine)
    d = decisions.operator_decisions(job)
    bore = next(f for f in job.features if f.type == "bore")
    assert [x.key for x in d.empty_required] == [f"bore-{bore.id}"]  # a bore typed by hand: its place is not known
    form = {**decisions.proposal_form(d), f"bore-{bore.id}-open_end": "left", f"bore-{bore.id}-from_mm": "0",
            f"bore-{bore.id}-bottom": "drill_point"}
    result = decisions.apply_decisions(job, form, "Bohdan")
    assert (bore.bore_open_end, bore.bore_position_by) == ("left", "Bohdan")
    centring = next(op for op in job.current_operations if op.tool_type == "centre_drilling")
    assert (centring.status, centring.skipped_by, centring.skipped_note) == ("skipped", "Bohdan",
                                                                            decisions.CENTRING_SKIP_NOTE)
    assert bore.depth_decided_by == "Bohdan" and bore.depth_mode in ("full_diameter", "tip")
    assert result.done[-1].endswith("1 skipped")
