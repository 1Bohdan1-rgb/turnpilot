"""Tools found in a catalogue's text layer (catalogue_tools): synthetic pages laid out like a product table's text
(own numbers, no catalogue's)."""
from turnpilot import catalogue_tools as ct

DRILL_PAGE = """B 12
DRILLING
CoroDrill® 860 solid carbide drill
For steel
Internal coolant supply
SIG
140°
P Dimensions, mm, inch
DC
DC"
LU
LU"
ULDR
CZCMS Ordering code
4234
DCONMS
DCONMS"
OAL
19.00
.748
60.0
2.362
3
20
860.1-1900-055A1-PM
★
20.0
.787
131
DIN 6537 K
20.00
.787
63.0
2.480
3
20
860.1-2000-055A1-PM
★
20.0
.787
131
DIN 6537 K
20.00
.787
90.0
3.543
4
20
860.1-2000-080A1-PM
★
20.0
.787
150
DIN 6537 L
21.00
.800
63.0
2.480
3
20
860.1-2100-055A1-PM
★
"""

BAR_PAGE = """A 99
Internal tools
CoroTurn® 107 boring bar for turning
KAPR
95.0°
Dimensions, mm
CZCMS
DMIN1
OHX
OHN
CNSC
Ordering code
DCONMS
09
16
20.0
64.0
24.0
1
A16R-SCLCR/L 09-R
16.0
CCMT 09 T3 08
20
25.0
80.0
30.0
1
A20S-SCLCR/L 09-R
20.0
CCMT 09 T3 08
"""

GROOVE_PAGE = """B 6
CoroCut® 1-2 insert for parting
Dimensions,
mm
SSC
CW
REL
RER
CDX
PSIRL
PSIRR
Ordering code
1125
E
2.00
0.20
0.20
19.0
5°
L123E2-0200-0502-CM
★
7°
E
2.00
0.20
0.20
19.0
N123E2-0200-0002-CM
★
G
3.00
0.20
0.20
18.9
N123G2-0300-0002-CM
✩
"""


def docs(*pages):
    return [(1, "Test catalogue", {"texts": list(pages), "labels": [""] * len(pages)})]


def test_drill_rows_check_against_their_code():
    found, why = ct.read_page(DRILL_PAGE, 7, "Test catalogue", kinds=("drilling",))
    codes = [f.code for f in found]
    assert codes == ["860.1-1900-055A1-PM", "860.1-2000-055A1-PM", "860.1-2000-080A1-PM"]
    assert why == ['860.1-2100-055A1-PM: DC 21 / DC" / LU" do not check against the code and each other']
    f = found[1]
    assert f.values == {"diameter": 20.0, "max_depth": 63.0, "depth_ratio": 3.0, "point_angle": 140.0,
                        "coolant": "internal", "grade": "4234"}
    assert f.sources["grade"] == "grade column 4234"
    assert f.sources["max_depth"] == "LU 63.0 (20.00 .787 63.0 2.480 3 20 860.1-2000-055A1-PM)"
    assert f.sources["point_angle"] == "SIG 140°" and f.sources["coolant"] == "Internal coolant supply"
    assert f.where == "Test catalogue, B12 (PDF 7)"
    assert f.series == "CoroDrill® 860 solid carbide drill / For steel"


def test_drills_by_size_and_depth():
    fit, search = ct.drills(docs(DRILL_PAGE), 19.9, 21.3, 64)
    assert [f.code for f in fit] == ["860.1-2000-080A1-PM"]  # LU 63 does not reach 64
    fit, _ = ct.drills(docs(DRILL_PAGE), 18, 21.3, 35)
    assert [f.code for f in fit] == ["860.1-2000-055A1-PM", "860.1-2000-080A1-PM", "860.1-1900-055A1-PM"]
    assert search.pages == 1 and len(search.not_taken) == 1
    fit, _ = ct.drills(docs(DRILL_PAGE), 25, 30, None)
    assert fit == []


def test_page_value_only_when_stated_once():
    two = DRILL_PAGE.replace("SIG\n140°", "SIG\n140°\nSIG\n118°")
    (f, *_), _ = ct.read_page(two, 1, "T", kinds=("drilling",))
    assert "point_angle" not in f.values
    assert ct.page_value(two, "SIG") == (None, "SIG has 2 values on the page")


def test_boring_bars():
    found, why = ct.read_page(BAR_PAGE, 3, "T", kinds=("boring",))
    assert [f.code for f in found] == ["A16R-SCLCR/L 09-R", "A20S-SCLCR/L 09-R"] and why == []
    assert found[0].values == {"dmin": 20.0, "max_overhang": 64.0, "min_overhang": 24.0, "entering_angle": 95.0}
    assert found[0].sources["dmin"] == "DMIN1 20.0 (16 20.0 64.0 24.0 1 A16R-SCLCR/L 09-R)"
    fit, _ = ct.boring_bars(docs(BAR_PAGE), 22, 32)
    assert [f.code for f in fit] == ["A16R-SCLCR/L 09-R"]  # DMIN 25 does not go into Ø22
    fit, _ = ct.boring_bars(docs(BAR_PAGE), 30, 70)
    assert [f.code for f in fit] == ["A20S-SCLCR/L 09-R"]  # OHX 64 does not reach 70


def test_a_bar_row_that_does_not_check():
    wrong = BAR_PAGE.replace("09\n16\n20.0", "09\n12\n20.0")
    found, why = ct.read_page(wrong, 3, "T", kinds=("boring",))
    assert [f.code for f in found] == ["A20S-SCLCR/L 09-R"]
    assert why == ["A16R-SCLCR/L 09-R: the row does not check (CZCMS against the bar's Ø in the code)"]


def test_grooving_inserts_from_their_seat_letter():
    found, why = ct.read_page(GROOVE_PAGE, 9, "T", kinds=("grooving",))
    assert [f.code for f in found] == ["L123E2-0200-0502-CM", "N123E2-0200-0002-CM", "N123G2-0300-0002-CM"]
    assert found[1].values == {"width": 2.0, "max_depth": 19.0}
    assert found[1].sources["width"] == "CW 2.00 (E 2.00 0.20 0.20 19.0 N123E2-0200-0002-CM)"
    fit, _ = ct.grooving_inserts(docs(GROOVE_PAGE), 2.5, 10)
    assert [f.code for f in fit] == ["L123E2-0200-0502-CM", "N123E2-0200-0002-CM"]


def test_no_table_no_tools():
    assert ct.read_page("A 1\nSome text without a table\n860.1-2000-055A1-PM", 1, "T") == ([], [])


# --- the pages and adding to the library ---------------------------------------------------------------------------

import json  # noqa: E402

import pytest  # noqa: E402

from turnpilot import services  # noqa: E402
from turnpilot.models import CatalogueDocument, Tool, db  # noqa: E402


@pytest.fixture
def catalogue(app):
    document = CatalogueDocument(original_filename="srt.pdf", stored_filename="t.pdf", title="Test SRT", sha256="x" * 64,
                                 size_bytes=1, pages=3, text_pages=3)
    db.session.add(document)
    db.session.commit()
    path = services.catalogue_path(document, app.instance_path) + ".pages.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"texts": [DRILL_PAGE, BAR_PAGE, GROOVE_PAGE], "labels": ["", "", ""]}, f)
    services._CATALOGUE_TEXTS.clear()
    return document


def test_search_page(client, catalogue):
    page = client.get("/catalogue-tools?kind=drilling&a=19.9&b=21.3&depth=35").get_data(as_text=True)
    assert "860.1-2000-055A1-PM" in page and "860.1-2000-080A1-PM" in page and "860.1-1900" not in page
    assert "Test SRT, B12 (PDF 1)" in page and 'title="SIG 140°"' in page
    page = client.get("/catalogue-tools?kind=drilling&a=30&b=31").get_data(as_text=True)
    assert "Not found: no row of the uploaded catalogues fits and checks" in page


def _drill_form(**changes):
    form = {"name": "Drill 860-PM Ø20", "added_by": "Bohdan", "type": "drilling", "iso_group": ["P"],
            "vc_min": "100", "vc_max": "150", "f_min": "0.2", "f_max": "0.3", "source": "the operator's, test"}
    form.update(changes)
    return form


def test_add_a_drill_with_its_sources(client, catalogue):
    url = f"/catalogue-tools/add?document={catalogue.id}&page=1&code=860.1-2000-055A1-PM"
    page = client.get(url).get_data(as_text=True)
    assert "LU 63.0 (20.00 .787 63.0 2.480 3 20 860.1-2000-055A1-PM)" in page
    assert "internal coolant supply: the machine must give coolant through the tool" in page
    assert "No confirmed catalogue row for this code / grade" in page
    client.post(url, data=_drill_form(name=""))
    assert db.session.execute(db.select(Tool).filter_by(insert_code="860.1-2000-055A1-PM")).scalar() is None
    client.post(url, data=_drill_form(added_by=""))
    assert db.session.execute(db.select(Tool).filter_by(insert_code="860.1-2000-055A1-PM")).scalar() is None
    response = client.post(url, data=_drill_form())
    assert response.status_code == 302
    tool = db.session.execute(db.select(Tool).filter_by(insert_code="860.1-2000-055A1-PM")).scalar_one()
    assert (tool.type, tool.diameter, tool.max_depth, tool.point_angle_deg, tool.ap_min, tool.ap_max, tool.grade) == (
        "drilling", 20, 63, 140, 10, 10, "4234")
    assert tool.max_depth_source == ("Test SRT, B12 (PDF 1), LU 63.0 (20.00 .787 63.0 2.480 3 20 "
                                     "860.1-2000-055A1-PM)")
    assert tool.point_angle_source == "Test SRT, B12 (PDF 1), SIG 140°"
    ref = json.loads(tool.catalogue_ref)
    assert ref["added_by"] == "Bohdan" and ref["pdf_page"] == 1 and ref["sources"]["diameter"].startswith("DC 20.00")


def test_add_prefills_confirmed_rows(client, catalogue):
    from datetime import datetime, timezone

    from turnpilot.models import CuttingDataRow, Job, Material
    steel = db.session.execute(db.select(Material).filter_by(name="Steel 45 (C45)")).scalar_one()
    job = Job(name="J", material=steel, quantity=1, blank_diameter=40, blank_length=60)
    db.session.add_all([job, CuttingDataRow(kind="geometry", catalogue="Test SRT", insert_code="860.1-2000-055A1-PM",
                                            material_group="P1.2", f_min=0.25, f_rec=0.3, f_max=0.35, page="B76",
                                            origin="model", status="confirmed", confirmed_at=datetime.now(timezone.utc)),
                        CuttingDataRow(kind="grade_vc", catalogue="Test SRT", grade="4234", application="drilling",
                                       material_group="P1.2", vc_min=110, vc_max=160, vc_points="135", page="B76",
                                       origin="model", status="confirmed", confirmed_at=datetime.now(timezone.utc))])
    db.session.commit()
    page = client.get(f"/catalogue-tools/add?document={catalogue.id}&page=1&code=860.1-2000-055A1-PM&job={job.id}"
                      ).get_data(as_text=True)
    assert "From the confirmed catalogue rows for P1.2: ap / f Test SRT B76; Vc Test SRT B76" in page
    assert 'name="f_rec" type="number" step="any" min="0" value="0.3"' in page and 'value="135"' in page


def test_add_a_boring_bar_needs_its_insert(client, catalogue):
    url = f"/catalogue-tools/add?document={catalogue.id}&page=2&code=A16R-SCLCR/L 09-R"
    form = _drill_form(name="Boring bar A16R", type="boring", ap_min="0.1", ap_max="2")
    client.post(url, data=form)
    assert db.session.execute(db.select(Tool).filter_by(holder_code="A16R-SCLCR/L 09-R")).scalar() is None
    client.post(url, data={**form, "insert_code": "CCMT09T304-PF", "max_depth": "70"})  # above OHX 64
    assert db.session.execute(db.select(Tool).filter_by(holder_code="A16R-SCLCR/L 09-R")).scalar() is None
    client.post(url, data={**form, "insert_code": "CCMT09T304-PF", "max_depth": "50"})
    bar = db.session.execute(db.select(Tool).filter_by(holder_code="A16R-SCLCR/L 09-R")).scalar_one()
    assert (bar.min_bore_diameter, bar.entering_angle_deg, bar.max_depth, bar.insert_code) == (20, 95, 50,
                                                                                               "CCMT09T304-PF")
    assert bar.min_bore_source.startswith("Test SRT, A99 (PDF 2), DMIN1 20.0")


def test_a_code_not_on_its_page(client, catalogue):
    assert client.get(f"/catalogue-tools/add?document={catalogue.id}&page=2&code=860.1-2000-055A1-PM").status_code == 404


def test_setup_card_names_catalogue_drills(client, catalogue):
    from datetime import datetime, timezone

    from gcode_jobs import make_job, ready_machine

    from turnpilot.models import Feature
    machine = ready_machine()
    bore = Feature(type="bore", diameter=22, length=32, tolerance="+0.21", bore_open_end="right", bore_from_mm=0,
                   bore_bottom="drill_point", bore_position_source="dxf", bore_position_at=datetime.now(timezone.utc))
    job = make_job([Feature(type="face"), Feature(type="od_turn", diameter=34.8, length=80), bore,
                    Feature(type="parting")], free_end="right", blank=38, length=90, stickout=95, machine=machine)
    needs = services.catalogue_needs(job, machine, client.application.instance_path)
    texts = [n.what for n in needs]
    assert texts[0] == ("a drill Ø22 makes the bore Ø22 × 32 at once (its tolerance and roughness need no boring)")
    assert needs[0].found == []  # the test page has no Ø22
    assert texts[1].startswith("a drill up to Ø21.3 (Ø22 less the boring bar's finishing pass) for the bore Ø22 × 32: "
                               "larger than the planned Ø10")
    assert [f.code for f in needs[1].found] == ["860.1-2000-055A1-PM", "860.1-2000-080A1-PM", "860.1-1900-055A1-PM"]
    page = client.get(f"/jobs/{job.id}").get_data(as_text=True)
    assert "From the uploaded catalogues (not in the library)" in page and "860.1-2000-055A1-PM" in page
    assert f"code=860.1-2000-055A1-PM&amp;job={job.id}" in page
    client.post(f"/catalogue-tools/add?document={catalogue.id}&page=1&code=860.1-2000-055A1-PM&job={job.id}",
                data=_drill_form())
    needs = services.catalogue_needs(job, machine, client.application.instance_path)
    assert "860.1-2000-055A1-PM" not in [f.code for n in needs for f in n.found]  # in the library now
    (tool, why), = needs[1].in_library
    assert tool.name == "Drill 860-PM Ø20" and "no confirmed catalogue ap / f for 860.1-2000-055A1-PM" in why
    assert "In the library already: Drill 860-PM Ø20" in client.get(f"/jobs/{job.id}").get_data(as_text=True)
