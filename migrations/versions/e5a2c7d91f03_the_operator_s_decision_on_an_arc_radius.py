"""the operator's decision on an arc whose drawn radius differs from its dimension

Revision ID: e5a2c7d91f03
Revises: d81f3b6c0e45
Create Date: 2026-10-10 20:00:00

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'e5a2c7d91f03'
down_revision = 'd81f3b6c0e45'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('feature', schema=None) as batch_op:
        batch_op.add_column(sa.Column('radius_dimension', sa.Float(), nullable=True))
        batch_op.add_column(sa.Column('radius_geometry', sa.Float(), nullable=True))
        batch_op.add_column(sa.Column('radius_source', sa.String(length=20), nullable=True))
        batch_op.add_column(sa.Column('radius_decided_at', sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column('radius_note', sa.String(length=200), nullable=True))


def downgrade():
    with op.batch_alter_table('feature', schema=None) as batch_op:
        batch_op.drop_column('radius_note')
        batch_op.drop_column('radius_decided_at')
        batch_op.drop_column('radius_source')
        batch_op.drop_column('radius_geometry')
        batch_op.drop_column('radius_dimension')
