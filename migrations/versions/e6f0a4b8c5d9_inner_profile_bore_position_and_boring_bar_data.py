"""the inner profile: where a bore is (the face it opens at, its start, its bottom) and a boring bar's data (its
overhang's source, the spindle direction the operator set for it)

Revision ID: e6f0a4b8c5d9
Revises: d5e9f3a7b4c8
Create Date: 2026-10-23 23:00:00

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'e6f0a4b8c5d9'
down_revision = 'd5e9f3a7b4c8'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('feature', schema=None) as batch_op:
        batch_op.add_column(sa.Column('bore_open_end', sa.String(length=10), nullable=True))
        batch_op.add_column(sa.Column('bore_from_mm', sa.Float(), nullable=True))
        batch_op.add_column(sa.Column('bore_bottom', sa.String(length=20), nullable=True))
        batch_op.add_column(sa.Column('bore_cone_angle', sa.Float(), nullable=True))
        batch_op.add_column(sa.Column('bore_position_source', sa.String(length=20), nullable=True))
        batch_op.add_column(sa.Column('bore_position_by', sa.String(length=100), nullable=True))
        batch_op.add_column(sa.Column('bore_position_at', sa.DateTime(), nullable=True))
    with op.batch_alter_table('tool', schema=None) as batch_op:
        batch_op.add_column(sa.Column('max_depth_source', sa.String(length=200), nullable=True))
        batch_op.add_column(sa.Column('spindle_direction', sa.String(length=3), nullable=True))
        batch_op.add_column(sa.Column('spindle_direction_source', sa.String(length=200), nullable=True))


def downgrade():
    with op.batch_alter_table('tool', schema=None) as batch_op:
        batch_op.drop_column('spindle_direction_source')
        batch_op.drop_column('spindle_direction')
        batch_op.drop_column('max_depth_source')
    with op.batch_alter_table('feature', schema=None) as batch_op:
        batch_op.drop_column('bore_position_at')
        batch_op.drop_column('bore_position_by')
        batch_op.drop_column('bore_position_source')
        batch_op.drop_column('bore_cone_angle')
        batch_op.drop_column('bore_bottom')
        batch_op.drop_column('bore_from_mm')
        batch_op.drop_column('bore_open_end')
