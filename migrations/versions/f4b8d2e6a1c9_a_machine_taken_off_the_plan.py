"""a machine taken off the shop plan: a placement row marked removed (its last place kept, nothing deleted)

Revision ID: f4b8d2e6a1c9
Revises: e2a7c3d5f9b1
Create Date: 2026-10-22 09:00:00

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'f4b8d2e6a1c9'
down_revision = 'e2a7c3d5f9b1'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('machine_placement', schema=None) as batch_op:
        batch_op.add_column(sa.Column('removed', sa.Boolean(), server_default=sa.false(), nullable=False))


def downgrade():
    with op.batch_alter_table('machine_placement', schema=None) as batch_op:
        batch_op.drop_column('removed')
