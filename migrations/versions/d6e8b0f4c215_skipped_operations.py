"""an operation the operator skips (left out of the program): who, when, why, carried from

Revision ID: d6e8b0f4c215
Revises: c5d7a9e3b104
Create Date: 2026-10-16 12:00:00

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd6e8b0f4c215'
down_revision = 'c5d7a9e3b104'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('operation', schema=None) as batch_op:
        batch_op.add_column(sa.Column('skipped_by', sa.String(length=100), nullable=True))
        batch_op.add_column(sa.Column('skipped_at', sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column('skipped_note', sa.String(length=200), nullable=True))
        batch_op.add_column(sa.Column('skip_carried_from', sa.Integer(), nullable=True))


def downgrade():
    with op.batch_alter_table('operation', schema=None) as batch_op:
        batch_op.drop_column('skip_carried_from')
        batch_op.drop_column('skipped_note')
        batch_op.drop_column('skipped_at')
        batch_op.drop_column('skipped_by')
