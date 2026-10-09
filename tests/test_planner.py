from dataclasses import replace
import math

import pytest

from turnpilot.planner import (
    CHAMFER_NOTE,
    DEFAULT_FINISH_ALLOWANCE_MM,
    G96_NOTE,
    G97_THREAD_NOTE,
    PARTING_BORE_NOTE,
    PARTING_CENTER_NOTE,
    FeatureSpec,
    JobSpec,
    ToolSpec,
    TurretEntry,
    cutting_data,
    feature_to_steps,
    finish_feed_from_ra,
    nose_radius_from_insert,
    order_steps,
    plan_job,
    rough_passes,
    select_tool,
    spindle_speed,
    thread_depth,
    thread_infeed,
)


def make_tool(tool_id, tool_type, iso="PMN", insert="CNMG 120408", **ranges):
    data = dict(vc_min=100, vc_max=300, f_min=0.1, f_max=0.5, ap_min=0.5, ap_max=4.5)
    data.update(ranges)
    return ToolSpec(id=tool_id, name=f"{tool_type}-{tool_id}", type=tool_type, iso_group=iso, insert_code=insert, **data)


@pytest.fixture
def turret():
    return [
        TurretEntry(1, make_tool(1, "facing")),
        TurretEntry(2, make_tool(2, "turning_rough", iso="P")),
        TurretEntry(3, make_tool(3, "turning_rough", iso="M")),
        TurretEntry(4, make_tool(4, "turning_finish", insert="DNMG 150408", f_min=0.05, f_max=0.3)),
        TurretEntry(5, make_tool(5, "grooving", iso="PN", insert_width=3.0)),
        TurretEntry(6, make_tool(6, "threading", ap_min=0.05, ap_max=0.2)),
        TurretEntry(7, make_tool(7, "parting", insert_width=3.0)),
    ]


# --- spindle speed -------------------------------------------------------

def test_spindle_speed_formula():
    n, limited = spindle_speed(200, 50, max_rpm=10000)
    assert n == round(1000 * 200 / (math.pi * 50))  # 1273
    assert n == 1273
    assert limited is False


def test_spindle_speed_capped_at_max_rpm():
    n, limited = spindle_speed(300, 5, max_rpm=4000)  # uncapped ~19099 rpm
    assert n == 4000
    assert limited is True


def test_spindle_speed_rejects_zero_diameter():
    with pytest.raises(ValueError):
        spindle_speed(200, 0, max_rpm=4000)


# --- tool selection ------------------------------------------------------

def test_select_tool_by_type_and_iso_group(turret):
    entry, warning = select_tool("turning_rough", "M", turret)
    assert warning is None
    assert entry.position == 3


def test_select_tool_accepts_multi_group_grade(turret):
    entry, warning = select_tool("grooving", "N", turret)
    assert warning is None
    assert entry.position == 5


def test_select_tool_missing_iso_group_gives_warning(turret):
    entry, warning = select_tool("grooving", "M", turret)
    assert entry is None
    assert "ISO M" in warning and "grooving" in warning


def test_select_tool_missing_type_gives_warning(turret):
    entry, warning = select_tool("boring", "P", turret)
    assert entry is None
    assert "No boring tool in the turret" in warning


def test_missing_tool_operation_is_kept_with_warning(turret):
    job = JobSpec("P", 60, 100, (FeatureSpec(1, "bore", diameter=30),))
    ops = plan_job(job, turret, max_rpm=4000)
    # centring, drilling (no boring tool: no allowance, no drill), rough and finish boring
    assert [op.tool_type for op in ops] == ["centre_drilling", "drilling", "boring", "boring"]
    for op in ops:
        assert op.tool_id is None and op.n is None
        assert op.warnings


# --- finish feed from Ra (Ra formula, 32 not 8) --------------------------

def test_finish_feed_from_ra_formula():
    # f = sqrt(1.6 * 32 * 0.8 / 1000) = 0.2024
    assert finish_feed_from_ra(1.6, 0.8, 0.01, 1.0) == pytest.approx(math.sqrt(1.6 * 32 * 0.8 / 1000), abs=1e-3)
    assert finish_feed_from_ra(1.6, 0.8, 0.01, 1.0) == pytest.approx(0.202, abs=1e-3)


def test_finish_feed_clamped_to_tool_range():
    assert finish_feed_from_ra(0.1, 0.4, 0.08, 0.2) == 0.08  # raw ~0.036
    assert finish_feed_from_ra(12.5, 1.2, 0.08, 0.2) == 0.2  # raw ~0.69


def test_nose_radius_from_insert_code():
    assert nose_radius_from_insert("CNMG 120408") == 0.8
    assert nose_radius_from_insert("DNMG150404") == 0.4
    assert nose_radius_from_insert("GRV 3.0") == 0.4  # fallback


# --- rough vs finish cutting data ----------------------------------------

def test_rough_uses_low_vc_and_high_f_ap():
    tool = make_tool(1, "turning_rough")
    vc, f, ap = cutting_data(tool, "rough")
    assert vc < (tool.vc_min + tool.vc_max) / 2
    assert f > (tool.f_min + tool.f_max) / 2
    assert ap > (tool.ap_min + tool.ap_max) / 2


def test_finish_uses_high_vc_small_ap_and_ra_feed():
    tool = make_tool(4, "turning_finish", insert="DNMG 150408", f_min=0.05, f_max=0.3)
    vc, f, ap = cutting_data(tool, "finish", ra=1.6)
    assert vc > (tool.vc_min + tool.vc_max) / 2
    assert ap == tool.ap_min
    assert f == finish_feed_from_ra(1.6, 0.8, 0.05, 0.3)


# --- threading feed = pitch ----------------------------------------------

def test_threading_feed_equals_pitch(turret):
    job = JobSpec("P", 30, 60, (FeatureSpec(1, "thread", diameter=20, ra=0.8, pitch=1.5),))
    (op,) = plan_job(job, turret, max_rpm=4000)
    assert op.tool_type == "threading"
    assert op.f == 1.5


def test_threading_without_pitch_warns(turret):
    job = JobSpec("P", 30, 60, (FeatureSpec(1, "thread", diameter=20),))
    (op,) = plan_job(job, turret, max_rpm=4000)
    assert op.f is None
    assert any("pitch" in w for w in op.warnings)


# --- rough passes with finishing allowance -------------------------------

def test_rough_passes_formula():
    assert rough_passes(60, 50, 2.0) == (3, 1.667)  # 5 mm / 2.0 -> 3 equal passes
    assert rough_passes(60, 52, 2.0) == (2, 2.0)  # exactly 2
    assert rough_passes(50, 50, 2.0) == (0, 0.0)


def test_rough_passes_leave_finish_allowance():
    # stock = (60 - 50) / 2 - 0.2 = 4.8 -> ceil(4.8 / 2.0) = 3 passes of 1.6
    assert rough_passes(60, 50, 2.0, finish_allowance=0.2) == (3, 1.6)
    # allowance eats all the stock
    assert rough_passes(40.4, 40, 2.0, finish_allowance=0.2) == (0, 0.0)


def test_rough_operation_splits_stock_evenly(turret):
    job = JobSpec("P", 60, 100, (FeatureSpec(1, "od_turn", diameter=40),))
    rough, finish = plan_job(job, turret, max_rpm=4000)
    rough_tool = turret[1].tool
    stock = (60 - 40) / 2 - finish.ap  # finishing ap is the allowance
    assert rough.passes == math.ceil(stock / rough_tool.ap_max)
    assert rough.ap == pytest.approx(stock / rough.passes, abs=1e-3)
    assert rough.ap <= rough_tool.ap_max
    assert any(f"leaves {finish.ap:g} mm/side" in n for n in rough.notes)
    assert finish.passes is None


def test_rough_uses_default_allowance_without_finish_tool(turret):
    turret = [e for e in turret if e.tool.type != "turning_finish"]
    job = JobSpec("P", 60, 100, (FeatureSpec(1, "od_turn", diameter=40),))
    rough = plan_job(job, turret, max_rpm=4000)[0]
    stock = 10 - DEFAULT_FINISH_ALLOWANCE_MM
    assert rough.ap * rough.passes == pytest.approx(stock, abs=1e-2)
    assert any("default allowance" in n for n in rough.notes)


# --- reference diameter and notes ----------------------------------------

def test_rough_n_uses_blank_diameter(turret):
    job = JobSpec("P", 80, 100, (FeatureSpec(1, "od_turn", diameter=40),))
    rough, finish = plan_job(job, turret, max_rpm=10000)
    assert rough.ref_diameter == 80
    assert rough.n == spindle_speed(rough.vc, 80, 10000)[0]
    assert finish.ref_diameter == 40


def test_face_and_parting_get_g96_note(turret):
    job = JobSpec("P", 60, 100, (FeatureSpec(1, "face"), FeatureSpec(2, "parting")))
    ops = plan_job(job, turret, max_rpm=4000)
    assert [op.tool_type for op in ops] == ["facing", "parting"]
    assert all(G96_NOTE in op.notes for op in ops)


# --- ordering ------------------------------------------------------------

def test_operation_order():
    features = [
        FeatureSpec(1, "parting"),
        FeatureSpec(2, "thread", diameter=20, pitch=1.5),
        FeatureSpec(3, "groove", diameter=18),
        FeatureSpec(4, "od_turn", diameter=20),
        FeatureSpec(5, "face"),
    ]
    steps = order_steps([s for f in features for s in feature_to_steps(f)])
    assert [s.stage for s in steps] == ["face", "rough", "finish", "groove", "thread", "parting"]


# --- chamfer is part of the finishing pass -------------------------------

def test_chamfer_merged_into_finish_pass(turret):
    job = JobSpec("P", 60, 100, (
        FeatureSpec(1, "od_turn", diameter=40, ra=1.6),
        FeatureSpec(2, "chamfer", diameter=40),
    ))
    ops = plan_job(job, turret, max_rpm=4000)
    assert [(op.feature_id, op.mode) for op in ops] == [(1, "rough"), (1, "finish")]
    assert CHAMFER_NOTE in ops[1].notes
    assert CHAMFER_NOTE not in ops[0].notes


def test_chamfer_without_matching_diameter_gets_own_pass(turret):
    job = JobSpec("P", 60, 100, (
        FeatureSpec(1, "od_turn", diameter=40),
        FeatureSpec(2, "chamfer", diameter=30),
    ))
    ops = plan_job(job, turret, max_rpm=4000)
    chamfer_ops = [op for op in ops if op.feature_id == 2]
    assert len(chamfer_ops) == 1
    assert any("chamfer machined separately" in n for n in chamfer_ops[0].notes)
    assert all(CHAMFER_NOTE not in op.notes for op in ops)


# --- groove: n from the start diameter, width and depth instead of ap -----

def test_groove_speed_from_start_diameter(turret):
    job = JobSpec("P", 60, 100, (FeatureSpec(1, "groove", diameter=36, length=3, start_diameter=40),))
    (op,) = plan_job(job, turret, max_rpm=4000)
    assert op.ref_diameter == 40
    assert op.n == spindle_speed(op.vc, 40, 4000)[0]
    assert op.n < spindle_speed(op.vc, 36, 4000)[0]  # not computed at the bottom


def test_groove_shows_width_and_depth_instead_of_ap(turret):
    job = JobSpec("P", 60, 100, (FeatureSpec(1, "groove", diameter=36, start_diameter=40),))
    (op,) = plan_job(job, turret, max_rpm=4000)
    assert op.ap is None
    assert op.insert_width == 3.0
    assert op.depth == 2.0  # (40 - 36) / 2 per side


def test_groove_without_start_diameter_uses_blank(turret):
    job = JobSpec("P", 60, 100, (FeatureSpec(1, "groove", diameter=36),))
    (op,) = plan_job(job, turret, max_rpm=4000)
    assert op.ref_diameter == 60
    assert op.depth == 12.0
    assert any("blank" in n for n in op.notes)


# --- metric external thread ----------------------------------------------

def test_thread_depth_is_0613_pitch():
    assert thread_depth(1.5) == pytest.approx(0.613 * 1.5, abs=1e-3)
    assert thread_depth(1.0) == 0.613


def test_thread_infeed_decreases_and_ends_with_spring_pass():
    h = thread_depth(1.5)
    infeed, method = thread_infeed(h, ap_max=0.2, ap_min=0.05)
    cutting, spring = infeed[:-1], infeed[-1]
    assert method == "decreasing"
    assert spring == 0.0
    assert sum(cutting) == pytest.approx(h, abs=1e-3)
    assert cutting[0] <= 0.2
    assert all(a > b for a, b in zip(cutting, cutting[1:]))  # strictly decreasing
    assert all(d >= 0.05 for d in cutting)


# (pitch, ap_max, ap_min): common pitches, tight and loose ap ranges
INFEED_CASES = [
    (p, ap_max, ap_min)
    for p in (0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0, 4.0)
    for ap_max, ap_min in ((0.2, 0.05), (0.1, 0.06), (0.35, 0.03), (0.15, 0.1), (0.3, 0.15))
]


@pytest.mark.parametrize("pitch, ap_max, ap_min", INFEED_CASES)
def test_thread_infeed_never_increases(pitch, ap_max, ap_min):
    h = thread_depth(pitch)
    infeed, _ = thread_infeed(h, ap_max, ap_min)
    # every next pass is <= the previous one, spring pass included
    assert all(b <= a for a, b in zip(infeed, infeed[1:])), infeed
    assert sum(infeed) == pytest.approx(h, abs=1e-6)
    assert infeed[-1] == 0.0
    assert infeed[0] <= ap_max + 1e-9


def test_thread_infeed_small_remainder_recalculated_evenly():
    # The decreasing series for M1.0 with ap 0.06..0.1 would need passes < 0.06:
    # the depth is spread evenly instead of adding a remainder to the last pass.
    infeed, method = thread_infeed(0.613, ap_max=0.1, ap_min=0.06)
    cutting = infeed[:-1]
    assert method == "equal"
    assert all(d >= 0.06 for d in cutting)
    assert max(cutting) - min(cutting) <= 0.001 + 1e-9  # only rounding differences
    assert sum(cutting) == pytest.approx(0.613, abs=1e-6)


def test_thread_operation_shows_depth_passes_and_g97(turret):
    job = JobSpec("P", 30, 60, (FeatureSpec(1, "thread", diameter=20, pitch=1.5),))
    (op,) = plan_job(job, turret, max_rpm=4000)
    h = thread_depth(1.5)
    assert op.depth == h
    assert op.passes == 6 + 1  # P1.5: 6 passes by Sandvik TT 2020 C77, then the spring pass
    assert op.ap is None
    assert G97_THREAD_NOTE in op.notes
    assert G96_NOTE not in op.notes


# --- parting: width and depth instead of ap -------------------------------

def test_parting_to_center(turret):
    job = JobSpec("P", 60, 100, (FeatureSpec(1, "parting"),))
    (op,) = plan_job(job, turret, max_rpm=4000)
    assert op.ap is None
    assert op.insert_width == 3.0
    assert op.depth == 30.0  # blank diameter / 2
    assert PARTING_CENTER_NOTE in op.notes
    assert "reduce feed ~50% for last 2 mm before center" in op.notes
    assert G96_NOTE in op.notes


def test_parting_uses_feature_diameter(turret):
    job = JobSpec("P", 60, 100, (FeatureSpec(1, "parting", diameter=40),))
    (op,) = plan_job(job, turret, max_rpm=4000)
    assert op.depth == 20.0
    assert op.ref_diameter == 40


def test_parting_to_inner_diameter(turret):
    turret = turret + [TurretEntry(9, make_tool(9, "boring"))]
    job = JobSpec("P", 60, 100, (
        FeatureSpec(1, "bore", diameter=24),
        FeatureSpec(2, "bore", diameter=20),
        FeatureSpec(3, "parting"),
    ))
    op = plan_job(job, turret, max_rpm=4000)[-1]
    assert op.tool_type == "parting"
    assert op.depth == 20.0  # (60 - 20) / 2, to the smallest bore
    assert PARTING_BORE_NOTE in op.notes
    assert "reduce feed ~50% for last 2 mm before breakthrough into bore" in op.notes
    assert PARTING_CENTER_NOTE not in op.notes


# --- blank suggestion ----------------------------------------------------------------

from turnpilot.planner import GRINDING_WARNING, iso_fit_grade, needs_grinding, suggest_blank, tolerance_band_mm  # noqa: E402

BARS = (20, 22, 25, 28, 30, 32, 35, 36, 38, 40, 42, 45, 48, 50)


def _suggest(features, overall_length=None, bars=BARS):
    return suggest_blank(features, overall_length, bars, diameter_allowance=2.0, facing_allowance=2.0,
                         parting_width=3.0)


def test_blank_diameter_rounds_up_to_next_bar():
    s = _suggest([FeatureSpec(1, "od_turn", diameter=40, length=30), FeatureSpec(2, "od_turn", diameter=32, length=50)])
    assert s.diameter == 42  # 40 + 2 = 42, an exact bar size
    s = _suggest([FeatureSpec(1, "od_turn", diameter=41, length=30)])
    assert s.diameter == 45  # 41 + 2 = 43 -> next bar 45


def test_blank_length_adds_facing_and_parting():
    s = _suggest([FeatureSpec(1, "od_turn", diameter=30, length=60)], overall_length=90)
    assert s.length == 95  # 90 + 2 facing + 3 parting width
    assert s.notes == ()


def test_blank_length_falls_back_to_sum_of_sections():
    s = _suggest([FeatureSpec(1, "od_turn", diameter=40, length=30), FeatureSpec(2, "od_turn", diameter=32, length=50.5)])
    assert s.length == 86  # 80.5 + 5 = 85.5 -> rounded up
    assert any("sum of the OD sections" in n for n in s.notes)


def test_blank_ignores_bores_and_uses_groove_start_diameter():
    s = _suggest([
        FeatureSpec(1, "bore", diameter=60, length=20),
        FeatureSpec(2, "groove", diameter=30, start_diameter=35, length=3),
        FeatureSpec(3, "od_turn", diameter=33, length=20),
    ], overall_length=20)
    assert s.diameter == 38  # groove start Ø35 + 2 = 37 -> 38; the Ø60 bore does not count


def test_blank_larger_than_bar_list():
    s = _suggest([FeatureSpec(1, "od_turn", diameter=49.5, length=10)], overall_length=10)
    assert s.diameter is None
    assert any("No bar size" in n for n in s.notes)


def test_blank_without_dimensions():
    s = _suggest([FeatureSpec(1, "face")])
    assert s.diameter is None and s.length is None and len(s.notes) == 2


# --- grinding warning ------------------------------------------------------------------

@pytest.mark.parametrize(
    "tolerance, diameter, ra, expected",
    [
        ("h6", 30, None, False),
        ("h5", 30, None, True),  # IT5
        ("js4", 30, None, True),  # finer than IT5
        ("H7", 30, 1.6, False),
        (None, 30, 0.4, True),  # Ra <= 0.4
        (None, 30, 0.2, True),
        ("h6", 30, 0.8, False),
        ("±0.004", 30, None, True),  # band 0.008 <= IT5 for Ø30 (0.009)
        ("±0.005", 30, None, False),  # band 0.010 > 0.009
        ("0/-0.011", 40, None, True),  # IT5 for Ø30-50 is 0.011
        ("+0.02/-0.01", 30, None, False),
        ("6g", 20, None, False),  # thread class is not a fit grade
        ("M20x1.5-6g", 20, None, False),
        (None, None, None, False),
    ],
)
def test_needs_grinding(tolerance, diameter, ra, expected):
    assert needs_grinding(tolerance, diameter, ra) is expected


def test_tolerance_parsing():
    assert iso_fit_grade("h6") == 6 and iso_fit_grade("H7") == 7 and iso_fit_grade("js5") == 5
    assert iso_fit_grade("M20x1.5") is None and iso_fit_grade("±0.01") is None
    assert tolerance_band_mm("±0.01") == 0.02
    assert tolerance_band_mm("+0.02/-0.01") == 0.03
    assert tolerance_band_mm("0.05") is None


def test_grinding_warning_on_finish_operations_only(turret):
    job = JobSpec("P", 60, 100, (
        FeatureSpec(1, "od_turn", diameter=40, tolerance="h5"),
        FeatureSpec(2, "od_turn", diameter=30, ra=0.4),
        FeatureSpec(3, "od_turn", diameter=20, tolerance="h7", ra=1.6),
    ))
    ops = plan_job(job, turret, max_rpm=4000)
    flagged = {(op.feature_id, op.mode) for op in ops if GRINDING_WARNING in op.warnings}
    assert flagged == {(1, "finish"), (2, "finish")}


def test_grinding_warning_kept_when_tool_is_missing(turret):
    turret = [e for e in turret if e.tool.type != "turning_finish"]
    job = JobSpec("P", 60, 100, (FeatureSpec(1, "od_turn", diameter=40, tolerance="h5"),))
    finish = plan_job(job, turret, max_rpm=4000)[1]
    assert finish.tool_id is None
    assert GRINDING_WARNING in finish.warnings


# --- OD under an external thread ----------------------------------------------------------

from turnpilot.planner import THREAD_MAJOR_NOTE, thread_major_diameter  # noqa: E402


def test_thread_major_diameter():
    assert thread_major_diameter(20, 1.5) == 19.85
    assert thread_major_diameter(12, 1.75) == 11.825
    assert thread_major_diameter(20, 1.0) == 19.9


def test_od_under_thread_turned_to_major_diameter(turret):
    job = JobSpec("P", 40, 100, (
        FeatureSpec(1, "od_turn", diameter=30, length=60),
        FeatureSpec(2, "od_turn", diameter=20, length=30),
        FeatureSpec(3, "thread", diameter=20, length=25, pitch=1.5),
    ))
    ops = plan_job(job, turret, max_rpm=4000)
    rough20, finish20 = [op for op in ops if op.feature_id == 2]

    assert finish20.ref_diameter == 19.85
    assert f"{THREAD_MAJOR_NOTE}: Ø19.85 (nominal Ø20)" in finish20.notes
    # roughing stock is taken down to the reduced diameter as well
    stock = (40 - 19.85) / 2 - finish20.ap
    assert rough20.ap * rough20.passes == pytest.approx(stock, abs=1e-2)
    assert not any(THREAD_MAJOR_NOTE in n for n in rough20.notes)

    # the plain Ø30 section and the thread itself are unchanged
    finish30 = next(op for op in ops if op.feature_id == 1 and op.mode == "finish")
    assert finish30.ref_diameter == 30
    assert not any(THREAD_MAJOR_NOTE in n for n in finish30.notes)
    thread = next(op for op in ops if op.tool_type == "threading")
    assert thread.ref_diameter == 20


def test_od_without_thread_keeps_nominal_diameter(turret):
    job = JobSpec("P", 40, 100, (
        FeatureSpec(1, "od_turn", diameter=20, length=30),
        FeatureSpec(2, "thread", diameter=16, length=20, pitch=2.0),  # different diameter
    ))
    finish = next(op for op in plan_job(job, turret, max_rpm=4000) if op.mode == "finish")
    assert finish.ref_diameter == 20
    assert not any(THREAD_MAJOR_NOTE in n for n in finish.notes)


def test_chamfer_still_merged_on_thread_diameter(turret):
    job = JobSpec("P", 40, 100, (
        FeatureSpec(1, "od_turn", diameter=20, length=30),
        FeatureSpec(2, "chamfer", diameter=20, length=1),
        FeatureSpec(3, "thread", diameter=20, length=25, pitch=1.5),
    ))
    finish = next(op for op in plan_job(job, turret, max_rpm=4000) if op.mode == "finish")
    assert CHAMFER_NOTE in finish.notes
    assert any(THREAD_MAJOR_NOTE in n for n in finish.notes)


# --- taper and fillet: recognised, not planned ----------------------------------------------

from turnpilot.planner import MANUAL_OPERATION_WARNING  # noqa: E402


def test_fillet_and_arc_become_manual_operations_a_taper_is_turned(turret):
    job = JobSpec("P", 85, 150, (
        FeatureSpec(1, "od_turn", diameter=60, length=60),
        FeatureSpec(2, "taper", diameter=55, start_diameter=60, length=30),
        FeatureSpec(3, "fillet", radius=10),
        FeatureSpec(4, "arc", diameter=40, start_diameter=55, radius=12.5, length=15),
    ))
    ops = plan_job(job, turret, max_rpm=4000)
    manual = [op for op in ops if op.tool_type == "manual"]
    assert [op.feature_id for op in manual] == [3, 4]
    taper = [op for op in ops if op.feature_id == 2]
    assert [(op.tool_type, op.mode) for op in taper] == [("turning_rough", "rough"), ("turning_finish", "finish")]
    assert all(op.tool_id and op.vc and op.f for op in taper)
    for op in manual:
        assert op.warnings == [MANUAL_OPERATION_WARNING]
        assert op.tool_id is None and op.n is None
    assert all(op.tool_id for op in ops if op.feature_id == 1)  # the rest is planned as usual


def test_blank_suggestion_counts_taper_diameters():
    s = _suggest([FeatureSpec(1, "od_turn", diameter=30, length=40),
                  FeatureSpec(2, "taper", diameter=34, start_diameter=38, length=20)], overall_length=60)
    assert s.diameter == 40  # taper start Ø38 + 2 = 40


# --- geometry checks ------------------------------------------------------------------------

from turnpilot.planner import geometry_warnings  # noqa: E402

REAL_SHAFT = [  # the operator-checked real shaft (lengths from baseline dimensions)
    FeatureSpec(1, "od_turn", diameter=80, length=40.1),
    FeatureSpec(2, "fillet", radius=10),
    FeatureSpec(3, "od_turn", diameter=60, length=40),
    FeatureSpec(4, "taper", diameter=55, start_diameter=60, length=30),
    FeatureSpec(5, "groove", diameter=44, start_diameter=48, length=4),
    FeatureSpec(6, "od_turn", diameter=48, length=16),
    FeatureSpec(7, "thread", diameter=48, length=16, pitch=1.5),
    FeatureSpec(8, "chamfer", diameter=48, length=1.5),
]


def test_consistent_geometry_has_no_warnings():
    # 40.1 + 10 (fillet R10 along the axis) + 40 + 30 + 4 + 16 = 140.1; thread and chamfer lie on top
    assert geometry_warnings(REAL_SHAFT, 140.1) == []


def test_section_sum_mismatch():
    # the model's typical mistake: the 20 mm dimension taken as the Ø48 section, groove included
    features = [f if f.id != 6 else FeatureSpec(6, "od_turn", diameter=48, length=20) for f in REAL_SHAFT]
    assert geometry_warnings(features, 140.1) == ["section lengths sum to 144.1, overall length is 140.1"]


def test_sum_within_tolerance():
    assert geometry_warnings(REAL_SHAFT, 140.25) == []
    assert geometry_warnings(REAL_SHAFT, 140.35) != []


def test_missing_lengths_are_reported():
    features = [f if f.id != 3 else FeatureSpec(3, "od_turn", diameter=60) for f in REAL_SHAFT]
    assert geometry_warnings(features, 140.1) == [
        "section lengths sum to 100.1, overall length is 140.1 (1 section without length)"
    ]


def test_thread_longer_than_its_section():
    features = [f if f.id != 7 else FeatureSpec(7, "thread", diameter=48, length=20, pitch=1.5) for f in REAL_SHAFT]
    assert geometry_warnings(features, 140.1) == ["thread Ø48 is 20 long, longer than its section (16)"]


def test_no_overall_length_skips_the_sum():
    assert geometry_warnings([FeatureSpec(1, "od_turn", diameter=20, length=5)], None) == []


# --- the section a thread is cut on: diameter and length ------------------------------------

from turnpilot.planner import geometry_warnings, thread_section  # noqa: E402


def _fitting():
    """The fitting from real_03: a Ø10 collar L3.5 and an M10×1.5 thread L8 on a Ø10 section L8."""
    return (
        FeatureSpec(1, "od_turn", diameter=10, length=3.5),
        FeatureSpec(2, "groove", diameter=8, length=8, start_diameter=10),
        FeatureSpec(3, "od_turn", diameter=13, length=3.5),
        FeatureSpec(4, "groove", diameter=8, length=1.5, start_diameter=10),
        FeatureSpec(5, "od_turn", diameter=10, length=8),
        FeatureSpec(6, "thread", diameter=10, length=8, pitch=1.5),
    )


def test_thread_section_uses_length_as_well_as_diameter():
    features = _fitting()
    assert thread_section(features[5], features) is features[4]


def test_collar_of_thread_diameter_not_reduced(turret):
    ops = plan_job(JobSpec("P", 16, 30, _fitting()), turret, max_rpm=4000)
    collar = next(op for op in ops if op.feature_id == 1 and op.mode == "finish")
    section = next(op for op in ops if op.feature_id == 5 and op.mode == "finish")
    assert collar.ref_diameter == 10
    assert not any(THREAD_MAJOR_NOTE in n for n in collar.notes)
    assert section.ref_diameter == 9.85


def test_no_false_thread_length_warning_on_fitting():
    assert geometry_warnings(_fitting(), 24.5) == []


def test_thread_longer_than_every_section_still_warned():
    features = (
        FeatureSpec(1, "od_turn", diameter=10, length=3.5),
        FeatureSpec(2, "od_turn", diameter=10, length=6),
        FeatureSpec(3, "thread", diameter=10, length=8, pitch=1.5),
    )
    assert thread_section(features[2], features) is features[1]
    assert geometry_warnings(features, None) == ["thread Ø10 is 8 long, longer than its section (6)"]


def test_a_taper_is_roughed_from_its_larger_ends_neighbour(turret):
    """Zavisa 36 pin: Ø22 then a taper Ø22 → Ø13 at the free end; the taper's steps start at Ø22."""
    job = JobSpec("P", 38, 120, (
        FeatureSpec(1, "od_turn", diameter=34.8, length=52.5), FeatureSpec(2, "od_turn", diameter=22, length=26),
        FeatureSpec(3, "taper", start_diameter=22, diameter=13, length=6),
    ), axial_order=True)
    rough = next(op for op in plan_job(job, turret, max_rpm=4000) if op.feature_id == 3 and op.mode == "rough")
    allowance = float(next(n for n in rough.notes if n.startswith("leaves")).split()[1])
    assert rough.ref_diameter == 22 and rough.passes * rough.ap == pytest.approx((22 - 13) / 2 - allowance)
    assert "roughed from Ø22, the neighbouring section towards the chuck" in rough.notes
    no_order = replace(job, axial_order=False)
    rough = next(op for op in plan_job(no_order, turret, max_rpm=4000) if op.feature_id == 3 and op.mode == "rough")
    assert rough.ref_diameter == 38  # from the bar
