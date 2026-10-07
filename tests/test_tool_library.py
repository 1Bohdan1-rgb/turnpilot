"""Tool library on the Machine page: edit, delete (or retire when used), the source of the cutting data."""
from turnpilot import services
from turnpilot.models import Job, Material, Operation, Tool, TurretSlot, db

FORM = dict(name="Drill 20", type="drilling", iso_group=["P", "M"], insert_code="HSS 20", grade="",
            vc_min="20", vc_max="40", f_min="0.1", f_max="0.3", ap_min="10", ap_max="10", diameter="20",
            source="")


def _tool(name):
    return db.session.execute(db.select(Tool).filter_by(name=name)).scalar_one_or_none()


def _add(client, **overrides):
    client.post("/tools", data={**FORM, **overrides})
    return _tool(overrides.get("name", FORM["name"]))


def _edit_form(tool, **overrides):
    form = {k: str(getattr(tool, k)) for k in ("name", "vc_min", "vc_max", "f_min", "f_max", "ap_min", "ap_max")}
    form.update(insert_code=tool.insert_code or "", grade=tool.grade or "", iso_group=list(tool.iso_group),
                insert_width="" if tool.insert_width is None else str(tool.insert_width),
                diameter="" if tool.diameter is None else str(tool.diameter), source=tool.source or "")
    form.update(overrides)
    return form


def _put_in_turret(tool, position=10):
    slot = next(s for s in services.get_machine().slots if s.position == position)
    slot.tool_id = tool.id
    db.session.commit()


# --- source ---------------------------------------------------------------------------------------

def test_source_is_saved_and_shown_in_the_library(client):
    tool = _add(client, source="Sandvik catalogue 2024, p. 112")
    assert tool.source == "Sandvik catalogue 2024, p. 112"
    assert "Sandvik catalogue 2024, p. 112" in client.get("/machine").get_data(as_text=True)


def test_seed_tools_carry_a_source_only_when_checked_against_the_catalogue(app):
    tools = {t.name: t for t in db.session.execute(db.select(Tool)).scalars()}
    checked = ("Facing SCMT", "Rough turning CNMG (P)", "Finish turning DNMG (P)", "Grooving 3 mm",
               "Threading 60 deg", "Parting 3 mm", "Boring bar CCMT")
    assert all(tools[name].source.startswith("Sandvik Coromant Turning tools 2020") for name in checked)
    assert all(t.source is None for name, t in tools.items() if name not in checked)


# --- edit -----------------------------------------------------------------------------------------

def test_edit_tool(client):
    tool = _add(client)
    page = client.get(f"/tools/{tool.id}/edit").get_data(as_text=True)
    assert 'value="Drill 20"' in page and "not editable" in page
    client.post(f"/tools/{tool.id}/edit", data=_edit_form(
        tool, name="Drill 20 TiN", vc_max="60", iso_group=["P", "M", "N"], diameter="19.5", source="my catalogue"))
    db.session.expire_all()
    tool = db.session.get(Tool, tool.id)
    assert (tool.name, tool.vc_max, tool.iso_group, tool.diameter, tool.source) == (
        "Drill 20 TiN", 60, "PMN", 19.5, "my catalogue")
    assert tool.type == "drilling"


def test_edit_tool_cannot_change_the_type(client):
    tool = _add(client)
    client.post(f"/tools/{tool.id}/edit", data=_edit_form(tool, type="boring"))
    db.session.expire_all()
    assert db.session.get(Tool, tool.id).type == "drilling"


def test_edit_tool_rejects_min_above_max_and_no_iso_group(client):
    tool = _add(client)
    response = client.post(f"/tools/{tool.id}/edit", data=_edit_form(tool, vc_min="50"))
    assert "vc_min must not exceed vc_max" in response.get_data(as_text=True)
    response = client.post(f"/tools/{tool.id}/edit", data=_edit_form(tool, iso_group=[]))
    assert "Select at least one ISO group" in response.get_data(as_text=True)
    db.session.expire_all()
    assert (db.session.get(Tool, tool.id).vc_min, db.session.get(Tool, tool.id).iso_group) == (20, "PM")


# --- delete / retire ------------------------------------------------------------------------------

def test_tool_in_the_turret_is_not_deleted(client):
    tool = _add(client)
    _put_in_turret(tool, 10)
    response = client.post(f"/tools/{tool.id}/delete", follow_redirects=True)
    assert "is in the turret at T10: remove it from there first" in response.get_data(as_text=True)
    assert _tool("Drill 20") is not None and not _tool("Drill 20").is_retired


def test_unused_tool_is_deleted(client):
    tool = _add(client)
    client.post(f"/tools/{tool.id}/delete")
    assert _tool("Drill 20") is None


def _job_with_an_operation_of(client, tool):
    material = db.session.execute(db.select(Material).filter_by(iso_group="P")).scalar_one()
    client.post("/jobs", data=dict(name="Bushing", material_id=material.id, quantity=1, blank_diameter=38,
                                   blank_length=85))
    job = db.session.execute(db.select(Job)).scalar_one()
    client.post(f"/jobs/{job.id}/features", data=dict(type="od_turn", diameter=34.8, length=80))
    client.post(f"/jobs/{job.id}/features", data=dict(type="bore", diameter=20, length=10))
    client.post(f"/jobs/{job.id}/calculate")
    op = db.session.execute(db.select(Operation).filter_by(tool_id=tool.id)).scalar_one()
    return job, op


def test_used_tool_is_retired_and_its_operation_keeps_it(client):
    tool = _add(client)
    _put_in_turret(tool, 10)
    job, op = _job_with_an_operation_of(client, tool)
    next(s for s in services.get_machine().slots if s.position == 10).tool_id = None  # out of the turret
    db.session.commit()
    client.post(f"/tools/{tool.id}/delete")
    db.session.expire_all()
    tool = db.session.get(Tool, tool.id)
    assert tool.is_retired
    client.get("/machine")  # shows (and clears) the flash messages that name the tool
    page = client.get("/machine").get_data(as_text=True)
    assert "Drill 20" not in page  # neither in the library nor in the turret's choices
    assert db.session.get(Operation, op.id).tool_id == tool.id
    assert "Drill 20" in client.get(f"/jobs/{job.id}/operations").get_data(as_text=True)
    assert client.get(f"/tools/{tool.id}/edit").status_code == 404


def test_operation_note_shows_the_source(client):
    tool = _add(client, source="Guhring catalogue, p. 40")
    _put_in_turret(tool, 10)
    _, op = _job_with_an_operation_of(client, tool)
    assert "cutting data ranges: Guhring catalogue, p. 40" in op.note


def test_turret_slots_never_hold_a_retired_tool(app):
    assert all(not s.tool or not s.tool.is_retired for s in db.session.execute(db.select(TurretSlot)).scalars())


# --- note on jobs calculated before a tool changed -------------------------------------------------

BANNER = "Tool data changed after this calculation"


def test_banner_after_a_tool_edit_until_recalculated(client):
    tool = _add(client)
    _put_in_turret(tool, 10)
    job, _ = _job_with_an_operation_of(client, tool)
    assert BANNER not in client.get(f"/jobs/{job.id}/operations").get_data(as_text=True)
    client.post(f"/tools/{tool.id}/edit", data=_edit_form(tool, vc_max="35"))
    page = client.get(f"/jobs/{job.id}/operations").get_data(as_text=True)
    assert BANNER in page and "Drill 20." in page
    client.post(f"/jobs/{job.id}/calculate")
    assert BANNER not in client.get(f"/jobs/{job.id}/operations").get_data(as_text=True)


def test_no_banner_when_another_tool_changes(client):
    tool = _add(client)
    _put_in_turret(tool, 10)
    job, _ = _job_with_an_operation_of(client, tool)
    other = _add(client, name="Unused drill", diameter="5")
    client.post(f"/tools/{other.id}/edit", data=_edit_form(other, vc_max="35"))
    assert BANNER not in client.get(f"/jobs/{job.id}/operations").get_data(as_text=True)


def test_banner_names_a_retired_tool(client):
    tool = _add(client)
    _put_in_turret(tool, 10)
    job, _ = _job_with_an_operation_of(client, tool)
    next(s for s in services.get_machine().slots if s.position == 10).tool_id = None
    db.session.commit()
    client.post(f"/tools/{tool.id}/delete")
    page = client.get(f"/jobs/{job.id}/operations").get_data(as_text=True)
    assert BANNER in page and "Drill 20 (retired)" in page


def test_operations_without_a_recorded_time_get_no_banner(client):
    tool = _add(client)
    _put_in_turret(tool, 10)
    job, op = _job_with_an_operation_of(client, tool)
    for o in job.current_operations:
        o.created_at = None  # calculated before the time was recorded
    db.session.commit()
    client.post(f"/tools/{tool.id}/edit", data=_edit_form(tool, vc_max="35"))
    assert BANNER not in client.get(f"/jobs/{job.id}/operations").get_data(as_text=True)


# --- data migration 3748554069af: the seed tools of an existing database -----------------------------

def _seed_migration(filename="3748554069af_seed_tools_t1_t2_t4_t9_from_the_sandvik_.py"):
    import importlib.util
    from pathlib import Path
    path = Path(__file__).parents[1] / "migrations" / "versions" / filename
    spec = importlib.util.spec_from_file_location("seed_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_seed_migration_writes_what_the_seed_has():
    from turnpilot.seed import TURRET_TOOLS
    migration = _seed_migration()
    seed = {tool["name"]: tool for tool in TURRET_TOOLS.values()}
    renamed = {"Finish turning DNMG (P/M)": "Finish turning DNMG (P)"}  # by migration 5fb0e3d2a1c4
    for name, (_old, new) in migration.TOOLS.items():
        assert migration._same(seed[renamed.get(name, name)], new), name


def test_seed_migration_leaves_an_edited_tool():
    migration = _seed_migration()
    old = migration.TOOLS["Rough turning CNMG (P)"][0]
    assert migration._same(dict(old), old)
    assert not migration._same({**old, "vc_max": 280}, old)
    assert not migration._same({**old, "source": "my catalogue"}, old)


T678 = "5fb0e3d2a1c4_seed_tools_t6_t7_t8_from_the_sandvik_.py"


def test_seed_migration_t6_t7_t8_writes_what_the_seed_has():
    from turnpilot.seed import TURRET_TOOLS
    migration = _seed_migration(T678)
    unset = dict(insert_width=None, ap_rec=None, f_rec=None, vc_points=None, source=None)
    seed = {tool["name"]: {**unset, **tool} for tool in TURRET_TOOLS.values()}
    for name, (_old, new) in migration.TOOLS.items():
        assert migration._same(seed[name], new), name
    assert migration.RENAME[1] in seed


def test_seed_migration_t6_t7_t8_leaves_an_edited_tool():
    migration = _seed_migration(T678)
    old = migration.TOOLS["Grooving 3 mm"][0]
    assert migration._same(dict(old), old) and not migration._same({**old, "f_max": 0.2}, old)
