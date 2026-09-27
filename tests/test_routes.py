from turnpilot.models import Edit, Job, Material, Operation, db


def _create_job(client, iso_group="P"):
    material = db.session.execute(db.select(Material).filter_by(iso_group=iso_group)).scalar_one()
    client.post("/jobs", data=dict(name="Shaft", material_id=material.id, quantity=5,
                                   blank_diameter=60, blank_length=120))
    return db.session.execute(db.select(Job)).scalar_one()


def test_pages_render(client):
    assert client.get("/machine").status_code == 200
    assert client.get("/jobs").status_code == 200


def test_calculate_approve_and_edit_flow(client):
    job = _create_job(client)
    client.post(f"/jobs/{job.id}/features", data=dict(type="face"))
    client.post(f"/jobs/{job.id}/features", data=dict(type="od_turn", diameter=40, length=50, ra=1.6))
    client.post(f"/jobs/{job.id}/features", data=dict(type="thread", diameter=40, length=20, pitch=1.5))

    resp = client.post(f"/jobs/{job.id}/calculate", follow_redirects=True)
    assert resp.status_code == 200
    assert b"G96 constant surface speed" in resp.data

    ops = db.session.execute(db.select(Operation).order_by(Operation.sequence)).scalars().all()
    assert [op.tool_type for op in ops] == ["facing", "turning_rough", "turning_finish", "threading"]
    assert all(op.status == "proposed" for op in ops)

    client.post(f"/operations/{ops[0].id}/approve")
    assert db.session.get(Operation, ops[0].id).status == "approved"

    rough = ops[1]
    old_vc, old_n = rough.vc, rough.n
    client.post(f"/operations/{rough.id}/edit", data=dict(
        turret_position=rough.turret_position, vc=old_vc + 20, f=rough.f, ap=rough.ap, passes=rough.passes))
    rough = db.session.get(Operation, rough.id)
    assert rough.status == "edited"
    edits = {e.field: e for e in db.session.execute(db.select(Edit)).scalars()}
    assert set(edits) == {"vc", "n"}
    assert float(edits["vc"].old_value) == old_vc
    assert int(edits["n"].old_value) == old_n


def test_missing_tool_warning_shown(client):
    job = _create_job(client, iso_group="M")  # seed grooving tool does not cover ISO M
    client.post(f"/jobs/{job.id}/features", data=dict(type="groove", diameter=50, length=3))
    resp = client.post(f"/jobs/{job.id}/calculate", follow_redirects=True)
    assert b"No grooving tool for ISO M" in resp.data


def _current_ops():
    return db.session.execute(
        db.select(Operation).filter_by(is_archived=False).order_by(Operation.sequence)
    ).scalars().all()


def test_recalculate_archives_operations_and_keeps_edit_log(client):
    job = _create_job(client)
    client.post(f"/jobs/{job.id}/features", data=dict(type="od_turn", diameter=40, length=50, ra=1.6))
    client.post(f"/jobs/{job.id}/calculate")

    rough = _current_ops()[0]
    client.post(f"/operations/{rough.id}/edit", data=dict(
        turret_position=rough.turret_position, vc=rough.vc, f=rough.f + 0.05, ap=rough.ap, passes=rough.passes))
    edit_ids = [e.id for e in db.session.execute(db.select(Edit)).scalars()]
    assert edit_ids

    client.post(f"/jobs/{job.id}/calculate")

    # Edit rows survive recalculation and still point at the archived operation.
    edits = db.session.execute(db.select(Edit)).scalars().all()
    assert [e.id for e in edits] == edit_ids
    archived = db.session.get(Operation, rough.id)
    assert archived.is_archived and archived.status == "edited"
    assert archived.calculation_version == 1
    assert all(e.operation_id == archived.id for e in edits)

    current = _current_ops()
    assert len(current) == 2
    assert all(op.calculation_version == 2 and op.status == "proposed" for op in current)

    # The table shows only current operations; history shows the archived ones.
    page = client.get(f"/jobs/{job.id}/operations").data
    assert page.count(b'badge-edited">edited') == 0
    history = client.get(f"/jobs/{job.id}/history").data
    assert b"Version 1" in history and b"edited" in history

    # Archived operations are read-only.
    client.post(f"/operations/{archived.id}/approve")
    assert db.session.get(Operation, archived.id).status == "edited"


def test_deleting_feature_keeps_operations_and_edits(client):
    job = _create_job(client)
    client.post(f"/jobs/{job.id}/features", data=dict(type="face"))
    client.post(f"/jobs/{job.id}/calculate")
    op = _current_ops()[0]
    client.post(f"/operations/{op.id}/edit", data=dict(
        turret_position=op.turret_position, vc=op.vc + 10, f=op.f, ap=op.ap))
    edits_before = db.session.execute(db.select(db.func.count(Edit.id))).scalar()

    client.post(f"/jobs/{job.id}/features/{op.feature_id}/delete")

    assert db.session.execute(db.select(db.func.count(Edit.id))).scalar() == edits_before > 0
    assert db.session.get(Operation, op.id) is not None
