"""Feature type "arc": a formed section of the outer profile (from the DXF reader), planned as a manual operation."""
import pytest
from pydantic import ValidationError

from turnpilot import drawing_reader
from turnpilot.extraction_schema import RECORD_PART_TOOL, TOOL_FEATURE_TYPES, ExtractedFeature
from turnpilot.models import Feature, Job, Material, Operation, db
from turnpilot.planner import MANUAL_OPERATION_WARNING, FeatureSpec, geometry_warnings, suggest_blank


def test_arc_is_not_offered_to_the_model():
    assert "arc" not in TOOL_FEATURE_TYPES
    enum = RECORD_PART_TOOL["input_schema"]["properties"]["features"]["items"]["properties"]["type"]["enum"]
    assert "arc" not in enum
    # the prompt versions (prompt + tool schema) are the ones measured before the arc type existed
    assert drawing_reader.prompt_version("features") == "ae6963808bf03c12"
    assert drawing_reader.prompt_version("dimensions_first") == "e91adddb38cc3854"


def test_arc_fields():
    arc = ExtractedFeature(type="arc", diameter=30, start_diameter=38.5, radius=12.5, length=15)
    assert (arc.diameter, arc.start_diameter, arc.radius, arc.length) == (30, 38.5, 12.5, 15)
    with pytest.raises(ValidationError, match="radius is only valid for fillets and arcs"):
        ExtractedFeature(type="od_turn", diameter=30, radius=5)


def test_arc_blank_and_geometry():
    features = [FeatureSpec(1, "od_turn", diameter=30, length=40),
                FeatureSpec(2, "arc", diameter=20, start_diameter=36, radius=12, length=10)]
    blank = suggest_blank(features, 50, (32, 36, 40), 2.0, 2.0, 3.0)
    assert blank.diameter == 40  # the arc's start Ø36 + 2
    assert geometry_warnings(features, 50) == []  # 40 + 10: the arc counts along the axis


def test_arc_through_the_app(client):
    material = db.session.execute(db.select(Material).filter_by(iso_group="P")).scalar_one()
    client.post("/jobs", data=dict(name="Pin", material_id=material.id, quantity=1, blank_diameter=40, blank_length=80))
    job = db.session.execute(db.select(Job)).scalar_one()
    client.post(f"/jobs/{job.id}/features", data=dict(type="od_turn", diameter=36, length=50))
    client.post(f"/jobs/{job.id}/features",
                data=dict(type="arc", diameter=20, start_diameter=36, radius=12, length=10))
    arc = db.session.execute(db.select(Feature).filter_by(type="arc")).scalar_one()
    assert (arc.diameter, arc.start_diameter, arc.radius, arc.length) == (20, 36, 12, 10)
    client.post(f"/jobs/{job.id}/calculate")
    op = db.session.execute(db.select(Operation).filter_by(feature_id=arc.id)).scalar_one()
    assert op.tool_type == "manual" and MANUAL_OPERATION_WARNING in op.warning
