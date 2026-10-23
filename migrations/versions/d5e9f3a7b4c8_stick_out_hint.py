"""the stick-out hint: the machine's margin beyond the parting cut (the operator's), and what a job's stick-out was taken from

Revision ID: d5e9f3a7b4c8
Revises: c4d8e2f6a3b7
Create Date: 2026-10-23 13:00:00

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd5e9f3a7b4c8'
down_revision = 'c4d8e2f6a3b7'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('machine', schema=None) as batch_op:
        batch_op.add_column(sa.Column('stickout_margin_mm', sa.Float(), nullable=True))
    with op.batch_alter_table('job', schema=None) as batch_op:
        batch_op.add_column(sa.Column('stickout_note', sa.String(length=200), nullable=True))


def downgrade():
    with op.batch_alter_table('job', schema=None) as batch_op:
        batch_op.drop_column('stickout_note')
    with op.batch_alter_table('machine', schema=None) as batch_op:
        batch_op.drop_column('stickout_margin_mm')
