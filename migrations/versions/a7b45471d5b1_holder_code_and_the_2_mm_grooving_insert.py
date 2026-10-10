"""holder code of a tool; the 2 mm grooving insert in the library

Schema: Tool.holder_code (the operator's). Data: the CoroCut 1-2 grooving insert N123E2-0200-0002-GM (2 mm, seat E,
steel P1.2, Sandvik Coromant Turning tools 2020) is added to the tool library of an existing database, not to the
turret: the operator chooses its position. Its holder code is left empty (the shank size is the operator's).
Values are copied here so the migration does not change with seed.py.

Revision ID: a7b45471d5b1
Revises: 500ead6679f1
Create Date: 2026-10-10 18:00:00

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a7b45471d5b1'
down_revision = '500ead6679f1'
branch_labels = None
depends_on = None

GROOVING_2MM = dict(
    name="Grooving 2 mm", type="grooving", insert_code="N123E2-0200-0002-GM", grade="GC4325", iso_group="P",
    vc_min=140, vc_max=315, f_min=0.03, f_max=0.10, ap_min=2.0, ap_max=2.0, insert_width=2.0, nose_radius=0.2,
    max_depth=8.0, f_rec=0.07, vc_points="0.05:315, 0.5:140", is_retired=False,
    source="Sandvik Coromant Turning tools 2020, steel P1.2, with coolant: B10-B11 (insert, PDF 314-315), B139 (f, "
           "graph, PDF 443), B130 (Vc), B29 (holder R/LF123E08, max depth 8, PDF 333)")

tool = sa.table("tool", sa.column("id", sa.Integer), *(sa.column(k) for k in GROOVING_2MM))
operation = sa.table("operation", sa.column("id", sa.Integer), sa.column("tool_id", sa.Integer))


def upgrade():
    with op.batch_alter_table('tool', schema=None) as batch_op:
        batch_op.add_column(sa.Column('holder_code', sa.String(length=50), nullable=True))

    bind = op.get_bind()
    if not bind.execute(sa.select(tool.c.id).where(tool.c.insert_code == GROOVING_2MM["insert_code"])).first():
        bind.execute(tool.insert().values(**GROOVING_2MM))


def downgrade():
    bind = op.get_bind()
    found = bind.execute(sa.select(tool.c.id).where(tool.c.insert_code == GROOVING_2MM["insert_code"])).first()
    if found is not None and not bind.execute(
            sa.select(operation.c.id).where(operation.c.tool_id == found[0])).first():
        bind.execute(tool.delete().where(tool.c.id == found[0]))

    with op.batch_alter_table('tool', schema=None) as batch_op:
        batch_op.drop_column('holder_code')
