"""Seed data: one machine, three materials and a turret with typical tools."""

import click

from .models import Machine, Material, Tool, TurretSlot, TURRET_POSITIONS, db

# PLACEHOLDER: machine profile is an example, replace with the real machine data.
MACHINE = dict(name="Lathe 1", max_rpm=4000, power_kw=11.0, max_diameter=300.0)

# PLACEHOLDER: kc1 / mc are catalogue-type values written from memory, NOT checked against a catalogue.
# Replace them with the values of the tool maker's catalogue in use (Machine page, Materials).
KC_PLACEHOLDER = "PLACEHOLDER: catalogue-type value for {group} (e.g. Sandvik Coromant {code}), not verified"
MATERIALS = [
    dict(name="Steel 45 (C45)", iso_group="P", hardness_hb=200, kc1=1600, mc=0.25,
         kc_source=KC_PLACEHOLDER.format(group="ISO P unalloyed steel", code="P1.2")),
    dict(name="AISI 304", iso_group="M", hardness_hb=180, kc1=2000, mc=0.21,
         kc_source=KC_PLACEHOLDER.format(group="ISO M austenitic stainless steel", code="M2")),
    dict(name="Aluminium 6061", iso_group="N", hardness_hb=95, kc1=600, mc=0.25,
         kc_source=KC_PLACEHOLDER.format(group="ISO N wrought aluminium alloy", code="N1.2")),
]

# Key = turret position. T1, T2, T4, T9: Sandvik Coromant 2020 values for steel P1.2 with their pages
# (docs/turnpilot_catalog_P1.2.md; grade = the catalogue's first choice, the operator's decision of 2026-10-07),
# ISO P only: they are checked for steel only. Vc with coolant, as the catalogue gives it.
# The other tools are PLACEHOLDERS, NOT validated. The grooving tool deliberately does not cover ISO M,
# so an AISI 304 job shows the "tool missing" warning.
CATALOGUE_P12 = "Sandvik Coromant Turning tools 2020, steel P1.2, with coolant"
TURRET_TOOLS = {
    1: dict(name="Facing SCMT", type="facing", insert_code="SCMT120408-PM", grade="GC4325",
            iso_group="P", vc_min=215, vc_max=455, f_min=0.12, f_max=0.37, ap_min=0.6, ap_max=3.6,
            ap_rec=0.96, f_rec=0.25, vc_points="0.1:455, 0.4:305, 0.8:215",
            source=f"{CATALOGUE_P12}: A49 (grade), A290 (ap, f), A279 (Vc)"),
    2: dict(name="Rough turning CNMG (P)", type="turning_rough", insert_code="CNMG120408-PM", grade="GC4325",
            iso_group="P", vc_min=215, vc_max=455, f_min=0.15, f_max=0.5, ap_min=0.5, ap_max=5.5,
            ap_rec=3, f_rec=0.3, vc_points="0.1:455, 0.4:305, 0.8:215",
            source=f"{CATALOGUE_P12}: A283 (ap, f), A279 (Vc)"),
    3: dict(name="Rough turning CNMG (M/N)", type="turning_rough", insert_code="CNMG 120408", grade="M30",
            iso_group="MN", vc_min=120, vc_max=250, f_min=0.2, f_max=0.35, ap_min=1.0, ap_max=3.0),
    4: dict(name="Finish turning DNMG (P/M)", type="turning_finish", insert_code="DNMG150604-PF", grade="GC4315",
            iso_group="P", vc_min=265, vc_max=510, f_min=0.07, f_max=0.3, ap_min=0.25, ap_max=1.5,
            ap_rec=0.4, f_rec=0.15, vc_points="0.1:510, 0.4:365, 0.8:265",
            source=f"{CATALOGUE_P12}: A160 (grade), A285 (ap, f), A278 (Vc)"),
    5: dict(name="Finish turning VCGT (N)", type="turning_finish", insert_code="VCGT 160404", grade="H10",
            iso_group="N", vc_min=300, vc_max=600, f_min=0.05, f_max=0.2, ap_min=0.2, ap_max=1.5),
    6: dict(name="Grooving 3 mm", type="grooving", insert_code="GRV 3.0", grade="P25",
            iso_group="PN", vc_min=90, vc_max=160, f_min=0.05, f_max=0.12, ap_min=3.0, ap_max=3.0, insert_width=3.0),
    7: dict(name="Threading 60 deg", type="threading", insert_code="16ER AG60", grade="P25",
            iso_group="PMN", vc_min=80, vc_max=150, f_min=0.5, f_max=3.0, ap_min=0.05, ap_max=0.2),
    8: dict(name="Parting 3 mm", type="parting", insert_code="PRT 3.0", grade="P25",
            iso_group="PMN", vc_min=80, vc_max=150, f_min=0.05, f_max=0.12, ap_min=3.0, ap_max=3.0, insert_width=3.0),
    9: dict(name="Boring bar CCMT", type="boring", insert_code="CCMT09T304-PF", grade="GC4315",
            iso_group="P", vc_min=265, vc_max=510, f_min=0.06, f_max=0.23, ap_min=0.11, ap_max=2.0,
            ap_rec=0.35, f_rec=0.11, vc_points="0.1:510, 0.4:365, 0.8:265",
            source=f"{CATALOGUE_P12}: A41 (grade), A289 (ap, f), A278 (Vc)"),
}


def seed_database():
    """Insert seed data. Does nothing if a machine already exists."""
    if db.session.execute(db.select(Machine)).first():
        return False

    machine = Machine(**MACHINE)
    db.session.add(machine)
    db.session.add_all(Material(**m) for m in MATERIALS)
    for position in TURRET_POSITIONS:
        tool_data = TURRET_TOOLS.get(position)
        tool = Tool(**tool_data) if tool_data else None
        if tool:
            db.session.add(tool)
        machine.slots.append(TurretSlot(position=position, tool=tool))
    db.session.commit()
    return True


@click.command("seed")
def seed_command():
    """Insert seed data. Run `flask --app turnpilot db upgrade` first."""
    if seed_database():
        click.echo("Seed data inserted.")
    else:
        click.echo("Database already has data, nothing to do.")
