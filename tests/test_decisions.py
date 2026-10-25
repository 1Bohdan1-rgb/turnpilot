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
