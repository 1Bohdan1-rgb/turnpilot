"""DMIN: a boring holder does not go into a bore smaller than its DMIN (the catalogue's, taken by the operator)."""
from dataclasses import replace

from conftest import seed_turret

from turnpilot.models import Tool, db
from turnpilot.planner import FeatureSpec, JobSpec, dmin_suggestions, plan_job


def turret(dmin_rough=None, dmin_finish=None):
    out = []
    for e in seed_turret():
        if e.tool.type == "boring_rough":
            e = replace(e, tool=replace(e.tool, min_bore_diameter=dmin_rough))
        elif e.tool.type == "boring":
            e = replace(e, tool=replace(e.tool, min_bore_diameter=dmin_finish))
        out.append(e)
    return out


def hole_ops(t, diameter=10.0, length=5.0, tolerance=None, ra=6.3):
    features = (FeatureSpec(1, "od_turn", diameter=20, length=24),
                FeatureSpec(2, "bore", diameter=diameter, length=length, tolerance=tolerance, ra=ra))
    return [op for op in plan_job(JobSpec("P", 22, 29, features), t, max_rpm=4000)
            if op.feature_id == 2 and op.tool_type != "centre_drilling"]


def test_suggestions_from_the_catalogue():
    assert dmin_suggestions("CCMT09T304-PF") == [("A16R-SCLCR/L 09-R", 20.0, "Sandvik TT 2020 (PDF 106)"),
                                                ("A20S-SCLCR/L 09-R", 25.0, "Sandvik TT 2020 (PDF 106)")]
    assert dmin_suggestions("CCMT 09 T3 04-PM", "A20S-SCLCR 09-R") == [("A20S-SCLCR/L 09-R", 25.0,
                                                                         "Sandvik TT 2020 (PDF 106)")]
    assert dmin_suggestions("CNMG120408-PM") == []


def test_a_bore_too_small_for_every_holder_is_drilled_to_size():
    # 03057789-2-01: Ø10 × 5, the CCMT 09 bars go into Ø20 / Ø25 at least; no Ø10 drill in the turret
    t = [e for e in turret(20, 25) if not (e.tool.type == "drilling" and e.tool.diameter == 10.0)]
    ops = hole_ops(t)
    assert [op.tool_type for op in ops] == ["drilling"]
    (drill,) = ops
    assert drill.tool_id is None and drill.ref_diameter == 10
    assert drill.warnings == ["no boring tool goes into Ø10 (T5 DMIN 20, T9 DMIN 25): the hole is to be drilled to "
                              "size; no Ø10 drill in the turret"]


def test_with_a_drill_of_the_size_it_is_drilled_only():
    ops = hole_ops(turret(20, 25), tolerance="H7", ra=1.6)  # the seed's Ø10 drill; a tolerance that needs boring
    assert [op.tool_type for op in ops] == ["drilling"]
    assert any("needs boring, but no boring tool goes into it" in w for w in ops[0].warnings)


def test_a_bore_the_holders_go_into_is_bored_as_before():
    ops = hole_ops(turret(20, 25), diameter=30, length=10)
    assert [op.tool_type for op in ops] == ["drilling", "boring_rough", "boring"]
    assert not any("DMIN" in w for op in ops for w in op.warnings)


def test_dmin_not_set_is_a_warning():
    ops = hole_ops(turret(), diameter=30, length=10)
    boring = [op for op in ops if op.tool_type in ("boring_rough", "boring")]
    assert all(any(w.startswith(f"the smallest bore of T{op.turret_position} (DMIN) is not set") for w in op.warnings)
               for op in boring)


def test_the_operator_takes_the_dmin(app, client):
    tool = db.session.execute(db.select(Tool).filter_by(insert_code="CCMT09T304-PF")).scalar_one()
    assert "The holder is A16R-SCLCR/L 09-R: take 20.0 mm" in client.get(f"/tools/{tool.id}/edit").get_data(
        as_text=True)
    assert tool.min_bore_diameter is None
    client.post(f"/tools/{tool.id}/dmin", data={"holder": "A20S-SCLCR/L 09-R"})
    assert tool.min_bore_diameter == 25.0
    assert tool.min_bore_source == "Sandvik TT 2020 (PDF 106), holder A20S-SCLCR/L 09-R (confirmed by the operator)"
    assert "DMIN 25.0" in client.get("/machine").get_data(as_text=True)
