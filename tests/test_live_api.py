"""Live checks against the real API. Skipped by default; run with:  pytest -m live

They send each strict tool schema with a tiny max_tokens, so the API compiles the schema (and
rejects it with a 400 if it is too complex) at almost no cost. Needs ANTHROPIC_API_KEY (.env).
"""

import os

import anthropic
import pytest

from turnpilot import drawing_reader
from turnpilot.dimensions_first import RECORD_DIMENSIONS_TOOL
from turnpilot.extraction_schema import RECORD_PART_TOOL

pytestmark = pytest.mark.live


@pytest.fixture(scope="module")
def api():
    client = drawing_reader.make_client()  # loads .env
    if not (client.api_key or client.auth_token):
        pytest.skip("no ANTHROPIC_API_KEY")
    return client


@pytest.mark.parametrize("tool", [RECORD_PART_TOOL, RECORD_DIMENSIONS_TOOL], ids=lambda t: t["name"])
def test_strict_schema_is_accepted_by_the_api(api, tool):
    model = os.environ.get("ANTHROPIC_MODEL") or drawing_reader.DEFAULT_MODEL
    try:
        response = api.messages.create(
            model=model,
            max_tokens=16,
            tools=[tool],
            tool_choice={"type": "auto"},
            messages=[{"role": "user", "content": "Reply with the word ok."}],
        )
    except anthropic.BadRequestError as exc:
        pytest.fail(f"{tool['name']} rejected by the API: {exc}")
    assert response.stop_reason in ("end_turn", "max_tokens", "tool_use")
