"""Spindle power check of roughing: Pc = Vc·ap·f·kc / 60000 against power_kw × drive efficiency."""
from turnpilot import services
from turnpilot.models import Material, db

PROFILE = dict(action="profile", name="Lathe 1", max_rpm="4000", power_kw="11", max_diameter="300")


def _materials():
    return {m.name: m for m in db.session.execute(db.select(Material)).scalars()}


def test_seed_kc_values_are_marked_placeholders(app):
    materials = _materials()
    assert (materials["Steel 45 (C45)"].kc1, materials["Steel 45 (C45)"].mc) == (1600, 0.25)
    assert all(m.kc1 and m.mc and m.kc_source.startswith("PLACEHOLDER") for m in materials.values())
    assert services.get_machine().drive_efficiency is None  # not guessed


def test_machine_page_saves_and_clears_the_drive_efficiency(client):
    client.post("/machine", data={**PROFILE, "drive_efficiency": "0.8"})
    assert services.get_machine().drive_efficiency == 0.8
    client.post("/machine", data={**PROFILE, "drive_efficiency": ""})
    assert services.get_machine().drive_efficiency is None


def test_machine_page_rejects_an_efficiency_above_one(client):
    response = client.post("/machine", data={**PROFILE, "drive_efficiency": "80"}, follow_redirects=True)
    assert "Drive efficiency is a share of the power" in response.get_data(as_text=True)
    db.session.expire_all()
    assert services.get_machine().drive_efficiency is None


def test_machine_page_edits_the_materials_kc(client):
    steel = _materials()["Steel 45 (C45)"]
    page = client.get("/machine").get_data(as_text=True)
    assert f'name="m{steel.id}-kc1"' in page and "PLACEHOLDER" in page
    form = {"action": "materials"}
    for m in _materials().values():
        form.update({f"m{m.id}-kc1": str(m.kc1), f"m{m.id}-mc": str(m.mc), f"m{m.id}-kc_source": m.kc_source})
    form.update({f"m{steel.id}-kc1": "1700", f"m{steel.id}-mc": "0.25", f"m{steel.id}-kc_source": "my catalogue p. 12"})
    client.post("/machine", data=form)
    db.session.expire_all()
    steel = _materials()["Steel 45 (C45)"]
    assert (steel.kc1, steel.mc, steel.kc_source) == (1700, 0.25, "my catalogue p. 12")
    form.update({f"m{steel.id}-kc1": "", f"m{steel.id}-mc": "", f"m{steel.id}-kc_source": ""})
    client.post("/machine", data=form)
    db.session.expire_all()
    steel = _materials()["Steel 45 (C45)"]
    assert (steel.kc1, steel.mc, steel.kc_source) == (None, None, None)


def test_machine_page_rejects_an_mc_of_one_or_more(client):
    steel = _materials()["Steel 45 (C45)"]
    response = client.post("/machine", data={"action": "materials", f"m{steel.id}-kc1": "1600",
                                             f"m{steel.id}-mc": "25"}, follow_redirects=True)
    assert "mc is an exponent below 1" in response.get_data(as_text=True)
    db.session.expire_all()
    assert _materials()["Steel 45 (C45)"].mc == 0.25


# --- planner ----------------------------------------------------------------------------------------

import math  # noqa: E402

import pytest  # noqa: E402

from turnpilot.planner import (  # noqa: E402
    FeatureSpec,
    JobSpec,
    PowerSpec,
    ToolSpec,
    TurretEntry,
    cutting_power,
    plan_job,
    rough_passes,
    specific_cutting_force,
)
from turnpilot.seed import TURRET_TOOLS  # noqa: E402

SEED = [TurretEntry(pos, ToolSpec(id=pos, **{k: v for k, v in tool.items() if k != "grade"}))
        for pos, tool in TURRET_TOOLS.items()]
C45 = dict(material_name="Steel 45 (C45)", kc1=1600, mc=0.25, kc_source="test catalogue")
SEED_POWER = PowerSpec(11, 0.8)  # 8.8 kW at the spindle


def _rough(features, blank, power=SEED_POWER, max_rpm=4000, **material):
    job = JobSpec("P", blank, 100, tuple(features), axial_order=True, **{**C45, **material})
    return {op.feature_id: op for op in plan_job(job, SEED, max_rpm, None, power) if op.tool_type == "turning_rough"}


def test_formula():
    kc = specific_cutting_force(1600, 0.25, 0.4)
    assert kc == pytest.approx(1600 * 0.4 ** -0.25) == pytest.approx(2012, abs=1)
    assert cutting_power(200, 3.05, 0.4, kc) == pytest.approx(8.18, abs=0.01)


ND012 = [FeatureSpec(1, "od_turn", diameter=50, length=44), FeatureSpec(2, "od_turn", diameter=45, length=5),
         FeatureSpec(3, "od_turn", diameter=38.5, length=5), FeatureSpec(4, "od_turn", diameter=24, length=22),
         FeatureSpec(5, "thread", diameter=24, length=22, pitch=1.5)]


def test_nd012_within_power_unchanged_with_a_note():
    rough = _rough(ND012, 55)
    op = rough[3]  # Ø38.5 from Ø45: 3.05 × 1, Pc 8.18
    assert (op.passes, op.ap, op.vc, op.f) == (1, 3.05, 200, 0.4) and not op.warnings
    assert any(n.startswith("Pc 8.") and "≤ 8.80 kW (11 × 0.8); kc 2012 N/mm² (test catalogue)" in n
               for n in op.notes)


def test_nd012_section_above_power_gets_more_thinner_passes():
    op = _rough(ND012, 55)[4]  # Ø24 (under M24, 23.85) from Ø38.5: was 2 × 3.562, Pc 9.56
    assert (op.passes, op.ap) == (3, 2.375) and (op.vc, op.f) == (200, 0.4)  # Vc and f unchanged
    assert not op.warnings
    note = next(n for n in op.notes if n.startswith("ap reduced"))
    assert "Pc 9.56 > 8.80 kW (11 × 0.8) at ap 3.562; now 3 × ap 2.375, Pc 6.37 kW" in note


def test_without_efficiency_a_warning_and_no_change():
    with_none = _rough(ND012, 55, power=PowerSpec(11, None))[4]
    unchecked = _rough(ND012, 55, power=None)[4]
    assert (with_none.passes, with_none.ap) == (unchecked.passes, unchecked.ap) == (2, 3.562)
    assert with_none.warnings == ["check the spindle power: Pc 9.56 kW at ap 3.562, power 11 kW, but the drive "
                                  "efficiency is not set on the Machine page"]


def test_without_machine_power_a_warning():
    op = _rough(ND012, 55, power=PowerSpec(None, 0.8))[4]
    assert op.passes == 2 and op.warnings == ["check the spindle power: Pc 9.56 kW at ap 3.562, the machine power is "
                                              "not set"]


def test_material_without_kc_is_not_checked():
    op = _rough(ND012, 55, kc1=None, mc=None)[4]
    assert op.passes == 2 and op.warnings == [
        "no kc1 / mc for Steel 45 (C45): spindle power not checked (Machine page, Materials)"]


def test_no_power_spec_means_no_check():
    op = _rough(ND012, 55, power=None)[4]
    assert not op.warnings and not any("Pc" in n for n in op.notes)


def test_not_enough_power_even_at_ap_min():
    op = _rough([FeatureSpec(1, "od_turn", diameter=30, length=40)], 45, power=PowerSpec(2, 0.8))
    op = op[1]  # 1.6 kW available; even ap_min 1.5 needs more
    # 7.3 mm/side split by ap_min 1.5: 5 passes of 1.46
    assert (op.passes, op.ap) == rough_passes(45, 30, TURRET_TOOLS[2]["ap_min"], 0.2) == (5, 1.46)
    assert "spindle power is not enough even at the tool's ap_min 1.5" in op.warnings[0]


def test_power_from_the_actual_speed_when_n_is_capped():
    # Ø10 from Ø20 with Vc 200 would need 3183 rpm; capped at 1000 rpm the actual Vc is π·20·1000/1000 = 62.8
    op = _rough([FeatureSpec(1, "od_turn", diameter=20, length=20), FeatureSpec(2, "od_turn", diameter=10,
                                                                                  length=20)], 22, max_rpm=1000)[2]
    kc = specific_cutting_force(1600, 0.25, op.f)
    expected = cutting_power(math.pi * 20 * 1000 / 1000, op.ap, op.f, kc)
    assert any(n.startswith(f"Pc {expected:.2f} kW") for n in op.notes)


def test_power_check_through_the_app(client):
    from turnpilot.models import Job, Operation
    material = _materials()["Steel 45 (C45)"]
    client.post("/jobs", data=dict(name="Heavy", material_id=material.id, quantity=1, blank_diameter=45,
                                   blank_length=60))
    job = db.session.execute(db.select(Job)).scalar_one()
    client.post(f"/jobs/{job.id}/features", data=dict(type="od_turn", diameter=36, length=40))  # 4.3 mm/side

    def rough():
        client.post(f"/jobs/{job.id}/calculate")
        return db.session.execute(db.select(Operation).filter_by(tool_type="turning_rough",
                                                                 is_archived=False)).scalar_one()

    op = rough()  # seed: no efficiency
    assert op.passes == 2 and "drive efficiency is not set" in op.warning
    client.post("/machine", data={**PROFILE, "drive_efficiency": "0.5"})  # 5.5 kW: 2 × 2.15 gives Pc 5.77
    op = rough()
    assert op.passes == 3 and op.warning is None and "ap reduced for spindle power" in op.note
    assert "PLACEHOLDER" in op.note  # the kc source is shown
