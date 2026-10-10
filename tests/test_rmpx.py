"""RMPX (the max in-copying angle) with its source: the catalogue's value for a holder style is a suggestion the
operator takes; a profile going down steeper than every finishing tool's RMPX is a simulation error."""
from gcode_jobs import make_job, ready_machine

from turnpilot import services
from turnpilot.gcode import fanuc
from turnpilot.models import Feature, Tool, db
from turnpilot.planner import rmpx_suggestions


def finishing_tool():
    return db.session.execute(db.select(Tool).filter_by(insert_code="DNMG150604-PF")).scalar_one()


def test_suggestions_from_the_catalogue():
    assert rmpx_suggestions("DNMG150604-PF") == [("DDJNR/PDJNR", 27.0, "Sandvik TT 2020 A204 (PDF 208), 93° holder")]
    assert rmpx_suggestions("VNMG 16 04 04-PF") == [("DVJNR", 44.0, "Sandvik TT 2020 A212 (PDF 216)"),
                                                    ("PVJNR", 41.0, "Sandvik TT 2020 A212 (PDF 216)")]
    assert rmpx_suggestions("VNMG160408-PM", "PVJNR2525M16") == [("PVJNR", 41.0, "Sandvik TT 2020 A212 (PDF 216)")]
    assert rmpx_suggestions("CNMG120408-PM") == []


def test_the_operator_takes_the_suggestion(app, client):
    tool = finishing_tool()
    page = client.get(f"/tools/{tool.id}/edit").get_data(as_text=True)
    assert "The holder is DDJNR/PDJNR: take 27.0°" in page
    assert tool.max_ramp_angle is None  # never filled by itself
    client.post(f"/tools/{tool.id}/rmpx", data={"holder_style": "DDJNR/PDJNR"})
    assert tool.max_ramp_angle == 27.0
    assert tool.max_ramp_source == ("Sandvik TT 2020 A204 (PDF 208), 93° holder, holder DDJNR/PDJNR (confirmed by "
                                    "the operator)")
    assert "RMPX 27.0°" in client.get("/machine").get_data(as_text=True)


def concave_job():
    return make_job([Feature(type="od_turn", diameter=30, length=10),
                     Feature(type="arc", start_diameter=30, diameter=30, length=17.32, radius=10, arc_convex=False),
                     Feature(type="od_turn", diameter=30, length=10)], blank=32, length=50, stickout=50)


def simulate(job, machine):
    _, program = services.gcode_program(job, machine)
    text = fanuc.render(program)
    return services.gcode_simulation(job, machine, text)


def test_a_profile_steeper_than_rmpx_is_an_error_with_the_way_out(app):
    machine = ready_machine()
    tool = finishing_tool()
    tool.max_ramp_angle = 27.0
    db.session.commit()
    result = simulate(concave_job(), machine)
    (line, message), = [(line, m) for line, m in result.errors if "towards the chuck" in m]
    assert line == 0
    assert message.startswith("Z-10.01 to Z-27.31: the profile goes down at up to 59° towards the chuck (steepest "
                              "at Z-10. D30.)")
    assert "T04 RMPX 27°: not cut. Use a tool with RMPX ≥ 59°, or a second set-up" in message


def test_without_rmpx_the_error_says_so(app):
    machine = ready_machine()
    result = simulate(concave_job(), machine)
    assert any("T04 RMPX not set: not cut" in m for _, m in result.errors)


def test_a_way_out_is_named_when_the_catalogue_has_one(app):
    # a concave R30 between Ø30 ends, 10 long: it goes down at about 10° only... so take a deeper one, 40°
    machine = ready_machine()
    tool = finishing_tool()
    tool.max_ramp_angle = 27.0
    db.session.commit()
    job = make_job([Feature(type="od_turn", diameter=30, length=10),
                    Feature(type="arc", start_diameter=30, diameter=30, length=38.57, radius=30, arc_convex=False),
                    Feature(type="od_turn", diameter=30, length=10)], blank=32, length=70, stickout=70)
    message = next(m for _, m in simulate(job, machine).errors if "towards the chuck" in m)
    assert "goes down at up to 40°" in message
    assert "(e.g. VNMG16 in DVJNR (44°, Sandvik TT 2020 A212 (PDF 216)); VNMG16 in PVJNR (41°, Sandvik TT 2020 A212 " \
           "(PDF 216)))" in message


def test_the_vnmg_inserts_are_in_the_library_not_in_the_turret(app, client):
    from turnpilot.models import TurretSlot
    pf = db.session.execute(db.select(Tool).filter_by(insert_code="VNMG160404-PF")).scalar_one()
    pm = db.session.execute(db.select(Tool).filter_by(insert_code="VNMG160408-PM")).scalar_one()
    assert (pf.type, pf.grade, pf.ap_rec, pf.f_rec, pf.vc_points) == ("turning_finish", "GC4315", 0.4, 0.15,
                                                                      "0.1:510, 0.4:365, 0.8:265")
    assert (pm.type, pm.grade, pm.ap_rec, pm.f_rec, pm.vc_points) == ("turning_rough", "GC4325", 2.0, 0.3,
                                                                      "0.1:455, 0.4:305, 0.8:215")
    for tool in (pf, pm):
        assert tool.max_ramp_angle is None and tool.holder_code is None  # until the operator confirms the holder
        assert db.session.execute(db.select(TurretSlot).filter_by(tool_id=tool.id)).first() is None
    assert "The holder is DVJNR: take 44.0°" in client.get(f"/tools/{pf.id}/edit").get_data(as_text=True)
    from conftest import confirm_p12_catalogue
    from turnpilot.models import Material
    confirm_p12_catalogue()
    steel = db.session.execute(db.select(Material).filter_by(name="Steel 45 (C45)")).scalar_one()
    spec = services.tool_spec(pf, steel, services.confirmed_rows())
    assert (spec.ap_rec, spec.f_rec, spec.vc_points) == (0.4, 0.15, ((0.1, 510.0), (0.4, 365.0), (0.8, 265.0)))
    assert spec.catalogue_warning is None and "A288" in spec.catalogue_note
