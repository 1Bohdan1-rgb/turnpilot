"""the operator keeps a bore's finishing pass where the planner would bore it to size in one operation: who, when

Revision ID: a8c2e6f0b4d1
Revises: f7a1b5c9d2e3
Create Date: 2026-10-24 12:00:00

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a8c2e6f0b4d1'
down_revision = 'f7a1b5c9d2e3'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('feature', schema=None) as batch_op:
        batch_op.add_column(sa.Column('bore_finish_kept_by', sa.String(length=100), nullable=True))
        batch_op.add_column(sa.Column('bore_finish_kept_at', sa.DateTime(), nullable=True))


def downgrade():
    with op.batch_alter_table('feature', schema=None) as batch_op:
        batch_op.drop_column('bore_finish_kept_at')
        batch_op.drop_column('bore_finish_kept_by')
