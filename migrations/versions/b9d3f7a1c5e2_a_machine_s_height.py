"""a machine's height (passport or operator, mm; the shop's 3D view): empty: a conditional height by its type

Revision ID: b9d3f7a1c5e2
Revises: a8c2e6f0b4d1
Create Date: 2026-10-25 09:00:00

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b9d3f7a1c5e2'
down_revision = 'a8c2e6f0b4d1'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('machine', schema=None) as batch_op:
        batch_op.add_column(sa.Column('height_mm', sa.Float(), nullable=True))


def downgrade():
    with op.batch_alter_table('machine', schema=None) as batch_op:
        batch_op.drop_column('height_mm')
