"""material S355J2+N (EN 10025-2), Sandvik group P1.2

Data only: added to an existing database when no material of that name is there. Group P1.2 from the Sandvik
Coromant Turning tools 2020 material cross reference list (PDF 682: S355J2G3, 1.0570 / 1.0841 = P1.2.Z.AN; S355J2+N
is its current name); kc1 1600 from TT A278 (PDF 282) as for C45; mc 0.25 not verified. Hardness not given.

Revision ID: a3c6e81f4d27
Revises: f2b9d3a61c08
Create Date: 2026-10-15 21:00:00

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a3c6e81f4d27'
down_revision = 'f2b9d3a61c08'
branch_labels = None
depends_on = None

S355 = dict(name="S355J2+N (EN 10025-2)", iso_group="P", hardness_hb=None, kc1=1600, mc=0.25, catalogue_group="P1.2",
            kc_source="kc1 1600: Sandvik TT 2020 A278 (PDF 282), P1.2; group P1.2: TT cross reference list (PDF 682), "
                      "S355J2G3 = P1.2.Z.AN; mc 0.25: not verified")
material = sa.table("material", sa.column("id", sa.Integer), *(sa.column(k) for k in S355))
job = sa.table("job", sa.column("id", sa.Integer), sa.column("material_id", sa.Integer))


def upgrade():
    bind = op.get_bind()
    if not bind.execute(sa.select(material.c.id).where(material.c.name == S355["name"])).first():
        bind.execute(material.insert().values(**S355))


def downgrade():
    bind = op.get_bind()
    found = bind.execute(sa.select(material.c.id).where(material.c.name == S355["name"])).first()
    if found and not bind.execute(sa.select(job.c.id).where(job.c.material_id == found[0])).first():
        bind.execute(material.delete().where(material.c.id == found[0]))
