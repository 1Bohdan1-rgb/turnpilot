"""max depth of drills; 860-GM drills on T10-T12

Schema: Tool.max_depth. Data: the CoroDrill 860-GM drills Ø6 / Ø8 / Ø10 (Sandvik Solid round tools 2020, steel
P1.2) are added to the tool library of an existing database and put on T10 / T11 / T12, but only on a position
that is still empty; otherwise the drill is only added to the library. Values are copied here so the migration
does not change with seed.py.

Revision ID: 450337eecff1
Revises: 8c2d41e7b9a3
Create Date: 2026-10-07 19:27:15.389024

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '450337eecff1'
down_revision = '8c2d41e7b9a3'
branch_labels = None
depends_on = None

SOURCE = ("Sandvik Coromant Solid round tools 2020, steel P1.2, 860-GM 3×D, external coolant: {page} (code), "
          "B70 (Vc), B71 (f)")
# position, diameter, code, page of the code, (f min, f rec, f max)
DRILLS = (
    (10, 6.0, "860.1-0600-016A0-GM", "B25", (0.15, 0.20, 0.25)),
    (11, 8.0, "860.1-0800-025A0-GM", "B26", (0.16, 0.22, 0.28)),
    (12, 10.0, "860.1-1000-029A0-GM", "B26", (0.20, 0.25, 0.30)),
)


def drill(d, code, page, feeds):
    f_min, f_rec, f_max = feeds
    return dict(name=f"Drill 860-GM Ø{d:g}", type="drilling", insert_code=code, grade="X1BM", iso_group="P",
                vc_min=100, vc_max=150, f_min=f_min, f_max=f_max, ap_min=d / 2, ap_max=d / 2, diameter=d,
                max_depth=3 * d, f_rec=f_rec, vc_points="125", source=SOURCE.format(page=page), is_retired=False)


tool = sa.table("tool", sa.column("id", sa.Integer), *(sa.column(k) for k in drill(6.0, "", "", (0, 0, 0))))
slot = sa.table("turret_slot", sa.column("id", sa.Integer), sa.column("position", sa.Integer),
                sa.column("tool_id", sa.Integer))
operation = sa.table("operation", sa.column("id", sa.Integer), sa.column("tool_id", sa.Integer))


def upgrade():
    with op.batch_alter_table('tool', schema=None) as batch_op:
        batch_op.add_column(sa.Column('max_depth', sa.Float(), nullable=True))

    bind = op.get_bind()
    for position, d, code, page, feeds in DRILLS:
        values = drill(d, code, page, feeds)
        if bind.execute(sa.select(tool.c.id).where(tool.c.name == values["name"])).first():
            continue
        bind.execute(tool.insert().values(**values))
        new_id = bind.execute(sa.select(tool.c.id).where(tool.c.name == values["name"])).scalar_one()
        bind.execute(slot.update().where(slot.c.position == position, slot.c.tool_id.is_(None))
                     .values(tool_id=new_id))


def downgrade():
    bind = op.get_bind()
    for position, d, code, page, feeds in DRILLS:
        found = bind.execute(sa.select(tool.c.id).where(tool.c.name == f"Drill 860-GM Ø{d:g}")).first()
        if found is None:
            continue
        bind.execute(slot.update().where(slot.c.tool_id == found[0]).values(tool_id=None))
        if not bind.execute(sa.select(operation.c.id).where(operation.c.tool_id == found[0])).first():
            bind.execute(tool.delete().where(tool.c.id == found[0]))

    with op.batch_alter_table('tool', schema=None) as batch_op:
        batch_op.drop_column('max_depth')
