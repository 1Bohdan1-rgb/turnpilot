"""Holes: centre drilling, a drill chosen by its diameter, peck drilling, boring only after drilling."""
from conftest import seed_turret
from turnpilot import services
from turnpilot.models import TOOL_TYPES, Tool, db

DRILL_FORM = dict(name="Drill 20", type="drilling", iso_group=["P", "M", "N"], insert_code="HSS 20",
                  vc_min="20", vc_max="40", f_min="0.1", f_max="0.3", ap_min="10", ap_max="10", diameter="20")


def test_centre_drilling_is_a_tool_type():
    assert "centre_drilling" in TOOL_TYPES


def test_tool_form_saves_a_drill_diameter(client):
    client.post("/tools", data=DRILL_FORM)
    drill = db.session.execute(db.select(Tool).filter_by(name="Drill 20")).scalar_one()
    assert drill.diameter == 20
    page = client.get("/machine").get_data(as_text=True)
    assert 'name="diameter"' in page and "<td>20.0</td>" in page
    assert services.tool_spec(drill).diameter == 20


def test_tool_form_without_a_diameter(client):
    client.post("/tools", data={**DRILL_FORM, "name": "Boring bar 2", "type": "boring", "diameter": ""})
    assert db.session.execute(db.select(Tool).filter_by(name="Boring bar 2")).scalar_one().diameter is None


# --- planner ----------------------------------------------------------------------------------------

import pytest  # noqa: E402

from turnpilot import planner  # noqa: E402
from turnpilot.planner import FeatureSpec, JobSpec, ToolSpec, TurretEntry, drill_gives, plan_job, vc_at  # noqa: E402
from turnpilot.seed import TURRET_TOOLS  # noqa: E402

SEED = seed_turret()  # boring bar T9: CCMT PF, ap 0.11–0.35 (rec)–2; no drills, no centre drill


def _drill(position, diameter, tool_type="drilling"):
    return TurretEntry(position, ToolSpec(id=position, name=f"{tool_type} {diameter}", type=tool_type,
                                          iso_group="PMN", insert_code="", vc_min=20, vc_max=40, f_min=0.1,
                                          f_max=0.3, ap_min=diameter / 2, ap_max=diameter / 2, diameter=diameter))


DRILLS = SEED + [_drill(10, 3.15, "centre_drilling")] + [_drill(11 + i, d) for i, d in enumerate((6, 8, 10, 16, 20))]


def _ops(features, turret=DRILLS, blank=40):
    return plan_job(JobSpec("P", blank, 100, tuple(features)), turret, max_rpm=4000)


def _of(ops, fid):
    return [op for op in ops if op.feature_id == fid]


def test_bushing_bore_drilled_then_bored():
    # Завіса 36 bushing: blind Ø22+0,21 (IT12) depth 32, no Ø22 drill in the turret
    ops = _ops([FeatureSpec(1, "od_turn", diameter=34.8, length=80),
                FeatureSpec(2, "bore", diameter=22, length=32, tolerance="+0.21")])
    centre, drill, rough, finish = _of(ops, 2)
    assert [op.tool_type for op in (centre, drill, rough, finish)] == ["centre_drilling", "drilling", "boring", "boring"]
    assert centre.tool_name == "centre_drilling 3.15" and centre.n and not centre.warnings
    assert (drill.tool_name, drill.ref_diameter, drill.depth) == ("drilling 20", 20, 32) and not drill.warnings
    assert "drill Ø20: the largest up to Ø21.3 (Ø22 less the boring tool's finishing pass 0.35 mm/side)" in drill.notes
    assert not any("G83" in n for n in drill.notes)  # 32 < 3 × 20
    # (22 − 20) / 2 − 0.35 = 0.65 by ap rec 0.35: 2 passes
    assert (rough.passes, rough.ap) == (2, 0.325)
    assert any("bored from the drilled Ø20" in n for n in rough.notes)
    assert finish.mode == "finish"
    assert [op.tool_type for op in ops][:3] == ["centre_drilling", "drilling", "turning_rough"]  # drill first


def test_pin_hole_made_by_its_drill():
    # Завіса 36 pin: Ø10+0,15 (IT12) depth 5, no Ra: the Ø10 drill makes it
    ops = _of(_ops([FeatureSpec(1, "bore", diameter=10, length=5, tolerance="+0.15")]), 1)
    assert [op.tool_type for op in ops] == ["centre_drilling", "drilling"]
    assert ops[1].tool_name == "drilling 10"
    assert "drill Ø10 makes the hole: tolerance and roughness need no boring" in ops[1].notes


@pytest.mark.parametrize("tolerance, ra", [("H7", None), (None, 1.6), ("+0.1", None)])
def test_finer_hole_is_bored_even_with_a_drill_of_its_size(tolerance, ra):
    ops = _of(_ops([FeatureSpec(1, "bore", diameter=10, length=5, tolerance=tolerance, ra=ra)]), 1)
    assert [op.tool_type for op in ops] == ["centre_drilling", "drilling", "boring", "boring"]
    assert ops[1].tool_name == "drilling 8"  # the largest up to 10 − 0.4


def test_drill_gives():
    assert drill_gives(None, 10, None) and drill_gives("H12", 10, 6.3) and drill_gives("±IT14/2", 22, None)
    assert drill_gives("+0.21", 22, None)  # IT12 on Ø22
    assert not drill_gives("+0.1", 22, None) and not drill_gives("H11", 10, None) and not drill_gives(None, 10, 3.2)


def test_deep_hole_is_peck_drilled():
    ops = _of(_ops([FeatureSpec(1, "bore", diameter=8, length=40, tolerance="H7")]), 1)
    drill = ops[1]
    assert drill.tool_name == "drilling 6" and "G83 peck drilling: depth 40 > 3 × Ø6" in drill.notes
    assert (ops[2].passes, ops[2].ap) == (2, 0.325)  # (8 − 6) / 2 − 0.35 by ap rec 0.35


def test_large_bore_several_boring_passes():
    ops = _of(_ops([FeatureSpec(1, "bore", diameter=30, length=45, tolerance="H7")], blank=50), 1)
    drill, rough = ops[1], ops[2]
    assert drill.tool_name == "drilling 20" and not any("G83" in n for n in drill.notes)
    # (30 − 20) / 2 − 0.35 = 4.65 by T9's ap rec 0.35 (a finishing-geometry PF insert): 14 passes
    assert (rough.passes, rough.ap) == (14, 0.332)


def test_seed_turret_warns_and_does_not_guess():
    ops = _of(_ops([FeatureSpec(1, "bore", diameter=22, length=32)], turret=SEED), 1)
    centre, drill, rough, _ = ops
    assert planner.NO_CENTRE_DRILL in centre.warnings
    assert drill.tool_id is None and drill.warnings == [
        "no drill up to Ø21.3 in the turret (Ø22 less the boring allowance 0.35 mm/side): drill by hand"]
    assert rough.passes is None and planner.FROM_SOLID_NOTE in rough.notes


def test_no_boring_tool_no_drill_chosen():
    turret = [e for e in DRILLS if e.tool.type != "boring"]
    drill = _of(_ops([FeatureSpec(1, "bore", diameter=22, length=32, tolerance="H7")], turret=turret), 1)[1]
    assert drill.tool_id is None and drill.warnings == [planner.NO_BORING_ALLOWANCE]


def test_drill_without_a_diameter_is_not_chosen():
    turret = SEED + [TurretEntry(11, ToolSpec(id=11, name="drill ?", type="drilling", iso_group="PMN",
                                              insert_code="", vc_min=20, vc_max=40, f_min=0.1, f_max=0.3,
                                              ap_min=1, ap_max=1))]
    drill = _of(_ops([FeatureSpec(1, "bore", diameter=22, length=32)], turret=turret), 1)[1]
    assert drill.tool_id is None


def test_coaxial_holes_share_the_drilling_and_drills_go_from_the_smallest():
    turret = DRILLS + [_drill(20, 12)]
    ops = _ops([FeatureSpec(1, "bore", diameter=18, length=10, tolerance="H7"),
                FeatureSpec(2, "bore", diameter=12.3, length=20, tolerance="H7"),
                FeatureSpec(3, "thread", diameter=14, length=28, pitch=2, tolerance="6H", location="internal")],
               turret=turret)
    drilling = [op for op in ops if op.tool_type in ("centre_drilling", "drilling")]
    # the Ø12.3 bore's Ø12 drill is made by the Ø12 tap drill (deeper): one drill Ø12, then Ø16
    assert [(op.tool_type, op.feature_id, op.ref_diameter) for op in drilling] == [
        ("centre_drilling", 1, 3.15), ("drilling", 3, 12), ("drilling", 1, 16)]
    rough_12 = next(op for op in ops if op.feature_id == 2 and op.tool_type == "boring" and op.mode == "rough")
    assert any("bored from the drilled Ø12" in n for n in rough_12.notes)
    assert ops.index(drilling[-1]) < ops.index(rough_12)


def test_tap_drill_by_its_diameter():
    thread = FeatureSpec(1, "thread", diameter=14, length=28, pitch=2, tolerance="6H", location="internal")
    drill = _of(_ops([thread]), 1)[1]
    assert drill.tool_id is None and drill.warnings == ["no Ø12 drill in the turret (tap drill for M14×2)"]
    drill = _of(_ops([thread], turret=DRILLS + [_drill(20, 12)]), 1)[1]
    assert drill.tool_name == "drilling 12"


def test_internal_chamfer_of_a_drill_only_hole_gets_its_own_pass():
    ops = _ops([FeatureSpec(1, "bore", diameter=10, length=5),
                FeatureSpec(2, "chamfer", diameter=10, length=0.5, location="internal")])
    assert [op.tool_type for op in _of(ops, 2)] == ["boring"]


def test_bore_through_the_app(client):
    from turnpilot.models import Job, Material, Operation
    material = db.session.execute(db.select(Material).filter_by(iso_group="P")).scalar_one()
    for d, kind in ((3.15, "centre_drilling"), (20, "drilling")):
        client.post("/tools", data={**DRILL_FORM, "name": f"Tool {d}", "type": kind, "diameter": str(d)})
        tool = db.session.execute(db.select(Tool).filter_by(name=f"Tool {d}")).scalar_one()
        slot = next(s for s in services.get_machine().slots if s.tool_id is None)
        slot.tool_id = tool.id
    db.session.commit()
    client.post("/jobs", data=dict(name="Bushing", material_id=material.id, quantity=1, blank_diameter=38,
                                   blank_length=85))
    job = db.session.execute(db.select(Job)).scalar_one()
    client.post(f"/jobs/{job.id}/features", data=dict(type="od_turn", diameter=34.8, length=80))
    client.post(f"/jobs/{job.id}/features", data=dict(type="bore", diameter=22, length=32, tolerance="+0.21"))
    client.post(f"/jobs/{job.id}/calculate")
    ops = db.session.execute(db.select(Operation).filter_by(job_id=job.id, is_archived=False)
                             .order_by(Operation.sequence)).scalars().all()
    assert [op.tool_type for op in ops][:2] == ["centre_drilling", "drilling"]
    drill = ops[1]
    assert drill.ref_diameter == 20 and drill.depth == 32 and "drill Ø20" in drill.note


# --- rough boring with its own tool ---------------------------------------------------------------

ROUGH_BORING = TurretEntry(5, ToolSpec(
    id=50, name="Rough boring CCMT PM", type="boring_rough", iso_group="P", insert_code="CCMT09T304-PM",
    vc_min=215, vc_max=455, f_min=0.08, f_max=0.23, ap_min=0.25, ap_max=3, ap_rec=0.64, f_rec=0.15,
    vc_points=((0.1, 455.0), (0.4, 305.0), (0.8, 215.0)), source="TT A41, A289, A279"))
WITH_ROUGH_BORING = [e for e in DRILLS if e.position != 5] + [ROUGH_BORING]


def test_rough_boring_by_its_own_tool_finishing_by_the_finishing_bar():
    ops = _of(_ops([FeatureSpec(1, "bore", diameter=30, length=45, tolerance="H7")], turret=WITH_ROUGH_BORING,
                   blank=50), 1)
    _, drill, rough, finish = ops
    assert drill.tool_name == "drilling 20"
    assert (rough.tool_type, rough.tool_name, rough.mode) == ("boring_rough", "Rough boring CCMT PM", "rough")
    # (30 − 20) / 2 − 0.35 (T9's finishing pass) = 4.65 by the rough tool's ap rec 0.64: 8 passes
    assert (rough.passes, rough.ap, rough.f) == (8, 0.581, 0.15)
    assert rough.vc == pytest.approx(vc_at(ROUGH_BORING.tool.vc_points, 0.15), abs=0.05)
    assert any("leaves 0.35 mm/side for finishing" in n for n in rough.notes)
    assert not any(planner.NO_ROUGH_BORING_TOOL in n for n in rough.notes)
    assert (finish.tool_type, finish.tool_name, finish.ap) == ("boring", "Boring bar CCMT", 0.35)


def test_without_a_rough_boring_tool_the_finishing_bar_roughs_with_a_note():
    rough = _of(_ops([FeatureSpec(1, "bore", diameter=30, length=45, tolerance="H7")], blank=50), 1)[2]
    assert (rough.tool_type, rough.tool_name) == ("boring", "Boring bar CCMT")
    assert planner.NO_ROUGH_BORING_TOOL in rough.notes


def test_drill_choice_still_by_the_finishing_pass():
    drill = _of(_ops([FeatureSpec(1, "bore", diameter=22, length=32, tolerance="H7")], turret=WITH_ROUGH_BORING), 1)[1]
    assert "Ø22 less the boring tool's finishing pass 0.35 mm/side" in drill.notes[0]


def test_boring_rough_is_a_tool_type_on_the_form(client):
    assert "boring_rough" in TOOL_TYPES
    assert "<option>boring_rough</option>" in client.get("/machine").get_data(as_text=True)
