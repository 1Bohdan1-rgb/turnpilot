import re
from pathlib import Path

from turnpilot import i18n

ROOT = Path(__file__).parent.parent / "turnpilot"
CALL = re.compile(r"""\b_\(\s*"((?:[^"\\]|\\.)*)"|\b_\(\s*'((?:[^'\\]|\\.)*)'""")


def asked_texts():
    """Every text the templates, the routes and the scripts ask for."""
    out = {}
    files = list((ROOT / "templates").glob("*.html")) + [ROOT / "routes.py", ROOT / "__init__.py"]
    for path in files:
        for m in CALL.finditer(path.read_text(encoding="utf-8")):
            text = m.group(1) if m.group(1) is not None else m.group(2)
            out.setdefault(text.encode().decode("unicode_escape") if "\\" in text else text, path.name)
    return out


def test_every_text_has_its_ukrainian():
    missing = {text: where for text, where in asked_texts().items() if text not in i18n.UK}
    assert not missing


def test_script_texts_are_in_the_dictionary():
    assert [k for k in i18n.JS_KEYS if k not in i18n.UK] == []


def test_the_default_is_ukrainian(app):
    html = app.test_client().get("/jobs").get_data(as_text=True)
    assert '<html lang="uk">' in html
    assert ">Завдання</a>" in html and ">Цех</a>" in html


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
    assert uk["Jobs"] == "Завдання"
    assert uk["Cutting data"] == "Режими різання"


def test_placeholders_are_kept(app):
    with app.test_request_context(headers={"Cookie": "lang=uk"}):
        for en, ua in i18n.UK.items():
            assert set(re.findall(r"\{(\w+)\}", en)) == set(re.findall(r"\{(\w+)\}", ua)), en
