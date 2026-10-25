"""Cutting data rows read from a catalogue page's text by code (catalogue_text: no model, no call). The pages are
synthetic, laid out as the Sandvik 2020 text layer gives them (one cell a line)."""
import io
import json

import pymupdf

from turnpilot import catalogue_text as ct
from turnpilot.models import CatalogueDocument, CuttingDataRow, Material, db

# TT A278 as its text layer: the ISO M part (grade columns with feeds; kc1 and HB before the cells)
TURNING = """A 278
GENERAL TURNING
Cutting data
Cutting speed recommendations
The recommendations are valid for use with cutting fluid.
ISO M
CMC
No.
Stainless steel
<<<< WEAR RESISTANCE
GC1115
GC2015
hex, mm ≈ feed fn, mm/r
0.1-0.2-0.3
0.2-0.4-0.6
MC No.
Material
N/mm2
HB
Cutting speed (vc), m/min
Austenitic
Bars/forged
M1.0.Z.AQ
05.21
Austenitic
1800
180
265-215-165
290-240-190
M1.0.Z.PH
05.22
PH-hardened
2850
330
185-150-120
130-100-80
M1.0.C.UT
15.21
Austenitic
1700
180
280-225-170
-"""

# TT C73: a single Vc per grade, no feeds
THREADING = """C 73
Cutting data
THREAD TURNING
Cutting speed recommendations
ISO P
Hardness Brinell Grades
GC1125
H13A
MC No.
N/mm2
HB
Cutting speed (vc), m/min
ISO M
Bars/forged
Austenitic
M1.0.Z.AQ
05.21
Austenitic
1800
180
140
75"""

# TT A283: ap Rec / Min / Max then fn Rec / Min / Max after each code
GEOMETRY = """A 283
Cutting data
GENERAL TURNING
Recommended depth of cut and cutting feed
Insert
ap = mm
fn = mm/r
Rec
Min
Max.
Rec
Min
Max.
CNMG120408-MF
0.4
0.1
1.5
0.2
0.1
0.4
CNMG120408-MM
3
0.5
5.7
0.25
0.1
0.45
CNMG120408-XX
3
5
0.5
0.25
0.1
0.45"""

# SRT B80: no kc1, no grade on the page (the grade is on the 860-MM product pages); the inch table repeats it
DRILLING = """B 80
DRILLING
Cutting data
CoroDrill® 860-MM
Internal coolant supply
ISO
MC No.
Material
Hardness Brinell
Cutting speed (Vc) m/min
HB
M
(min-start-max)
M1.0.Z.AQ
Annealed/quenched
200
48 - 60 - 72
ISO
Cutting speed (Vc) ft/min
M1.0.Z.AQ
Annealed/quenched
200
157 - 197 - 236"""

PRODUCT = """B 37
CoroDrill® 860 solid carbide drill
For stainless steel
Ordering code
2214
DCON
860.1-0600-018A1-MM
860.1-0800-024A1-MM"""

MATERIALS = [("M1.0", 1800, 180)]


def by(rows, key):
    return {(r["grade"], r["application"]) if r["kind"] == "grade_vc" else r["insert_code"]: r for r in rows}


def test_the_material_row_is_the_one_with_its_kc1():
    mc, where = ct.full_code([("TT", {282: TURNING})], "M1.0", {1800})
    assert (mc, where) == ("M1.0.Z.AQ", "TT A278 (PDF 282)")  # not .PH (2850) or the cast .C.UT (1700)
    assert ct.full_code([("TT", {282: TURNING})], "M1.0", {2000})[0] is None
    rows, not_taken, _also = ct.read([TURNING], [1], "M1.0", "", MATERIALS, "TT")
    found = by(rows, None)
    assert set(found) == {("GC1115", "turning"), ("GC2015", "turning")} and not not_taken
    gc1115 = found["GC1115", "turning"]
    assert gc1115["vc_points"] == "0.1:265, 0.2:215, 0.3:165" and found["GC2015", "turning"]["vc_points"] == (
        "0.2:290, 0.4:240, 0.6:190")
    assert gc1115["material_group"] == "M1.0" and gc1115["page"] == "A278" and gc1115["pdf_page"] == 1
    assert gc1115["coolant"] is True and gc1115["quote"].startswith("M1.0.Z.AQ 05.21 Austenitic 1800 180 265-215-165")
    assert gc1115["checks"] == ["row M1.0.Z.AQ (CMC 05.21)"]


def test_a_material_of_another_hardness_gets_a_check():
    rows, _, _also = ct.read([TURNING], [1], "M1.0", "", [("M1.0", 1800, 200)], "TT")
    assert "HB 180 in the table, the material's 200" in rows[0]["checks"]


def test_threading_has_one_vc_per_grade_without_feeds():
    rows, not_taken, _also = ct.read([TURNING, THREADING], [2], "M1.0", "", MATERIALS, "TT")
    found = by(rows, None)
    assert found["GC1125", "threading"]["vc_points"] == "140" and found["H13A", "threading"]["vc_points"] == "75"
    assert found["GC1125", "threading"]["page"] == "C73" and not not_taken  # the row by kc1 found on page 1


def test_geometry_rows_only_for_the_codes_asked_for():
    rows, not_taken, _also = ct.read([GEOMETRY], [1], "M1.0", "CNMG 12 04 08-MM\nCNMG120408-XX", MATERIALS, "TT")
    found = by(rows, None)
    assert set(found) == {"CNMG120408-MM"}  # -MF not asked for
    mm = found["CNMG120408-MM"]
    assert (mm["ap_min"], mm["ap_rec"], mm["ap_max"], mm["f_min"], mm["f_rec"], mm["f_max"]) == (
        0.5, 3, 5.7, 0.1, 0.25, 0.45)
    assert mm["material_group"] == "M" and mm["quote"] == "CNMG120408-MM 3 0.5 5.7 0.25 0.1 0.45"
    assert [(x["code"], x["reason"]) for x in not_taken] == [
        ("CNMG120408-XX", "min / rec / max are not in order: not taken")]


def test_a_page_without_the_column_header_gives_no_geometry():
    assert ct.geometry_rows(GEOMETRY.replace("Max.", "Max"), 1, ["CNMG120408-MM"], "M1.0") == ([], [])


def test_drilling_min_start_max_with_the_grade_of_the_product_pages():
    texts = [DRILLING, PRODUCT]
    rows, not_taken, _also = ct.read(texts, [1], "M1.0", "", MATERIALS, "SRT", others=[("TT", [TURNING])])
    assert len(rows) == 1 and not not_taken  # the inch table is left out
    row = rows[0]
    assert (row["grade"], row["application"], row["vc_min"], row["vc_points"], row["vc_max"]) == (
        "2214", "drilling", 48, "60", 72)
    assert row["quote"] == "M1.0.Z.AQ Annealed/quenched 200 48 - 60 - 72"
    assert row["checks"] == [
        "grade 2214 from the MM drill pages (PDF 2–2): not on this page", "HB 200 in the table, the material's 180",
        "row M1.0.Z.AQ: the table gives no kc1, the row is the one with the material's kc1 on TT A278 (PDF 1)"]


def test_without_a_kc1_table_nothing_is_taken():
    rows, not_taken, _also = ct.read([DRILLING, PRODUCT], [1], "M1.0", "", MATERIALS, "SRT")
    assert rows == [] and not_taken[0]["reason"] == "no M1.0 row with kc1 1800 in the catalogues: not taken"


def test_a_row_that_does_not_match_the_header_is_not_taken():
    broken = TURNING.replace("265-215-165\n290-240-190", "265-215-165\nGC9999")
    rows, not_taken, _also = ct.read([broken], [1], "M1.0", "", MATERIALS, "TT")
    assert rows == [] and not_taken[0]["reason"] == "the row's cells do not match the header's grades: not taken"


# --- through the app: rows to check, no call --------------------------------------------------------------------

def catalogue_pdf(*texts):
    doc = pymupdf.open()
    for text in texts:
        page = doc.new_page(height=1400)
        for i, line in enumerate(text.splitlines()):
            page.insert_text((50, 30 + 14 * i), line, fontsize=9)
    return doc.tobytes()


def test_read_from_the_text_makes_rows_to_check(app, client):
    material = db.session.execute(db.select(Material).filter_by(name="AISI 304")).scalar_one()
    assert (material.catalogue_group, material.kc1, material.hardness_hb) == ("M1.0", 1800, 180)
    client.post("/cutting-data/catalogues", data={"title": "TT test", "catalogue": (
        io.BytesIO(catalogue_pdf(TURNING.replace("≈", "~"), GEOMETRY)), "tt.pdf")}, content_type="multipart/form-data")
    document = db.session.execute(db.select(CatalogueDocument)).scalar_one()
    client.post(f"/cutting-data/catalogues/{document.id}", data={"pages": "1, 2", "material_groups": "M1.0",
                                                                "codes": "CNMG 12 04 08-MM"})
    page = client.get(f"/cutting-data/catalogues/{document.id}").get_data(as_text=True)
    assert "Read from the text (no model)" in page
    response = client.post(f"/cutting-data/catalogues/{document.id}/read-text", follow_redirects=True)
    assert "3 row(s) read from the text, 0 not taken." in response.get_data(as_text=True)
    rows = db.session.execute(db.select(CuttingDataRow).filter_by(document_id=document.id)).scalars().all()
    assert {(r.kind, r.grade or r.insert_code, r.origin, r.status) for r in rows} == {
        ("grade_vc", "GC1115", "page_text", "read"), ("grade_vc", "GC2015", "page_text", "read"),
        ("geometry", "CNMG120408-MM", "page_text", "read")}
    assert all(json.loads(r.checks or "[]") is not None for r in rows)
    client.post(f"/cutting-data/catalogues/{document.id}/read-text")  # again: the earlier rows set aside
    statuses = [r.status for r in db.session.execute(db.select(CuttingDataRow).filter_by(
        document_id=document.id)).scalars()]
    assert statuses.count("read") == 3 and statuses.count("rejected") == 3


# --- a page continuing a table without naming its rows (TT A279 after A278, SRT B81 after B80) ------------------------

# TT A279 as its text layer: more grade columns of A278's table, the cells row by row (the rows: A278's)
TURNING_ON = """A 279
Cutting data
GENERAL TURNING
Cutting speed recommendations
TOUGHNESS >>>>
GC2025
GC2035
0.2-0.4-0.6
0.2-0.4-0.6
190-145-110
170-145-115
100-70-45
85-65-45
-
95-70-50"""

def test_a_continuation_is_read_with_the_page_it_continues():
    rows, not_taken, also = ct.read([TURNING, TURNING_ON], [1], "M1.0", "", MATERIALS, "TT")
    assert also == [(2, 1)] and not not_taken
    found = by([r for r in rows if r["pdf_page"] == 2], None)
    assert set(found) == {("GC2025", "turning"), ("GC2035", "turning")}
    gc2025 = found["GC2025", "turning"]
    assert gc2025["vc_points"] == "0.2:190, 0.4:145, 0.6:110" and gc2025["page"] == "A279"
    assert gc2025["quote"] == "GC2025 0.2-0.4-0.6 190-145-110" and gc2025["coolant"] is True
    assert gc2025["checks"] == ["row 1 of the table on A278 (M1.0.Z.AQ): A279 names no rows, the row is taken by its "
                                "place"]
    rows, _, also = ct.read([TURNING, TURNING_ON], [1, 2], "M1.0", "", MATERIALS, "TT")
    assert also == [] and len([r for r in rows if r["pdf_page"] == 2]) == 2  # picked: read, not "also"
    rows, _, _ = ct.read([TURNING, TURNING_ON], [1], "M1.0", "", [("M1.0", 2850, 330)], "TT")  # the PH row: 2nd
    assert by([r for r in rows if r["pdf_page"] == 2], None)["GC2025", "turning"]["vc_points"] == "0.2:100, 0.4:70, 0.6:45"
    rows, _, _ = ct.read([TURNING, TURNING_ON], [1], "M1.0", "", [("M1.0", 1700, 180)], "TT")  # the cast row: 3rd
    assert [r["grade"] for r in rows if r["pdf_page"] == 2] == ["GC2035"]  # "-": no value, left out


def test_a_continuation_whose_cells_do_not_fit_is_not_taken():
    short = TURNING_ON.replace("-\n95-70-50", "95-70-50")  # 5 cells: not 3 rows × 2 columns
    rows, not_taken, _ = ct.read([TURNING, short], [1], "M1.0", "", MATERIALS, "TT")
    assert [r for r in rows if r["pdf_page"] == 2] == []
    assert not_taken[0]["reason"] == ("no block of A279 has as many cells as the section of M1.0.Z.AQ on A278 "
                                      "(rows × columns): not taken")


def test_a_continuation_that_fits_two_sections_is_not_taken():
    page = TURNING + "\nISO K\nK1.1.C.NS\n07.2\nPearlitic\n900\n230\n1-1-1\n1-1-1\nK2.1.C.UT\n08.1\nGrey\n890\n180" \
        "\n1-1-1\n1-1-1\nK2.2.C.UT\n08.2\nGrey\n970\n220\n1-1-1\n1-1-1"
    assert [len(s) for s in ct.table_sections(page)] == [3, 3]
    rows, not_taken, _ = ct.read([page, TURNING_ON], [1], "M1.0", "", MATERIALS, "TT")
    assert [r for r in rows if r["pdf_page"] == 2] == []
    assert not_taken[0]["reason"] == "6 cells in 2 columns fit 2 sections of A278: not taken"


def test_the_page_says_what_continuation_was_read_too(app, client):
    client.post("/cutting-data/catalogues", data={"title": "TT test", "catalogue": (
        io.BytesIO(catalogue_pdf(TURNING.replace("≈", "~"), TURNING_ON)), "tt.pdf")}, content_type="multipart/form-data")
    document = db.session.execute(db.select(CatalogueDocument)).scalar_one()
    client.post(f"/cutting-data/catalogues/{document.id}", data={"pages": "1", "material_groups": "M1.0", "codes": ""})
    page = client.post(f"/cutting-data/catalogues/{document.id}/read-text", follow_redirects=True).get_data(as_text=True)
    assert "4 row(s) read from the text, 0 not taken." in page
    assert "Also read: A279 (continuation of A278)." in page
    rows = db.session.execute(db.select(CuttingDataRow).filter_by(page="A279")).scalars().all()
    assert sorted(r.grade for r in rows) == ["GC2025", "GC2035"] and all(r.status == "read" for r in rows)
