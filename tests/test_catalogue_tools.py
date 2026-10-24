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
                        "coolant": "internal"}
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
