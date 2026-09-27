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
