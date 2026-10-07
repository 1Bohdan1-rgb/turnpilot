"""seed tools T6 T7 T8 from the Sandvik 2020 catalogue; T4 renamed

Data only. As 3748554069af: the grooving, threading and parting seed tools of an existing database get
the catalogue values of the new seed (steel P1.2, docs/turnpilot_catalog_P1.2.md; the grooving and parting
feeds read from graphs), but only a tool that still has exactly its old placeholder values. "Finish turning
DNMG (P/M)" becomes "Finish turning DNMG (P)" when its ISO group is P (it is checked for steel only).

Revision ID: 5fb0e3d2a1c4
Revises: 3748554069af
Create Date: 2026-10-07 22:00:00

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '5fb0e3d2a1c4'
down_revision = '3748554069af'
branch_labels = None
depends_on = None

CATALOGUE = "Sandvik Coromant Turning tools 2020, steel P1.2, with coolant"
FIELDS = ("insert_code", "grade", "iso_group", "vc_min", "vc_max", "f_min", "f_max", "ap_min", "ap_max",
          "insert_width", "ap_rec", "f_rec", "vc_points", "source")
EMPTY = dict(ap_rec=None, f_rec=None, vc_points=None, source=None)

# name: (old placeholder values, catalogue values)
TOOLS = {
    "Grooving 3 mm": (
        dict(insert_code="GRV 3.0", grade="P25", iso_group="PN", vc_min=90, vc_max=160, f_min=0.05, f_max=0.12,
             ap_min=3.0, ap_max=3.0, insert_width=3.0, **EMPTY),
        dict(insert_code="N123G2-0300-0003-GM", grade="GC4325", iso_group="P", vc_min=140, vc_max=315,
             f_min=0.04, f_max=0.14, ap_min=3.0, ap_max=3.0, insert_width=3.0, ap_rec=None, f_rec=0.07,
             vc_points="0.05:315, 0.5:140", source=f"{CATALOGUE}: B11 (insert), B139 (f, graph), B130 (Vc)"),
    ),
    "Threading 60 deg": (
        dict(insert_code="16ER AG60", grade="P25", iso_group="PMN", vc_min=80, vc_max=150, f_min=0.5, f_max=3.0,
             ap_min=0.05, ap_max=0.2, insert_width=None, **EMPTY),
        dict(insert_code="266RG-16VM01A001M", grade="GC1125", iso_group="P", vc_min=195, vc_max=195, f_min=1.0,
             f_max=2.0, ap_min=0.05, ap_max=0.2, insert_width=None, ap_rec=None, f_rec=None, vc_points="195",
             source=f"{CATALOGUE}: C5 (insert, pitch 1-2), C73 (Vc)"),
    ),
    "Parting 3 mm": (
        dict(insert_code="PRT 3.0", grade="P25", iso_group="PMN", vc_min=80, vc_max=150, f_min=0.05, f_max=0.12,
             ap_min=3.0, ap_max=3.0, insert_width=3.0, **EMPTY),
        dict(insert_code="QD-NG-0300-0002-CM", grade="GC1125", iso_group="P", vc_min=115, vc_max=265, f_min=0.07,
             f_max=0.16, ap_min=3.0, ap_max=3.0, insert_width=3.0, ap_rec=None, f_rec=0.1,
             vc_points="0.05:265, 0.5:115", source=f"{CATALOGUE}: B53 (insert), B144 (f, graph), B131 (Vc)"),
    ),
}
RENAME = ("Finish turning DNMG (P/M)", "Finish turning DNMG (P)")

tool = sa.table("tool", sa.column("id", sa.Integer), sa.column("name", sa.String),
                *(sa.column(f) for f in FIELDS))


def _same(row, values):
    for key, expected in values.items():
        actual = row[key]
        if isinstance(expected, (int, float)) and actual is not None:
            if abs(float(actual) - float(expected)) > 1e-9:
                return False
        elif actual != expected:
            return False
    return True


def _replace(frm):
    bind = op.get_bind()
    for name, values in TOOLS.items():
        old, new = (values[0], values[1]) if frm == "old" else (values[1], values[0])
        for row in bind.execute(sa.select(tool).where(tool.c.name == name)).mappings().all():
            if _same(row, old):
                bind.execute(tool.update().where(tool.c.id == row["id"]).values(**new))


def upgrade():
    _replace("old")
    op.execute(tool.update().where(tool.c.name == RENAME[0], tool.c.iso_group == "P").values(name=RENAME[1]))


def downgrade():
    op.execute(tool.update().where(tool.c.name == RENAME[1], tool.c.iso_group == "P").values(name=RENAME[0]))
    _replace("new")
