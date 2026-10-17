"""Rapids and the material: roughing starts from what the passes before left, a rapid keeps 1 mm from the material
(else the tool feeds), the move between blocks with the same tool goes X first then Z, and the simulation errs on
a G00 into the material, between blocks too."""
from gcode_jobs import make_job, ready_machine

from turnpilot import services
from turnpilot.gcode import fanuc
from turnpilot.gcode.program import Block, Rapid
from turnpilot.models import Feature


def taper_job():
    """Ø20 L10 at the free end, a taper Ø20 -> Ø30 over 10, a groove, Ø40 L20: the planner passes over the groove, so
    the taper is roughed from the bar Ø50 on the process sheet, but the Ø40's roughing before it has already taken
    the material down (as НД 012's taper after its arc)."""
    return make_job([Feature(type="face"), Feature(type="od_turn", diameter=20, length=10),
                     Feature(type="taper", start_diameter=20, diameter=30, length=10),
                     Feature(type="groove", diameter=27, start_diameter=40, length=3),
                     Feature(type="od_turn", diameter=40, length=20), Feature(type="parting")],
                    blank=50, length=48, stickout=53)


def test_roughing_rapids_over_what_the_passes_before_took(app):
    machine = ready_machine()
    job = taper_job()
    readiness, program = services.gcode_program(job, machine)
    notes = [n for b in program.blocks for n in b.notes]
    # the taper's steps run to the Ø40's face: the Ø40 and Ø20 roughing rapid over what they took
    assert any("a rapid there" in n for n in notes), notes
    text = fanuc.render(program)
    result = services.gcode_simulation(job, machine, text)
    assert result.ok and result.complete, (result.errors, result.incomplete)


def test_a_rapid_keeps_a_margin_from_the_material_else_the_tool_feeds(app):
    machine = ready_machine()
    job = taper_job()
    taper = next(b for b in services.gcode_program(job, machine)[1].blocks if "TAPER" in b.title)
    lines = fanuc._block(taper)
    i = next(k for k, line in enumerate(lines) if line.startswith("G00 Z-"))  # the way in to the pass along the taper
    above = float(lines[i - 1][5:])  # the diameter it travels at
    assert lines[i - 1].startswith("G00 X") and above >= 2 * (20 / 2 + 0.4) + 2 * 1.0 - 1e-6  # the last step + 1 mm
    assert lines[i + 1].startswith("G01 X")  # down to the pass fed, not rapid


def test_between_blocks_with_the_same_tool_x_first_then_z():
    block = Block(5, "ROUGH", 2, "Rough turning", [1], "turning_rough", commands=[Rapid(x=34.0, z=-10.0)])
    lines = fanuc._block(block, home=False)
    assert "G28 U0." not in lines and lines[lines.index("T0202") + 1:lines.index("T0202") + 3] == ["G00 X34.",
                                                                                                    "G00 Z-10."]
    assert "G00 X34. Z-10." in fanuc._block(block)  # after G28 the tool comes from the reference point


def test_the_simulation_errs_on_a_rapid_into_the_material_also_between_blocks(app):
    machine = ready_machine()
    job = make_job([Feature(type="face"), Feature(type="od_turn", diameter=20, length=10),
                    Feature(type="od_turn", diameter=26, length=10), Feature(type="od_turn", diameter=32, length=10),
                    Feature(type="parting")], blank=36, length=40, stickout=45)
    text = fanuc.render(services.gcode_program(job, machine)[1])
    assert services.gcode_simulation(job, machine, text).ok
    lines = text.splitlines()
    cut = next(k for k, line in enumerate(lines) if line.startswith("G01 Z-"))
    into = "\n".join(lines[:cut] + [lines[cut].replace("G01", "G00").split(" F")[0]] + lines[cut + 1:]) + "\n"
    assert any(m.startswith("G00 through material") for _, m in services.gcode_simulation(job, machine, into).errors)
    # the second block with the same tool: no G28; its way in made a diagonal into the material
    t = next(k for k, line in enumerate(lines) if line == "T0202" and lines[k - 1] != "G28 W0.")
    assert lines[t + 3:t + 5] == ["G00 X40.", "G00 Z3."] or lines[t + 3].startswith("G00 X")
    diagonal = "\n".join(lines[:t + 3] + ["G00 X20. Z-15."] + lines[t + 5:]) + "\n"
    errors = services.gcode_simulation(job, machine, diagonal).errors
    assert any(m.startswith("G00 through material") for _, m in errors), errors
