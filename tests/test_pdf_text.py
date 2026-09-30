"""Numbers read from the vector text of a drawing PDF (turnpilot/pdf_text.py)."""
import pymupdf
import pytest
from conftest import FIXTURES

from turnpilot.pdf_text import _attach_symbols, normalize, parse_numbers, read_numbers


def _kinds(numbers):
    return sorted((n.kind, n.value) for n in numbers)


def test_symbol_font_codes_are_mapped():
    assert normalize("Ç", "HPDFAB+Symbol_A") == "Ø"
    assert normalize("Å", "HPDFAB+Symbol_A") == "°"
    assert normalize("Ç", "GOSTTypeA") == "Ç"  # only in the symbol font


def test_cyrillic_look_alikes_and_times_sign():
    assert normalize("M14-7Н") == "M14-7H"  # Cyrillic Н (U+041D)
    assert normalize("2,5•45") == "2,5×45"


@pytest.mark.parametrize("text, expected", [
    ("Ø36 0/-0,016", [("diameter", 36)]),
    ("Ø80 +0,5/-1,3", [("diameter", 80)]),
    ("Ø30 H7 THRU", [("diameter", 30)]),
    ("90js12(±0,175)", [("linear", 90)]),
    ("3±0,1", [("linear", 3)]),
    ("44*", [("linear", 44)]),
    ("Rz 40(", [("roughness", 40)]),
    ("Ra 1,6", [("roughness", 1.6)]),
    ("M20x1.5-6g", [("thread", 20), ("thread_pitch", 1.5)]),
    ("M10", [("thread", 10)]),
    ("2,5×45°", [("chamfer", 2.5)]),
    ("R10±0,05", [("radius", 10)]),
    ("S14", [("across_flats", 14)]),
    ("Groove 3 x Ø17", [("linear", 3), ("diameter", 17)]),
    ("ISO 2768-m", []),
    ("50 pcs", []),
    ("Break sharp edges 0.3", []),
    ("1 *Размер для справок", []),
])
def test_parse_numbers(text, expected):
    assert [(n.kind, n.value) for n in parse_numbers(text)] == expected


def test_diameter_sign_drawn_as_its_own_span_joins_the_nearest_number():
    spans = [("20", "GOSTTypeA", (74, 290, 88, 318), 20.7), ("Ø", "Symbol_A", (71, 318, 86, 330), 22.6),
             ("44", "GOSTTypeA", (270, 460, 290, 475), 20.7)]
    assert _attach_symbols(spans) == [("Ø20", (74, 290, 88, 318)), ("44", (270, 460, 290, 475))]


def test_synthetic_drawing_pdf():
    assert _kinds(read_numbers(FIXTURES / "02_threaded_shaft.pdf")) == [
        ("chamfer", 1.0), ("diameter", 17.0), ("diameter", 30.0), ("linear", 3.0), ("linear", 25.0),
        ("linear", 30.0), ("linear", 60.0), ("linear", 90.0), ("roughness", 1.6), ("thread", 20.0),
        ("thread_pitch", 1.5),
    ]  # the title block (scale 3:2, 50 pcs) and the notes are left out


def _pdf(tmp_path, texts):
    doc = pymupdf.open()
    page = doc.new_page(width=842, height=595)
    for x, y, text in texts:
        page.insert_text((x, y), text, fontsize=10)
    path = tmp_path / "drawing.pdf"
    doc.save(path)
    return path


def test_each_drawn_copy_is_read_once(tmp_path):
    path = _pdf(tmp_path, [(100, 100, "12"), (100, 100, "12"), (200, 100, "12")])
    assert _kinds(read_numbers(path)) == [("linear", 12.0), ("linear", 12.0)]


def test_title_block_numbers_are_left_out(tmp_path):
    path = _pdf(tmp_path, [(100, 100, "44"), (800, 580, "0,1"), (780, 570, "2:1")])
    assert _kinds(read_numbers(path)) == [("linear", 44.0)]


@pytest.mark.parametrize("name", ["01_stepped_shaft.png", "01_stepped_shaft.photo.jpg"])
def test_images_give_none(name):
    assert read_numbers(FIXTURES / name) is None


def test_pdf_without_text_gives_none(tmp_path):
    doc = pymupdf.open()
    doc.new_page().draw_line((10, 10), (100, 100))  # a scan or a drawing with text as curves
    path = tmp_path / "scan.pdf"
    doc.save(path)
    assert read_numbers(path) is None


def test_damaged_or_missing_pdf_gives_none(tmp_path):
    broken = tmp_path / "broken.pdf"
    broken.write_bytes(b"%PDF-1.4 this is not a pdf")
    assert read_numbers(broken) is None
    assert read_numbers(tmp_path / "missing.pdf") is None
