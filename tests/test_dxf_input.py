import pytest

import dxf_drawings as dd
from turnpilot.dxf_input import parse_chamfer, parse_size, parse_thread, part_results
from turnpilot.dxf_reader import read_dxf


@pytest.fixture
def results(tmp_path):
    return part_results(read_dxf(dd.write(tmp_path / "two.dxf", dd.draw_shaft, dd.draw_pin)))


def _rows(result):
    out = []
    for feature, row in zip(result.data.features, result.report["rows"]):
        values = {k: v for k, v in feature.model_dump().items()
                  if v not in (None, False) and k not in ("ra_param", "length_derived")}
        out.append((row["section"], values))
    return out


def test_dimension_texts():
    assert parse_size("Ø22+0,21") == (22, "+0.21")
    assert parse_size("Ø40h12") == (40, "h12")
    assert parse_size("R2,5") == (2.5, None)
    assert parse_thread("M22×1,5-6g") == (22, 1.5, "6g")
    assert parse_thread("M24") == (24, None, None)
    assert parse_chamfer("1,5×45°") == 1.5


def test_shaft_rows(results):
    shaft = results[0]
    assert _rows(shaft) == [
        (1, {"type": "od_turn", "diameter": 40, "length": 20, "tolerance": "h12"}),
        (1, {"type": "chamfer", "diameter": 40, "length": 1, "location": "external", "face": "left"}),
        (2, {"type": "groove", "diameter": 30.2, "start_diameter": 36, "length": 3}),  # the text, not the geometry
        (3, {"type": "thread", "diameter": 36, "length": 27, "tolerance": "6g", "pitch": 1.5,
             "location": "external"}),
        (3, {"type": "chamfer", "diameter": 36, "length": 1.5, "location": "external", "face": "right"}),
    ]
    assert shaft.data.part_type == "turned" and shaft.data.overall_length == 50
    rows = shaft.report["rows"]
    assert rows[2]["dims"] == ["Ø30,2"] and rows[2]["notes"] == ["geometry 30 ≠ text 30.2"]
    # 20 = 50 − 27 − 3: no single dimension, so the length is marked
    assert shaft.data.features[0].length_derived
    assert rows[0]["notes"] == ["length computed from other dimensions (no dimension spans this section alone)"]
    assert not shaft.data.features[4].length_derived and rows[4]["notes"] == []
    assert any("Ø10H11" in w and "inner profile" in w for w in shaft.data.warnings)


def test_pin_rows(results):
    pin = results[1]
    assert _rows(pin) == [
        (1, {"type": "od_turn", "diameter": 24, "length": 10}),
        (2, {"type": "taper", "diameter": 16, "start_diameter": 24, "length": 10}),
        (3, {"type": "od_turn", "diameter": 16, "length": 8}),
        (4, {"type": "od_turn", "diameter": 28, "length": 12}),
        (4, {"type": "fillet", "radius": 2}),
        (5, {"type": "od_turn", "diameter": 18, "length": 10}),
        (6, {"type": "arc", "start_diameter": 18, "radius": 9, "length": 9}),  # spherical end: no Ø at the end
    ]
    notes = [row["notes"] for row in pin.report["rows"]]
    assert notes[1] == []  # both taper ends meet dimensioned cylinders (Ø24, Ø16)
    assert notes[5] == ["length from the geometry: no dimension ends at its boundary (x 50 from the left face)"]
    assert pin.report["label"] == "Part 2 of 2: Ø28 × 59, 6 sections"


def test_repeated_diameter_dimensioned_once(tmp_path):
    def draw(s):
        s.axis(-3, 33)
        s.vertical(0, 0, 10)
        s.line(0, 10, 10, 10)
        s.vertical(10, 8, 10)
        s.line(10, 8, 13, 8)
        s.vertical(13, 8, 10)
        s.line(13, 10, 30, 10)
        s.vertical(30, 0, 10)
        s.diameter(5, 20, f"{dd.DIA}20h9")
        s.diameter(11.5, 16, f"{dd.DIA}16")
        s.length(0, 30, "30")
        s.length(10, 13, "3", r_from=8)
    (result,) = part_results(read_dxf(dd.write(tmp_path / "rep.dxf", draw)))
    third = result.data.features[2]
    assert (third.diameter, third.tolerance) == (20, "h9")
    assert result.report["rows"][2]["notes"][0] == "Ø20h9 is dimensioned on section 1 (same diameter): check"


def test_report_lists_every_dimension(results):
    dims = results[0].report["dims"]
    assert len(dims) == 10
    inner = [d for d in dims if d["inner"]]
    assert [d["text"] for d in inner] == ["Ø10H11"] and not inner[0]["bound"]
    flagged = [d for d in dims if d["flags"]]
    assert [(d["text"], d["binding"]) for d in flagged] == [("Ø30,2", "section 2")]


def test_not_dimensioned_overall_length(tmp_path):
    def draw(s):
        s.axis(-3, 33)
        s.vertical(0, 0, 10)
        s.line(0, 10, 30, 10)
        s.vertical(30, 0, 10)
        s.diameter(15, 20, f"{dd.DIA}20")
    (result,) = part_results(read_dxf(dd.write(tmp_path / "nolen.dxf", draw)))
    assert result.data.overall_length == 30
    assert "overall length 30 from the geometry: not dimensioned, check" in result.data.warnings
