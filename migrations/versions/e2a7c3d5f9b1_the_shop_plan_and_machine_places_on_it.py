"""the shop's plan (from a JSON file), the machines' places on it, their type and footprint

Revision ID: e2a7c3d5f9b1
Revises: d1f6b2c4e8a3
Create Date: 2026-10-17 13:00:00

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'e2a7c3d5f9b1'
down_revision = 'd1f6b2c4e8a3'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'shop_plan',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('width_mm', sa.Float(), nullable=False),
        sa.Column('height_mm', sa.Float(), nullable=False),
        sa.Column('data', sa.Text(), nullable=False),
        sa.Column('filename', sa.String(length=255), nullable=True),
        sa.Column('uploaded_by', sa.String(length=100), nullable=False),
        sa.Column('uploaded_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_table(
        'machine_placement',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('machine_id', sa.Integer(), nullable=False),
        sa.Column('x_mm', sa.Float(), nullable=False),
        sa.Column('y_mm', sa.Float(), nullable=False),
        sa.Column('rotation', sa.Integer(), nullable=False),
        sa.Column('placed_by', sa.String(length=100), nullable=False),
        sa.Column('placed_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['machine_id'], ['machine.id'], name='fk_machine_placement_machine_id'),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('machine', schema=None) as batch_op:
        batch_op.add_column(sa.Column('machine_kind', sa.String(length=20), nullable=True))
        batch_op.add_column(sa.Column('footprint_length_mm', sa.Float(), nullable=True))
        batch_op.add_column(sa.Column('footprint_width_mm', sa.Float(), nullable=True))


def downgrade():
    with op.batch_alter_table('machine', schema=None) as batch_op:
        batch_op.drop_column('footprint_width_mm')
        batch_op.drop_column('footprint_length_mm')
        batch_op.drop_column('machine_kind')
    op.drop_table('machine_placement')
    op.drop_table('shop_plan')
