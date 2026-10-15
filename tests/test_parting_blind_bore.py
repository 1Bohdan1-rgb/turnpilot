"""Parting: to the bore only when the bore runs the whole length; a blind bore leaves a solid core to cut through."""
from conftest import seed_turret

from turnpilot.planner import FeatureSpec, JobSpec, plan_job


def parting(features):
    ops = plan_job(JobSpec("P", 22, 29, tuple(features)), seed_turret(), max_rpm=4000)
    return next(op for op in ops if op.tool_type == "parting")


def stud(bore_length):
    """03057789-2-01: M20 × 24 with a Ø10 bore from the free end."""
    return [FeatureSpec(1, "od_turn", diameter=20, length=24), FeatureSpec(2, "bore", diameter=10, length=bore_length,
                                                                           ra=6.3),
            FeatureSpec(3, "parting")]


def test_a_blind_bore_does_not_reach_the_cut():
    op = parting(stud(5))
    assert op.depth == 11.0  # Ø22 to the axis
    assert "the bore Ø10 × 5 does not reach the cut (part length 24): parting to the axis" in op.notes
    assert not any(n.startswith("parting to bore") for n in op.notes)


def test_a_through_bore_is_parted_to():
    op = parting(stud(24))
    assert op.depth == 6.0 and "parting to bore Ø10" in op.notes
