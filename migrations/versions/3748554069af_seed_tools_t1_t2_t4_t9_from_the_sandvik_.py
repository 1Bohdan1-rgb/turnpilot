"""seed tools T1 T2 T4 T9 from the Sandvik 2020 catalogue

Data only. The seed tools of an existing database get the catalogue values of the new seed (steel P1.2,
docs/turnpilot_catalog_P1.2.md), but only a tool that still has exactly its old placeholder values: a tool
the operator has edited is left as it is. Values are copied here so the migration does not change with seed.py.

Revision ID: 3748554069af
Revises: 0558af27f2f6
Create Date: 2026-10-07 21:10:00

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '3748554069af'
down_revision = '0558af27f2f6'
branch_labels = None
depends_on = None

CATALOGUE = "Sandvik Coromant Turning tools 2020, steel P1.2, with coolant"
FIELDS = ("insert_code", "grade", "iso_group", "vc_min", "vc_max", "f_min", "f_max", "ap_min", "ap_max",
          "ap_rec", "f_rec", "vc_points", "source")
EMPTY = dict(ap_rec=None, f_rec=None, vc_points=None, source=None)

# name: (old placeholder values, catalogue values)
TOOLS = {
    "Facing SCMT": (
        dict(insert_code="SCMT 120408", grade="P25", iso_group="PMN", vc_min=150, vc_max=300, f_min=0.15,
             f_max=0.35, ap_min=0.5, ap_max=2.5, **EMPTY),
        dict(insert_code="SCMT120408-PM", grade="GC4325", iso_group="P", vc_min=215, vc_max=455, f_min=0.12,
             f_max=0.37, ap_min=0.6, ap_max=3.6, ap_rec=0.96, f_rec=0.25, vc_points="0.1:455, 0.4:305, 0.8:215",
             source=f"{CATALOGUE}: A49 (grade), A290 (ap, f), A279 (Vc)"),
    ),
    "Rough turning CNMG (P)": (
        dict(insert_code="CNMG 120408", grade="P25", iso_group="P", vc_min=180, vc_max=260, f_min=0.25,
             f_max=0.45, ap_min=1.5, ap_max=4.0, **EMPTY),
        dict(insert_code="CNMG120408-PM", grade="GC4325", iso_group="P", vc_min=215, vc_max=455, f_min=0.15,
             f_max=0.5, ap_min=0.5, ap_max=5.5, ap_rec=3, f_rec=0.3, vc_points="0.1:455, 0.4:305, 0.8:215",
             source=f"{CATALOGUE}: A283 (ap, f), A279 (Vc)"),
    ),
    "Finish turning DNMG (P/M)": (
        dict(insert_code="DNMG 150404", grade="P15", iso_group="PM", vc_min=200, vc_max=300, f_min=0.08,
             f_max=0.2, ap_min=0.2, ap_max=1.0, **EMPTY),
        dict(insert_code="DNMG150604-PF", grade="GC4315", iso_group="P", vc_min=265, vc_max=510, f_min=0.07,
             f_max=0.3, ap_min=0.25, ap_max=1.5, ap_rec=0.4, f_rec=0.15, vc_points="0.1:510, 0.4:365, 0.8:265",
             source=f"{CATALOGUE}: A160 (grade), A285 (ap, f), A278 (Vc)"),
    ),
    "Boring bar CCMT": (
        dict(insert_code="CCMT 09T304", grade="P25", iso_group="PMN", vc_min=120, vc_max=220, f_min=0.08,
             f_max=0.25, ap_min=0.2, ap_max=2.0, **EMPTY),
        dict(insert_code="CCMT09T304-PF", grade="GC4315", iso_group="P", vc_min=265, vc_max=510, f_min=0.06,
             f_max=0.23, ap_min=0.11, ap_max=2.0, ap_rec=0.35, f_rec=0.11, vc_points="0.1:510, 0.4:365, 0.8:265",
             source=f"{CATALOGUE}: A41 (grade), A289 (ap, f), A278 (Vc)"),
    ),
}

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


def _replace(frm, to):
    bind = op.get_bind()
    for name, values in TOOLS.items():
        old, new = (values[0], values[1]) if frm == "old" else (values[1], values[0])
        rows = bind.execute(sa.select(tool).where(tool.c.name == name)).mappings().all()
        for row in rows:
            if _same(row, old):
                bind.execute(tool.update().where(tool.c.id == row["id"]).values(**new))


def upgrade():
    _replace("old", "new")


def downgrade():
    _replace("new", "old")
