"""a drill's point angle with its source; the operator's decision on a bore's depth (full Ø / the point)

Revision ID: e8a1b2c3d4f5
Revises: d6e8b0f4c215
Create Date: 2026-10-16 15:00:00

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'e8a1b2c3d4f5'
down_revision = 'd6e8b0f4c215'
branch_labels = None
depends_on = None

SOURCE = "Sandvik Coromant Solid round tools 2020, B26 (PDF 226), column SIG"


def upgrade():
    with op.batch_alter_table('tool', schema=None) as batch_op:
        batch_op.add_column(sa.Column('point_angle_deg', sa.Float(), nullable=True))
        batch_op.add_column(sa.Column('point_angle_source', sa.String(length=200), nullable=True))
    with op.batch_alter_table('feature', schema=None) as batch_op:
        batch_op.add_column(sa.Column('depth_mode', sa.String(length=20), nullable=True))
        batch_op.add_column(sa.Column('depth_decided_by', sa.String(length=100), nullable=True))
        batch_op.add_column(sa.Column('depth_decided_at', sa.DateTime(), nullable=True))
    # the 860-GM drills on B26 (Ø8, Ø10): SIG 140°; Ø6 is on B25, not checked, left empty
    op.execute(sa.text("UPDATE tool SET point_angle_deg = 140, point_angle_source = :s WHERE type = 'drilling' "
                       "AND insert_code IN ('860.1-0800-025A0-GM', '860.1-1000-029A0-GM') "
                       "AND point_angle_deg IS NULL").bindparams(s=SOURCE))


def downgrade():
    with op.batch_alter_table('feature', schema=None) as batch_op:
        batch_op.drop_column('depth_decided_at')
        batch_op.drop_column('depth_decided_by')
        batch_op.drop_column('depth_mode')
    with op.batch_alter_table('tool', schema=None) as batch_op:
        batch_op.drop_column('point_angle_source')
        batch_op.drop_column('point_angle_deg')
