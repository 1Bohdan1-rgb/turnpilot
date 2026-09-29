"""Hex sections: sizes across flats / corners, planning (turn Ø across corners, mill the flats), review."""
import io
import json

import pytest
from conftest import FIXTURES, FakeClient, feature, make_response, part
from test_planner import turret  # noqa: F401  (fixture)

from turnpilot import planner
from turnpilot.drawing_reader import SYSTEM_PROMPT, parse_response, prompt_version
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


def test_hex_is_offered_to_the_model():
    item = RECORD_PART_TOOL["input_schema"]["properties"]["features"]["items"]
    assert "hex" in item["properties"]["type"]["enum"]
    # a plain number with 0 as "none": the strict schema keeps its 13 nullable parameters (limit 16)
    assert item["properties"]["across_flats"]["type"] == "number"
    assert "across_flats" in item["required"]
    assert json.dumps(RECORD_PART_TOOL).count('"anyOf"') == 13
    rule = next(r for r in SYSTEM_PROMPT.split("\n- ") if r.startswith("A hexagon"))
    assert "one hex feature" in rule and "across_flats" in rule
    assert "chamfer" not in rule  # a chamfer is recorded as drawn; the code binds it to the hex
    assert prompt_version("features") != "b423ae0e1ab7fb64"


def test_dimensions_first_does_not_know_hex_yet():
    from turnpilot.dimensions_first import RECORD_DIMENSIONS_TOOL
    assert "hex" not in json.dumps(RECORD_DIMENSIONS_TOOL)
    assert prompt_version("dimensions_first") == "e91adddb38cc3854"


# --- the 0 marker and chamfers on a hex -------------------------------------------------------------------


def test_zero_across_flats_becomes_none():
    assert ExtractedFeature(type="od_turn", diameter=12, across_flats=0).across_flats is None
    hex_ = ExtractedFeature(type="hex", diameter=13, across_flats=0)
    assert (hex_.across_flats, hex_.size_derived) == (11.26, "across_flats")


def test_zero_marker_from_the_model_never_reaches_the_planner():
    features = [dict(feature("od_turn", 12, length=25), across_flats=0),
                dict(feature("hex", None, length=10), across_flats=17)]
    data = parse_response(make_response(part(features, overall_length=35)))
    assert data.features[0].across_flats is None
    assert (data.features[1].diameter, data.features[1].across_flats) == (19.63, 17)
    assert planner.geometry_warnings(data.features, 35) == []


@pytest.mark.parametrize("chamfer_d, bound", [(17, 19.63), (19.63, 19.63), (12, 12)])
def test_chamfer_on_a_hex_goes_to_its_corners(chamfer_d, bound):
    data = DrawingData(part_type="turned", features=[
        {"type": "hex", "across_flats": 17, "length": 10},
        {"type": "od_turn", "diameter": 12, "length": 25},
        {"type": "chamfer", "diameter": chamfer_d, "length": 1, "location": "external", "face": "left"},
    ])
    assert data.features[2].diameter == bound


def test_internal_chamfer_is_not_moved_to_a_hex():
    data = DrawingData(part_type="turned", features=[
        {"type": "hex", "across_flats": 17, "length": 10},
        {"type": "chamfer", "diameter": 17, "length": 1, "location": "internal", "face": "left"},
    ])
    assert data.features[1].diameter == 17


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


# --- hex bar stock -------------------------------------------------------------------------------------------

from turnpilot import services  # noqa: E402
from turnpilot.models import Machine  # noqa: E402


def _suggest(features, sizes=(8, 10, 11, 12, 13, 14, 17)):
    return planner.suggest_blank(
        list(features), overall_length=24.5, bar_diameters=(12, 14, 16, 18, 20), diameter_allowance=2,
        facing_allowance=2, parting_width=3, hex_bar_sizes=sizes,
    )


def test_hex_bar_suggested_when_the_hex_is_largest_and_in_stock():
    suggestion = _suggest(_fitting())
    assert (suggestion.shape, suggestion.diameter) == ("hex", 11)
    assert "Hex bar S11: the flats are not machined." in suggestion.notes


def test_round_bar_when_no_hex_bar_of_that_size():
    features = (FeatureSpec(1, "od_turn", diameter=10, length=5), FeatureSpec(2, "hex", diameter=13, length=3.5))
    suggestion = _suggest(features)  # S11.26 is not a bar size
    assert (suggestion.shape, suggestion.diameter) == ("round", 16)
    assert "No hex bar S11.26 in stock: round bar, the hex is milled." in suggestion.notes


def test_round_bar_when_a_diameter_is_larger_than_the_hex():
    features = (FeatureSpec(1, "od_turn", diameter=20, length=5), FeatureSpec(2, "hex", across_flats=11, length=3))
    suggestion = _suggest(features)
    assert suggestion.shape == "round"
    assert "Ø20 is larger than the hex size across flats S11: round bar, the hex is milled." in suggestion.notes


def test_hex_from_hex_bar_is_not_machined(turret):
    job = JobSpec("P", 11, 30, _fitting(), blank_shape="hex")
    ops = planner.plan_job(job, turret, max_rpm=4000)
    hex_ops = [op for op in ops if op.feature_id == 3]
    assert [op.tool_type for op in hex_ops] == ["hex_bar"]
    assert hex_ops[0].notes == [f"hex S11: {planner.HEX_FROM_BAR_NOTE}"] and not hex_ops[0].warnings
    # the chamfer on the corners has no finish pass to go with: it gets its own
    assert any(op.feature_id == 4 for op in ops)
    # the other sections are turned from the hex bar's diameter across corners
    rough = next(op for op in ops if op.feature_id == 1 and op.mode == "rough")
    assert rough.ref_diameter == 12.7


def test_parse_sizes():
    assert services.parse_sizes("8, 10;11  12") == (8, 10, 11, 12)
    assert services.parse_sizes("") == ()
    with pytest.raises(ValueError):
        services.parse_sizes("8, ten")


def test_machine_page_edits_hex_bar_sizes(app, client):
    page = client.get("/machine").data.decode()
    assert 'value="8, 10, 11, 12, 13, 14, 17, 19, 22, 24, 27, 30, 32, 36, 41"' in page
    client.post("/machine", data={"action": "stock", "hex_bar_sizes": "13, 11, 17"})
    machine = db.session.execute(db.select(Machine)).scalars().first()
    assert machine.hex_bar_sizes == "11, 13, 17"
    assert services.hex_bar_sizes(machine, app.config) == (11, 13, 17)
    client.post("/machine", data={"action": "stock", "hex_bar_sizes": "11, x"})
    assert machine.hex_bar_sizes == "11, 13, 17"


def test_job_with_a_hex_bar_blank(app, client):
    client.post("/jobs", data={"name": "Fitting", "material_id": "1", "blank_shape": "hex", "blank_diameter": "11",
                               "blank_length": "30"})
    job = db.session.execute(db.select(Job)).scalar_one()
    assert (job.blank_shape, job.blank_label) == ("hex", "hex S11")
    # a feature up to the hex bar's corners (12.7) fits the blank
    client.post(f"/jobs/{job.id}/features", data={"type": "hex", "across_flats": "11", "length": "3.5"})
    assert len(job.active_features) == 1
    page = client.post(f"/jobs/{job.id}/calculate", follow_redirects=True).data.decode()
    assert planner.HEX_FROM_BAR_NOTE in page


def test_review_suggests_the_hex_bar(app, client):
    app.config["ANTHROPIC_CLIENT"] = FakeClient(make_response(part([
        feature("od_turn", 10, length=3.5), {**feature("hex", 12.7, length=3.5)},
    ], overall_length=7)))
    png = (FIXTURES / "01_stepped_shaft.png").read_bytes()
    client.post("/jobs/upload", data={"drawing": (io.BytesIO(png), "fitting.png")}, content_type="multipart/form-data")
    page = client.get("/extractions/1/review").data.decode()
    assert "<option selected>hex</option>" in page
    assert "Hex bar S11: the flats are not machined." in page


# --- a hex with both D and S ----------------------------------------------------------------------------------


@pytest.mark.parametrize("diameter, across_flats, expected", [
    (12.7, 11, []),  # D = S / cos 30°
    (12.2, 11, []),  # corners slightly rounded: 1.109 x S
    (13, 11, []),  # turned larger before milling: the milling takes the rest
    (12, 11, ["hex Ø12 is well below S11 / cos 30° = Ø12.7: corners cut off, check"]),  # 1.09 x S
    (10.5, 11, ["hex Ø10.5 is smaller than its size across flats S11: check"]),
])
def test_hex_size_warnings(diameter, across_flats, expected):
    assert planner.hex_size_warnings(diameter, across_flats) == expected
    feature_ = FeatureSpec(1, "hex", diameter=diameter, across_flats=across_flats, length=3)
    assert planner.geometry_warnings([feature_], None) == expected


def test_hex_with_both_sizes_is_kept_as_written():
    data = DrawingData(part_type="turned", features=[{"type": "hex", "diameter": 12.2, "across_flats": 11}])
    hex_ = data.features[0]
    assert (hex_.diameter, hex_.across_flats, hex_.size_derived) == (12.2, 11, None)


def test_hex_milling_depth_from_the_turned_diameter(turret):
    ops = _ops(turret, (FeatureSpec(1, "hex", diameter=13, across_flats=11, length=3.5),))
    finish = next(op for op in ops if op.mode == "finish" and op.tool_type == "turning_finish")
    mill = next(op for op in ops if op.tool_type == "milling")
    assert finish.ref_diameter == 13 and mill.depth == 1.0


def test_form_accepts_d_below_s_over_cos30_but_not_below_s(app, client):
    job_id = _job(client)
    client.post(f"/jobs/{job_id}/features", data={"type": "hex", "diameter": "10.5", "across_flats": "11"})
    assert not db.session.get(Job, job_id).active_features
    client.post(f"/jobs/{job_id}/features", data={"type": "hex", "diameter": "12.2", "across_flats": "11"})
    hex_ = db.session.get(Job, job_id).active_features[0]
    assert (hex_.diameter, hex_.across_flats) == (12.2, 11)


def test_review_warns_about_cut_corners(app, client):
    app.config["ANTHROPIC_CLIENT"] = FakeClient(make_response(part([feature("hex", 12, length=3.5)])))
    png = (FIXTURES / "01_stepped_shaft.png").read_bytes()
    client.post("/jobs/upload", data={"drawing": (io.BytesIO(png), "fitting.png")}, content_type="multipart/form-data")
    extraction_id = 1
    # the model cannot send S yet: the reviewer types it in, the check runs on the submitted rows
    form = {"name": "Fitting", "material_id": "1", "quantity": "1", "blank_diameter": "", "blank_length": "12",
            "feature_count": "1", "f0-include": "1", "f0-type": "hex", "f0-diameter": "12", "f0-across_flats": "11",
            "f0-length": "3.5"}
    page = client.post(f"/extractions/{extraction_id}/confirm", data=form).data.decode()
    assert "hex Ø12 is well below S11 / cos 30° = Ø12.7: corners cut off, check" in page


# --- hex bar: other diameters inside S, tolerance on S -------------------------------------------------------


def test_hex_bar_needs_every_other_diameter_within_s():
    # Ø12 fits under the corners (12.7) but not inside the flats (11): a hex bar would leave it unround
    features = (FeatureSpec(1, "od_turn", diameter=12, length=5), FeatureSpec(2, "hex", across_flats=11, length=3))
    suggestion = _suggest(features)
    assert suggestion.shape == "round"
    assert "Ø12 is larger than the hex size across flats S11: round bar, the hex is milled." in suggestion.notes
    inside = (FeatureSpec(1, "od_turn", diameter=11, length=5), FeatureSpec(2, "hex", across_flats=11, length=3))
    assert _suggest(inside).shape == "hex"


@pytest.mark.parametrize("tolerance, size, tight", [
    ("h9", 11, True), ("h10", 11, True), ("h11", 11, False), ("h12", 11, False), (None, 11, False),
    ("±0.02", 11, True), ("0/-0.11", 11, False), ("0/-0.2", 11, False),
])
def test_tighter_than_h11(tolerance, size, tight):
    assert planner.tighter_than_h11(tolerance, size) is tight


def test_hex_bar_with_a_tight_s_tolerance_gets_its_flats_milled(turret):
    features = (FeatureSpec(1, "od_turn", diameter=10, length=5),
                FeatureSpec(2, "hex", across_flats=11, length=3.5, tolerance="h9"),
                FeatureSpec(3, "chamfer", diameter=12.7, length=1, location="external"))
    suggestion = _suggest(features)
    assert (suggestion.shape, suggestion.diameter) == ("hex", 11)
    assert "Hex bar S11: tolerance h9 is tighter than the bar's h11, the flats have to be milled." in suggestion.notes

    ops = planner.plan_job(JobSpec("P", 11, 30, features, blank_shape="hex"), turret, max_rpm=4000)
    hex_ops = [op for op in ops if op.feature_id == 2]
    assert [op.tool_type for op in hex_ops] == ["milling"]  # no turning: the bar already has the flats
    assert hex_ops[0].depth is None
    assert "flats of the hex bar (h11) milled to h9: skim, depth set by the bar" in hex_ops[0].notes
    assert any(op.feature_id == 3 for op in ops)  # the chamfer gets its own pass
