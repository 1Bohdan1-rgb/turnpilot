"""CLI commands besides `seed`."""
import click

from . import services
from .models import Job, db


@click.command("gcode-demo")
@click.option("--job", "job_id", type=int, help="also fill this DXF job's G-code set-up with DEMO values")
def gcode_demo_command(job_id):
    """Fill the empty programming values (and a job's set-up) with DEMO values, to look at a program before the
    machine's values are known. They are marked DEMO: no program is ready to run while one is used."""
    machine = services.get_machine()
    job = db.session.get(Job, job_id) if job_id else None
    if job_id and job is None:
        raise click.ClickException(f"no job {job_id}")
    services.set_gcode_demo(machine, job)
    click.echo("DEMO values set (source: demo). The operator replaces them on the Machine page and the job page.")
