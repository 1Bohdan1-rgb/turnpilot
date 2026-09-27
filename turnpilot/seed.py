"""Seed data: one machine, three materials and a turret with typical tools."""

import click

from .models import Machine, Material, Tool, TurretSlot, TURRET_POSITIONS, db

# PLACEHOLDER: machine profile is an example, replace with the real machine data.
MACHINE = dict(name="Lathe 1", max_rpm=4000, power_kw=11.0, max_diameter=300.0)

MATERIALS = [
    dict(name="Steel 45 (C45)", iso_group="P", hardness_hb=200),
    dict(name="AISI 304", iso_group="M", hardness_hb=180),
    dict(name="Aluminium 6061", iso_group="N", hardness_hb=95),
]

# PLACEHOLDER: cutting data ranges below are NOT validated.
# Replace them with values from the tool manufacturer's catalogue before real use.
# Key = turret position. The grooving tool deliberately does not cover ISO M,
# so an AISI 304 job shows the "tool missing" warning.
TURRET_TOOLS = {
    1: dict(name="Facing SCMT", type="facing", insert_code="SCMT 120408", grade="P25",
            iso_group="PMN", vc_min=150, vc_max=300, f_min=0.15, f_max=0.35, ap_min=0.5, ap_max=2.5),
    2: dict(name="Rough turning CNMG (P)", type="turning_rough", insert_code="CNMG 120408", grade="P25",
            iso_group="P", vc_min=180, vc_max=260, f_min=0.25, f_max=0.45, ap_min=1.5, ap_max=4.0),
    3: dict(name="Rough turning CNMG (M/N)", type="turning_rough", insert_code="CNMG 120408", grade="M30",
            iso_group="MN", vc_min=120, vc_max=250, f_min=0.2, f_max=0.35, ap_min=1.0, ap_max=3.0),
    4: dict(name="Finish turning DNMG (P/M)", type="turning_finish", insert_code="DNMG 150404", grade="P15",
            iso_group="PM", vc_min=200, vc_max=300, f_min=0.08, f_max=0.2, ap_min=0.2, ap_max=1.0),
    5: dict(name="Finish turning VCGT (N)", type="turning_finish", insert_code="VCGT 160404", grade="H10",
            iso_group="N", vc_min=300, vc_max=600, f_min=0.05, f_max=0.2, ap_min=0.2, ap_max=1.5),
    6: dict(name="Grooving 3 mm", type="grooving", insert_code="GRV 3.0", grade="P25",
            iso_group="PN", vc_min=90, vc_max=160, f_min=0.05, f_max=0.12, ap_min=3.0, ap_max=3.0, insert_width=3.0),
    7: dict(name="Threading 60 deg", type="threading", insert_code="16ER AG60", grade="P25",
            iso_group="PMN", vc_min=80, vc_max=150, f_min=0.5, f_max=3.0, ap_min=0.05, ap_max=0.2),
    8: dict(name="Parting 3 mm", type="parting", insert_code="PRT 3.0", grade="P25",
            iso_group="PMN", vc_min=80, vc_max=150, f_min=0.05, f_max=0.12, ap_min=3.0, ap_max=3.0, insert_width=3.0),
    9: dict(name="Boring bar CCMT", type="boring", insert_code="CCMT 09T304", grade="P25",
            iso_group="PMN", vc_min=120, vc_max=220, f_min=0.08, f_max=0.25, ap_min=0.2, ap_max=2.0),
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
