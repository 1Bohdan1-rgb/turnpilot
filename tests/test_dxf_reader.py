import pytest

import dxf_drawings as dd
from turnpilot import dxf_reader
from turnpilot.dxf_reader import DxfReadError, read_dxf


def _sections(part):
    return [(s.kind, s.size(), s.x0, s.x1) for s in part.sections]


def _bindings(reading, part):
    return {d.text: (d.kind, d.binding, d.flags) for d in reading.dims_of(part)}


@pytest.fixture
def two_parts(tmp_path):
    return read_dxf(dd.write(tmp_path / "two.dxf", dd.draw_shaft, dd.draw_pin))


def test_shaft_profile(tmp_path):
    reading = read_dxf(dd.write(tmp_path / "shaft.dxf", dd.draw_shaft))
    (part,) = reading.parts
    assert part.axis_y == 0 and (part.x_min, part.x_max) == (0, 50)
    assert _sections(part) == [
        ("od_turn", "Ø40", 0, 20),
        ("groove", "Ø30", 20, 23),
        ("thread", "Ø36", 23, 50),  # the M in the dimension text makes it a thread
    ]
    assert part.sections[0].chamfers == [("left", 1.0, 0.0)]
    assert part.sections[2].chamfers == [("right", 1.5, 50.0)]
    assert part.boundaries == [0, 20, 23, 50]


def test_shaft_dimensions(tmp_path):
    reading = read_dxf(dd.write(tmp_path / "shaft.dxf", dd.draw_shaft))
    (part,) = reading.parts
    bound = _bindings(reading, part)
    assert bound["50"][1] == "x 0 → 50 (boundary 0 → boundary 3)"
    assert bound["3"][1] == "x 20 → 23 (boundary 1 → boundary 2)"
    assert bound["15"][1] == "x 0 → 15 (boundary 0 → inside section 1)"  # the bore depth
    assert bound["Ø40h12"][1] == "section 1"
    assert bound["M36×1,5-6g"][:2] == ("diameter", "section 3")  # KOMPAS codes and Cyrillic М decoded
    assert bound["1×45°"][:2] == ("chamfer", "chamfer of section 1 at x 0")
    assert bound["1,5×45°"][1] == "chamfer of section 3 at x 50"
    assert bound["Ø10H11"][1] == "inner"
    assert bound["Ø30,2"] == ("diameter", "section 2", ["geometry 30 ≠ text 30.2"])
    assert not any(flags for text, (_, _, flags) in bound.items() if text != "Ø30,2")


def test_pin_taper_transition_and_spherical_end(two_parts):
    pin = two_parts.parts[1]
    assert _sections(pin) == [
        ("od_turn", "Ø24", 0, 10),
        ("taper", "Ø24→Ø16", 10, 20),
        ("od_turn", "Ø16", 20, 28),
        ("od_turn", "Ø28", 28, 40),
        ("od_turn", "Ø18", 40, 50),
        ("arc", "R9", 50, 59),  # ends on the axis: a section, not a transition
    ]
    assert pin.sections[3].fillets == [("right", 2)]  # the R2 transition belongs to the Ø28 flat
    bound = _bindings(two_parts, pin)
    assert bound["Ø16"][1] == "section 3"  # on the taper / cylinder boundary: the cylinder
    assert bound["R2"][1] == "transition at x 40"
    assert bound["R9"][1] == "section 6 (arc)"


def test_two_parts_on_one_sheet_nearest_axis(two_parts):
    shaft, pin = two_parts.parts
    assert (shaft.axis_y, pin.axis_y) == (0, 100)
    assert len(two_parts.dims_of(shaft)) == 10 and len(two_parts.dims_of(pin)) == 10
    assert all(d.part is not None for d in two_parts.dims)
    assert not two_parts.unassigned


def test_dimension_off_the_contour_is_flagged(tmp_path):
    def draw(s):
        s.axis(-3, 33)
        s.vertical(0, 0, 10)
        s.line(0, 10, 30, 10)
        s.vertical(30, 0, 10)
        s.diameter(15, 21, f"{dd.DIA}21")  # drawn Ø20
        s.length(0, 30, "30")
    reading = read_dxf(dd.write(tmp_path / "off.dxf", draw))
    (dim,) = [d for d in reading.dims if d.kind == "diameter"]
    assert dim.binding == "section 1" and dim.flags == ["Ø21 ≠ contour Ø20 at x 15"]


def test_dimension_text_cleaning():
    assert dxf_reader.clean("{\\fSymbol_A;}22") == "Ø22"
    assert dxf_reader.clean("%%c30%%p0,1") == "Ø30±0,1"


def test_not_a_dxf(tmp_path):
    path = tmp_path / "bad.dxf"
    path.write_text("not a drawing", encoding="utf-8")
    with pytest.raises(DxfReadError, match="cannot be read"):
        read_dxf(path)


def test_no_kompas_linetypes(tmp_path):
    doc = dd.new_doc()
    msp = doc.modelspace()
    msp.add_line((0, 10), (30, 10))  # continuous: not a KOMPAS contour line
    path = tmp_path / "plain.dxf"
    doc.saveas(path)
    with pytest.raises(DxfReadError, match="Only KOMPAS-3D DXF"):
        read_dxf(path)


def test_no_centre_line(tmp_path):
    def draw(s):
        s.vertical(0, 0, 10)
        s.line(0, 10, 30, 10)
        s.vertical(30, 0, 10)
    with pytest.raises(DxfReadError, match="No centre line"):
        read_dxf(dd.write(tmp_path / "noaxis.dxf", draw))


def test_contour_not_symmetric_about_the_axis(tmp_path):
    def draw(s):
        s.axis(-3, 33)
        s._line((0, 5), (30, 5))
        s._line((30, 5), (30, 20))
        s._line((30, 20), (0, 20))
        s._line((0, 20), (0, 5))
    with pytest.raises(DxfReadError, match="No turned part"):
        read_dxf(dd.write(tmp_path / "offaxis.dxf", draw))
