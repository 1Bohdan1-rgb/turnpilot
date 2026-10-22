"""the entering angle κr of a turning tool in its holder (the operator's, with its source)

Revision ID: a9d3f5b7c1e2
Revises: f4b8d2e6a1c9
Create Date: 2026-10-22 20:30:00

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a9d3f5b7c1e2'
down_revision = 'f4b8d2e6a1c9'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('tool', schema=None) as batch_op:
        batch_op.add_column(sa.Column('entering_angle_deg', sa.Float(), nullable=True))
        batch_op.add_column(sa.Column('entering_angle_source', sa.String(length=200), nullable=True))


def downgrade():
    with op.batch_alter_table('tool', schema=None) as batch_op:
        batch_op.drop_column('entering_angle_source')
        batch_op.drop_column('entering_angle_deg')
