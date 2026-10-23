"""a turret copy carries the programming values the other machine has: both sets kept with the copy

Revision ID: c4d8e2f6a3b7
Revises: b3e7c9d1f5a4
Create Date: 2026-10-23 12:00:00

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c4d8e2f6a3b7'
down_revision = 'b3e7c9d1f5a4'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('machine_turret_copy', schema=None) as batch_op:
        batch_op.add_column(sa.Column('programming', sa.Text(), nullable=True))


def downgrade():
    with op.batch_alter_table('machine_turret_copy', schema=None) as batch_op:
        batch_op.drop_column('programming')
