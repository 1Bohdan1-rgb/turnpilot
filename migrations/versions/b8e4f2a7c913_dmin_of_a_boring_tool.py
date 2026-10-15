"""DMIN of a boring tool (the smallest bore its holder goes into) and its source

Revision ID: b8e4f2a7c913
Revises: a3c6e81f4d27
Create Date: 2026-10-15 22:30:00

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b8e4f2a7c913'
down_revision = 'a3c6e81f4d27'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('tool', schema=None) as batch_op:
        batch_op.add_column(sa.Column('min_bore_diameter', sa.Float(), nullable=True))
        batch_op.add_column(sa.Column('min_bore_source', sa.String(length=200), nullable=True))


def downgrade():
    with op.batch_alter_table('tool', schema=None) as batch_op:
        batch_op.drop_column('min_bore_source')
        batch_op.drop_column('min_bore_diameter')
