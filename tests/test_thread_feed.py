"""Thread speed check: the Z axis moves at n·P when threading; the machine's limit, or a warning without one."""
from turnpilot import services
from turnpilot.models import db

PROFILE = dict(action="profile", name="Lathe 1", max_rpm="4000", power_kw="11", max_diameter="300")


def test_seed_machine_has_no_thread_feed_limit(app):
    assert services.get_machine().max_thread_feed is None  # not guessed


def test_machine_page_saves_and_clears_the_limit(client):
    client.post("/machine", data={**PROFILE, "max_thread_feed": "2000"})
    assert services.get_machine().max_thread_feed == 2000
    assert 'name="max_thread_feed" type="number" step="any" min="0" value="2000.0"' in client.get("/machine").get_data(as_text=True)
    client.post("/machine", data={**PROFILE, "max_thread_feed": ""})
    assert services.get_machine().max_thread_feed is None


def test_machine_page_rejects_a_negative_limit(client):
    client.post("/machine", data={**PROFILE, "max_thread_feed": "-5"})
    db.session.expire_all()
    assert services.get_machine().max_thread_feed is None


# --- planner ----------------------------------------------------------------------------------------

import pytest  # noqa: E402

from turnpilot import planner  # noqa: E402
from turnpilot.planner import FeatureSpec, JobSpec, ToolSpec, TurretEntry, plan_job  # noqa: E402
from turnpilot.seed import TURRET_TOOLS  # noqa: E402

SEED = [TurretEntry(pos, ToolSpec(id=pos, **{k: v for k, v in tool.items() if k != "grade"}))
        for pos, tool in TURRET_TOOLS.items()]
M24 = (FeatureSpec(1, "od_turn", diameter=24, length=22), FeatureSpec(2, "thread", diameter=24, length=22, pitch=1.5))


def _thread_op(limit, features=M24, turret=SEED, max_rpm=4000, tool_types=("threading",)):
    ops = plan_job(JobSpec("P", 55, 100, features), turret, max_rpm, limit)
    return [op for op in ops if op.tool_type in tool_types]


def test_nd012_m24_without_a_limit_asks_to_check_n_p():
    (op,) = _thread_op(None)
    assert (op.n, op.f) == (1757, 1.5)  # as before: n·P = 2635.5
    assert op.warnings == ["check n·P = 2635.5 mm/min for your machine: no threading feed limit is set on the "
                           "Machine page"]


def test_limit_not_exceeded_keeps_n_with_a_note():
    (op,) = _thread_op(3000)
    assert op.n == 1757 and not op.warnings
    assert "Z feed n·P 2635.5 mm/min" in op.notes


def test_limit_exceeded_reduces_n():
    (op,) = _thread_op(2000)
    assert op.n == 1333 and op.n * op.f <= 2000 and not op.warnings
    assert "n reduced to 1333 rpm: n·P 1999.5 ≤ 2000 mm/min (was 1757 rpm, n·P 2635.5)" in op.notes
    assert planner.G97_THREAD_NOTE == op.notes[0]  # still first


def test_detal_1_m22_with_a_limit():
    features = (FeatureSpec(1, "od_turn", diameter=22, length=21), FeatureSpec(2, "thread", diameter=22, length=21,
                                                                                pitch=1.5))
    (op,) = _thread_op(2000, features)
    assert op.n == 1333 and any("(was 1917 rpm, n·P 2875.5)" in n for n in op.notes)


def test_max_rpm_first_then_the_thread_feed():
    features = (FeatureSpec(1, "od_turn", diameter=8, length=10), FeatureSpec(2, "thread", diameter=8, length=10,
                                                                               pitch=1.25))
    (op,) = _thread_op(3000, features, max_rpm=4000)  # Vc 132.5 on Ø8 would be 5272 rpm
    assert op.n == 2400  # 4000 → n·P 5000 > 3000 → 2400
    assert "n limited to machine max 4000 rpm" in op.notes
    assert "n reduced to 2400 rpm: n·P 3000 ≤ 3000 mm/min (was 4000 rpm, n·P 5000)" in op.notes


def test_thread_without_pitch_is_not_checked():
    (op,) = _thread_op(None, (FeatureSpec(1, "thread", diameter=24, length=22),))
    assert op.warnings == ["Thread pitch is missing: feed equals pitch."]


def test_limit_below_one_revolution_is_a_warning():
    (op,) = _thread_op(1)
    assert op.n == 1757 and "below one revolution per minute" in op.warnings[0]


def _internal_turret(tool_type):
    tool = ToolSpec(id=90, name=tool_type, type=tool_type, iso_group="PMN", insert_code="", vc_min=10, vc_max=30,
                    f_min=0.1, f_max=0.2, ap_min=0.05, ap_max=0.15)
    drill = ToolSpec(id=91, name="drill", type="drilling", iso_group="PMN", insert_code="", vc_min=20, vc_max=40,
                     f_min=0.1, f_max=0.2, ap_min=0.1, ap_max=1)
    return SEED + [TurretEntry(10, drill), TurretEntry(11, tool)]


M14_INTERNAL = (FeatureSpec(1, "bore", diameter=12, length=28),
                FeatureSpec(2, "thread", diameter=14, length=20, pitch=2, location="internal"))


@pytest.mark.parametrize("tool_type", ["tapping", "threading_internal"])
def test_tap_and_internal_threading_bar_are_checked_too(tool_type):
    turret = _internal_turret(tool_type)
    (op,) = _thread_op(None, M14_INTERNAL, turret, tool_types=(tool_type,))
    assert op.n == 455 and "check n·P = 910 mm/min" in op.warnings[0]  # Vc 20 on Ø14
    (op,) = _thread_op(600, M14_INTERNAL, turret, tool_types=(tool_type,))
    assert op.n == 300 and "n reduced to 300 rpm: n·P 600 ≤ 600 mm/min (was 455 rpm, n·P 910)" in op.notes


def test_tap_drill_is_not_checked():
    (drill,) = _thread_op(None, M14_INTERNAL, _internal_turret("tapping"), tool_types=("drilling",))
    assert not any("n·P" in w for w in drill.warnings) and not any("n·P" in n for n in drill.notes)


def test_limit_from_the_machine_page_through_the_app(client):
    from turnpilot.models import Job, Material, Operation
    material = db.session.execute(db.select(Material).filter_by(iso_group="P")).scalar_one()
    client.post("/jobs", data=dict(name="M24", material_id=material.id, quantity=1, blank_diameter=30, blank_length=40))
    job = db.session.execute(db.select(Job)).scalar_one()
    client.post(f"/jobs/{job.id}/features", data=dict(type="od_turn", diameter=24, length=22))
    client.post(f"/jobs/{job.id}/features", data=dict(type="thread", diameter=24, length=22, pitch=1.5))

    def thread_op():
        client.post(f"/jobs/{job.id}/calculate")
        return db.session.execute(db.select(Operation).filter_by(tool_type="threading", is_archived=False)).scalar_one()

    op = thread_op()
    assert op.n == 1757 and "check n·P = 2635.5 mm/min" in op.warning
    assert "check n·P = 2635.5 mm/min" in client.get(f"/jobs/{job.id}/operations").get_data(as_text=True)
    client.post("/machine", data={**PROFILE, "max_thread_feed": "2000"})
    op = thread_op()
    assert op.n == 1333 and op.warning is None and "n reduced to 1333 rpm" in op.note
