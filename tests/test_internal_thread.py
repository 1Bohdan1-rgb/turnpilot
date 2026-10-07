"""Internal threads: location from the drawing / class letter, tap drill, tap or internal threading bar."""

import io

import pytest
from conftest import FIXTURES, FakeClient, feature, make_response, part

from turnpilot import planner, services
from turnpilot.drawing_reader import SYSTEM_PROMPT, parse_response
from turnpilot.models import Job, Tool, db
from turnpilot.planner import FeatureSpec, JobSpec


def thread(diameter, tolerance, location=None, pitch=2, length=28):
    f = feature("thread", diameter, length=length, pitch=pitch, tolerance=tolerance)
    if location:
        f["location"] = location
    return f


# --- class letter and location -----------------------------------------------------------------------

@pytest.mark.parametrize("tolerance, location", [("7H", "internal"), ("6H", "internal"), ("5H6H", "internal"),
                                                 ("6g", "external"), ("4h6h", "external")])
def test_location_from_class_when_not_given(tolerance, location):
    data = parse_response(make_response(part([thread(14, tolerance)])))
    assert data.features[0].location == location
    assert not any("thread M14: class" in w for w in data.warnings)


def test_mismatch_between_class_and_location_is_reported():
    data = parse_response(make_response(part([thread(14, "7H", location="external")])))
    assert data.features[0].location == "external"  # kept as read
    assert "thread M14: class 7H means an internal thread, but it was read as external: check" in data.warnings


def test_matching_location_gives_no_warning():
    data = parse_response(make_response(part([thread(14, "7H", location="internal")])))
    assert not any("thread M14: class" in w for w in data.warnings)


def test_prompt_mentions_internal_threads():
    assert "A thread in a hole (internal" in SYSTEM_PROMPT.replace("\n", " ")


# --- planning ---------------------------------------------------------------------------------------------

@pytest.mark.parametrize("nominal, pitch, drill", [(14, 2, 12), (20, 2.5, 17.5), (8, 1.25, 6.8), (20, 1.5, 18.5)])
def test_tap_drill_diameter(nominal, pitch, drill):
    assert planner.tap_drill_diameter(nominal, pitch) == drill


def _job(location="internal"):
    return JobSpec("P", 29, 50, (
        FeatureSpec(1, "od_turn", diameter=25, length=30),
        FeatureSpec(2, "bore", diameter=16, length=16),
        FeatureSpec(3, "thread", diameter=14, length=28, pitch=2, tolerance="7H", location=location),
    ))


def test_internal_thread_without_tools_becomes_manual_operations(app):
    turret = services.turret_entries(services.get_machine())  # the seed turret has no drill, tap or bar
    ops = [op for op in planner.plan_job(_job(), turret, 4000) if op.feature_id == 3]
    drill, tap = ops
    assert drill.tool_type == "drilling" and drill.ref_diameter == 12 and drill.depth == 28
    # the seed turret has 860-GM drills Ø6 / Ø8 / Ø10 but no Ø12 tap drill
    assert "tap drill Ø12 for M14×2" in drill.notes
    assert drill.warnings == ["no Ø12 drill in the turret (tap drill for M14×2)"]
    assert tap.f == 2 and planner.NO_INTERNAL_THREAD_TOOL in tap.warnings
    assert all(op.tool_type != "threading" for op in ops)  # never the external threading tool
    assert [op.sequence for op in ops] == sorted(op.sequence for op in ops)  # drill before the thread


def _tool(tool_type, **ranges):
    data = dict(vc_min=10, vc_max=30, f_min=0.1, f_max=0.2, ap_min=0.05, ap_max=0.15)
    data.update(ranges)
    return planner.ToolSpec(id=90, name=tool_type, type=tool_type, iso_group="PMN", insert_code="", **data)


def test_internal_thread_with_drill_and_tap(app):
    turret = services.turret_entries(services.get_machine()) + [
        # a drill is chosen by its diameter: the Ø12 tap drill for M14×2
        planner.TurretEntry(10, _tool("drilling", vc_min=20, vc_max=40, f_min=0.1, f_max=0.2, diameter=12)),
        planner.TurretEntry(11, _tool("tapping")),
    ]
    drill, tap = [op for op in planner.plan_job(_job(), turret, 4000) if op.feature_id == 3]
    assert drill.turret_position == 10 and drill.n and not drill.warnings
    assert tap.tool_type == "tapping" and tap.turret_position == 11 and tap.f == 2
    assert planner.TAPPING_NOTE in tap.notes


def test_internal_threading_bar_when_there_is_no_tap(app):
    turret = services.turret_entries(services.get_machine()) + [planner.TurretEntry(12, _tool("threading_internal"))]
    tap = [op for op in planner.plan_job(_job(), turret, 4000) if op.feature_id == 3][1]
    assert tap.tool_type == "threading_internal"
    assert tap.depth == round(0.541 * 2, 3) and tap.passes >= 2
    assert planner.G97_THREAD_NOTE in tap.notes


def test_external_thread_is_planned_as_before(app):
    turret = services.turret_entries(services.get_machine())
    ops = [op for op in planner.plan_job(_job("external"), turret, 4000) if op.feature_id == 3]
    assert [op.tool_type for op in ops] == ["threading"]


def test_internal_thread_does_not_shrink_an_od_of_the_same_size():
    features = [FeatureSpec(1, "od_turn", diameter=14, length=10),
                FeatureSpec(2, "thread", diameter=14, length=10, pitch=2, location="internal")]
    assert planner.match_thread_diameters(features) == {}


# --- through the app ------------------------------------------------------------------------------------

def test_review_and_job_keep_the_internal_thread(app, client):
    app.config["ANTHROPIC_CLIENT"] = FakeClient(make_response(part([
        feature("od_turn", 25, length=30), thread(14, "7H", location="internal"),
    ], overall_length=30)))
    png = (FIXTURES / "01_stepped_shaft.png").read_bytes()
    client.post("/jobs/upload", data={"drawing": (io.BytesIO(png), "vtulka.png")}, content_type="multipart/form-data")
    page = client.get("/extractions/1/review").data.decode()
    assert "<option selected>internal</option>" in page
    form = {"name": "Vtulka", "material_id": "1", "quantity": "1", "blank_diameter": "29", "blank_length": "36",
            "feature_count": "2", "f0-include": "1", "f0-type": "od_turn", "f0-diameter": "25", "f0-length": "30",
            "f1-include": "1", "f1-type": "thread", "f1-diameter": "14", "f1-length": "28", "f1-pitch": "2",
            "f1-tolerance": "7H", "f1-position": "internal"}
    client.post("/extractions/1/confirm", data=form)
    job = db.session.execute(db.select(Job)).scalar_one()
    stored = next(f for f in job.features if f.type == "thread")
    assert (stored.location, stored.face) == ("internal", None)
    page = client.post(f"/jobs/{job.id}/calculate", follow_redirects=True).data.decode()
    assert "tap drill Ø12 for M14×2" in page and planner.NO_INTERNAL_THREAD_TOOL in page


def test_new_tool_types_can_be_added(app, client):
    client.post("/tools", data=dict(name="Tap M14", type="tapping", iso_group=["P", "M", "N"], vc_min=5, vc_max=15,
                                    f_min=2, f_max=2, ap_min=0.1, ap_max=0.1))
    assert db.session.execute(db.select(Tool).filter_by(type="tapping")).scalar_one().name == "Tap M14"
