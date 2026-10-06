"""Holes: centre drilling, a drill chosen by its diameter, peck drilling, boring only after drilling."""
from turnpilot import services
from turnpilot.models import TOOL_TYPES, Tool, db

DRILL_FORM = dict(name="Drill 20", type="drilling", iso_group=["P", "M", "N"], insert_code="HSS 20",
                  vc_min="20", vc_max="40", f_min="0.1", f_max="0.3", ap_min="10", ap_max="10", diameter="20")


def test_centre_drilling_is_a_tool_type():
    assert "centre_drilling" in TOOL_TYPES


def test_tool_form_saves_a_drill_diameter(client):
    client.post("/tools", data=DRILL_FORM)
    drill = db.session.execute(db.select(Tool).filter_by(name="Drill 20")).scalar_one()
    assert drill.diameter == 20
    page = client.get("/machine").get_data(as_text=True)
    assert 'name="diameter"' in page and "<td>20.0</td>" in page
    assert services.tool_spec(drill).diameter == 20


def test_tool_form_without_a_diameter(client):
    client.post("/tools", data={**DRILL_FORM, "name": "Boring bar 2", "type": "boring", "diameter": ""})
    assert db.session.execute(db.select(Tool).filter_by(name="Boring bar 2")).scalar_one().diameter is None
