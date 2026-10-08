"""Roadmap stage 5, step 3: the planner takes ap / f / Vc from the confirmed catalogue rows of the job's material."""
from dataclasses import fields

import pytest
from conftest import confirm_p12_catalogue

from turnpilot import cutting_data as cd
from turnpilot import services
from turnpilot.models import CuttingDataRow, Feature, Job, Material, Operation, db

NUMBERS = [f.name for f in fields(services.planner.ToolSpec)
           if f.name not in ("source", "catalogue_note", "catalogue_warning")]


def material(name):
    return db.session.execute(db.select(Material).filter_by(name=name)).scalar_one()


def make_job(name="Shaft", material_name="Steel 45 (C45)"):
    job = Job(name=name, material=material(material_name), quantity=1, blank_diameter=40, blank_length=80)
    job.features = [
        Feature(type="face"), Feature(type="od_turn", diameter=36, length=40, ra=1.6),
        Feature(type="od_turn", diameter=24, length=20), Feature(type="thread", diameter=24, length=20, pitch=1.5),
        Feature(type="groove", diameter=20, start_diameter=24, length=3), Feature(type="bore", diameter=8, length=20),
        Feature(type="parting"),
    ]
    db.session.add(job)
    db.session.commit()
    return job


def numbers(job):
    return [(op.tool_type, op.rough_finish, op.turret_position, op.vc, op.n, op.f, op.ap, op.passes)
            for op in job.current_operations]


def test_p12_rows_give_the_seed_tools_their_own_numbers(app):
    """Equivalence: the confirmed P1.2 file gives every seed tool the values it was given from that file."""
    machine = services.get_machine()
    own = {e.position: e.tool for e in services.turret_entries(machine)}
    confirm_p12_catalogue()
    steel = material("Steel 45 (C45)")
    for entry in services.turret_entries(machine, steel):
        assert [getattr(entry.tool, n) for n in NUMBERS] == [getattr(own[entry.position], n) for n in NUMBERS], \
            f"T{entry.position}"
        if entry.position == 3:  # CNMG M/N, grade M30: not in the P1.2 file
            assert entry.tool.catalogue_note is None and "GC" not in entry.tool.catalogue_warning
            continue
        assert entry.tool.catalogue_warning is None, f"T{entry.position}: {entry.tool.catalogue_warning}"
        assert entry.tool.catalogue_note.startswith("cutting data for P1.2: ap / f: Sandvik Coromant")
        assert entry.tool.source is None


def test_plan_is_the_same_with_the_confirmed_p12_rows(app):
    machine = services.get_machine()
    job = make_job()
    services.calculate_operations(job, machine)
    before = numbers(job)
    assert all(cd.NO_GEOMETRY_WARNING.split("{")[0] in op.warning for op in job.current_operations
               if op.tool_type in ("turning_rough", "turning_finish", "threading", "grooving", "parting"))
    confirm_p12_catalogue()
    services.calculate_operations(job, machine)
    assert numbers(job) == before
    for op in job.current_operations:
        if op.tool_id is None:
            continue
        assert "(check)" not in (op.warning or ""), (op.tool_type, op.warning)
        assert "cutting data for P1.2" in op.note
    rough = next(op for op in job.current_operations if op.tool_type == "turning_rough")
    assert "ap / f: Sandvik Coromant Turning tools 2020 p. A283, A279 (confirmed" in rough.note
    assert "Vc: Sandvik Coromant Turning tools 2020 p. A279" in rough.note
    groove = next(op for op in job.current_operations if op.tool_type == "grooving")
    assert "B139, B130, from a graph" in groove.note


def test_a_confirmed_row_with_other_values_changes_the_plan(app):
    machine = services.get_machine()
    confirm_p12_catalogue()
    job = make_job()
    services.calculate_operations(job, machine)
    rough = lambda: [op for op in job.current_operations if op.tool_type == "turning_rough"]  # noqa: E731
    assert [(op.ap, op.passes, op.f) for op in rough()] == [(1.6, 1, 0.3), (2.558, 3, 0.3)]  # ap rec 3
    row = db.session.execute(db.select(CuttingDataRow).filter_by(insert_code="CNMG120408-PM")).scalar_one()
    row.status = "replaced"
    db.session.add(CuttingDataRow(kind="geometry", catalogue="Other catalogue", insert_code="CNMG 12 04 08-PM",
                                  material_group="P", ap_min=0.5, ap_rec=2, ap_max=4, f_min=0.15, f_rec=0.25,
                                  f_max=0.4, page="12", origin="operator", status="confirmed"))
    db.session.commit()
    services.calculate_operations(job, machine)
    assert [(op.ap, op.passes, op.f) for op in rough()] == [(1.6, 1, 0.25), (1.919, 4, 0.25)]  # ap rec 2
    assert "ap / f: Other catalogue p. 12 (entered by the operator)" in rough()[0].note


def test_rows_not_confirmed_are_not_used(app):
    machine = services.get_machine()
    services.import_hand_typed(services.os.path.join(app.root_path, "..", "docs"), "turnpilot_catalog_P1.2.md")
    db.session.execute(db.update(CuttingDataRow).where(CuttingDataRow.insert_code == "SCMT120408-PM")
                       .values(status="rejected"))
    db.session.commit()
    facing = next(e.tool for e in services.turret_entries(machine, material("Steel 45 (C45)")) if e.position == 1)
    assert facing.catalogue_note is None
    assert facing.catalogue_warning.startswith("no confirmed catalogue ap / f for SCMT120408-PM in P1.2")


def test_material_without_a_group_asks_to_check_every_tool(app):
    confirm_p12_catalogue()
    job = make_job(material_name="AISI 304")  # ISO M: the seed T3 roughs, the rest has no M tool
    services.calculate_operations(job, services.get_machine())
    rough = next(op for op in job.current_operations if op.tool_type == "turning_rough")
    assert "AISI 304 has no catalogue group" in rough.warning


def test_tools_outside_the_catalogue_applications():
    values = cd.catalogue_values("milling", "R390", "GC1130", "C45", "P1.2", [])
    assert values == {"catalogue_warning": "no catalogue cutting data for milling tools: the tool's own values (check)"}


@pytest.mark.parametrize("vc_min, vc_max, points, expected", [
    (None, None, "0.1:455, 0.4:305, 0.8:215", (215, 455)),
    (100, 150, "125", (100, 150)),
    (None, None, "195", (195, 195)),
])
def test_vc_range_from_the_grade_row(vc_min, vc_max, points, expected):
    row = CuttingDataRow(id=1, kind="grade_vc", catalogue="C", grade="GC1", application="turning",
                         material_group="P1.2", vc_min=vc_min, vc_max=vc_max, vc_points=points, origin="operator")
    values = cd.catalogue_values("turning_rough", "X", "GC1", "C45", "P1.2", [row])
    assert (values["vc_min"], values["vc_max"]) == expected
    assert "no confirmed catalogue ap / f for X" in values["catalogue_warning"]
    assert "source" not in values  # ap / f still the tool's: its own source stays


def test_operations_page_shows_the_catalogue_source(app, client):
    confirm_p12_catalogue()
    job = make_job()
    client.post(f"/jobs/{job.id}/calculate")
    page = client.get(f"/jobs/{job.id}/operations").get_data(as_text=True)
    assert "cutting data for P1.2" in page
    assert db.session.execute(db.select(db.func.count(Operation.id))).scalar() > 0
