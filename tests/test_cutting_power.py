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
