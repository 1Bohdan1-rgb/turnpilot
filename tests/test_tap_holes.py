"""Holes for cutting taps: PHD / PHDX from Sandvik Solid round tools 2020, C157 (coarse) and C158 (fine)."""
import pytest
from conftest import seed_turret

from turnpilot import planner
from turnpilot.planner import FeatureSpec, JobSpec, ToolSpec, TurretEntry, plan_job, tap_drill_diameter, tap_hole

SEED = [e for e in seed_turret() if e.tool.type != "drilling"]


def _drill(position, diameter):
    return TurretEntry(position, ToolSpec(id=position, name=f"drill {diameter}", type="drilling", iso_group="P",
                                          insert_code="", vc_min=20, vc_max=40, f_min=0.1, f_max=0.3,
                                          ap_min=diameter / 2, ap_max=diameter / 2, diameter=diameter))


@pytest.mark.parametrize("nominal, pitch, phd, phdx, page", [
    (10, 1.5, 8.5, 8.676, "C157"), (33, 3.5, 29.5, 29.771, "C157"), (48, 5.0, 43.0, 43.297, "C157"),
    (10, 1.25, 8.8, 8.912, "C158"), (24, 2.0, 22.0, 22.21, "C158"),
])
def test_tap_hole_from_the_catalogue(nominal, pitch, phd, phdx, page):
    hole = tap_hole(nominal, pitch)
    assert (hole.phd, hole.phdx, hole.source) == (phd, phdx, f"Sandvik Solid round tools 2020, {page}")
    assert tap_drill_diameter(nominal, pitch) == phd


def test_m10x125_was_d_minus_p_now_the_catalogue():
    assert tap_drill_diameter(10, 1.25) == 8.8  # d − P would give 8.75


@pytest.mark.parametrize("nominal, pitch", [(10, 1.75), (64, 6.0), (11, 1.0)])
def test_thread_not_in_the_tables_is_d_minus_p(nominal, pitch):
    hole = tap_hole(nominal, pitch)
    assert (hole.phd, hole.phdx, hole.source) == (round(nominal - pitch, 2), None, None)


def _tap_drill_op(turret, nominal=10, pitch=1.5):
    features = (FeatureSpec(1, "thread", diameter=nominal, length=15, pitch=pitch, tolerance="6H", location="internal"),)
    ops = plan_job(JobSpec("P", 30, 60, features), turret, 4000)
    return next(op for op in ops if op.tool_type == "drilling")


@pytest.mark.parametrize("diameter, chosen", [(8.5, True), (8.6, True), (8.676, True), (8.7, False), (8.45, False)])
def test_drill_between_phd_and_phdx(diameter, chosen):
    op = _tap_drill_op(SEED + [_drill(10, diameter)])
    assert (op.tool_id is not None) == chosen
    assert "tap drill Ø8.5 (PHD, max Ø8.676 PHDX): Sandvik Solid round tools 2020, C157" in op.notes
    if not chosen:
        assert op.warnings == ["no Ø8.5 drill in the turret (tap drill for M10×1.5)"]


def test_the_drill_nearest_to_phd():
    op = _tap_drill_op(SEED + [_drill(10, 8.65), _drill(11, 8.5), _drill(12, 8.6)])
    assert (op.tool_name, op.ref_diameter) == ("drill 8.5", 8.5)


def test_thread_not_in_the_tables_keeps_the_old_rule_with_a_note():
    op = _tap_drill_op(SEED + [_drill(10, 9.0)], nominal=11, pitch=2.0)
    assert op.tool_name == "drill 9.0" and "tap drill Ø9 (d − P): not in catalogue" in op.notes
    assert planner.TAP_HOLE_SOURCE.format(page="C157") not in " ".join(op.notes)
