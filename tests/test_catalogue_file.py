"""Roadmap stage 5, step 2: the hand-typed catalogue file (docs/turnpilot_catalog_P1.2.md) as rows to check."""
from pathlib import Path

import pytest

from turnpilot import catalogue_file as cf
from turnpilot.models import CuttingDataRow, db

DOCS = Path(__file__).parent.parent / "docs"
P12 = "turnpilot_catalog_P1.2.md"


@pytest.fixture(scope="module")
def p12_rows():
    return cf.parse_hand_typed((DOCS / P12).read_text(encoding="utf-8"), P12)


def find(rows, kind, key, application=None):
    return [r for r in rows if r["kind"] == kind and (r.get("insert_code") == key or r.get("grade") == key)
            and (application is None or r.get("application") == application)]


def test_cells():
    assert cf.parse_triple("0.6 – 0.96 – 3.6") == (dict(min=0.6, rec=0.96, max=3.6), False)
    assert cf.parse_triple("~0.07 стартова (діапазон ~0.04–0.14)") == (dict(rec=0.07, min=0.04, max=0.14), True)
    assert cf.parse_triple("~0.1 стартова (графік)") == (dict(rec=0.1), True)
    assert cf.parse_triple("проходи за таблицею C77") == ({}, False)
    assert cf.parse_triple("= крок") == ({}, False) and cf.parse_triple("—") == ({}, False)
    assert cf.parse_vc("455 / 305 / 215", [0.1, 0.4, 0.8]) == "0.1:455, 0.4:305, 0.8:215"
    assert cf.parse_vc("GC4315: 510 / 365 / 265", [0.1, 0.4, 0.8]) == "0.1:510, 0.4:365, 0.8:265"
    assert cf.parse_vc("315 → 140 (f 0.05 → 0.5)", [0.1, 0.4, 0.8]) == "0.05:315, 0.5:140"
    assert cf.parse_vc("195", [0.1, 0.4, 0.8]) == "195"
    assert cf.parse_vc("за сплавом, A278–A279", [0.1, 0.4, 0.8]) is None
    assert cf.parse_grade("GC4315 ★ (GC4325 ☆)") == "GC4315" and cf.parse_grade("звірити ★ на A45") is None
    assert cf.parse_codes("266RG-16VM01A001M (крок 1–2) / A002M (1.5–3)") == [
        ("266RG-16VM01A001M", (1.0, 2.0)), ("266RG-16VM01A002M", (1.5, 3.0))]
    assert cf.parse_codes("DNMG 15 06 04-PF (замість 150404: PF у розмірі 15 04 04 у каталозі немає)") == [
        ("DNMG150604-PF", None)]
    assert cf.parse_source("TT A283 (ap/f), A278–A279 (Vc)") == (cf.CATALOGUES["TT"], ["A283"], ["A278–A279"])
    assert cf.parse_source("TT B11 (код), B139 (f, графік), B130 (Vc)") == (cf.CATALOGUES["TT"], ["B139"], ["B130"])
    assert cf.parse_source("SRT B26, B71") == (cf.CATALOGUES["SRT"], ["B26", "B71"], ["B26", "B71"])


def test_p12_file_gives_the_seed_turret_values(p12_rows):
    """The rows of the file carry the numbers the seed tools T1, T2, T4–T12 were given from it."""
    (t1,) = find(p12_rows, "geometry", "SCMT120408-PM")
    assert (t1["ap_min"], t1["ap_rec"], t1["ap_max"], t1["f_min"], t1["f_rec"], t1["f_max"]) == (
        0.6, 0.96, 3.6, 0.12, 0.25, 0.37)
    assert t1["page"] == "A290" and t1["material_group"] == "P1.2" and t1["origin"] == "hand_typed"
    (gc4325,) = find(p12_rows, "grade_vc", "GC4325", "turning")
    assert gc4325["vc_points"] == "0.1:455, 0.4:305, 0.8:215" and gc4325["coolant"] is True
    (t5,) = find(p12_rows, "geometry", "CCMT09T304-PM")
    assert (t5["ap_rec"], t5["f_rec"]) == (0.64, 0.15)
    (t6,) = find(p12_rows, "geometry", "N123G2-0300-0003-GM")
    assert (t6["f_min"], t6["f_rec"], t6["f_max"], t6["from_graph"]) == (0.04, 0.07, 0.14, True)
    assert t6["checks"] == [cf.GRAPH_CHECK]
    (grooving,) = find(p12_rows, "grade_vc", "GC4325", "grooving")
    assert grooving["vc_points"] == "0.05:315, 0.5:140"
    (t7,) = find(p12_rows, "geometry", "266RG-16VM01A001M")
    assert (t7["f_min"], t7["f_max"], t7.get("ap_rec")) == (1.0, 2.0, None)
    (threading,) = find(p12_rows, "grade_vc", "GC1125", "threading")
    assert threading["vc_points"] == "195"
    (parting,) = find(p12_rows, "grade_vc", "GC1125", "parting")
    assert parting["vc_points"] == "0.05:265, 0.5:115"
    (drill,) = find(p12_rows, "grade_vc", "X1BM", "drilling")
    assert (drill["vc_min"], drill["vc_points"], drill["vc_max"], drill["page"]) == (100, "125", 150, "B70")
    assert drill["catalogue"] == cf.CATALOGUES["SRT"]
    (d8,) = find(p12_rows, "geometry", "860.1-0800-025A0-GM")
    assert (d8["f_min"], d8["f_rec"], d8["f_max"]) == (0.16, 0.22, 0.28)


def test_p12_file_rows_are_merged_without_conflicts(p12_rows):
    """CNMG-PM and GC4325 appear in sections 0 and 1 with the same numbers: one row each, no conflict."""
    assert len(find(p12_rows, "geometry", "CNMG120408-PM")) == 1
    assert not [c for r in p12_rows for c in r["checks"] if "other values" in c]
    # grades not decided in the file ("звірити") give no Vc row; their geometry is still there
    assert find(p12_rows, "geometry", "VNMG160404-PF")
    assert {r["grade"] for r in p12_rows if r["kind"] == "grade_vc"} == {"GC4325", "GC4315", "GC1125", "X1BM"}


def test_conflicting_lines_are_both_kept_with_a_check():
    text = """# Test для сталі P1.2
## 1. Turning
| Роль | Пластина | Сплав | ap, мм | f, мм/об | Vc, м/хв при f 0.1 / 0.4 | Source |
|---|---|---|---|---|---|---|
| a | CNMG 12 04 08-PM | GC4325 | 0.5 – 3 – 5.5 | 0.15 – 0.3 – 0.5 | 455 / 305 | TT A283 |
| b | CNMG 12 04 08-PM | GC4325 | 0.5 – 2 – 5.5 | 0.15 – 0.3 – 0.5 | 455 / 305 | TT A283 |
"""
    rows = cf.parse_hand_typed(text, "test.md")
    geometry = find(rows, "geometry", "CNMG120408-PM")
    assert [r["ap_rec"] for r in geometry] == [3, 2]
    assert all("another line of the file gives other values" in r["checks"][0] for r in geometry)
    assert len(find(rows, "grade_vc", "GC4325")) == 1


def test_a_file_without_a_material_group_is_refused():
    with pytest.raises(ValueError, match="no material group"):
        cf.parse_hand_typed("# Catalogue notes\n", "x.md")


def test_import_adds_rows_to_check_once(app, client):
    page = client.get("/cutting-data").get_data(as_text=True)
    assert P12 in page
    response = client.post("/cutting-data/import", data={"filename": P12}, follow_redirects=True)
    rows = db.session.execute(db.select(CuttingDataRow)).scalars().all()
    assert rows and {r.status for r in rows} == {"read"} and {r.origin for r in rows} == {"hand_typed"}
    assert f"{len(rows)} row(s) to check added; 0 already there." in response.get_data(as_text=True)
    response = client.post("/cutting-data/import", data={"filename": P12}, follow_redirects=True)
    assert f"0 row(s) to check added; {len(rows)} already there." in response.get_data(as_text=True)
    assert db.session.execute(db.select(db.func.count(CuttingDataRow.id))).scalar() == len(rows)


def test_import_only_takes_files_from_the_list(app, client):
    response = client.post("/cutting-data/import", data={"filename": "../README.md"}, follow_redirects=True)
    assert "No such file" in response.get_data(as_text=True)
    assert db.session.execute(db.select(db.func.count(CuttingDataRow.id))).scalar() == 0
