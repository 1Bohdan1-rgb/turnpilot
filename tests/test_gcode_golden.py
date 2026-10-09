"""Golden programs of synthetic parts: the printed text must not change unnoticed.

They are regression references. The operator checked, as a turner, the program of the real Zavisa 36 pin (local,
not in git) made by this generator on 2026-10-09; these synthetic texts were not read line by line by the operator.
A deliberate change: run with UPDATE_GOLDEN=1, look at the diff, commit it with the reason.
"""
import os
from pathlib import Path

import pytest
from gcode_jobs import chamfer, make_job, ready_machine

from turnpilot import services
from turnpilot.gcode import fanuc
from turnpilot.models import Feature

GOLDEN = Path(__file__).parent / "fixtures" / "gcode"


def pin():
    """Ø20 L15 (1×45° at the free end), groove Ø16 L3, Ø24 L20, Ø30 L25 (1×45°): Ø32 bar."""
    return dict(features=None)


def threaded_shaft():
    """M24×1.5 L22 at the free end (1.5×45° chamfer), relief Ø20.5 L3, taper Ø24 → Ø30 L4, Ø30 L20 with a 2×45°
    back chamfer, a Ø8 hole 16 deep: Ø32 bar."""
    return dict(features=[
        Feature(type="face"), Feature(type="bore", diameter=8, length=16, tolerance="H12"),
        Feature(type="od_turn", diameter=24, length=22), chamfer(24, 1.5, "left"),
        Feature(type="thread", diameter=24, length=22, pitch=1.5, location="external"),
        Feature(type="groove", diameter=20.5, start_diameter=24, length=3),
        Feature(type="taper", start_diameter=24, diameter=30, length=4),
        Feature(type="od_turn", diameter=30, length=20), chamfer(30, 2, "right"), Feature(type="parting")],
        length=60, stickout=60, name="Threaded shaft")


@pytest.mark.parametrize("name, part", [("pin", pin), ("threaded_shaft", threaded_shaft)])
def test_golden_program(app, name, part):
    machine = ready_machine()
    job = make_job(**part())
    readiness, program = services.gcode_program(job, machine)
    assert readiness.ok, readiness.blockers
    text = fanuc.render(program)
    result = services.gcode_simulation(job, machine, text)
    assert result.ok and result.complete, (result.errors, result.incomplete)
    path = GOLDEN / f"{name}.nc"
    if os.environ.get("UPDATE_GOLDEN"):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="ascii", newline="\n")
    assert text == path.read_text(encoding="ascii"), f"{name}: the program changed (UPDATE_GOLDEN=1 to accept)"
