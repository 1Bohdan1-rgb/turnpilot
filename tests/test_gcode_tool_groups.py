"""Operations one after another with the same tool: one G28 and tool change for the group, but every block keeps
its T word (no indexing: the offset is active, the operator may restart from its N) and its safe retract."""
from gcode_jobs import make_job, ready_machine

from turnpilot import services
from turnpilot.gcode import fanuc
from turnpilot.gcode.cycle_time import estimate
from turnpilot.models import Feature


def blocks(text):
    """{N: the block's lines} in the printed program."""
    out, current = {}, None
    for line in text.splitlines():
        if line.startswith("N"):
            current = int(line.split()[0][1:])
            out[current] = []
        if current is not None:
            out[current].append(line)
    return out


def test_one_tool_change_for_operations_with_the_same_tool(app):
    machine = ready_machine()
    # Ø20 L10, Ø26 L10, Ø32 L10 (a stepped shaft): roughing operations with T02 one after the other
    job = make_job([Feature(type="face"), Feature(type="od_turn", diameter=20, length=10),
                    Feature(type="od_turn", diameter=26, length=10), Feature(type="od_turn", diameter=32, length=10),
                    Feature(type="parting")], blank=36, length=40, stickout=45)
    readiness, program = services.gcode_program(job, machine)
    text = fanuc.render(program)
    rough = [b for b in program.blocks if b.tool_type == "turning_rough"]
    assert len(rough) >= 2 and len({b.tool_position for b in rough}) == 1
    by_n = blocks(text)
    first, second = by_n[rough[0].number], by_n[rough[1].number]
    t = f"T{rough[0].tool_position:02d}{rough[0].tool_position:02d}"
    assert "G28 U0." in first and t in first and "M01" not in first  # no stop before the same tool
    assert "G28 U0." not in second and t in second  # the T word again, without going home
    assert first[-1].startswith("G00 X") and second[second.index(t) + 3].startswith("G00 X")  # the safe retract / start
    result = services.gcode_simulation(job, machine, text)
    assert result.ok, result.errors
    runs = 1 + sum(a.tool_position != b.tool_position for a, b in zip(program.blocks, program.blocks[1:]))
    assert sum(op.tool_changes for op in estimate(text, 20, 24, 1.5).operations) == runs < len(program.blocks)


def test_another_tool_away_from_the_reference_point_is_still_an_error(app):
    machine = ready_machine()
    job = make_job()
    text = fanuc.render(services.gcode_program(job, machine)[1])
    lines = text.splitlines()
    i = max(k for k, line in enumerate(lines) if line == "G28 W0." and k < len(lines) - 3)
    broken = "\n".join(lines[:i - 1] + lines[i + 1:]) + "\n"  # a later tool change without G28 U0. / W0.
    assert any("tool change away from the reference point" in m
               for _, m in services.gcode_simulation(job, machine, broken).errors)
