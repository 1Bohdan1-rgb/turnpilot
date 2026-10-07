"""rough boring tool on T5

Data only. Adds "Rough boring CCMT (P)" (CCMT09T304-PM GC4325, Sandvik TT 2020: A41, A289, A279) to the
tool library and puts it on T5 when T5 still holds the seed's "Finish turning VCGT (N)" with its placeholder
values; VCGT stays in the library. A turret the operator has changed only gets the tool in the library.
Values are copied here so the migration does not change with seed.py.

Revision ID: 8c2d41e7b9a3
Revises: 5fb0e3d2a1c4
Create Date: 2026-10-07 23:00:00

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '8c2d41e7b9a3'
down_revision = '5fb0e3d2a1c4'
branch_labels = None
depends_on = None

ROUGH_BORING = dict(
    name="Rough boring CCMT (P)", type="boring_rough", insert_code="CCMT09T304-PM", grade="GC4325", iso_group="P",
    vc_min=215, vc_max=455, f_min=0.08, f_max=0.23, ap_min=0.25, ap_max=3.0, ap_rec=0.64, f_rec=0.15,
    vc_points="0.1:455, 0.4:305, 0.8:215", source="Sandvik TT 2020: сплав A41 (★), ap/f A289, Vc A279",
    is_retired=False,
)
VCGT = dict(name="Finish turning VCGT (N)", type="turning_finish", insert_code="VCGT 160404", grade="H10",
            iso_group="N", vc_min=300, vc_max=600, f_min=0.05, f_max=0.2, ap_min=0.2, ap_max=1.5)
POSITION = 5

tool = sa.table("tool", sa.column("id", sa.Integer), *(sa.column(k) for k in ROUGH_BORING if k != "id"),
                sa.column("insert_width"), sa.column("diameter"))
slot = sa.table("turret_slot", sa.column("id", sa.Integer), sa.column("position", sa.Integer),
                sa.column("tool_id", sa.Integer))
operation = sa.table("operation", sa.column("id", sa.Integer), sa.column("tool_id", sa.Integer))


def _same(row, values):
    for key, expected in values.items():
        actual = row[key]
        if isinstance(expected, (int, float)) and not isinstance(expected, bool) and actual is not None:
            if abs(float(actual) - float(expected)) > 1e-9:
                return False
        elif actual != expected:
            return False
    return True


def upgrade():
    bind = op.get_bind()
    if bind.execute(sa.select(tool.c.id).where(tool.c.name == ROUGH_BORING["name"])).first():
        return  # already there
    bind.execute(tool.insert().values(**ROUGH_BORING))
    new_id = bind.execute(sa.select(tool.c.id).where(tool.c.name == ROUGH_BORING["name"])).scalar_one()
    for row in bind.execute(sa.select(slot).where(slot.c.position == POSITION)).mappings().all():
        if row["tool_id"] is None:
            continue
        held = bind.execute(sa.select(tool).where(tool.c.id == row["tool_id"])).mappings().first()
        if held is not None and _same(held, VCGT):
            bind.execute(slot.update().where(slot.c.id == row["id"]).values(tool_id=new_id))


def downgrade():
    bind = op.get_bind()
    new = bind.execute(sa.select(tool.c.id).where(tool.c.name == ROUGH_BORING["name"])).first()
    if new is None:
        return
    vcgt = bind.execute(sa.select(tool.c.id).where(tool.c.name == VCGT["name"])).first()
    bind.execute(slot.update().where(slot.c.position == POSITION, slot.c.tool_id == new[0])
                 .values(tool_id=vcgt[0] if vcgt else None))
    if not bind.execute(sa.select(operation.c.id).where(operation.c.tool_id == new[0])).first():
        bind.execute(tool.delete().where(tool.c.id == new[0]))
