"""Roughing from the neighbouring section towards the chuck (features in their order along the axis)."""
import pytest
from conftest import seed_turret

from turnpilot import planner
from turnpilot.planner import (
    PIT_WARNING,
    THREAD_MAJOR_NOTE,
    TWO_SIDES_NOTE,
    FeatureSpec,
    JobSpec,
    plan_job,
    rough_passes,
)

TURRET = seed_turret()
AP_MAX = planner.roughing_ap_limit(TURRET[1].tool)  # T2 rough turning: its catalogue ap rec
ALLOWANCE = planner.finishing_ap(TURRET[3].tool)  # left for T4, the finishing tool: its ap rec


def _rough(features, blank=45, axial_order=True):
    ops = plan_job(JobSpec("P", blank, 100, tuple(features), axial_order=axial_order), TURRET, max_rpm=4000)
    return {op.feature_id: op for op in ops if op.tool_type == "turning_rough"}


def _od(fid, d, length=20):
    return FeatureSpec(fid, "od_turn", diameter=d, length=length)


def _expected(start, d):
    return rough_passes(start, d, AP_MAX, ALLOWANCE)


def real(d):
    """A neighbour as its roughing leaves it: its Ø plus the finishing allowance on each side."""
    return round(d + 2 * ALLOWANCE, 3)


def test_monotonic_shaft():
    rough = _rough([_od(1, 40), _od(2, 30), _od(3, 20)])
    assert [(rough[i].passes, rough[i].ap) for i in (1, 2, 3)] == [
        _expected(45, 40), _expected(real(40), 30), _expected(real(30), 20)]
    assert [rough[i].ref_diameter for i in (1, 2, 3)] == [45, real(40), real(30)]
    assert rough[1].n < rough[2].n < rough[3].n  # smaller start Ø, higher speed
    assert "roughed from the bar Ø45" in rough[1].notes
    assert (f"roughed from Ø{real(40):g}: the neighbouring section towards the chuck as its roughing leaves it "
            f"(Ø40 + 2 × {ALLOWANCE:g} for finishing)") in rough[2].notes
    assert not any(TWO_SIDES_NOTE in n for op in rough.values() for n in op.notes)


def test_mirrored_shaft_gives_the_same():
    left = _rough([_od(1, 40), _od(2, 30), _od(3, 20)])
    right = _rough([_od(3, 20), _od(2, 30), _od(1, 40)])
    for i in (1, 2, 3):
        assert (left[i].passes, left[i].ap, left[i].ref_diameter) == (right[i].passes, right[i].ap, right[i].ref_diameter)


def test_order_unknown_keeps_roughing_from_the_bar():
    rough = _rough([_od(1, 40), _od(2, 30), _od(3, 20)], axial_order=False)
    assert [(rough[i].passes, rough[i].ap) for i in (1, 2, 3)] == [
        _expected(45, 40), _expected(45, 30), _expected(45, 20)]
    assert all(op.ref_diameter == 45 for op in rough.values())
    assert not any("roughed from" in n for op in rough.values() for n in op.notes)


def test_pit_is_flagged_and_roughed_from_the_bar():
    rough = _rough([_od(1, 40), _od(2, 25), _od(3, 40)])
    assert PIT_WARNING in rough[2].warnings
    assert (rough[2].passes, rough[2].ap, rough[2].ref_diameter) == (*_expected(45, 25), 45)
    assert not rough[1].warnings and not rough[3].warnings
    assert rough[1].ref_diameter == rough[3].ref_diameter == 45


def test_section_beyond_a_pit_takes_the_next_larger_one():
    rough = _rough([_od(1, 40), _od(2, 25), _od(3, 35)])
    assert PIT_WARNING in rough[2].warnings
    assert rough[3].ref_diameter == real(40) and (rough[3].passes, rough[3].ap) == _expected(real(40), 35)


def test_largest_in_the_middle_machined_from_both_sides():
    rough = _rough([_od(1, 30), _od(2, 40), _od(3, 25)])
    assert rough[1].ref_diameter == rough[3].ref_diameter == real(40)
    assert rough[2].ref_diameter == 45 and TWO_SIDES_NOTE in rough[2].notes


def test_grooves_tapers_and_arcs_are_passed_over():
    rough = _rough([
        _od(1, 40),
        FeatureSpec(2, "groove", diameter=28, length=3, start_diameter=30),
        _od(3, 30),
        FeatureSpec(4, "taper", diameter=20, start_diameter=30, length=10),
        _od(5, 20),
        FeatureSpec(6, "arc", start_diameter=20, radius=10, length=10),
    ])
    assert rough[3].ref_diameter == real(40)  # the groove is cut later, not a neighbour
    assert rough[5].ref_diameter == real(30)  # a taper is not a neighbour the others start from
    assert not rough[3].warnings and not rough[5].warnings


def test_neighbour_under_a_thread_is_its_reduced_major_diameter():
    rough = _rough([
        _od(1, 30),
        FeatureSpec(2, "od_turn", diameter=20, length=25),
        FeatureSpec(3, "thread", diameter=20, length=25, pitch=1.5, location="external"),
        _od(4, 16, length=5),
    ], blank=32)
    assert rough[2].ref_diameter == real(30) and (rough[2].passes, rough[2].ap) == _expected(real(30), 19.85)
    assert rough[4].ref_diameter == real(19.85)  # the section next to the thread starts from what was cut there
    ops = plan_job(JobSpec("P", 32, 100, (_od(1, 30), FeatureSpec(2, "od_turn", diameter=20, length=25),
                                          FeatureSpec(3, "thread", diameter=20, length=25, pitch=1.5)),
                           axial_order=True), TURRET, max_rpm=4000)
    finish = next(op for op in ops if op.feature_id == 2 and op.mode == "finish")
    assert f"{THREAD_MAJOR_NOTE}: Ø19.85 (nominal Ø20)" in finish.notes


def test_no_stock_left_after_the_neighbour_is_a_note_not_a_warning():
    rough = _rough([_od(1, 40), _od(2, 30), FeatureSpec(3, "groove", diameter=26, length=3, start_diameter=30),
                    _od(4, 30)])
    assert rough[4].ref_diameter == real(30) and rough[4].passes == 0
    assert f"no roughing stock left after Ø{real(30):g} (the neighbouring section towards the chuck)" in rough[4].notes
    assert not rough[4].warnings


@pytest.mark.parametrize("blank", [45, 50])
def test_fewer_passes_than_from_the_bar(blank):
    shaft = [_od(1, 40), _od(2, 30), _od(3, 20)]
    after = sum(op.passes for op in _rough(shaft, blank).values())
    before = sum(op.passes for op in _rough(shaft, blank, axial_order=False).values())
    assert after < before
