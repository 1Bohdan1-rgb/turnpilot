"""CLI commands besides `seed`."""
import click

from . import services
from .models import Job, db


@click.command("gcode-demo")
@click.option("--job", "job_id", type=int, help="also fill this DXF job's G-code set-up with DEMO values")
def gcode_demo_command(job_id):
    """Fill the empty programming values (and a job's set-up) with DEMO values, to look at a program before the
    machine's values are known. They are marked DEMO: no program is ready to run while one is used."""
    job = db.session.get(Job, job_id) if job_id else None
    if job_id and job is None:
        raise click.ClickException(f"no job {job_id}")
    machine = services.job_machine(job) if job else services.get_machine()
    services.set_gcode_demo(machine, job)
    click.echo("DEMO values set (source: demo). The operator replaces them on the Machine page and the job page.")


@click.command("stock-states")
@click.option("--program", "program_id", type=int, help="only this program")
@click.option("--dry-run", is_flag=True, help="list the programs without states, compute nothing")
def stock_states_command(program_id, dry_run):
    """Compute the stock after each block (the 3D view's states) for programs made before they were kept: each is
    simulated again from its text with the job's and the turret's current data, and the states are saved with the
    time (services.stock_states). Programs that have them are left as they are."""
    import json
    import time

    from .models import GcodeProgram

    query = db.select(GcodeProgram).order_by(GcodeProgram.id)
    if program_id:
        query = query.filter_by(id=program_id)
    records = db.session.execute(query).scalars().all()
    if program_id and not records:
        raise click.ClickException(f"no program {program_id}")
    todo = [r for r in records if "stock_by_block" not in json.loads(r.simulation)]
    click.echo(f"{len(records)} program(s), {len(todo)} without states")
    for record in todo:
        if dry_run:
            click.echo(f"program {record.id} (job {record.job_id}): no states")
            continue
        start = time.monotonic()
        try:
            states, computed_at = services.stock_states(record)
        except Exception as e:  # one program that cannot be simulated again does not stop the others
            db.session.rollback()
            click.echo(f"program {record.id} (job {record.job_id}): not computed: {e}")
            continue
        click.echo(f"program {record.id} (job {record.job_id}): {len(states)} state(s), "
                   f"{time.monotonic() - start:.1f} s, computed {computed_at}")
