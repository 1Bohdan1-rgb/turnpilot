"""source of a tool's RMPX (max in-copying angle)

Revision ID: c4e1f0a9b2d7
Revises: a7b45471d5b1
Create Date: 2026-10-10 18:30:00

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c4e1f0a9b2d7'
down_revision = 'a7b45471d5b1'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('tool', schema=None) as batch_op:
        batch_op.add_column(sa.Column('max_ramp_source', sa.String(length=200), nullable=True))


def downgrade():
    with op.batch_alter_table('tool', schema=None) as batch_op:
        batch_op.drop_column('max_ramp_source')
