"""Roadmap stage 5, step 5: reading the picked catalogue pages with the model (a fake client: no paid call) and
checking the rows by code."""
import io
import json

import pymupdf
import pytest
from conftest import FakeClient, make_response

from turnpilot import catalogue_reader as cr
from turnpilot.models import CatalogueDocument, CuttingDataRow, db

PAGES = {
    1: ["Contents"],
    2: ["CNMG 12 04 08-PM  ap 0.5 3 5.5  fn 0.15 0.3 0.5", "DNMG 15 06 04-PF  ap 0.25 0.4 1.5  fn 0.07 0.15 0.3"],
    3: ["Cutting speed Vc m/min, with coolant", "P1.2  GC4325  fn 0.1 0.4 0.8  455 305 215"],
}


def catalogue_pdf():
    doc = pymupdf.open()
    for lines in PAGES.values():
        page = doc.new_page()
        for i, line in enumerate(lines):
            page.insert_text((50, 72 + 20 * i), line)
    return doc.tobytes()


def geometry(code="CNMG 12 04 08-PM", page=2, quote="CNMG 12 04 08-PM  ap 0.5 3 5.5  fn 0.15 0.3 0.5",
             group="P", **values):
    row = dict(kind="geometry", insert_code=code, grade="", application="none", material_group=group,
               ap_min=0.5, ap_rec=3, ap_max=5.5, f_min=0.15, f_rec=0.3, f_max=0.5, vc_min=None, vc_max=None,
               vc_points=[], coolant="not_stated", from_graph=False, pdf_page=page, page_label="A283", quote=quote)
    row.update(values)
    return row


def grade(**values):
    row = dict(kind="grade_vc", insert_code="", grade="GC4325", application="turning", material_group="P1.2",
               ap_min=None, ap_rec=None, ap_max=None, f_min=None, f_rec=None, f_max=None, vc_min=None, vc_max=None,
               vc_points=[{"f": 0.1, "vc": 455}, {"f": 0.4, "vc": 305}, {"f": 0.8, "vc": 215}], coolant="yes",
               from_graph=False, pdf_page=3, page_label="A279", quote="P1.2  GC4325  fn 0.1 0.4 0.8  455 305 215")
    row.update(values)
    return row


TEXTS = {n: "\n".join(lines) for n, lines in PAGES.items()}


def check(*rows, codes=None, pages=(2, 3)):
    return cr.check_reading({"rows": list(rows)}, {n: TEXTS[n] for n in pages}, "P1.2", codes)


def test_tool_schema_is_strict_and_asks_for_page_and_quote():
    tool = cr.RECORD_CUTTING_DATA_TOOL
    row = tool["input_schema"]["properties"]["rows"]["items"]
    assert tool["strict"] and row["additionalProperties"] is False
    assert {"pdf_page", "quote", "from_graph", "material_group"} <= set(row["required"]) == set(row["properties"])
    assert cr.prompt_version().startswith("catalogue:")


def test_good_rows_are_taken_without_checks():
    taken, not_taken = check(geometry(), grade())
    assert not_taken == []
    g, v = taken
    assert (g["insert_code"], g["material_group"], g["ap_rec"], g["f_max"], g["page"], g["pdf_page"], g["checks"]) == (
        "CNMG120408-PM", "P", 3, 0.5, "A283", 2, [])
    assert (v["grade"], v["application"], v["vc_points"], v["coolant"], v["checks"]) == (
        "GC4325", "turning", "0.1:455, 0.4:305, 0.8:215", True, [])
    assert "vc_min" not in g and g["vc_points"] is None


def test_one_recommended_vc_is_one_point():
    (v,), _ = check(grade(vc_points=[{"f": 0, "vc": 305}], vc_min=215, vc_max=455))
    assert (v["vc_points"], v["vc_min"], v["vc_max"]) == ("305", 215, 455)


def test_quote_and_numbers_not_on_the_page_get_a_check():
    (row,), _ = check(geometry(quote="CNMG 12 04 08-PM ap 0.5-3-5.5", ap_rec=3.5))
    assert row["checks"] == ["the quote is not on page 2", "not on the page: 3.5"]


@pytest.mark.parametrize("row, reason", [
    (geometry(from_graph=True), cr.GRAPH_NOT_TAKEN),
    (geometry(page=1), "no page among the picked ones"),
    (geometry(quote=" "), "no quote"),
    (geometry(group="M"), "material group 'M' was not asked for"),
    (grade(material_group="P1.1"), "material group 'P1.1' was not asked for"),
    (grade(application="none"), "no grade or application"),
    (geometry(ap_rec=6), "ap min / rec / max are not in order"),
    (grade(vc_points=[{"f": 0.4, "vc": 305}, {"f": 0.1, "vc": 455}]), "the feeds do not rise"),
    (geometry(ap_min=None, ap_rec=None, ap_max=None, f_min=None, f_rec=None, f_max=None), "no values"),
    (geometry(code=""), "no insert code"),
])
def test_rows_not_taken(row, reason):
    taken, not_taken = check(row)
    assert taken == [] and reason in not_taken[0]["reason"]


def test_only_the_codes_asked_for():
    taken, not_taken = check(geometry(), geometry(code="DNMG 15 06 04-PF", quote="DNMG 15 06 04-PF", ap_min=0.25,
                                                  ap_rec=0.4, ap_max=1.5, f_min=0.07, f_rec=0.15, f_max=0.3),
                             codes="CNMG120408-PM\nGC4325")
    assert [r["insert_code"] for r in taken] == ["CNMG120408-PM"]
    assert "DNMG 15 06 04-PF is not in the list to record" in not_taken[0]["reason"]


def test_numbers_on_a_page():
    assert cr.page_numbers("fn 0,15 .3 5.5 A283") >= {0.15, 0.3, 5.5, 283.0}


# --- through the app ----------------------------------------------------------------------------------

@pytest.fixture
def document(app, client):
    client.post("/cutting-data/catalogues", data={"title": "Test catalogue 2020",
                "catalogue": (io.BytesIO(catalogue_pdf()), "cat.pdf")}, content_type="multipart/form-data")
    document = db.session.execute(db.select(CatalogueDocument)).scalar_one()
    client.post(f"/cutting-data/catalogues/{document.id}", data={"pages": "2, 3", "material_groups": "P1.2",
                                                                "codes": "CNMG 12 04 08-PM\nGC4325"})
    return document


def fake(app, *rows):
    client = FakeClient(make_response({"rows": list(rows)}, tool_name=cr.TOOL_NAME))
    app.config["ANTHROPIC_CLIENT"] = client
    return client


def test_estimate_is_shown_and_no_call_without_the_tick(app, client, document):
    page = client.get(f"/cutting-data/catalogues/{document.id}").get_data(as_text=True)
    assert "1 API call</strong> to test-model" in page and "input tokens" in page
    assert "<title>Catalogue pages · TurnPilot</title>" in page
    fake_client = fake(app, geometry())
    response = client.post(f"/cutting-data/catalogues/{document.id}/read", follow_redirects=True)
    assert "Tick that you start a paid reading" in response.get_data(as_text=True)
    assert fake_client.messages.calls == []


def test_reading_adds_rows_to_check(app, client, document):
    fake_client = fake(app, geometry(), grade(), geometry(from_graph=True))
    client.post(f"/cutting-data/catalogues/{document.id}/read", data={"paid": "1"})
    (call,) = fake_client.messages.calls
    content = call["messages"][0]["content"]
    assert [c["type"] for c in content] == ["text", "image", "text", "image", "text"]
    assert content[0]["text"].startswith("=== Page 2 ===") and "Material groups: P1.2" in content[-1]["text"]
    assert "Only these codes and grades: CNMG 12 04 08-PM, GC4325" in content[-1]["text"]
    db.session.refresh(document)
    assert (document.status, document.prompt_version) == ("read", cr.prompt_version())
    rows = db.session.execute(db.select(CuttingDataRow)).scalars().all()
    assert [(r.kind, r.status, r.origin, r.catalogue, r.document_id) for r in rows] == [
        ("geometry", "read", "model", "Test catalogue 2020", document.id),
        ("grade_vc", "read", "model", "Test catalogue 2020", document.id)]
    assert json.loads(document.not_taken)[0]["reason"] == cr.GRAPH_NOT_TAKEN
    page = client.get(f"/cutting-data/catalogues/{document.id}").get_data(as_text=True)
    assert "Not taken from the last reading (1)" in page


def test_reading_again_sets_aside_the_unconfirmed_rows(app, client, document):
    fake(app, geometry())
    client.post(f"/cutting-data/catalogues/{document.id}/read", data={"paid": "1"})
    first = db.session.execute(db.select(CuttingDataRow)).scalar_one()
    first.status = "confirmed"
    db.session.commit()
    fake(app, geometry(), grade())
    client.post(f"/cutting-data/catalogues/{document.id}/read", data={"paid": "1"})
    fake(app, grade())
    client.post(f"/cutting-data/catalogues/{document.id}/read", data={"paid": "1"})
    statuses = [(r.kind, r.status) for r in db.session.execute(db.select(CuttingDataRow).order_by(CuttingDataRow.id)).scalars()]
    assert statuses == [("geometry", "confirmed"), ("geometry", "rejected"), ("grade_vc", "rejected"),
                        ("grade_vc", "read")]


def test_a_failed_reading_is_kept(app, client, document):
    app.config["ANTHROPIC_CLIENT"] = FakeClient(make_response(None, stop_reason="max_tokens"))
    response = client.post(f"/cutting-data/catalogues/{document.id}/read", data={"paid": "1"}, follow_redirects=True)
    assert "output token limit" in response.get_data(as_text=True)
    db.session.refresh(document)
    assert document.status == "failed" and db.session.execute(db.select(CuttingDataRow)).first() is None
