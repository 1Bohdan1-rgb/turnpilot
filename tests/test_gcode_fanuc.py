"""Roadmap stage 6, commit 5: the Fanuc 0i-T dialect, printing and reading the text back strictly."""
import pytest
from gcode_jobs import make_job, ready_machine

from turnpilot import services
from turnpilot.gcode import fanuc
from turnpilot.gcode.program import Block, Comment, Coolant, Feed, Program, Rapid, Spindle, ThreadPass


@pytest.mark.parametrize("value, text", [(20, "20."), (19.6, "19.6"), (-0.8, "-0.8"), (0, "0."), (-0.0, "0."),
                                         (-63.0, "-63."), (22.0115, "22.012"), (0.25, "0.25")])
def test_numbers_always_carry_a_decimal_point(value, text):
    assert fanuc.number(value) == text


def test_render_of_a_small_program():
    block = Block(4, "ROUGH D30", 2, "Rough turning CNMG (P)", [7], "turning_rough",
                  [Spindle("css", 355, "M04", 3500), Rapid(34, 3), Coolant(True), Rapid(x=30.8),
                   Feed(z=-63, f=0.3), Feed(x=31.8), Rapid(z=3), ThreadPass(22.5, -27, 1.5),
                   Spindle("rpm", 1200), Comment("note"), Coolant(False)],
                  warnings=["chamfers: check"], notes=["leaves 0.4 mm"])
    program = Program(12, "PIN", ["JOB 12 PIN"], [block], ["coolant: check"], {9: "boring: not generated"})
    assert fanuc.render(program) == """%
O0012 (PIN)
(JOB 12 PIN)
(NOT IN THIS PROGRAM: OPERATION 9: BORING: NOT GENERATED)
(CHECK: COOLANT: CHECK)
G21 G18 G40 G99
N4 (T02 ROUGH TURNING CNMG P - ROUGH D30)
(LEAVES 0.4 MM)
(CHECK: CHAMFERS: CHECK)
G28 U0.
G28 W0.
T0202
G50 S3500
G96 S355 M04
G00 X34. Z3.
M08
G00 X30.8
G01 Z-63. F0.3
G01 X31.8
G00 Z3.
G92 X22.5 Z-27. F1.5
G97 S1200
(NOTE)
M09
M01
M05
G28 U0.
G28 W0.
M30
%
"""


def test_long_comments_are_wrapped():
    lines = fanuc.comment_lines("word " * 30)
    assert all(len(line) <= fanuc.COMMENT_WIDTH + 2 for line in lines) and len(lines) == 3


@pytest.mark.parametrize("line, message", [
    ("G01 X20 F0.3", "X20 without a decimal point (Fanuc reads it in µm)"),
    ("G01 X20. F.", "F without a value"),
    ("G02 X20. Z-1.", "G02 is not used by this generator"),
    ("M98 P1000", "M98 is not used by this generator"),
    ("g01 x20.", "lower-case letters"),
    ("(Палець)", "non-ASCII characters"),
    ("(A (B))", "unbalanced or nested parentheses"),
    ("G01 Y5.", "unknown word Y5."),
    ("G96 S355.5", "S355.5: a whole number is expected"),
    ("G01 X20.#", "cannot read '#'"),
])
def test_strict_reading(line, message):
    parsed = fanuc.parse(f"%\n{line}\n%\n")
    assert (2, message) in parsed.errors


def test_program_must_be_framed_by_percent_lines():
    assert (1, "the program must start and end with a % line") in fanuc.parse("G00 X1.\n").errors


def test_the_pin_reads_back_without_errors(app):
    machine = ready_machine()
    readiness, program = services.gcode_program(make_job(), machine)
    text = fanuc.render(program)
    parsed = fanuc.parse(text)
    assert parsed.ok, parsed.errors
    assert "(DIALECT FANUC 0I-T G CODE SYSTEM A)" in text
    first = next(line for line in parsed.lines if line.g == [96])
    assert first.words == {"S": 380} and first.m == [4]
