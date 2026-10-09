"""Roadmap stage 6, commit 1: where an operation's cutting data come from, and what may go into a program."""
from conftest import confirm_p12_catalogue

from turnpilot import services
from turnpilot.gcode import readiness as rd
from turnpilot.models import Feature, Job, Material, Operation, db


def make_job(axial=True):
    steel = db.session.execute(db.select(Material).filter_by(name="Steel 45 (C45)")).scalar_one()
    job = Job(name="Pin", material=steel, quantity=1, blank_diameter=32, blank_length=70, axial_order_known=axial)
    job.features = [Feature(type="face"), Feature(type="od_turn", diameter=20, length=20),
                    Feature(type="od_turn", diameter=30, length=30), Feature(type="taper", start_diameter=20,
                                                                             diameter=24, length=5),
                    Feature(type="parting")]
    db.session.add(job)
    db.session.commit()
    return job


def info(**kw):
    data = dict(id=1, sequence=1, tool_type="turning_rough", feature_type="od_turn", status="approved", has_tool=True,
                origin="catalogue", vc=200, n=2000, f=0.3)
    data.update(kw)
    return rd.OperationInfo(**data)


def test_origin_of_the_cutting_data(app):
    machine = services.get_machine()
    job = make_job()
    services.calculate_operations(job, machine)
    assert {op.cutting_data_origin for op in job.current_operations if op.tool_id} == {"tool"}
    confirm_p12_catalogue()
    services.calculate_operations(job, machine)
    origins = {(op.tool_type, op.cutting_data_origin) for op in job.current_operations}
    assert ("turning_rough", "catalogue") in origins and ("manual", None) in origins  # the taper: no tool
    rough = next(op for op in job.current_operations if op.tool_type == "turning_rough")
    services.apply_operation_edit(rough, machine, rough.turret_position, rough.vc, 0.25, rough.ap, rough.passes)
    assert rough.cutting_data_origin == "operator"


def test_direct_planner_calls_have_no_origin():
    from conftest import seed_turret

    from turnpilot.planner import FeatureSpec, JobSpec, plan_job
    ops = plan_job(JobSpec("P", 30, 50, (FeatureSpec(1, "od_turn", 20, 20),)), seed_turret(), 4000)
    assert {op.data_origin for op in ops} == {None}


def test_blockers():
    result = rd.check(False, [info(status="proposed", sequence=3)], ["Rough turning CNMG (P)"], "seed")
    assert result.blockers == [rd.NOT_IN_AXIAL_ORDER, rd.NOT_APPROVED.format(sequences="3"),
                               rd.TOOLS_CHANGED.format(names="Rough turning CNMG (P)"), rd.NO_MAX_RPM]
    assert not result.ok
    assert rd.check(True, [], [], "passport").blockers == [rd.NO_OPERATIONS]
    ok = rd.check(True, [info(status="edited")], [], "operator")
    assert ok.ok and ok.usable == [1]


def test_operations_left_out_with_their_reason():
    ops = [info(id=1, sequence=1), info(id=2, sequence=2, has_tool=False, tool_type="manual"),
           info(id=3, sequence=3, tool_type="boring"), info(id=4, sequence=4, origin="tool"),
           info(id=5, sequence=5, origin=None), info(id=6, sequence=6, feature_type="chamfer", tool_type="turning_finish"),
           info(id=7, sequence=7, f=None), info(id=8, sequence=8, tool_type="drilling", origin="operator")]
    result = rd.check(True, ops, [], "passport")
    assert result.usable == [1, 8]
    assert result.skipped[2] == rd.SKIP_NO_TOOL
    assert result.skipped[3].startswith("boring: not generated")
    assert "(tool)" in result.skipped[4] and "(not known)" in result.skipped[5]
    assert result.skipped[6] == rd.SKIP_CHAMFER and result.skipped[7] == rd.SKIP_NO_DATA


def test_readiness_of_a_job(app):
    machine = services.get_machine()
    job = make_job(axial=False)
    confirm_p12_catalogue()
    services.calculate_operations(job, machine)
    result = services.gcode_readiness(job, machine)
    assert rd.NOT_IN_AXIAL_ORDER in result.blockers and rd.NO_MAX_RPM in result.blockers  # the seed's 4000
    assert any(b.startswith("operations not approved yet") for b in result.blockers)
    job.axial_order_known = True
    for op in job.current_operations:
        op.status = "approved"
    services.set_machine_value(machine, "max_rpm", 3500, "operator")
    db.session.commit()
    result = services.gcode_readiness(job, machine)
    assert result.ok
    taper = next(op for op in job.current_operations if op.feature.type == "taper")
    assert result.skipped[taper.id] == rd.SKIP_NO_TOOL
    assert db.session.get(Operation, result.usable[0]).tool_type == "facing"
