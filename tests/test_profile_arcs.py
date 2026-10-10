"""Arcs G02/G03, commit 2: arcs and fillets in the profile (their centre from the confirmed rows), the checks
that keep an arc out of a program, and the operator's choice of a radius drawn differently from its dimension."""
import math

import pytest
from gcode_jobs import make_job

from turnpilot.gcode import profile as gp
from turnpilot.models import Feature, db


def fd(id_, type_, **kw):
    return gp.FeatureData(id=id_, type=type_, **kw)


def test_a_spherical_free_end():
    """деталь 1: R10 from the axis to Ø20 at the free end (drawn on the right), then Ø20."""
    p = gp.build([fd(1, "od_turn", diameter=20, length=5), fd(2, "arc", start_diameter=20, length=10, radius=10,
                                                              arc_convex=True)], "right")
    sphere = p.sections[0]
    assert (sphere.d_free, sphere.d_chuck) == (0, 20) and sphere.arc is not None
    assert (round(sphere.arc.cz, 6), round(sphere.arc.cr, 6), sphere.arc.convex) == (-10, 0, True)
    assert p.turned(-5) == pytest.approx(math.sqrt(100 - 25), abs=1e-3) and p.turned(0) == 0
    assert p.warnings == []


def test_a_concave_arc_between_two_diameters():
    p = gp.build([fd(1, "od_turn", diameter=30, length=10),
                  fd(2, "arc", start_diameter=30, diameter=30, length=17.32, radius=10, arc_convex=False),
                  fd(3, "od_turn", diameter=30, length=10)], "left")
    arc = p.sections[1].arc
    assert arc.cr == pytest.approx(20, abs=1e-3) and p.turned(-18.66) == pytest.approx(10, abs=1e-3)


@pytest.mark.parametrize("kw, why", [
    (dict(radius=10, arc_convex=None), "its radius or its shape (convex / concave) is not known"),
    (dict(radius=3, arc_convex=True), "no circle R3 through its ends"),
    (dict(radius=10, arc_convex=True, drawn_radius=51.61), "drawn R51.61 ≠ dimensioned R10: choose the radius"),
])
def test_arcs_that_are_not_programmed(kw, why):
    p = gp.build([fd(1, "arc", start_diameter=30, diameter=20, length=8, **kw)], "left")
    assert p.sections[0].arc is None and why in p.sections[0].arc_problem
    assert p.turned(-4) == 15  # not modelled: the larger end along it
    assert "not programmed" in p.warnings[0]


def test_an_arc_that_turns_back_along_the_axis():
    arc, why = gp.arc_through((0, 2), (-1, 12), 5.1, True)
    assert arc is None and why == "the arc turns back along the axis"


@pytest.mark.parametrize("face, convex, free, chuck", [
    ("left", True, (2, True), None),  # an edge rounding on the free-end side: from Ø16 up to Ø20
    ("right", False, None, (2, False)),  # an inside corner towards a larger neighbour
])
def test_fillets_in_the_profile(face, convex, free, chuck):
    p = gp.build([fd(1, "od_turn", diameter=20, length=10), fd(2, "fillet", radius=2, face=face, arc_convex=convex),
                  fd(3, "od_turn", diameter=30, length=10)], "left")
    s = p.sections[0]
    assert (s.fillet_free, s.fillet_chuck) == (free, chuck) and p.warnings == []
    if free:
        first = s.segments()[0]
        assert (first.z0, first.r0, first.z1, first.r1) == (0, 8, -2, 10) and first.arc.convex
        assert p.turned(-2 + 2 * math.cos(math.radians(45))) == pytest.approx(8 + 2 * math.sin(math.radians(45)),
                                                                                abs=1e-3)
    else:
        last = s.segments()[-1]
        assert (last.z0, last.r0, last.z1, last.r1) == (-8, 10, -10, 12) and not last.arc.convex


def test_the_operator_chooses_the_radius(app, client):
    job = make_job([Feature(type="od_turn", diameter=30, length=10),
                    Feature(type="arc", start_diameter=30, diameter=20, length=8, radius=20.46, arc_convex=True,
                            drawn_radius=51.61)], approve=False)
    page = client.get(f"/jobs/{job.id}").get_data(as_text=True)
    assert "dimension R20.46 ≠ geometry R51.61" in page
    arc = next(f for f in job.features if f.type == "arc")
    client.post(f"/jobs/{job.id}/features/{arc.id}/radius", data={"radius": "drawn"})
    assert (arc.radius, arc.drawn_radius) == (51.61, 51.61)
    assert "≠ geometry" not in client.get(f"/jobs/{job.id}").get_data(as_text=True)
    assert client.post(f"/jobs/{job.id}/features/{arc.id}/radius", data={"radius": "x"}).status_code == 400
    db.session.refresh(arc)
