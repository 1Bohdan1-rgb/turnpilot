import math

import pytest

from turnpilot.planner import (
    CHAMFER_NOTE,
    DEFAULT_FINISH_ALLOWANCE_MM,
    G96_NOTE,
    G97_THREAD_NOTE,
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
        TurretEntry(7, make_tool(7, "parting")),
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
    assert len(ops) == 2
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
    infeed = thread_infeed(h, ap_max=0.2, ap_min=0.05)
    cutting, spring = infeed[:-1], infeed[-1]
    assert spring == 0.0
    assert sum(cutting) == pytest.approx(h, abs=1e-3)
    assert cutting[0] <= 0.2
    assert all(a > b for a, b in zip(cutting, cutting[1:]))  # strictly decreasing
    assert all(d >= 0.05 for d in cutting)


def test_thread_infeed_respects_min_depth():
    infeed = thread_infeed(0.613, ap_max=0.1, ap_min=0.06)
    assert all(d >= 0.06 for d in infeed[:-1])
    assert sum(infeed) == pytest.approx(0.613, abs=1e-3)


def test_thread_operation_shows_depth_passes_and_g97(turret):
    job = JobSpec("P", 30, 60, (FeatureSpec(1, "thread", diameter=20, pitch=1.5),))
    (op,) = plan_job(job, turret, max_rpm=4000)
    h = thread_depth(1.5)
    assert op.depth == h
    assert op.passes == len(thread_infeed(h, 0.2, 0.05))
    assert op.ap is None
    assert G97_THREAD_NOTE in op.notes
    assert G96_NOTE not in op.notes
