"""a tool added from a catalogue: where every value of it was read (document, page, quote), who added it and when

Revision ID: f7a1b5c9d2e3
Revises: e6f0a4b8c5d9
Create Date: 2026-10-24 09:00:00

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'f7a1b5c9d2e3'
down_revision = 'e6f0a4b8c5d9'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('tool', schema=None) as batch_op:
        batch_op.add_column(sa.Column('catalogue_ref', sa.Text(), nullable=True))


def downgrade():
    with op.batch_alter_table('tool', schema=None) as batch_op:
        batch_op.drop_column('catalogue_ref')
