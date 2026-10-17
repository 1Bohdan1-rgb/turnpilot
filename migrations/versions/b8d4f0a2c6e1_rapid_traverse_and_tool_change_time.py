"""the machine's rapid traverse X / Z and tool change time, for the cycle time estimate

Revision ID: b8d4f0a2c6e1
Revises: a7c3e9d1f2b4
Create Date: 2026-10-17 09:00:00

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b8d4f0a2c6e1'
down_revision = 'a7c3e9d1f2b4'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('machine', schema=None) as batch_op:
        batch_op.add_column(sa.Column('rapid_x_m_min', sa.Float(), nullable=True))
        batch_op.add_column(sa.Column('rapid_z_m_min', sa.Float(), nullable=True))
        batch_op.add_column(sa.Column('tool_change_s', sa.Float(), nullable=True))


def downgrade():
    with op.batch_alter_table('machine', schema=None) as batch_op:
        batch_op.drop_column('tool_change_s')
        batch_op.drop_column('rapid_z_m_min')
        batch_op.drop_column('rapid_x_m_min')
