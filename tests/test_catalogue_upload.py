"""Roadmap stage 5, step 4: a catalogue PDF kept in instance/catalogues/, its pages found and picked."""
import io
import os

import pymupdf

from turnpilot.models import CatalogueDocument, db


def catalogue_pdf():
    doc = pymupdf.open()
    for text in ("Contents of the catalogue", "CNMG 12 04 08-PM ap 0.5 - 3 - 5.5 mm fn 0.15 - 0.3 - 0.5 mm/r",
                 "GC4325 Vc m/min P1.2 455 305 215 fn 0.1 0.4 0.8"):
        doc.new_page().insert_text((50, 72), text)
    return doc.tobytes()


def upload(client):
    return client.post("/cutting-data/catalogues", data={"title": "Test catalogue",
                       "catalogue": (io.BytesIO(catalogue_pdf()), "cat.pdf")}, content_type="multipart/form-data")


def test_upload_keeps_the_pdf_in_instance_catalogues(app, client):
    upload(client)
    document = db.session.execute(db.select(CatalogueDocument)).scalar_one()
    assert (document.title, document.pages, document.text_pages) == ("Test catalogue", 3, 3)
    folder = os.path.join(app.instance_path, "catalogues")
    assert sorted(os.listdir(folder)) == sorted([document.stored_filename, document.stored_filename + ".pages.json"])


def test_not_a_pdf_is_refused(app, client):
    response = client.post("/cutting-data/catalogues", data={"catalogue": (io.BytesIO(b"hello"), "x.pdf")},
                           content_type="multipart/form-data", follow_redirects=True)
    assert "must be a PDF" in response.get_data(as_text=True)


def test_search_likely_and_pick(app, client):
    upload(client)
    document = db.session.execute(db.select(CatalogueDocument)).scalar_one()
    page = client.get(f"/cutting-data/catalogues/{document.id}").get_data(as_text=True)
    assert "likely" in page
    page = client.get(f"/cutting-data/catalogues/{document.id}?q=CNMG120408-PM").get_data(as_text=True)
    assert "CNMG 12 04 08-PM" in page
    client.post(f"/cutting-data/catalogues/{document.id}", data={"pages": "2-3", "material_groups": "P1.2",
                                                                "codes": "CNMG120408-PM\nGC4325"})
    db.session.refresh(document)
    assert (document.selected, document.material_groups) == ([2, 3], "P1.2")
    response = client.post(f"/cutting-data/catalogues/{document.id}", data={"pages": "9", "material_groups": "P1.2"},
                           follow_redirects=True)
    assert "not within 1–3" in response.get_data(as_text=True)
