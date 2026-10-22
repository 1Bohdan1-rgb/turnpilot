"""Jobs for the G-code tests: a machine with programming values, DXF-like jobs (features in axial order)."""
from conftest import confirm_p12_catalogue

from turnpilot import services
from turnpilot.models import Feature, Job, Material, db

PROGRAMMING = dict(max_rpm=3500, spindle_right_hand="M04", clearance_x=1, clearance_z=2, retract_mm=0.5,
                   chuck_safety_mm=5, thread_run_in_mm=6, peck_depth_mm=5, facing_overshoot_mm=0.4,
                   groove_reference="toward Z0", parting_overshoot_mm=0.25)


def ready_machine(**changes):
    """The seed machine with the operator's programming values and a confirmed max rpm, and the P1.2 rows."""
    confirm_p12_catalogue()
    machine = services.get_machine()
    for name, value in {**PROGRAMMING, **changes}.items():
        services.set_machine_value(machine, name, value, "operator")
    db.session.commit()
    return machine


def chamfer(d, leg, face):
    return Feature(type="chamfer", diameter=d, length=leg, face=face, location="external")


def pin_features():
    """Ø20 L15 (1×45° on the free end), groove Ø16 L3 from Ø20, Ø24 L20, Ø30 L25 (1×45° on its free side):
    63 long, drawn with the free end on the left."""
    return [Feature(type="face"), Feature(type="od_turn", diameter=20, length=15), chamfer(20, 1, "left"),
            Feature(type="groove", diameter=16, start_diameter=20, length=3),
            Feature(type="od_turn", diameter=24, length=20), Feature(type="od_turn", diameter=30, length=25),
            chamfer(30, 1, "left"), Feature(type="parting")]


def make_job(features=None, free_end="left", blank=32, length=70, stickout=75, face_stock=1.0, approve=True,
             machine=None, name="Pin"):
    steel = db.session.execute(db.select(Material).filter_by(name="Steel 45 (C45)")).scalar_one()
    job = Job(name=name, material=steel, quantity=1, blank_diameter=blank, blank_length=length,
              axial_order_known=True, free_end=free_end, stickout_mm=stickout, face_stock_mm=face_stock)
    job.features = features if features is not None else pin_features()
    db.session.add(job)
    db.session.commit()
    services.calculate_operations(job, machine or services.get_machine())
    if approve:
        for op in job.current_operations:
            op.status = "approved"
        db.session.commit()
    return job


def only_flat_edge_errors(result):
    """Until the tools have their entering angle κr (stage 7.3 fix, commit 3): the insert's edge beside the nose is a
    flat ap max long at the nose's lowest point, which goes under a profile going up behind a tool going down (a V / D
    insert's edge really rises there). The program is complete; its only errors are that stock below the profile."""
    assert result.complete, result.incomplete
    assert result.errors and all(m.startswith("the stock is left below the finished profile") and
                                 "the insert's edge beside the nose is taken as" in m for _, m in result.errors), \
        result.errors
