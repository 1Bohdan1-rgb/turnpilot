"""Roadmap stage 5, step 1: the cutting data table, the material's catalogue group, the matching rules."""
from types import SimpleNamespace

from turnpilot import cutting_data as cd
from turnpilot.models import CuttingDataRow, Material, db


def row(**kw):
    data = dict(id=1, kind="geometry", insert_code=None, grade=None, application=None, material_group="P1.2")
    data.update(kw)
    return SimpleNamespace(**data)


def test_seed_steel_has_its_catalogue_group(app):
    groups = {m.name: m.catalogue_group for m in db.session.execute(db.select(Material)).scalars()}
    assert groups == {"Steel 45 (C45)": "P1.2", "AISI 304": None, "Aluminium 6061": None}


def test_catalogue_group_is_saved_on_the_machine_page(app, client):
    steel = db.session.execute(db.select(Material).filter_by(iso_group="M")).scalar_one()
    form = {"action": "materials"}
    for m in db.session.execute(db.select(Material)).scalars():
        form.update({f"m{m.id}-kc1": m.kc1, f"m{m.id}-mc": m.mc, f"m{m.id}-kc_source": m.kc_source or "",
                     f"m{m.id}-catalogue_group": m.catalogue_group or ""})
    form[f"m{steel.id}-catalogue_group"] = " M1.1 "
    client.post("/machine", data=form)
    assert db.session.get(Material, steel.id).catalogue_group == "M1.1"
    page = client.get("/machine").get_data(as_text=True)
    assert 'value="M1.1"' in page and "Catalogue group" in page


def test_cutting_data_page_lists_rows_by_status(app, client):
    db.session.add_all([
        CuttingDataRow(kind="geometry", catalogue="TT 2020", insert_code="CNMG120408-PM", material_group="P1.2",
                       ap_min=0.5, ap_rec=3, ap_max=5.5, origin="hand_typed", status="read", page="A283"),
        CuttingDataRow(kind="grade_vc", catalogue="TT 2020", grade="GC4325", application="turning",
                       material_group="P1.2", vc_points="0.1:455, 0.4:305, 0.8:215", origin="hand_typed",
                       status="confirmed", page="A279"),
    ])
    db.session.commit()
    page = client.get("/cutting-data").get_data(as_text=True)
    assert "To check (1)" in page and "Confirmed: used by the planner (1)" in page
    assert "0.5 – 3 – 5.5" in page and "Vc(f) 0.1:455, 0.4:305, 0.8:215" in page and "p. A283" in page


def test_codes_are_compared_without_spaces_and_case():
    assert cd.normalize_code("CNMG 12 04 08-PM") == cd.normalize_code("cnmg120408-pm")


def test_application_of_tool_types():
    assert cd.application_for("boring_rough") == "turning"
    assert cd.application_for("parting") == "parting"
    assert cd.application_for("milling") is None and cd.application_for("centre_drilling") is None


def test_group_row_before_iso_letter_row():
    letter = row(id=2, insert_code="CNMG120408-PM", material_group="P", ap_rec=2)
    group = row(id=1, insert_code="CNMG 12 04 08-PM", material_group="P1.2", ap_rec=3)
    assert cd.geometry_row([letter, group], "CNMG120408-PM", "P1.2") is group
    assert cd.geometry_row([letter], "CNMG120408-PM", "P1.2") is letter
    assert cd.geometry_row([letter, group], "CNMG120408-PM", "M1.1") is None
    assert cd.geometry_row([group], "CNMG120408-PM", "P1.1") is None


def test_grade_row_needs_the_application():
    turning = row(kind="grade_vc", grade="GC4325", application="turning")
    grooving = row(id=2, kind="grade_vc", grade="GC4325", application="grooving")
    assert cd.grade_row([turning, grooving], "GC4325", "turning_rough", "P1.2") is turning
    assert cd.grade_row([turning, grooving], "GC4325", "grooving", "P1.2") is grooving
    assert cd.grade_row([turning], "GC4325", "parting", "P1.2") is None
    assert cd.grade_row([turning], "GC4325", "milling", "P1.2") is None
