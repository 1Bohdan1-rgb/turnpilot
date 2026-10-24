"""Boring in the program: the drill, rough passes (the edge stepping back towards the axis), the finishing pass
with G41, the bar's data as the operator set it (DMIN, overhang, spindle direction, κr), and the simulation."""
from datetime import datetime, timezone

import pytest
from gcode_jobs import make_job, ready_machine

from turnpilot import services
from turnpilot.gcode import fanuc
from turnpilot.models import Feature, Tool, TurretSlot, db

BAR = dict(min_bore_diameter=16, min_bore_source="test", max_depth=50, spindle_direction="M03", tip_direction=2,
           entering_angle_deg=95)


def bushing(bottom="drill_point", depth_mode="full_diameter", length=32, tolerance="+0.21"):
    """A bushing like Завіса 36's (own numbers): Ø34.8 × 80, a blind Ø22 bore opening at the free (right) end."""
    bore = Feature(type="bore", diameter=22, length=length, tolerance=tolerance, bore_open_end="right", bore_from_mm=0,
                   bore_bottom=bottom, bore_position_source="dxf", bore_position_at=datetime.now(timezone.utc),
                   depth_mode=depth_mode, depth_decided_by="Bohdan", depth_decided_at=datetime.now(timezone.utc))
    return [Feature(type="face"), Feature(type="od_turn", diameter=34.8, length=80), bore, Feature(type="parting")]


def _tool(position):
    return db.session.execute(db.select(TurretSlot).filter_by(position=position)).scalar_one().tool


@pytest.fixture
def machine(app):
    machine = ready_machine()
    slot = db.session.execute(db.select(TurretSlot).filter_by(position=12)).scalar_one()
    drill = Tool(name="Drill 20", type="drilling", iso_group="P", insert_code="860.1-2000-060A1-GM", vc_min=80,
                 vc_max=160, f_min=0.1, f_max=0.3, ap_min=10, ap_max=10, diameter=20, max_depth=60,
                 point_angle_deg=140, f_rec=0.2, vc_points="125")
    db.session.add(drill)
    slot.tool = drill
    for position in (5, 9):
        tool = _tool(position)
        for key, value in BAR.items():
            setattr(tool, key, value)
    db.session.commit()
    return machine


def program(job, machine):
    for op in job.current_operations:
        if op.tool_type == "drilling":
            op.cutting_data_origin = "operator"  # the test drill has no catalogue row
        op.status = "approved"
    db.session.commit()
    readiness, prog = services.gcode_program(job, machine)
    assert prog is not None, readiness.blockers
    return prog, fanuc.render(prog)


def make(machine, **kw):
    return make_job(bushing(**kw), free_end="right", blank=38, length=90, stickout=95, face_stock=1.0, name="Bushing")


def test_bushing_bored_clean_and_complete(machine):
    job = make(machine)
    prog, text = program(job, machine)
    titles = [b.title for b in prog.blocks]
    assert any(t.startswith("DRILL D20 DEPTH 32") for t in titles)
    assert any(t.startswith("ROUGH BORE D22") for t in titles) and "FINISH BORE D22 TO Z-32" in titles
    assert "G96 S" in text and " M03" in text  # the bar's own direction
    assert "G01 G41 X22." in text and "G00 G40 Z2." in text
    assert "SPINDLE FOR BORING BAR T09" in text and "FINISHING BORE: NOSE RADIUS COMPENSATION G41" in text
    assert "THE INNER PROFILE IS NOT SIMULATED" not in text
    result = services.gcode_simulation(job, machine, text)
    assert result.ok, result.errors
    assert result.complete, result.incomplete
    warnings = [m for _, m in result.warnings]
    assert any("the bore's inner corner at Z-32." in w for w in warnings)  # the nose radius at the wall's end


def test_checklist_names_the_bars(machine):
    job = make(machine)
    program(job, machine)
    _, record = services.generate_gcode(job, machine)
    items = dict(services.gcode_checklist(record))
    assert "T09 Boring bar CCMT M03" in items["boring_spindle"] and "T05" in items["boring_spindle"]
    assert "G41 in the bore: on the offset page T09 R0.4 T2" in items["bore_compensation"]


@pytest.mark.parametrize("key, value, reason", [
    ("spindle_direction", None, "the spindle direction for this bar is not set"),
    ("min_bore_diameter", None, "DMIN of the bar is not set"),
    ("max_depth", None, "the bar's overhang (its max depth, the tool page) is not set"),
    ("max_depth", 30, "the bore goes 32 mm deep, the bar's overhang reaches 30"),
])
def test_bar_data_missing(machine, key, value, reason):
    setattr(_tool(9), key, value)
    setattr(_tool(5), key, value)
    db.session.commit()
    job = make(machine)
    prog, _ = program(job, machine)
    reasons = [r for op_id, r in prog.skipped.items()]
    assert any(r.startswith(reason) for r in reasons), reasons


def test_bore_at_the_chuck_end_is_not_drilled_or_bored(machine):
    features = bushing()
    features[2].bore_open_end = "left"  # Z0 on the right: the bore opens at the chuck end
    job = make_job(features, free_end="right", blank=38, length=90, stickout=95, face_stock=1.0)
    prog, text = program(job, machine)
    assert all(not b.title.startswith(("DRILL", "ROUGH BORE", "FINISH BORE")) for b in prog.blocks)
    assert any("needs a second set-up" in r for r in prog.skipped.values())
    result = services.gcode_simulation(job, machine, text)
    assert any("material left in the bore" in m for m in result.incomplete)


def test_without_kr_the_cone_stays(machine):
    """Depth to the drill's point: its cone is above the bore's bottom, faced only with κr ≥ 90°."""
    for position in (5, 9):
        _tool(position).entering_angle_deg = None
    db.session.commit()
    job = make(machine, depth_mode="tip")
    prog, text = program(job, machine)
    finish = next(b for b in prog.blocks if b.title.startswith("FINISH BORE"))
    assert any("needs κr ≥ 90°" in w for w in finish.warnings)
    result = services.gcode_simulation(job, machine, text)
    assert any("material left in the bore" in m for m in result.incomplete)


def test_flat_bottom_faced_from_the_centre(machine):
    job = make(machine, bottom="flat", depth_mode="tip")
    prog, text = program(job, machine)
    finish = next(b for b in prog.blocks if b.title.startswith("FINISH BORE"))
    result = services.gcode_simulation(job, machine, text)
    assert result.ok, result.errors
    # the bar's head (DMIN 16) cannot reach into the narrow end of the drill's cone: it is left, and said so
    assert any("the drill's cone is left" in w for w in finish.warnings)
    assert any("material left in the bore" in m for m in result.incomplete)


def test_flat_bottom_with_the_point_below_it(machine):
    job = make(machine, bottom="flat", depth_mode="full_diameter")
    prog, _ = program(job, machine)
    assert any("the drill's point would go below it" in r for r in prog.skipped.values())


def test_3d_states_carry_the_bore(machine):
    job = make(machine)
    program(job, machine)
    _, record = services.generate_gcode(job, machine)
    data = services.part3d_data(record)
    assert data["final_inner"][:2] == [[0.0, 0.0], [0.0, 11.0]] and [-32.0, 11.0] in data["final_inner"]
    titles = [s["title"] for s in data["states"]]
    drilled = data["states"][titles.index(next(t for t in titles if "DRILL D20" in t))]
    assert "inner" not in data["states"][1] and drilled["inner"]  # the facing has no hole yet, the drill makes it
    assert max(r for _, r in data["states"][-1]["inner"]) == pytest.approx(11.0)


def test_3d_caption_switches_in_the_script():
    from pathlib import Path
    from turnpilot import i18n
    script = (Path(__file__).parent.parent / "turnpilot" / "static" / "part3d.js").read_text(encoding="utf-8")
    assert ".part3d-caption" in script and "Half section" in i18n.JS_KEYS
    for page in ("gcode_program.html", "_order_panel.html", "shop.html"):
        text = (Path(__file__).parent.parent / "turnpilot" / "templates" / page).read_text(encoding="utf-8")
        assert 'class="part3d-caption"' in text


def test_a_bar_as_wide_as_the_drilled_hole(machine):
    """CCMT 09 in A16R: DMIN 20 (TT 2020) in the Ø20 drilled hole: no room to step back there, it comes in over the
    face at its first pass; after a pass it steps back as far as its head allows."""
    for position in (5, 9):
        _tool(position).min_bore_diameter = 20
    db.session.commit()
    job = make(machine)
    prog, text = program(job, machine)
    assert any(b.title.startswith("ROUGH BORE") for b in prog.blocks), prog.skipped
    result = services.gcode_simulation(job, machine, text)
    assert result.ok, result.errors
    assert result.complete, result.incomplete


def test_a_point_angle_whose_point_is_rounded(machine):
    """jobs/11 (Завіса 36 bushing): 860-PM Ø20, SIG 147°, to the full Ø at 32: the point's Z is written as Z-34.962
    (rounded); the full Ø still reaches Z-32 and the bars bore the wall down to Z-32 (no 0.01 mm left at the bottom)."""
    drill = db.session.execute(db.select(TurretSlot).filter_by(position=12)).scalar_one().tool
    drill.point_angle_deg = 147
    db.session.commit()
    job = make(machine)
    prog, text = program(job, machine)
    assert "G01 Z-34.962" in text
    assert "Z-31.99" not in text and text.count("G01 Z-32.") >= 3  # two rough passes and the finishing pass
    assert not any("cone is left" in w or "finished down to" in w for b in prog.blocks for w in b.warnings)
    result = services.gcode_simulation(job, machine, text)
    assert result.ok, result.errors
    assert result.complete, result.incomplete


def test_the_bottom_as_the_drill_leaves_it(machine):
    drill = db.session.execute(db.select(TurretSlot).filter_by(position=12)).scalar_one().tool
    drill.point_angle_deg = 147
    db.session.commit()
    features = bushing()
    features[2].bore_cone_angle = 120
    job = make_job(features, free_end="right", blank=38, length=90, stickout=95, face_stock=1.0)
    _, text = program(job, machine)
    comments = " ".join(line.strip("()") for line in text.splitlines() if line.startswith("("))
    assert ("THE BOTTOM: A RING D20-D22 AT Z-32, THEN THE DRILL'S 147 DEG CONE TO Z-34.962 DRAWN 120 DEG, NOT "
            "DIMENSIONED: NOT MACHINED" in comments)
