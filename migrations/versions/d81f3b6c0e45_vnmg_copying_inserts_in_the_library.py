"""VNMG copying inserts (PF finishing, PM roughing) in the tool library

Data only: VNMG 16 04 04-PF GC4315 and VNMG 16 04 08-PM GC4325 (Sandvik Coromant Turning tools 2020, steel P1.2;
grades ★ on A172, checked on the page image; ap / f A288; Vc A278 / A279) are added to the tool library of an
existing database, not to the turret: the operator chooses the positions. The holder (DVJNR 2020K16 / 2525M16) and
so RMPX (44°, A212) are left empty until the operator confirms the holder. Values are copied here so the migration
does not change with seed.py.

Revision ID: d81f3b6c0e45
Revises: c4e1f0a9b2d7
Create Date: 2026-10-10 19:00:00

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd81f3b6c0e45'
down_revision = 'c4e1f0a9b2d7'
branch_labels = None
depends_on = None

CATALOGUE = "Sandvik Coromant Turning tools 2020, steel P1.2, with coolant"


def vnmg(name, kind, code, grade, ap, f, vc, vc_page):
    return dict(name=name, type=kind, insert_code=code, grade=grade, iso_group="P", vc_min=vc[-1], vc_max=vc[0],
                f_min=f[0], f_max=f[2], ap_min=ap[0], ap_max=ap[2], ap_rec=ap[1], f_rec=f[1],
                vc_points=f"0.1:{vc[0]}, 0.4:{vc[1]}, 0.8:{vc[2]}", is_retired=False,
                source=f"{CATALOGUE}: A172 (grade ★, checked on the page image), A288 (ap, f), {vc_page} (Vc); "
                       "holder DVJNR 2020K16 / 2525M16, RMPX 44° (A212, PDF 216): the operator's choice")


TOOLS = (
    vnmg("Copy finishing VNMG (P)", "turning_finish", "VNMG160404-PF", "GC4315", (0.25, 0.4, 1.5), (0.07, 0.15, 0.3),
         (510, 365, 265), "A278"),
    vnmg("Copy roughing VNMG (P)", "turning_rough", "VNMG160408-PM", "GC4325", (0.5, 2.0, 4.0), (0.15, 0.3, 0.5),
         (455, 305, 215), "A279"),
)

tool = sa.table("tool", sa.column("id", sa.Integer), *(sa.column(k) for k in TOOLS[0]))
operation = sa.table("operation", sa.column("id", sa.Integer), sa.column("tool_id", sa.Integer))


def upgrade():
    bind = op.get_bind()
    for values in TOOLS:
        if not bind.execute(sa.select(tool.c.id).where(tool.c.insert_code == values["insert_code"])).first():
            bind.execute(tool.insert().values(**values))


def downgrade():
    bind = op.get_bind()
    for values in TOOLS:
        found = bind.execute(sa.select(tool.c.id).where(tool.c.insert_code == values["insert_code"])).first()
        if found is not None and not bind.execute(
                sa.select(operation.c.id).where(operation.c.tool_id == found[0])).first():
            bind.execute(tool.delete().where(tool.c.id == found[0]))
