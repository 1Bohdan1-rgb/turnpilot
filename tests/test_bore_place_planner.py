"""The planner with a bore's place: its depth from the face, a flat bottom that the drill cannot make, the boring bar's
overhang (a rule of thumb: a warning only)."""
from dataclasses import replace

from test_drilling import DRILLS, _of, _ops
from turnpilot import planner
from turnpilot.planner import FeatureSpec, TurretEntry


def test_a_stepped_bores_narrow_part_is_drilled_from_the_face():
    ops = _ops([FeatureSpec(1, "od_turn", diameter=34, length=60),
                FeatureSpec(2, "bore", diameter=22, length=20, tolerance="H7", bore_from=0, bore_bottom="flat"),
                FeatureSpec(3, "bore", diameter=10, length=8, bore_from=20, bore_bottom="drill_point")])
    (drill,) = [op for op in _of(ops, 3) if op.tool_type == "drilling"]
    assert drill.depth == 28  # 20 + 8 from the face
    (wide,) = [op for op in _of(ops, 2) if op.tool_type == "drilling"]
    assert wide.depth == 20


def test_a_flat_bottom_needs_the_boring_bar():
    drill_only = _ops([FeatureSpec(1, "bore", diameter=10, length=15)])  # IT12-free, a Ø10 drill: drilled to size
    assert [op.tool_type for op in drill_only if op.tool_type != "centre_drilling"] == ["drilling"]
    flat = _ops([FeatureSpec(1, "bore", diameter=10, length=15, bore_bottom="flat")])
    types = [op.tool_type for op in flat if op.tool_type != "centre_drilling"]
    assert "drilling" in types and "boring" in types
    (drill,) = [op for op in flat if op.tool_type == "drilling"]
    assert any(planner.FLAT_BOTTOM_NOTE in n for n in drill.notes)


def test_a_flat_bottom_no_bar_goes_into():
    thick = [replace(e, tool=replace(e.tool, min_bore_diameter=16)) if e.tool.type in ("boring", "boring_rough")
             else e for e in DRILLS]
    ops = _ops([FeatureSpec(1, "bore", diameter=10, length=15, bore_bottom="flat")], turret=thick)
    (drill,) = [op for op in ops if op.tool_type == "drilling"]
    assert any("the flat bottom of Ø10 needs a boring bar" in w for w in drill.warnings)


def _bars(overhang):
    return [TurretEntry(e.position, replace(e.tool, max_depth=overhang, min_bore_diameter=12))
            if e.tool.type in ("boring", "boring_rough") else e for e in DRILLS]


def test_bar_overhang_rule_of_thumb_is_a_warning():
    bore = FeatureSpec(1, "bore", diameter=22, length=32, tolerance="H9", bore_from=0, bore_bottom="drill_point")
    ops = _ops([bore], turret=_bars(60))  # 60 > 4 × DMIN 12
    boring = [op for op in ops if op.tool_type in ("boring", "boring_rough")]
    assert boring and all(any("more than 4 × D (D taken as its DMIN 12)" in w for w in op.warnings) for op in boring)
    assert all(op.tool_id for op in boring)  # nothing blocked
    ops = _ops([bore], turret=_bars(40))
    assert not any("4 × D" in w for op in ops for w in op.warnings)
    ops = _ops([bore], turret=_bars(30))  # shorter than the 32 mm bore
    assert any("the bar's overhang reaches 30" in w for op in ops for w in op.warnings)
