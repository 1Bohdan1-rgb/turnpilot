"""history of the operator's decisions on a bore's depth (to the full Ø / to the drill's point)

Schema: BoreDepthDecision. Data: each bore already decided becomes its first history row.

Revision ID: a7c3e9d1f2b4
Revises: f1a2b3c4d5e6
Create Date: 2026-10-16 21:00:00

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a7c3e9d1f2b4'
down_revision = 'f1a2b3c4d5e6'
branch_labels = None
depends_on = None

feature = sa.table("feature", sa.column("id", sa.Integer), sa.column("depth_mode", sa.String),
                   sa.column("depth_decided_by", sa.String), sa.column("depth_decided_at", sa.DateTime))


def upgrade():
    decision = op.create_table(
        'bore_depth_decision',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('feature_id', sa.Integer(), nullable=False),
        sa.Column('mode', sa.String(length=20), nullable=False),
        sa.Column('note', sa.String(length=200), nullable=True),
        sa.Column('decided_by', sa.String(length=100), nullable=False),
        sa.Column('decided_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['feature_id'], ['feature.id'], name='fk_bore_depth_decision_feature_id'),
        sa.PrimaryKeyConstraint('id'),
    )
    bind = op.get_bind()
    rows = bind.execute(sa.select(feature).where(feature.c.depth_mode.is_not(None))).fetchall()
    for r in rows:
        bind.execute(decision.insert().values(feature_id=r.id, mode=r.depth_mode, decided_by=r.depth_decided_by or "",
                                              decided_at=r.depth_decided_at))


def downgrade():
    op.drop_table('bore_depth_decision')
