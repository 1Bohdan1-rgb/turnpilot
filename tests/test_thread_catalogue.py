"""Thread passes: the count from Sandvik TT 2020 C77, the infeed series from C82; depth stays the formula."""
import math

import pytest
from conftest import seed_turret

from turnpilot import planner
from turnpilot.planner import FeatureSpec, JobSpec, ToolSpec, TurretEntry, catalogue_thread_passes, plan_job

SEED = seed_turret()


def _thread_op(pitch, diameter=20):
    features = (FeatureSpec(1, "od_turn", diameter=diameter, length=25),
                FeatureSpec(2, "thread", diameter=diameter, length=25, pitch=pitch))
    return next(op for op in plan_job(JobSpec("P", 30, 60, features), SEED, 4000) if op.tool_type == "threading")


@pytest.mark.parametrize("pitch, passes", [(1.0, 5), (1.5, 6), (2.0, 8)])
def test_passes_from_c77(pitch, passes):
    op = _thread_op(pitch)
    assert op.passes == passes + 1  # and the spring pass
    assert f"{passes} passes: Sandvik TT 2020 C77 (count) and C82 (infeed series)" in op.notes
    h = planner.thread_depth(pitch)
    assert op.depth == h and f"profile depth {h:g} (0.613·P) and the spring pass: not in the catalogue" in op.notes
    assert not any("ap_min" in w for w in op.warnings)  # the placeholder ap range of T7 is not used


def test_infeed_series_is_c82():
    # C82: cumulative depth after pass x = h·√(ϕ / (n − 1)), ϕ = 0.3 for the first pass, then x − 1
    h, n = planner.thread_depth(1.5), 6
    cumulative = [h * math.sqrt((0.3 if x == 1 else x - 1) / (n - 1)) for x in range(1, n + 1)]
    expected = [round(b - a, 3) for a, b in zip([0.0] + cumulative, cumulative)]
    assert planner._decreasing_infeed(h, n) == pytest.approx(expected, abs=1e-3)
    op = _thread_op(1.5)
    assert op.notes[-3] == "radial infeed per pass: " + ", ".join(f"{d:g}" for d in planner._decreasing_infeed(h, n)) \
        + " + spring pass"


@pytest.mark.parametrize("pitch", [1.25, 3.0])
def test_pitch_not_in_c77_as_before(pitch):
    op = _thread_op(pitch, diameter=24)
    assert not any("C77" in n for n in op.notes)
    h = planner.thread_depth(pitch)
    assert op.passes == len(planner.thread_infeed(h, SEED[6].tool.ap_max, SEED[6].tool.ap_min)[0])


def test_catalogue_thread_passes():
    assert catalogue_thread_passes(1.5) == 6 and catalogue_thread_passes(1.5, "internal") == 6
    assert catalogue_thread_passes(2.0, "internal") is None and catalogue_thread_passes(1.75) is None


def test_internal_threading_bar_takes_c77_internal_count():
    bar = ToolSpec(id=90, name="bar", type="threading_internal", iso_group="P", insert_code="", vc_min=100,
                   vc_max=150, f_min=1, f_max=2, ap_min=0.05, ap_max=0.15)
    features = (FeatureSpec(1, "thread", diameter=20, length=20, pitch=1.5, location="internal"),)
    ops = plan_job(JobSpec("P", 30, 60, features), SEED + [TurretEntry(10, bar)], 4000)
    op = next(op for op in ops if op.tool_type == "threading_internal")
    assert op.passes == 6 + 1 and "6 passes: Sandvik TT 2020 C77 (count) and C82 (infeed series)" in op.notes
    assert "profile depth 0.812 (0.541·P) and the spring pass: not in the catalogue" in op.notes
