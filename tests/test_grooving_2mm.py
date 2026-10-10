"""The 2 mm grooving insert (CoroCut 1-2, seat E) in the library; the holder's seat and max depth."""
from conftest import seed_turret
from gcode_jobs import chamfer, make_job, ready_machine

from turnpilot import planner, services
from turnpilot.gcode import fanuc
from turnpilot.models import Feature, Tool, TurretSlot, db
from turnpilot.planner import FeatureSpec, JobSpec, TurretEntry, plan_job, seat_mismatch, seat_size
from turnpilot.seed import LIBRARY_TOOLS

GROOVING_2 = next(t for t in LIBRARY_TOOLS if t["insert_code"] == "N123E2-0200-0002-GM")


def test_seat_sizes():
    assert seat_size("N123E2-0200-0002-GM") == "E" and seat_size("N123G2-0300-0003-GM") == "G"
    assert seat_size("RF123E08-2525B", holder=True) == "E" and seat_size("LF123G10-2525B", holder=True) == "G"
    assert seat_size("CNMG120408-PM") is None and seat_size("DDJNR2525M15", holder=True) is None
    assert seat_mismatch("N123G2-0300-0003-GM", "RF123E08-2525B") == (
        "the insert N123G2-0300-0003-GM has seat size G, the holder RF123E08-2525B seat size E: it does not fit")
    assert seat_mismatch("N123E2-0200-0002-GM", "LF123E08-2020B") is None
    assert seat_mismatch("N123E2-0200-0002-GM", None) is None


def test_the_tool_form_refuses_a_holder_of_another_seat(app, client):
    tool = db.session.execute(db.select(Tool).filter_by(insert_code="N123E2-0200-0002-GM")).scalar_one()
    data = {k: getattr(tool, k) for k in ("name", "insert_code", "grade", "vc_min", "vc_max", "f_min", "f_max",
                                          "ap_min", "ap_max", "insert_width")}
    response = client.post(f"/tools/{tool.id}/edit", data={**data, "iso_group": "P", "holder_code": "RF123G10-2525B"},
                           follow_redirects=True)
    assert "seat size E, the holder RF123G10-2525B seat size G: it does not fit" in response.get_data(as_text=True)
    assert tool.holder_code is None
    client.post(f"/tools/{tool.id}/edit", data={**data, "iso_group": "P", "holder_code": "RF123E08-2525B"})
    assert tool.holder_code == "RF123E08-2525B"


def test_in_the_library_not_in_the_turret(app):
    tool = db.session.execute(db.select(Tool).filter_by(insert_code="N123E2-0200-0002-GM")).scalar_one()
    assert (tool.insert_width, tool.nose_radius, tool.max_depth, tool.holder_code) == (2.0, 0.2, 8.0, None)
    assert db.session.execute(db.select(TurretSlot).filter_by(tool_id=tool.id)).first() is None


def _turret_with_2mm(**changes):
    spec = planner.ToolSpec(id=20, **{**{k: v for k, v in GROOVING_2.items() if k != "grade"},
                                      "vc_points": planner.parse_vc_points(GROOVING_2["vc_points"]), **changes})
    return [e for e in seed_turret() if e.position != 3] + [TurretEntry(3, spec)]


def test_a_2_mm_groove_gets_the_2_mm_insert():
    feature = FeatureSpec(1, "groove", diameter=20, length=2, start_diameter=22)
    (op,) = [op for op in plan_job(JobSpec("P", 32, 60, (feature,)), _turret_with_2mm(), max_rpm=4000)
             if op.feature_id == 1]
    assert (op.tool_id, op.insert_width, op.passes, op.depth) == (20, 2.0, 1, 1.0)
    assert not op.warnings


def test_a_groove_deeper_than_the_holder_reaches():
    feature = FeatureSpec(1, "groove", diameter=20, length=2, start_diameter=40)  # 10 mm deep
    (op,) = [op for op in plan_job(JobSpec("P", 45, 60, (feature,)), _turret_with_2mm(max_depth=8.0),
                                   max_rpm=4000) if op.feature_id == 1]
    assert op.tool_id is None
    assert op.warnings == ["Groove 2 mm is narrower than the narrowest grooving insert in the turret (3 mm): "
                           "install a narrower insert."]  # T6 (no max depth) is left, and it is 3 mm wide


def test_the_deep_groove_with_only_the_2_mm_holder():
    feature = FeatureSpec(1, "groove", diameter=20, length=2, start_diameter=40)
    turret = [e for e in _turret_with_2mm(max_depth=8.0) if e.position != 6]
    (op,) = [op for op in plan_job(JobSpec("P", 45, 60, (feature,)), turret, max_rpm=4000) if op.feature_id == 1]
    assert op.tool_id is None
    assert op.warnings == ["Groove 10 mm deep is deeper than the grooving holders reach (8 mm): a holder for "
                           "deeper grooves is needed."]


def test_деталь_1_like_groove_in_the_program(app):
    # a 2 mm groove between a thread and a taper: with the 2 mm insert on T3 it is cut (one plunge)
    machine = ready_machine()
    tool = db.session.execute(db.select(Tool).filter_by(insert_code="N123E2-0200-0002-GM")).scalar_one()
    machine.slots[2].tool = tool
    db.session.commit()
    job = make_job([Feature(type="face"), Feature(type="od_turn", diameter=22, length=20), chamfer(22, 1, "left"),
                    Feature(type="groove", diameter=20, start_diameter=22, length=2),
                    Feature(type="od_turn", diameter=30, length=10), Feature(type="parting")],
                   blank=32, length=40, stickout=45)
    readiness, program = services.gcode_program(job, machine)
    assert readiness.ok, readiness.blockers
    text = fanuc.render(program)
    assert "N4 (T03 GROOVING 2 MM - GROOVE D20 W2, INSERT 2, 1 PLUNGE)" in text.replace("N5", "N4") or \
        "(T03 GROOVING 2 MM - GROOVE D20 W2, INSERT 2, 1 PLUNGE)" in text
    result = services.gcode_simulation(job, machine, text)
    assert result.ok and result.complete, (result.errors, result.incomplete)
