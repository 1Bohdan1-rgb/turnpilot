import re
from pathlib import Path

from turnpilot import i18n, services
from turnpilot.machine_spec import MACHINE_FIELDS, PROGRAMMING_FIELDS
from turnpilot.models import (
    BLANK_SHAPES, CUTTING_DATA_APPLICATIONS, CUTTING_DATA_STATUSES, FEATURE_TYPES, GCODE_STATUSES, MACHINE_KINDS,
    MACHINE_STATUSES, OPERATION_STATUSES, ORDER_STATUSES, TOOL_TYPES,
)

ROOT = Path(__file__).parent.parent / "turnpilot"
CALL = re.compile(r"""\b(?:_|gettext)\(\s*"((?:[^"\\]|\\.)*)"|\b(?:_|gettext)\(\s*'((?:[^'\\]|\\.)*)'""")
JS_CALL = re.compile(r"""\bt\(\s*"((?:[^"\\]|\\.)*)"\s*[,)]""")
PAGES = list((ROOT / "templates").glob("*.html"))


def _unescape(text):
    return text.replace('\\"', '"').replace("\\'", "'")


def asked_texts():
    """Every text the templates and the routes ask for."""
    out = {}
    for path in PAGES + [ROOT / "routes.py", ROOT / "__init__.py"]:
        source = path.read_text(encoding="utf-8")
        for m in CALL.finditer(source):
            text = m.group(1) if m.group(1) is not None else m.group(2)
            # a call split over lines in Python: "a " "b" (implicit concatenation)
            out.setdefault(_unescape(text), path.name)
    for path in [ROOT / "routes.py"]:
        for m in re.finditer(r'_\(((?:\s*"(?:[^"\\]|\\.)*")+)', path.read_text(encoding="utf-8")):
            parts = re.findall(r'"((?:[^"\\]|\\.)*)"', m.group(1))
            if len(parts) > 1:
                out.setdefault(_unescape("".join(parts)), path.name)
    return out


def test_every_text_has_its_ukrainian():
    texts = asked_texts()
    missing = {text: where for text, where in texts.items() if text not in i18n.UK}
    # the first piece of a text split over lines is not a text of its own
    missing = {t: w for t, w in missing.items()
               if not (w == "routes.py" and any(o != t and o.startswith(t) for o in texts))}
    assert not missing


def test_every_script_text_is_sent_to_the_page():
    asked = set()
    for path in PAGES + list((ROOT / "static").glob("*.js")):
        asked |= {_unescape(m) for m in JS_CALL.findall(path.read_text(encoding="utf-8"))}
    assert asked - set(i18n.JS_KEYS) == set()
    assert [k for k in i18n.JS_KEYS if k not in i18n.UK] == []


def test_texts_from_the_code_shown_on_the_pages():
    """Field labels and help, choices and codes shown as words."""
    texts = [f.label for f in MACHINE_FIELDS + PROGRAMMING_FIELDS] + [f.help for f in MACHINE_FIELDS + PROGRAMMING_FIELDS
                                                                      if f.help]
    texts += [o for f in PROGRAMMING_FIELDS for o in f.options if not o.startswith("M0")]
    texts += list(MACHINE_KINDS.values()) + list(services.BORE_DEPTH_MODES.values())
    texts += ["Free end (Z0) on the drawing", "Stick-out from the jaws", "Stock beyond Z0"]  # services.gcode_settings
    assert [t for t in texts if t not in i18n.UK] == []
    codes = (FEATURE_TYPES + TOOL_TYPES + BLANK_SHAPES + OPERATION_STATUSES + CUTTING_DATA_STATUSES + GCODE_STATUSES
             + ORDER_STATUSES + MACHINE_STATUSES + CUTTING_DATA_APPLICATIONS + ("rough", "finish"))
    assert [c for c in codes if c not in i18n.LABELS] == []


def test_a_translation_can_stand_in_an_attribute():
    """Texts go into the page as they are (also into placeholder="…" and title="…"): no double quote that the English
    does not have, and the same placeholders."""
    for en, ua in i18n.UK.items():
        assert ua.count('"') <= en.count('"'), en
        assert set(re.findall(r"\{(\w+)\}", en)) == set(re.findall(r"\{(\w+)\}", ua)), en


def test_the_default_is_ukrainian(app):
    html = app.test_client().get("/jobs").get_data(as_text=True)
    assert '<html lang="uk">' in html
    assert ">Завдання</a>" in html and ">Цех</a>" in html and "Нове завдання" in html


def test_switch_to_english_and_back(app):
    client = app.test_client()
    response = client.get("/lang/en?next=/jobs")
    assert response.status_code == 302 and response.headers["Location"].endswith("/jobs")
    html = client.get("/jobs").get_data(as_text=True)
    assert '<html lang="en">' in html and ">Jobs</a>" in html
    client.get("/lang/uk?next=/jobs")
    assert ">Завдання</a>" in client.get("/jobs").get_data(as_text=True)


def test_switch_goes_back_to_local_pages_only(app):
    client = app.test_client()
    assert client.get("/lang/en?next=https://example.com/").headers["Location"] == "/"
    assert client.get("/lang/en?next=//example.com/").headers["Location"] == "/"
    assert client.get("/lang/xx").status_code == 404


def test_glossary():
    uk = i18n.UK
    assert uk["Jobs"] == "Завдання" and uk["Job"] == "Завдання"
    assert uk["Cutting data"] == "Режими різання"
    assert uk["Turret"] == "Револьверна головка" and uk["holder"] == "державка" and uk["insert"] == "пластина"
    assert uk["Approve"] == "Затвердити" and uk["Ready to run"] == "Готово до запуску"
    assert uk["Process sheet: {job}"].startswith("Технологічний лист")
    assert uk["Stick-out from the jaws, mm"].startswith("Виліт")
    assert i18n.LABELS["facing"] == "торцювання" and i18n.LABELS["parting"] == "відрізання"
    assert i18n.LABELS["grooving"] == "канавка" and i18n.LABELS["threading"] == "нарізання різьби"
    assert (i18n.LABELS["rough"], i18n.LABELS["finish"]) == ("чорнова", "чистова")
    assert not [t for t in uk.values() if "машиніст" in t.lower() or "деталі завдання" in t.lower()]


def test_every_page_renders_in_ukrainian(app):
    """A few pages in the default language: nothing fails on a translated text with its values."""
    from turnpilot.models import Machine, db
    client = app.test_client()
    machine = db.session.execute(db.select(Machine)).scalars().first()
    for url in ("/jobs", "/jobs/upload", "/machines", f"/machines/{machine.id}", "/cutting-data", "/cutting-data/catalogues",
                "/shop", "/shop/plan", f"/machines/{machine.id}/passport"):
        response = client.get(url)
        assert response.status_code == 200, url
        assert 'lang="uk"' in response.get_data(as_text=True)


def test_generated_labels_and_states_in_ukrainian(app):
    """What the code writes that reads as the page's labels or states: by its shape; notes stay English."""
    with app.test_request_context(headers={"Cookie": "lang=uk"}):
        g = i18n.generated
        assert g("entered by the operator 2026-10-14") == "введено оператором 2026-10-14"
        assert g("copied from Lathe 1 (Bohdan) 2026-10-23") == "скопійовано з Lathe 1 (Bohdan) 2026-10-23"
        assert g("seed") == "початкові дані" and g("the operator") == "оператор"
        assert g("the job was calculated again after this program") == "завдання розраховано знову після цієї програми"
        assert g("values changed since this program: Spindle direction for a right-hand tool, Stick-out measured to") \
            == "значення змінилися після цієї програми: Напрям обертання шпинделя для правого інструмента, Виліт міряється до"
        assert g("skipped by the operator (Bohdan): no centring") == "пропустив(ла) оператор (Bohdan): no centring"
        assert g("the tool numbers and offsets match the turret on the machine").startswith("номери інструментів")
        assert g("T4: empty -> Grooving 3 mm (Bohdan, 2026-10-22)") == "T4: порожньо -> Grooving 3 mm (Bohdan, 2026-10-22)"
        assert g("program 16") == "№16" and g("50 on Lathe 1") == "50 на Lathe 1" and g("12.1 s") == "12.1 с"
        assert g("Rapid traverse X") == "Прискорене переміщення X"
        assert g("check the spindle power: Pc 8 kW") == "check the spindle power: Pc 8 kW"  # a note: as it is
        assert i18n.dxf_text("section 8") == "ділянка 8" and i18n.dxf_text("section 3 (arc)") == "ділянка 3 (дуга)"
        assert i18n.dxf_text("boundary 0 → inside section 2") == "межа 0 → всередині ділянки 2"
        assert i18n.label("diameter") == "діаметр"
    with app.test_request_context(headers={"Cookie": "lang=en"}):
        assert i18n.generated("seed") == "seed" and i18n.dxf_text("section 8") == "section 8"


def test_units_and_sources_on_the_machine_page_in_ukrainian(app):
    page = app.test_client().get("/machines/1").get_data(as_text=True)
    assert "Макс. частота обертання шпинделя, об/хв" in page and ", rpm" not in page
    assert "Потужність шпинделя S1 (тривала), кВт" in page and "Тиск МОР, бар" in page
    assert "entered by the operator" not in page
