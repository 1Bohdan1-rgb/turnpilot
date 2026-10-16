"""the sources of kc1 and mc apart: kc1 1600 of P1.2 is verified (TT A278), mc is not

Revision ID: f1a2b3c4d5e6
Revises: e8a1b2c3d4f5
Create Date: 2026-10-16 20:00:00

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'f1a2b3c4d5e6'
down_revision = 'e8a1b2c3d4f5'
branch_labels = None
depends_on = None

KC1_P12 = "Sandvik TT 2020 A278 (PDF 282), P1.2"
C45_PLACEHOLDER = ("PLACEHOLDER: catalogue-type value for ISO P unalloyed steel (e.g. Sandvik Coromant P1.2), "
                   "not verified")
S355_OLD = ("kc1 1600: Sandvik TT 2020 A278 (PDF 282), P1.2; group P1.2: TT cross reference list (PDF 682), "
            "S355J2G3 = P1.2.Z.AN; mc 0.25: not verified")
S355_KC1 = f"{KC1_P12}; group P1.2: TT cross reference list (PDF 682), S355J2G3 = P1.2.Z.AN"


def upgrade():
    with op.batch_alter_table('material', schema=None) as batch_op:
        batch_op.add_column(sa.Column('kc1_source', sa.String(length=200), nullable=True))
        batch_op.add_column(sa.Column('mc_source', sa.String(length=200), nullable=True))
    conn = op.get_bind()
    for material_id, kc1, source in conn.execute(sa.text("SELECT id, kc1, kc_source FROM material")).fetchall():
        if source == C45_PLACEHOLDER and kc1 == 1600:  # the seed's C45, unchanged: kc1 verified, mc not
            kc1_source, mc_source = KC1_P12, "not verified"
        elif source == S355_OLD:
            kc1_source, mc_source = S355_KC1, "not verified"
        else:  # the operator's text or a placeholder: it covered both numbers
            kc1_source = mc_source = source
        conn.execute(sa.text("UPDATE material SET kc1_source = :k, mc_source = :m WHERE id = :i"),
                     {"k": kc1_source, "m": mc_source, "i": material_id})
    with op.batch_alter_table('material', schema=None) as batch_op:
        batch_op.drop_column('kc_source')


def downgrade():
    with op.batch_alter_table('material', schema=None) as batch_op:
        batch_op.add_column(sa.Column('kc_source', sa.String(length=200), nullable=True))
    conn = op.get_bind()
    for material_id, k, m in conn.execute(sa.text("SELECT id, kc1_source, mc_source FROM material")).fetchall():
        joined = k if k == m else f"kc1: {k or '-'}; mc: {m or '-'}"
        conn.execute(sa.text("UPDATE material SET kc_source = :s WHERE id = :i"), {"s": joined[:200], "i": material_id})
    with op.batch_alter_table('material', schema=None) as batch_op:
        batch_op.drop_column('mc_source')
        batch_op.drop_column('kc1_source')
