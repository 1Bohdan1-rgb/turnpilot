"""the machine a job is planned for, the machine of its calculation, of a program and of a turret change

Data: every existing job, program and turret change was made with the first machine (Lathe 1).

Revision ID: c9e5a1b3d7f2
Revises: b8d4f0a2c6e1
Create Date: 2026-10-17 10:00:00

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c9e5a1b3d7f2'
down_revision = 'b8d4f0a2c6e1'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('job', schema=None) as batch_op:
        batch_op.add_column(sa.Column('machine_id', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('calculated_machine_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key('fk_job_machine_id', 'machine', ['machine_id'], ['id'])
        batch_op.create_foreign_key('fk_job_calculated_machine_id', 'machine', ['calculated_machine_id'], ['id'])
    with op.batch_alter_table('gcode_program', schema=None) as batch_op:
        batch_op.add_column(sa.Column('machine_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key('fk_gcode_program_machine_id', 'machine', ['machine_id'], ['id'])
    with op.batch_alter_table('job_turret_change', schema=None) as batch_op:
        batch_op.add_column(sa.Column('machine_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key('fk_job_turret_change_machine_id', 'machine', ['machine_id'], ['id'])
    first = op.get_bind().execute(sa.text("SELECT MIN(id) FROM machine")).scalar()
    if first is not None:
        op.execute(sa.text("UPDATE job SET machine_id = :m").bindparams(m=first))
        op.execute(sa.text("UPDATE job SET calculated_machine_id = :m WHERE id IN (SELECT job_id FROM operation)")
                   .bindparams(m=first))
        op.execute(sa.text("UPDATE gcode_program SET machine_id = :m").bindparams(m=first))
        op.execute(sa.text("UPDATE job_turret_change SET machine_id = :m").bindparams(m=first))


def downgrade():
    with op.batch_alter_table('job_turret_change', schema=None) as batch_op:
        batch_op.drop_constraint('fk_job_turret_change_machine_id', type_='foreignkey')
        batch_op.drop_column('machine_id')
    with op.batch_alter_table('gcode_program', schema=None) as batch_op:
        batch_op.drop_constraint('fk_gcode_program_machine_id', type_='foreignkey')
        batch_op.drop_column('machine_id')
    with op.batch_alter_table('job', schema=None) as batch_op:
        batch_op.drop_constraint('fk_job_calculated_machine_id', type_='foreignkey')
        batch_op.drop_constraint('fk_job_machine_id', type_='foreignkey')
        batch_op.drop_column('calculated_machine_id')
        batch_op.drop_column('machine_id')
