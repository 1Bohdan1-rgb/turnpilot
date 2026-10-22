"""a job's turret set-up accepted as a whole, the operator's "installed, offsets measured" marks, turret copies

Revision ID: b3e7c9d1f5a4
Revises: a9d3f5b7c1e2
Create Date: 2026-10-22 21:40:00

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b3e7c9d1f5a4'
down_revision = 'a9d3f5b7c1e2'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'turret_setup',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('job_id', sa.Integer(), nullable=False),
        sa.Column('machine_id', sa.Integer(), nullable=False),
        sa.Column('accepted_by', sa.String(length=100), nullable=False),
        sa.Column('accepted_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['job_id'], ['job.id'], name='fk_turret_setup_job_id'),
        sa.ForeignKeyConstraint(['machine_id'], ['machine.id'], name='fk_turret_setup_machine_id'),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('job_turret_change', schema=None) as batch_op:
        batch_op.add_column(sa.Column('setup_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key('fk_job_turret_change_setup_id', 'turret_setup', ['setup_id'], ['id'])
    op.create_table(
        'turret_install',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('change_id', sa.Integer(), nullable=False),
        sa.Column('installed_by', sa.String(length=100), nullable=False),
        sa.Column('installed_at', sa.DateTime(), nullable=False),
        sa.Column('undone_by', sa.String(length=100), nullable=True),
        sa.Column('undone_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['change_id'], ['job_turret_change.id'], name='fk_turret_install_change_id'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_table(
        'machine_turret_copy',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('machine_id', sa.Integer(), nullable=False),
        sa.Column('from_machine_id', sa.Integer(), nullable=False),
        sa.Column('copied_by', sa.String(length=100), nullable=False),
        sa.Column('copied_at', sa.DateTime(), nullable=False),
        sa.Column('before', sa.Text(), nullable=False),
        sa.Column('after', sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(['machine_id'], ['machine.id'], name='fk_machine_turret_copy_machine_id'),
        sa.ForeignKeyConstraint(['from_machine_id'], ['machine.id'], name='fk_machine_turret_copy_from_machine_id'),
        sa.PrimaryKeyConstraint('id'),
    )


def downgrade():
    op.drop_table('machine_turret_copy')
    op.drop_table('turret_install')
    with op.batch_alter_table('job_turret_change', schema=None) as batch_op:
        batch_op.drop_constraint('fk_job_turret_change_setup_id', type_='foreignkey')
        batch_op.drop_column('setup_id')
    op.drop_table('turret_setup')
