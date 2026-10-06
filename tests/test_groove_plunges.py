"""Grooves wider than the insert: several overlapping plunges; narrower than every insert: a warning."""
from dataclasses import replace

import pytest

from turnpilot.planner import (
    GROOVE_STEP_FACTOR,
    FeatureSpec,
    JobSpec,
    ToolSpec,
    TurretEntry,
    groove_plunges,
    plan_job,
    select_grooving_tool,
)
from turnpilot.seed import TURRET_TOOLS

SEED = [TurretEntry(pos, ToolSpec(id=pos, **{k: v for k, v in tool.items() if k != "grade"}))
        for pos, tool in TURRET_TOOLS.items()]  # one grooving insert: T6, 3 mm
GROOVING_3 = SEED[5].tool


def _groove_op(width, turret=SEED, **kwargs):
    feature = FeatureSpec(1, "groove", diameter=36, length=width, start_diameter=40, **kwargs)
    ops = plan_job(JobSpec("P", 45, 100, (feature,)), turret, max_rpm=4000)
    return [op for op in ops if op.feature_id == 1]


def _with_grooving(*widths):
    """The seed turret with its grooving tool replaced by inserts of these widths (positions 10, 11, ...)."""
    others = [e for e in SEED if e.tool.type != "grooving"]
    return others + [TurretEntry(10 + i, replace(GROOVING_3, id=10 + i, name=f"Grooving {w} mm", insert_width=w))
                     for i, w in enumerate(widths)]


@pytest.mark.parametrize("width, insert, expected", [
    (3, 3, (1, 0.0)),
    (3.005, 3, (1, 0.0)),
    (3.2, 3, (2, 0.2)),
    (8, 3, (4, 1.667)),
    (5.4, 3, (2, 2.4)),  # exactly one full step
    (5.5, 3, (3, 1.25)),
])
def test_groove_plunges(width, insert, expected):
    plunges, step = groove_plunges(width, insert)
    assert (plunges, step) == expected
    if plunges > 1:
        assert step <= GROOVE_STEP_FACTOR * insert + 1e-9  # overlap at least 20% of the insert
        assert insert + (plunges - 1) * step == pytest.approx(width, abs=1e-2)  # the last plunge at the wall


def test_groove_of_the_insert_width_is_one_plunge_as_before():
    (op,) = _groove_op(3)
    assert (op.ref_diameter, op.insert_width, op.depth, op.ap, op.passes) == (40, 3.0, 2.0, None, 1)
    assert not op.warnings and not any("plunges" in n for n in op.notes)


def test_wide_groove_several_overlapping_plunges():
    (op,) = _groove_op(8)
    assert op.passes == 4 and op.tool_id == GROOVING_3.id and not op.warnings
    assert "4 plunges, step 1.667 mm (overlap 1.333 mm)" in op.notes
    assert op.depth == 2.0  # every plunge to the bottom


def test_groove_narrower_than_every_insert_is_not_cut_wider():
    (op,) = _groove_op(2)
    assert op.tool_id is None and op.n is None and op.vc is None and op.passes is None
    assert op.warnings == ["Groove 2 mm is narrower than the narrowest grooving insert in the turret (3 mm): "
                           "install a narrower insert."]


def test_widest_insert_that_fits_is_chosen():
    turret = _with_grooving(2, 4)
    assert select_grooving_tool(5, "P", turret)[0].tool.insert_width == 4
    assert select_grooving_tool(3, "P", turret)[0].tool.insert_width == 2
    entry, warning = select_grooving_tool(1.5, "P", turret)
    assert entry is None and "narrowest grooving insert in the turret (2 mm)" in warning
    (op,) = _groove_op(5, turret)
    assert (op.insert_width, op.passes) == (4, 2)


def test_groove_without_a_width_is_one_plunge_with_a_note():
    (op,) = _groove_op(None)
    assert op.passes is None and "groove width not given: one plunge" in op.notes and not op.warnings


def test_insert_without_a_width_as_before():
    turret = [e for e in SEED if e.tool.type != "grooving"] + [
        TurretEntry(6, replace(GROOVING_3, insert_width=None))]
    (op,) = _groove_op(8, turret)
    assert op.passes is None and "Insert width is not set for this grooving tool." in op.warnings


def test_wide_groove_through_the_app(client):
    from turnpilot.models import Job, Material, Operation, db
    material = db.session.execute(db.select(Material).filter_by(iso_group="P")).scalar_one()
    client.post("/jobs", data=dict(name="Recess", material_id=material.id, quantity=1, blank_diameter=45,
                                   blank_length=60))
    job = db.session.execute(db.select(Job)).scalar_one()
    client.post(f"/jobs/{job.id}/features", data=dict(type="od_turn", diameter=40, length=50))
    client.post(f"/jobs/{job.id}/features", data=dict(type="groove", diameter=32, length=8, start_diameter=40))
    client.post(f"/jobs/{job.id}/calculate")
    groove = db.session.execute(db.select(Operation).filter_by(tool_type="grooving")).scalar_one()
    assert groove.passes == 4 and "4 plunges" in groove.note
    page = client.get(f"/jobs/{job.id}/operations").get_data(as_text=True)
    assert "4 plunges, step 1.667 mm (overlap 1.333 mm)" in page
