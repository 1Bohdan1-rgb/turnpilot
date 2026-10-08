"""Machine passport upload: the PDF kept, its pages' text, the operator picking the pages to read. No API."""
import io

import pytest

from passport_pdfs import make_passport
from turnpilot import passport_reader
from turnpilot.models import MachineDocument, db


@pytest.fixture
def passport(tmp_path):
    return make_passport(tmp_path / "passport.pdf").read_bytes()


def _upload(client, data, name="Паспорт TL-1.pdf"):
    return client.post("/machine/passport", data={"passport": (io.BytesIO(data), name)},
                       content_type="multipart/form-data")


def _document():
    return db.session.execute(db.select(MachineDocument)).scalar_one()


def test_page_texts_and_scans(tmp_path):
    texts = passport_reader.page_texts(make_passport(tmp_path / "p.pdf"))
    assert len(texts) == 5 and "Spindle speed range" in texts[2]
    assert [passport_reader.has_text(t) for t in texts] == [True, True, True, True, False]
    assert [passport_reader.likely_spec_page(t) for t in texts] == [False, False, True, False, False]


@pytest.mark.parametrize("text, pages", [("3", [3]), ("3, 5", [3, 5]), ("2-4", [2, 3, 4]), ("4,2;3", [2, 3, 4])])
def test_parse_pages(text, pages):
    assert passport_reader.parse_pages(text, 5) == pages


@pytest.mark.parametrize("text", ["0", "6", "4-2", "x"])
def test_parse_pages_rejects(text):
    with pytest.raises(ValueError):
        passport_reader.parse_pages(text, 5)


def test_upload_keeps_the_pdf_and_counts_its_pages(client, passport):
    response = _upload(client, passport)
    document = _document()
    assert response.location.endswith(f"/machine/passport/{document.id}")
    assert (document.original_filename, document.pages, document.text_pages, document.selected) == (
        "Паспорт TL-1.pdf", 5, 4, [])
    page = client.get(f"/machine/passport/{document.id}").get_data(as_text=True)
    assert "Spindle speed range" in page and "no text (an image)" in page and page.count(">likely<") == 1
    assert client.get(f"/machine/passport/{document.id}/file").data == passport
    assert "Паспорт TL-1.pdf" in client.get("/machine/passport").get_data(as_text=True)


def test_only_a_pdf_is_taken(client):
    response = _upload(client, b"\x89PNG\r\n\x1a\n...", "passport.png")
    assert response.location.endswith("/machine/passport")
    assert "The passport must be a PDF file." in client.get("/machine/passport").get_data(as_text=True)
    assert db.session.execute(db.select(MachineDocument)).first() is None


def test_the_operator_picks_the_pages(client, passport):
    _upload(client, passport)
    document = _document()
    client.post(f"/machine/passport/{document.id}", data={"page": ["3"]})
    assert _document().selected == [3]
    response = client.post(f"/machine/passport/{document.id}", data={}, follow_redirects=True)
    assert "Pick the pages with the machine" in response.get_data(as_text=True)
    assert _document().selected == [3]  # an empty pick changes nothing


def test_machine_page_links_to_the_passport(client):
    assert "/machine/passport" in client.get("/machine").get_data(as_text=True)
