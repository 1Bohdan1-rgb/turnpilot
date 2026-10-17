"""The cycle time estimate from a program's text: cutting feeds (G96 with the diameter), rapids per axis, G92 passes,
dwells and tool changes; the machine's rapid traverse and tool change time from the passport or the operator."""
import json

from turnpilot import passport_reader


def test_the_rapid_and_tool_change_fields_are_read_from_the_passport(app, client):
    schema = json.dumps(passport_reader.RECORD_MACHINE_TOOL)
    assert all(name in schema for name in ("rapid_x_m_min", "rapid_z_m_min", "tool_change_s"))
    page = client.get("/machine").get_data(as_text=True)
    assert "Rapid traverse X" in page and "Rapid traverse Z" in page and "Tool change time" in page
