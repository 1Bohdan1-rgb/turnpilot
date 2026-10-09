"""Fanuc 0i-T (G code system A): printing a program as text, and reading the text back strictly.

Only the words this generator writes are accepted. Every X / Z / U / W / F value carries a decimal point: on a
Fanuc control "X20" without one is 20 µm. Comments are upper-case ASCII in parentheses.
Haas lathes read the same subset (G00 / G01 / G28 / G50 / G92 / G96 / G97 / G99, T0101, M03 / M04 / M08 / M09 /
M01 / M30), but that has to be checked against the control's manual before a program is run there.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .program import (Block, Comment, Coolant, Dwell, Feed, Home, OptionalStop, Program, Rapid, Spindle,
                      ThreadPass, ToolCall, ascii_text)

NAME = "Fanuc 0i-T (G code system A)"
COMMENT_WIDTH = 60
G_CODES = {0, 1, 4, 18, 21, 28, 40, 50, 92, 96, 97, 99}
M_CODES = {1, 3, 4, 5, 8, 9, 30}
DECIMAL_WORDS = "XZUWF"
INTEGER_WORDS = "NOSTP"  # P: G04 dwell in milliseconds


def number(value: float) -> str:
    """20 -> "20.", 19.6 -> "19.6", -0.8 -> "-0.8", 0 -> "0." (always a decimal point, no trailing zeros)."""
    text = f"{round(value, 3):.3f}".rstrip("0")
    return "0." if text in ("-0.", "0.") else text


def _feed(f: float) -> str:
    return f"F{number(f)}"


def comment_lines(text: str) -> list[str]:
    words, lines, line = ascii_text(text).split(), [], ""
    for word in words:
        if line and len(line) + 1 + len(word) > COMMENT_WIDTH:
            lines.append(line)
            line = word
        else:
            line = f"{line} {word}".strip()
    if line:
        lines.append(line)
    return [f"({line})" for line in lines]


def _command(c) -> list[str]:
    if isinstance(c, Rapid):
        return ["G00" + (f" X{number(c.x)}" if c.x is not None else "") + (f" Z{number(c.z)}" if c.z is not None
                                                                          else "")]
    if isinstance(c, Feed):
        return ["G01" + (f" X{number(c.x)}" if c.x is not None else "") + (f" Z{number(c.z)}" if c.z is not None
                                                                          else "")
                + (f" {_feed(c.f)}" if c.f is not None else "")]
    if isinstance(c, ThreadPass):
        return [f"G92 X{number(c.x)} Z{number(c.z)} {_feed(c.pitch)}"]
    if isinstance(c, Spindle):
        lines = [f"G50 S{c.limit}"] if c.limit else []
        g = "G96" if c.mode == "css" else "G97"
        return lines + [f"{g} S{c.value}" + (f" {c.direction}" if c.direction else "")]
    if isinstance(c, Dwell):
        return [f"G04 P{round(c.seconds * 1000)}"]
    if isinstance(c, Coolant):
        return ["M08" if c.on else "M09"]
    if isinstance(c, Home):
        return ["G28 U0.", "G28 W0."]
    if isinstance(c, ToolCall):
        return [f"T{c.position:02d}{c.position:02d}"]
    if isinstance(c, OptionalStop):
        return ["M01"]
    if isinstance(c, Comment):
        return comment_lines(c.text)
    raise TypeError(f"unknown command {c!r}")


def _block(b: Block) -> list[str]:
    title = comment_lines(f"T{b.tool_position:02d} {b.tool_name} - {b.title}")
    lines = [f"N{b.number} " + title[0]] + title[1:]
    for note in b.notes:
        lines += comment_lines(note)
    for warning in b.warnings:
        lines += comment_lines(f"CHECK: {warning}")
    lines += _command(Home()) + _command(ToolCall(b.tool_position))
    for c in b.commands:
        lines += _command(c)
    lines += _command(OptionalStop())
    return lines


def render(program: Program) -> str:
    lines = ["%", f"O{program.number:04d} " + comment_lines(program.job_title or "TURNPILOT")[0]]
    for text in program.header:
        lines += comment_lines(text)
    for note in program.notes:
        lines += comment_lines(note)
    for op_id, reason in sorted(program.skipped.items()):
        lines += comment_lines(f"NOT IN THIS PROGRAM: OPERATION {op_id}: {reason}")
    for warning in program.warnings:
        lines += comment_lines(f"CHECK: {warning}")
    lines.append("G21 G18 G40 G99")
    for b in program.blocks:
        lines += _block(b)
    lines += ["M05"] + _command(Home()) + ["M30", "%"]
    return "\n".join(lines) + "\n"


# --- reading the text back ----------------------------------------------------------------------------

@dataclass
class Line:
    number: int  # the line in the text (1-based)
    text: str
    words: dict = field(default_factory=dict)  # letter -> value (the last of a letter), G / M as lists
    g: list = field(default_factory=list)
    m: list = field(default_factory=list)
    comment: str = ""


@dataclass
class Parsed:
    lines: list[Line] = field(default_factory=list)
    errors: list[tuple[int, str]] = field(default_factory=list)  # (line, message)

    @property
    def ok(self) -> bool:
        return not self.errors


WORD = re.compile(r"([A-Z])([-+]?[0-9]*\.?[0-9]*)")


def parse(text: str) -> Parsed:
    """Reads the subset this generator writes; anything else is an error with its line number."""
    out = Parsed()
    lines = text.splitlines()
    if not lines or lines[0].strip() != "%" or lines[-1].strip() != "%":
        out.errors.append((1, "the program must start and end with a % line"))
    for i, raw in enumerate(lines, start=1):
        line = raw.strip()
        if line == "%":
            continue
        if not line.isascii():
            out.errors.append((i, "non-ASCII characters"))
            continue
        comments = re.findall(r"\(([^()]*)\)", line)
        code = re.sub(r"\([^()]*\)", " ", line)
        if "(" in code or ")" in code:
            out.errors.append((i, "unbalanced or nested parentheses"))
            continue
        parsed = Line(i, raw, comment=" ".join(comments))
        if any(c != c.upper() for c in comments):
            out.errors.append((i, "lower-case letters in a comment"))
        rest = code.replace(" ", "")
        if rest != rest.upper():
            out.errors.append((i, "lower-case letters"))
            continue
        pos = 0
        for m in WORD.finditer(rest):
            if m.start() != pos:
                break
            pos = m.end()
            letter, value = m.group(1), m.group(2)
            if not value or value in "+-.":
                out.errors.append((i, f"{letter} without a value"))
                continue
            if letter in DECIMAL_WORDS:
                if "." not in value:
                    out.errors.append((i, f"{letter}{value} without a decimal point (Fanuc reads it in µm)"))
                    continue
                parsed.words[letter] = float(value)
            elif letter in INTEGER_WORDS + "GM":
                if "." in value:
                    out.errors.append((i, f"{letter}{value}: a whole number is expected"))
                    continue
                n = int(value)
                if letter == "G":
                    if n not in G_CODES:
                        out.errors.append((i, f"G{value} is not used by this generator"))
                    parsed.g.append(n)
                elif letter == "M":
                    if n not in M_CODES:
                        out.errors.append((i, f"M{value} is not used by this generator"))
                    parsed.m.append(n)
                else:
                    parsed.words[letter] = n
            else:
                out.errors.append((i, f"unknown word {letter}{value}"))
        if pos != len(rest):
            out.errors.append((i, f"cannot read '{rest[pos:]}'"))
        out.lines.append(parsed)
    return out
