"""The catalogue's recommended values on a tool: f rec, ap rec and Vc(f) points, used instead of range positions."""
import pytest

from turnpilot import planner
from turnpilot.models import Tool, db
from turnpilot.planner import (
    FeatureSpec,
    JobSpec,
    ToolSpec,
    TurretEntry,
    cutting_data,
    finishing_ap,
    format_vc_points,
    parse_vc_points,
    plan_job,
    roughing_ap_limit,
    vc_at,
)
from turnpilot.seed import TURRET_TOOLS

T2_POINTS = ((0.1, 455.0), (0.4, 305.0), (0.8, 215.0))  # TT A279, GC4325


def _tool(tool_type="turning_rough", **catalogue):
    data = dict(id=90, name=tool_type, type=tool_type, iso_group="P", insert_code="CNMG120408-PM",
                vc_min=215, vc_max=455, f_min=0.15, f_max=0.5, ap_min=0.5, ap_max=5.5)
    data.update(catalogue)
    return ToolSpec(**data)


# --- Vc(f) points ---------------------------------------------------------------------------------

def test_parse_and_format_vc_points():
    assert parse_vc_points("0.4:305, 0.1:455; 0.8:215") == T2_POINTS
    assert parse_vc_points("125") == ((0.0, 125.0),)
    assert parse_vc_points("") == () and parse_vc_points(None) == ()
    assert format_vc_points(T2_POINTS) == "0.1:455, 0.4:305, 0.8:215" and format_vc_points(((0.0, 125.0),)) == "125"
    for bad in ("0.1-455", "0.1:-5", "0.1:455, 0.1:300", "fast"):
        with pytest.raises(ValueError):
            parse_vc_points(bad)


@pytest.mark.parametrize("f, vc", [(0.1, 455), (0.4, 305), (0.8, 215), (0.3, 355), (0.6, 260), (0.05, 455), (1.0, 215)])
def test_vc_at_is_linear_between_points_and_flat_outside(f, vc):
    assert vc_at(T2_POINTS, f) == pytest.approx(vc)


def test_one_vc_for_every_feed():
    assert vc_at(((0.0, 125.0),), 0.22) == 125


# --- cutting data ---------------------------------------------------------------------------------

def test_rough_takes_the_recommended_values():
    tool = _tool(ap_rec=3, f_rec=0.3, vc_points=T2_POINTS)
    assert cutting_data(tool, "rough") == (355.0, 0.3, 3.0)  # Vc(0.3) between 455 at 0.1 and 305 at 0.4
    assert roughing_ap_limit(tool) == 3


def test_finish_takes_ap_rec_and_f_from_ra_with_vc_at_that_f():
    tool = _tool("turning_finish", insert_code="DNMG150604-PF", f_min=0.07, f_max=0.3, ap_min=0.25, ap_max=1.5,
                 ap_rec=0.4, f_rec=0.15, vc_points=((0.1, 510.0), (0.4, 365.0), (0.8, 265.0)))
    vc, f, ap = cutting_data(tool, "finish", ra=1.6)
    assert ap == 0.4 and finishing_ap(tool) == 0.4
    assert f == planner.finish_feed_from_ra(1.6, 0.4, 0.07, 0.3)
    assert vc == pytest.approx(vc_at(tool.vc_points, f), abs=0.05)
    assert cutting_data(tool, "finish")[1] == 0.15  # no Ra: f rec


def test_without_catalogue_values_as_before():
    tool = _tool()
    assert cutting_data(tool, "rough") == (275.0, 0.412, 4.25)  # 25% / 75% of the ranges
    assert finishing_ap(tool) == 0.5 and roughing_ap_limit(tool) == 5.5


def test_f_rec_without_points_keeps_the_vc_range_position():
    vc, f, _ = cutting_data(_tool(f_rec=0.3, ap_rec=3), "rough")
    assert (vc, f) == (275.0, 0.3)


# --- planner --------------------------------------------------------------------------------------

def _turret(**t2_catalogue):
    seed = [TurretEntry(pos, ToolSpec(id=pos, **{k: v for k, v in tool.items() if k != "grade"}))
            for pos, tool in TURRET_TOOLS.items()]
    return [TurretEntry(2, _tool(id=2, **t2_catalogue)) if e.position == 2 else e for e in seed]


def test_rough_passes_by_ap_rec_with_a_note():
    turret = _turret(ap_rec=3, f_rec=0.3, vc_points=T2_POINTS, source="TT A283, A279")
    ops = plan_job(JobSpec("P", 55, 100, (FeatureSpec(1, "od_turn", diameter=40, length=30),)), turret, 4000)
    rough = next(op for op in ops if op.tool_type == "turning_rough")
    # (55 − 40) / 2 − 0.2 (finishing allowance of the seed T4) = 7.3 by ap rec 3: 3 passes
    assert (rough.passes, rough.ap, rough.f, rough.vc) == (3, 2.433, 0.3, 355.0)
    assert "catalogue values: f rec 0.3, ap rec 3; Vc 355 from Vc(f) at f 0.3" in rough.notes
    assert "cutting data ranges: TT A283, A279" in rough.notes


# --- tool form ------------------------------------------------------------------------------------

FORM = dict(name="CNMG PM", type="turning_rough", iso_group=["P"], insert_code="CNMG120408-PM", grade="GC4325",
            vc_min="215", vc_max="455", f_min="0.15", f_max="0.5", ap_min="0.5", ap_max="5.5",
            ap_rec="3", f_rec="0.3", vc_points="0.1:455, 0.4:305, 0.8:215", source="TT A283")


def test_tool_form_saves_the_catalogue_values(client):
    client.post("/tools", data=FORM)
    tool = db.session.execute(db.select(Tool).filter_by(name="CNMG PM")).scalar_one()
    assert (tool.ap_rec, tool.f_rec, tool.vc_points) == (3, 0.3, "0.1:455, 0.4:305, 0.8:215")
    assert "Vc(f) 0.1:455, 0.4:305, 0.8:215" in client.get("/machine").get_data(as_text=True)


@pytest.mark.parametrize("field, value, message", [
    ("ap_rec", "6", "ap_rec must be within ap_min..ap_max"),
    ("f_rec", "0.1", "f_rec must be within f_min..f_max"),
    ("vc_points", "0.1-455", "Vc(f) points"),
])
def test_tool_form_rejects_bad_catalogue_values(client, field, value, message):
    response = client.post("/tools", data={**FORM, field: value}, follow_redirects=True)
    assert message in response.get_data(as_text=True)
    assert db.session.execute(db.select(Tool).filter_by(name="CNMG PM")).scalar_one_or_none() is None
