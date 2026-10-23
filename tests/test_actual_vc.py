"""When n is capped at the machine's max RPM, the process sheet shows the Vc the spindle gives, on the diameter n
is calculated on (facing: the blank's Ø)."""
from types import SimpleNamespace

from gcode_jobs import make_job, ready_machine

from turnpilot import services


def test_the_actual_vc_on_the_reference_diameter():
    op = SimpleNamespace(vc=380.0, n=4000, ref_diameter=22.0)  # facing a Ø22 blank, capped at 4000 rpm
    assert services.actual_vc(op) == (276.5, 22.0)
    assert services.actual_vc(SimpleNamespace(vc=125.0, n=3979, ref_diameter=10.0)) is None  # not capped
    assert services.actual_vc(SimpleNamespace(vc=None, n=None, ref_diameter=None)) is None


def test_the_process_sheet_shows_it(client):
    machine = ready_machine(max_rpm=1500)
    job = make_job()
    capped = [op for op in job.current_operations if services.actual_vc(op)]
    assert capped
    page = client.get(f"/jobs/{job.id}/operations").get_data(as_text=True)
    vc, d = services.actual_vc(capped[0])
    assert f"→ {vc} on Ø{d:g}" in page and f"capped at the machine&#39;s max 1500 rpm: on Ø{d:g}" not in page
    assert f"capped at the machine's max 1500 rpm: on Ø{d:g} (the diameter n is calculated on)" in page
