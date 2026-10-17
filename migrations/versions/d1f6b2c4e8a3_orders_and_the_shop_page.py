"""orders (a job, a quantity, a machine) with the operator's records; machine statuses and places on the shop plan

Revision ID: d1f6b2c4e8a3
Revises: c9e5a1b3d7f2
Create Date: 2026-10-17 11:00:00

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd1f6b2c4e8a3'
down_revision = 'c9e5a1b3d7f2'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'order',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('job_id', sa.Integer(), nullable=False),
        sa.Column('machine_id', sa.Integer(), nullable=False),
        sa.Column('program_id', sa.Integer(), nullable=True),
        sa.Column('quantity', sa.Integer(), nullable=False),
        sa.Column('done', sa.Integer(), server_default='0', nullable=False),
        sa.Column('status', sa.String(length=10), server_default='open', nullable=False),
        sa.Column('is_current', sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column('created_by', sa.String(length=100), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['job_id'], ['job.id'], name='fk_order_job_id'),
        sa.ForeignKeyConstraint(['machine_id'], ['machine.id'], name='fk_order_machine_id'),
        sa.ForeignKeyConstraint(['program_id'], ['gcode_program.id'], name='fk_order_program_id'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_table(
        'order_event',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('order_id', sa.Integer(), nullable=False),
        sa.Column('kind', sa.String(length=20), nullable=False),
        sa.Column('value', sa.String(length=100), nullable=True),
        sa.Column('decided_by', sa.String(length=100), nullable=False),
        sa.Column('decided_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['order_id'], ['order.id'], name='fk_order_event_order_id'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_table(
        'order_operation_mark',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('order_id', sa.Integer(), nullable=False),
        sa.Column('block', sa.Integer(), nullable=False),
        sa.Column('title', sa.String(length=200), nullable=True),
        sa.Column('done', sa.Boolean(), nullable=False),
        sa.Column('decided_by', sa.String(length=100), nullable=False),
        sa.Column('decided_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['order_id'], ['order.id'], name='fk_order_operation_mark_order_id'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_table(
        'machine_status',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('machine_id', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('note', sa.String(length=200), nullable=True),
        sa.Column('decided_by', sa.String(length=100), nullable=False),
        sa.Column('decided_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['machine_id'], ['machine.id'], name='fk_machine_status_machine_id'),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('machine', schema=None) as batch_op:
        batch_op.add_column(sa.Column('shop_row', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('shop_col', sa.Integer(), nullable=True))


def downgrade():
    with op.batch_alter_table('machine', schema=None) as batch_op:
        batch_op.drop_column('shop_col')
        batch_op.drop_column('shop_row')
    op.drop_table('machine_status')
    op.drop_table('order_operation_mark')
    op.drop_table('order_event')
    op.drop_table('order')
