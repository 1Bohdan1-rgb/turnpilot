"""The machine's documented limits in planning: min spindle speed, coolant, driven tools / C axis, blank size."""
import html
from dataclasses import replace

from conftest import seed_turret

from turnpilot import planner, services
from turnpilot.models import Job, Material, Operation, db
from turnpilot.planner import FeatureSpec, JobSpec, MachineLimits, ToolSpec, TurretEntry, plan_job

SEED = seed_turret()
MILL = TurretEntry(3, ToolSpec(id=90, name="End mill D8", type="milling", iso_group="PMN", insert_code="",
                               vc_min=60, vc_max=120, f_min=0.02, f_max=0.05, ap_min=0.5, ap_max=2))


def _ops(features, limits, turret=SEED, blank=60):
    return plan_job(JobSpec("P", blank, 100, tuple(features)), turret, 4000, None, None, limits)


def test_n_below_the_min_spindle_speed_is_a_warning():
    ops = _ops([FeatureSpec(1, "od_turn", diameter=50, length=20)], MachineLimits(min_rpm=2000))
    rough = next(op for op in ops if op.tool_type == "turning_rough")
    assert rough.n < 2000
    assert planner.MIN_RPM_WARNING.format(n=rough.n, min_rpm=2000) in rough.warnings


def test_no_coolant_warns_on_catalogue_tools_only():
    turret = [replace(e, tool=replace(e.tool, vc_points=())) if e.position == 1 else e for e in SEED]
    ops = _ops([FeatureSpec(1, "face"), FeatureSpec(2, "od_turn", diameter=50, length=20)],
               MachineLimits(coolant=False), turret)
    face = next(op for op in ops if op.tool_type == "facing")  # T1 without its Vc(f) points here
    rough = next(op for op in ops if op.tool_type == "turning_rough")
    assert planner.NO_COOLANT_WARNING in rough.warnings and planner.NO_COOLANT_WARNING not in face.warnings


def test_coolant_present_or_not_known_no_warning():
    for limits in (MachineLimits(coolant=True), MachineLimits(), None):
        ops = _ops([FeatureSpec(1, "od_turn", diameter=50, length=20)], limits)
        assert not any(planner.NO_COOLANT_WARNING in op.warnings for op in ops)


def test_hex_milling_is_manual_without_driven_tools_or_c_axis():
    hex_part = [FeatureSpec(1, "hex", across_flats=17, length=10)]
    turret = SEED + [MILL]
    milling = next(op for op in _ops(hex_part, MachineLimits(live_tooling=True, c_axis=True), turret, blank=25)
                   if op.tool_type == "milling")
    assert milling.tool_name == "End mill D8"
    milling = next(op for op in _ops(hex_part, MachineLimits(live_tooling=False), turret, blank=25)
                   if op.tool_type == "milling")
    assert milling.tool_id is None and milling.warnings == [
        "manual operation: the machine has no driven tools, mill the hex on a milling machine"]
    milling = next(op for op in _ops(hex_part, MachineLimits(live_tooling=False, c_axis=False), turret, blank=25)
                   if op.tool_type == "milling")
    assert "no driven tools and no C axis" in milling.warnings[0]


def test_limits_not_known_change_nothing():
    features = [FeatureSpec(1, "od_turn", diameter=50, length=20)]
    with_none = _ops(features, MachineLimits())
    without = _ops(features, None)
    assert [(op.n, op.warnings) for op in with_none] == [(op.n, op.warnings) for op in without]


# --- through the app: the Machine page values reach the planner, the blank is checked -------------

def _job(client, blank_diameter=60, blank_length=120):
    material = db.session.execute(db.select(Material).filter_by(iso_group="P")).scalar_one()
    client.post("/jobs", data=dict(name="Shaft", material_id=material.id, quantity=1,
                                   blank_diameter=blank_diameter, blank_length=blank_length))
    return db.session.execute(db.select(Job)).scalar_one()


def test_machine_page_values_reach_the_planner(client):
    client.post("/machine", data={"action": "profile", "name": "Lathe 1", "min_rpm": "2000", "coolant": "no"})
    job = _job(client)
    client.post(f"/jobs/{job.id}/features", data=dict(type="od_turn", diameter=50, length=20))
    client.post(f"/jobs/{job.id}/calculate")
    rough = db.session.execute(db.select(Operation).filter_by(tool_type="turning_rough")).scalar_one()
    assert "below the machine's min spindle speed 2000" in rough.warning
    assert planner.NO_COOLANT_WARNING in rough.warning


def test_blank_longer_than_the_turning_length_and_bar_thicker_than_the_spindle(client):
    job = _job(client, blank_diameter=60, blank_length=120)
    assert services.machine_warnings(job, services.get_machine()) == []  # not known: not checked
    client.post("/machine", data={"action": "profile", "name": "Lathe 1", "max_turning_length": "100",
                                  "max_bar_diameter": "51"})
    page = html.unescape(client.get(f"/jobs/{job.id}").get_data(as_text=True))
    assert "Blank length 120 mm is above the machine's max turning length 100 mm." in page
    assert "Bar Ø60 does not pass through the spindle (max Ø51): chuck a cut piece." in page
    client.post(f"/jobs/{job.id}/features", data=dict(type="od_turn", diameter=50, length=20))
    client.post(f"/jobs/{job.id}/calculate")
    assert "max turning length 100 mm" in client.get(f"/jobs/{job.id}/operations").get_data(as_text=True)
