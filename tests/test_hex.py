"""Hex sections: sizes across flats / corners, planning (turn Ø across corners, mill the flats), review."""
import io

import pytest
from conftest import FIXTURES, FakeClient, feature, make_response, part
from test_planner import turret  # noqa: F401  (fixture)

from turnpilot import planner
from turnpilot.drawing_reader import prompt_version
from turnpilot.extraction_schema import (
    RECORD_PART_TOOL,
    DrawingData,
    ExtractedFeature,
    hex_across_corners,
    hex_across_flats,
)
from turnpilot.models import Job, Tool, TurretSlot, db
from turnpilot.planner import FeatureSpec, JobSpec


def test_hex_sizes():
    assert hex_across_corners(11) == 12.7
    assert hex_across_flats(13) == 11.26
    assert hex_across_corners(17) == 19.63


def test_hex_with_one_size_gets_the_other_marked_derived():
    from_corners = ExtractedFeature(type="hex", diameter=13, length=3.5)
    assert (from_corners.across_flats, from_corners.size_derived) == (11.26, "across_flats")
    from_flats = ExtractedFeature(type="hex", across_flats=17, length=10)
    assert (from_flats.diameter, from_flats.size_derived) == (19.63, "diameter")
    both = ExtractedFeature(type="hex", diameter=19.6, across_flats=17)
    assert both.size_derived is None and both.diameter == 19.6


def test_across_flats_only_on_a_hex():
    with pytest.raises(ValueError):
        ExtractedFeature(type="od_turn", diameter=13, across_flats=11)


def test_non_standard_wrench_size_is_reported_not_changed():
    data = DrawingData(part_type="turned", features=[{"type": "hex", "diameter": 13, "length": 3.5}])
    assert data.features[0].across_flats == 11.26
    assert data.warnings == [
        "hex S11.26 (computed from the diameter across corners) is not a standard wrench size (nearest S11): check"
    ]
    standard = DrawingData(part_type="turned", features=[{"type": "hex", "across_flats": 17, "length": 10}])
    assert standard.warnings == []


def test_hex_is_not_offered_to_the_model_yet():
    # the prompt step adds it together with its rule; until then the reading is unchanged
    enum = RECORD_PART_TOOL["input_schema"]["properties"]["features"]["items"]["properties"]["type"]["enum"]
    assert "hex" not in enum
    assert "across_flats" not in RECORD_PART_TOOL["input_schema"]["properties"]["features"]["items"]["properties"]
    assert prompt_version("features") == "b423ae0e1ab7fb64"


# --- planner ---------------------------------------------------------------------------------------------


def _fitting():
    """The fitting from real_03 with the hex S11 (across corners 12.7) in the middle."""
    return (
        FeatureSpec(1, "od_turn", diameter=10, length=3.5),
        FeatureSpec(2, "groove", diameter=8, length=8, start_diameter=10),
        FeatureSpec(3, "hex", across_flats=11, length=3.5),
        FeatureSpec(4, "chamfer", diameter=12.7, length=1, location="external"),
        FeatureSpec(5, "groove", diameter=8, length=1.5, start_diameter=10),
        FeatureSpec(6, "od_turn", diameter=10, length=8),
        FeatureSpec(7, "thread", diameter=10, length=8, pitch=1.5),
    )


def _ops(turret, features=None):
    return planner.plan_job(JobSpec("N", 16, 30, features or _fitting()), turret, max_rpm=4000)


def test_hex_turned_to_corners_then_milled_manually_without_driven_tool(turret):
    ops = [op for op in _ops(turret) if op.feature_id == 3]
    assert [(op.tool_type, op.mode) for op in ops] == [
        ("turning_rough", "rough"), ("turning_finish", "finish"), ("milling", "finish"),
    ]
    finish, mill = ops[1], ops[2]
    assert finish.ref_diameter == 12.7
    assert f"{planner.HEX_CORNERS_NOTE} S11" in finish.notes
    assert planner.CHAMFER_NOTE in finish.notes  # the chamfer on the corners goes with the finish pass
    assert mill.tool_id is None and planner.NO_MILLING_TOOL in mill.warnings
    assert "mill hex S11 across flats, L3.5" in mill.notes
    assert mill.depth == 0.85  # (12.7 - 11) / 2


def test_hex_milling_comes_after_threading_and_before_parting(turret):
    ops = _ops(turret, _fitting() + (FeatureSpec(8, "parting"),))
    order = [op.tool_type for op in ops]
    assert order.index("threading") < order.index("milling") < order.index("parting")


def test_hex_milled_with_a_driven_tool_from_the_turret(turret):
    mill_tool = planner.ToolSpec(99, "End mill D8", "milling", "PMN", "", 60, 120, 0.02, 0.05, 0.5, 2)
    ops = _ops(turret + [planner.TurretEntry(12, mill_tool)])
    mill = next(op for op in ops if op.tool_type == "milling")
    assert (mill.tool_id, mill.turret_position) == (99, 12)
    assert planner.MILLING_DATA_NOTE in mill.notes and not mill.warnings


def test_hex_counts_in_the_section_sum():
    features = (
        FeatureSpec(1, "od_turn", diameter=10, length=3.5), FeatureSpec(2, "hex", diameter=13, length=3.5),
    )
    assert planner.geometry_warnings(features, 7) == []
    assert planner.geometry_warnings(features, 10) == ["section lengths sum to 7, overall length is 10"]


def test_blank_is_sized_from_the_hex_corners():
    suggestion = planner.suggest_blank(
        list(_fitting()), overall_length=24.5, bar_diameters=(12, 14, 16), diameter_allowance=2,
        facing_allowance=2, parting_width=3,
    )
    assert suggestion.diameter == 16  # 12.7 + 2 -> 16


# --- through the app ---------------------------------------------------------------------------------------


def test_review_shows_hex_sizes_and_confirm_keeps_them(app, client):
    app.config["ANTHROPIC_CLIENT"] = FakeClient(make_response(part([
        feature("od_turn", 10, length=3.5), feature("hex", 13, length=3.5),
    ], overall_length=7)))
    png = (FIXTURES / "01_stepped_shaft.png").read_bytes()
    client.post("/jobs/upload", data={"drawing": (io.BytesIO(png), "fitting.png")}, content_type="multipart/form-data")
    page = client.get("/extractions/1/review").data.decode()
    assert 'name="f1-across_flats" type="number" step="any" min="0" value="11.26"' in page
    assert "computed from the diameter across corners" in page
    form = {"name": "Fitting", "material_id": "1", "quantity": "1", "blank_diameter": "16", "blank_length": "12",
            "feature_count": "2", "f0-include": "1", "f0-type": "od_turn", "f0-diameter": "10", "f0-length": "3.5",
            "f1-include": "1", "f1-type": "hex", "f1-diameter": "", "f1-across_flats": "11", "f1-length": "3.5"}
    client.post("/extractions/1/confirm", data=form)
    job = db.session.execute(db.select(Job)).scalar_one()
    hex_ = next(f for f in job.features if f.type == "hex")
    assert (hex_.diameter, hex_.across_flats) == (12.7, 11)
    page = client.post(f"/jobs/{job.id}/calculate", follow_redirects=True).data.decode()
    assert "mill hex S11 across flats, L3.5" in page and planner.NO_MILLING_TOOL in page


def test_hex_needs_one_of_its_sizes(app, client):
    job_id = _job(client)
    client.post(f"/jobs/{job_id}/features", data={"type": "hex", "length": "3.5"})
    assert not db.session.get(Job, job_id).active_features
    client.post(f"/jobs/{job_id}/features", data={"type": "hex", "diameter": "13", "length": "3.5"})
    hex_ = db.session.get(Job, job_id).active_features[0]
    assert (hex_.diameter, hex_.across_flats) == (13, 11.26)


def test_driven_tool_can_be_added_and_used(app, client):
    client.post("/tools", data=dict(name="End mill D8", type="milling", iso_group=["P", "M", "N"], vc_min=60,
                                    vc_max=120, f_min=0.02, f_max=0.05, ap_min=0.5, ap_max=2))
    tool = db.session.execute(db.select(Tool).filter_by(type="milling")).scalar_one()
    slot = db.session.execute(db.select(TurretSlot).filter_by(tool_id=None)).scalars().first()
    slot.tool_id = tool.id
    db.session.commit()
    job_id = _job(client)
    client.post(f"/jobs/{job_id}/features", data={"type": "hex", "across_flats": "11", "length": "3.5"})
    page = client.post(f"/jobs/{job_id}/calculate", follow_redirects=True).data.decode()
    assert "End mill D8" in page and planner.MILLING_DATA_NOTE in page


def _job(client):
    client.post("/jobs", data={"name": "Fitting", "material_id": "1", "blank_diameter": "16", "blank_length": "30"})
    return db.session.execute(db.select(Job)).scalar_one().id
