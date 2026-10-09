"""What may go into a program: the rules that block a program, and the operations it leaves out (with the reason).

A program is built only from approved operations of a job whose sections are in their order along the axis (a DXF
job), with a max spindle speed confirmed by the passport or the operator. An operation goes in only when its
cutting data come from confirmed catalogue rows or from the operator, and when the generator supports it.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# Operations the generator writes (outer profile, and drilling on the axis), by tool type.
SUPPORTED_TOOL_TYPES = ("facing", "turning_rough", "turning_finish", "grooving", "threading", "drilling", "parting")
CONFIRMED_ORIGINS = ("catalogue", "operator")
MAX_RPM_SOURCES = ("passport", "operator")

NOT_IN_AXIAL_ORDER = ("the sections' order along the axis is not known (a job not read from a DXF): a program "
                      "needs the profile from the part zero")
NO_OPERATIONS = "no operations: calculate the job first"
NOT_APPROVED = "operations not approved yet: {sequences}"
TOOLS_CHANGED = "tools changed since the calculation ({names}): calculate again"
NO_MAX_RPM = ("the machine's max spindle speed is not confirmed (passport or operator): the program cannot limit the "
              "spindle (G50)")

SKIP_NO_TOOL = "no tool: not generated"
SKIP_UNSUPPORTED = "{tool_type}: not generated in this version (outer profile and drilling on the axis only)"
SKIP_CHAMFER = "a chamfer on its own: not generated (chamfers are cut with their diameter's finishing pass)"
SKIP_ORIGIN = ("cutting data not from confirmed catalogue rows or the operator ({origin}): not generated; confirm "
               "the rows on the Cutting data page or set the values on the operation")
SKIP_NO_DATA = "no cutting data (Vc / n or f missing): not generated"


@dataclass(frozen=True)
class OperationInfo:
    id: int
    sequence: int
    tool_type: str
    feature_type: str
    status: str
    has_tool: bool
    origin: str | None
    vc: float | None
    n: int | None
    f: float | None


@dataclass
class Readiness:
    blockers: list[str] = field(default_factory=list)  # no program at all
    skipped: dict[int, str] = field(default_factory=dict)  # operation id -> why it is not in the program
    usable: list[int] = field(default_factory=list)  # operation ids, in their order

    @property
    def ok(self) -> bool:
        return not self.blockers


def skip_reason(op: OperationInfo) -> str | None:
    if not op.has_tool:
        return SKIP_NO_TOOL
    if op.tool_type not in SUPPORTED_TOOL_TYPES:
        return SKIP_UNSUPPORTED.format(tool_type=op.tool_type)
    if op.feature_type == "chamfer":
        return SKIP_CHAMFER
    if op.origin not in CONFIRMED_ORIGINS:
        return SKIP_ORIGIN.format(origin=op.origin or "not known")
    if op.f is None or (op.vc is None and op.n is None):
        return SKIP_NO_DATA
    return None


def check(axial_order_known: bool, operations: list[OperationInfo], changed_tools: list[str],
          max_rpm_source: str | None) -> Readiness:
    result = Readiness()
    if not axial_order_known:
        result.blockers.append(NOT_IN_AXIAL_ORDER)
    if not operations:
        result.blockers.append(NO_OPERATIONS)
    pending = [str(op.sequence) for op in operations if op.status == "proposed"]
    if pending:
        result.blockers.append(NOT_APPROVED.format(sequences=", ".join(pending)))
    if changed_tools:
        result.blockers.append(TOOLS_CHANGED.format(names=", ".join(changed_tools)))
    if max_rpm_source not in MAX_RPM_SOURCES:
        result.blockers.append(NO_MAX_RPM)
    for op in sorted(operations, key=lambda o: o.sequence):
        reason = skip_reason(op)
        if reason:
            result.skipped[op.id] = reason
        else:
            result.usable.append(op.id)
    return result
