"""turret changes for one job (the job's own set-up over the machine's turret)

Revision ID: c5d7a9e3b104
Revises: b8e4f2a7c913
Create Date: 2026-10-16 10:00:00

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c5d7a9e3b104'
down_revision = 'b8e4f2a7c913'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'job_turret_change',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('job_id', sa.Integer(), nullable=False),
        sa.Column('position', sa.Integer(), nullable=False),
        sa.Column('tool_id', sa.Integer(), nullable=True),
        sa.Column('machine_tool_id', sa.Integer(), nullable=True),
        sa.Column('decided_by', sa.String(length=100), nullable=False),
        sa.Column('note', sa.String(length=200), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('removed_by', sa.String(length=100), nullable=True),
        sa.Column('removed_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['job_id'], ['job.id'], name='fk_job_turret_change_job_id'),
        sa.ForeignKeyConstraint(['tool_id'], ['tool.id'], name='fk_job_turret_change_tool_id'),
        sa.ForeignKeyConstraint(['machine_tool_id'], ['tool.id'], name='fk_job_turret_change_machine_tool_id'),
        sa.PrimaryKeyConstraint('id'),
    )


def downgrade():
    op.drop_table('job_turret_change')
