"""The stock after each block (gcode/stock3d.py): taken from the simulation's grid; the bar first, the program's stock
last; the generated text does not change."""
import json

import pytest
from gcode_jobs import make_job, ready_machine
from test_gcode_golden import pin, threaded_shaft

from turnpilot import services
from turnpilot.gcode import fanuc, stock3d
from turnpilot.gcode import simulate as gsim
from turnpilot.models import db

PARTS = [("pin", pin), ("threaded_shaft", threaded_shaft)]


def simulated(part):
    machine = ready_machine()
    job = make_job(**part())
    readiness, program = services.gcode_program(job, machine)
    assert readiness.ok, readiness.blockers
    text = fanuc.render(program)
    _, _, profile, _ = services.gcode_inputs(job, machine)
    return job, machine, text, profile, services.gcode_simulation(job, machine, text)


@pytest.mark.parametrize("name, part", PARTS)
def test_profile_after_starts_with_the_bar_and_ends_with_the_program_stock(app, name, part):
    job, _, text, profile, result = simulated(part)
    blocks = [ln.words["N"] for ln in fanuc.parse(text).lines if "N" in ln.words]
    assert [b.number for b in result.blocks] == blocks
    bar = stock3d.profile_after(result, 0)
    assert bar == [job.blank_diameter / 2] * len(bar)
    assert stock3d.profile_after(result, len(blocks)) == result.final_stock
    with pytest.raises(IndexError):
        stock3d.profile_after(result, len(blocks) + 1)


@pytest.mark.parametrize("name, part", PARTS)
def test_the_last_state_is_the_finished_profile(app, name, part):
    _, _, _, profile, result = simulated(part)
    final = stock3d.profile_after(result, len(result.blocks))
    for i, r in enumerate(final):
        z = result.z_top - i * gsim.DZ
        if z > gsim.TOL:
            assert r <= gsim.TOL, f"{name}: material beyond Z0 at Z{z:.2f}"
        elif -profile.length + gsim.TOL <= z < -gsim.TOL:  # inside the part (the end faces are the boundary)
            assert abs(r - profile.final(z)) <= 0.01 + gsim.TOL, f"{name}: Z{z:.2f} r {r} ≠ {profile.final(z)}"


@pytest.mark.parametrize("name, part", PARTS)
def test_no_block_adds_material(app, name, part):
    _, _, _, _, result = simulated(part)
    for k in range(1, len(result.blocks) + 1):
        before, after = stock3d.profile_after(result, k - 1), stock3d.profile_after(result, k)
        assert all(a <= b for a, b in zip(after, before)), f"{name}: block {k} adds material"


def test_simplify_stays_within_the_tolerance():
    pts = [(10 - i * 0.01, 16.0 if i < 300 else 12.0 + 0.002 * (i - 300) ** 1.1) for i in range(1000)]
    out = stock3d.simplify(pts)
    assert out[0] == pts[0] and out[-1] == pts[-1] and len(out) < len(pts) / 5

    def at(z):
        for (z0, r0), (z1, r1) in zip(out, out[1:]):
            if z1 <= z <= z0:
                return r0 if z0 == z1 else r0 + (r1 - r0) * (z - z0) / (z1 - z0)
    worst = max(abs(at(z) - r) for z, r in pts[310:])  # away from the step, where the distance is to a near-vertical
    assert worst <= stock3d.SIMPLIFY_MM + 1e-9


@pytest.mark.parametrize("name, part", PARTS)
def test_generated_program_keeps_the_states(app, name, part):
    machine = ready_machine()
    job = make_job(**part())
    _, record = services.generate_gcode(job, machine)
    states = json.loads(record.simulation)["stock_by_block"]
    blocks = [ln.words["N"] for ln in fanuc.parse(record.text).lines if "N" in ln.words]
    assert [s["n"] for s in states] == [None] + blocks
    assert {r for _, r in states[0]["points"]} == {job.blank_diameter / 2}
    assert services.stock_states(record) == (states, None)


def test_an_old_program_gets_its_states_once(app, monkeypatch):
    machine = ready_machine()
    job = make_job(**pin())
    _, record = services.generate_gcode(job, machine)
    sim = json.loads(record.simulation)
    expected = sim.pop("stock_by_block")
    record.simulation = json.dumps(sim)
    db.session.commit()
    states, computed_at = services.stock_states(record)
    assert states == expected and computed_at
    saved = json.loads(db.session.get(type(record), record.id).simulation)
    assert saved["stock_by_block"] == expected and saved["stock_by_block_computed_at"] == computed_at

    def no_simulation(*args, **kwargs):
        raise AssertionError("simulated again")
    monkeypatch.setattr(services, "gcode_simulation", no_simulation)
    assert services.stock_states(record) == (expected, computed_at)


def test_the_command_computes_the_states_of_old_programs_only(app):
    machine = ready_machine()
    job = make_job(**pin())
    _, old = services.generate_gcode(job, machine)
    _, new = services.generate_gcode(job, machine)
    sim = json.loads(old.simulation)
    expected = sim.pop("stock_by_block")
    old.simulation = json.dumps(sim)
    db.session.commit()
    runner = app.test_cli_runner()
    dry = runner.invoke(args=["stock-states", "--dry-run"])
    assert dry.exit_code == 0 and "2 program(s), 1 without states" in dry.output
    assert f"program {old.id} (job {job.id}): no states" in dry.output
    assert "stock_by_block" not in json.loads(db.session.get(type(old), old.id).simulation)
    before = new.simulation
    result = runner.invoke(args=["stock-states"])
    assert result.exit_code == 0 and f"program {old.id} (job {job.id}): {len(expected)} state(s)" in result.output
    assert json.loads(db.session.get(type(old), old.id).simulation)["stock_by_block"] == expected
    assert db.session.get(type(new), new.id).simulation == before  # it had its states: untouched
    again = runner.invoke(args=["stock-states"])
    assert "2 program(s), 0 without states" in again.output
    assert "no program 999" in runner.invoke(args=["stock-states", "--program", "999"]).output
