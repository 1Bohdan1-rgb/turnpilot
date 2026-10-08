from pathlib import Path

import pytest
from anthropic.types import Message, TextBlock, ToolUseBlock, Usage

from turnpilot import create_app
from turnpilot.models import db
from turnpilot.seed import seed_database

FIXTURES = Path(__file__).parent / "fixtures" / "drawings"


@pytest.fixture
def app(tmp_path):
    app = create_app(
        {"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:", "ANTHROPIC_MODEL": "test-model"},
        instance_path=str(tmp_path / "instance"),  # uploaded drawings go here, not into the real instance/
    )
    with app.app_context():
        db.create_all()  # in-memory test DB; migrations are checked in test_migrations.py
        seed_database()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


def make_response(tool_input=None, stop_reason="tool_use", text=None, usage=None, tool_name="record_part"):
    """A Messages API response as the SDK returns it, optionally with a record_part tool call."""
    content = []
    if text:
        content.append(TextBlock.model_construct(type="text", text=text))
    if tool_input is not None:
        content.append(
            ToolUseBlock.model_construct(type="tool_use", id="toolu_test", name=tool_name, input=tool_input)
        )
    return Message.model_construct(
        id="msg_test",
        type="message",
        role="assistant",
        model="test-model",
        content=content,
        stop_reason=stop_reason,
        stop_sequence=None,
        usage=Usage.model_construct(**(usage or {"input_tokens": 1500, "output_tokens": 400})),
    )


class FakeStream:
    def __init__(self, response):
        self.response = response

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_final_message(self):
        return self.response


class FakeMessages:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.response

    def stream(self, **kwargs):
        self.calls.append(kwargs)
        return FakeStream(self.response)


class FakeClient:
    """Stands in for anthropic.Anthropic(): records the request, returns a canned response."""

    def __init__(self, response):
        self.messages = FakeMessages(response)


def feature(type_, diameter=None, start_diameter=None, length=None, tolerance=None, ra=None, pitch=None,
            radius=None, confidence=0.95):
    return {
        "type": type_, "diameter": diameter, "start_diameter": start_diameter, "length": length,
        "tolerance": tolerance, "ra": ra, "pitch": pitch, "radius": radius, "confidence": confidence,
    }


def part(features, material="Steel 45 (C45)", blank_diameter=None, blank_length=None, overall_length=None,
         quantity=20, warnings=(), part_type="turned", general_ra=None):
    return {
        "part_type": part_type, "general_ra": general_ra, "material": material, "blank_diameter": blank_diameter, "blank_length": blank_length,
        "overall_length": overall_length, "quantity": quantity, "features": features, "warnings": list(warnings),
    }


def seed_turret():
    """The seed turret as the planner sees it (TurretEntry / ToolSpec), as services.turret_entries builds it."""
    from turnpilot.planner import ToolSpec, TurretEntry, parse_vc_points
    from turnpilot.seed import TURRET_TOOLS

    def spec(position, tool):
        data = {k: v for k, v in tool.items() if k != "grade"}
        data["vc_points"] = parse_vc_points(data.get("vc_points"))
        return ToolSpec(id=position, **data)

    return [TurretEntry(position, spec(position, tool)) for position, tool in TURRET_TOOLS.items()]


def confirm_p12_catalogue():
    """Import docs/turnpilot_catalog_P1.2.md and confirm all its rows, as the operator would on the review screen."""
    from datetime import datetime, timezone
    from pathlib import Path

    from turnpilot import services
    from turnpilot.models import CuttingDataRow

    services.import_hand_typed(str(Path(__file__).parent.parent / "docs"), "turnpilot_catalog_P1.2.md")
    for row in db.session.execute(db.select(CuttingDataRow)).scalars():
        row.status, row.confirmed_at = "confirmed", datetime.now(timezone.utc)
    db.session.commit()
