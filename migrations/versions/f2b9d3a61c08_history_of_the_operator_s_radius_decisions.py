"""history of the operator's decisions on an arc's radius (drawn ≠ dimension)

Schema: RadiusDecision. Data: each arc already decided becomes its first history row (the operator's name was not
asked then: empty).

Revision ID: f2b9d3a61c08
Revises: e5a2c7d91f03
Create Date: 2026-10-14 21:00:00

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'f2b9d3a61c08'
down_revision = 'e5a2c7d91f03'
branch_labels = None
depends_on = None

feature = sa.table("feature", sa.column("id", sa.Integer), sa.column("radius", sa.Float),
                   sa.column("radius_source", sa.String), sa.column("radius_dimension", sa.Float),
                   sa.column("radius_geometry", sa.Float), sa.column("radius_note", sa.String),
                   sa.column("radius_decided_at", sa.DateTime))


def upgrade():
    decision = op.create_table(
        'radius_decision',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('feature_id', sa.Integer(), nullable=False),
        sa.Column('radius', sa.Float(), nullable=False),
        sa.Column('source', sa.String(length=20), nullable=False),
        sa.Column('dimension', sa.Float(), nullable=True),
        sa.Column('geometry', sa.Float(), nullable=True),
        sa.Column('note', sa.String(length=200), nullable=True),
        sa.Column('decided_by', sa.String(length=100), nullable=True),
        sa.Column('decided_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['feature_id'], ['feature.id'], name='fk_radius_decision_feature_id'),
        sa.PrimaryKeyConstraint('id'),
    )
    bind = op.get_bind()
    rows = bind.execute(sa.select(feature).where(feature.c.radius_source.is_not(None))).fetchall()
    for r in rows:
        bind.execute(decision.insert().values(
            feature_id=r.id, radius=r.radius, source=r.radius_source, dimension=r.radius_dimension,
            geometry=r.radius_geometry, note=r.radius_note, decided_by=None, decided_at=r.radius_decided_at))


def downgrade():
    op.drop_table('radius_decision')
