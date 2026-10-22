"""DXF files from other CAD systems: the producer is told from the file, and a SolidWorks sheet is rewritten the
KOMPAS way for the KOMPAS reader (frozen by dxf-blind-freeze, not changed)."""
import ezdxf
import pytest
from dxf_drawings import new_doc
from sw_drawings import half_section_sheet, sheet

from turnpilot import dxf_adapter, dxf_input, dxf_reader
from turnpilot.dxf_reader import DxfReadError


def test_detect_the_cad():
    assert dxf_adapter.detect_cad(new_doc()) == dxf_adapter.KOMPAS
    assert dxf_adapter.detect_cad(sheet()) == dxf_adapter.SOLIDWORKS
    assert dxf_adapter.detect_cad(ezdxf.new()) == dxf_adapter.UNKNOWN


def test_an_unknown_cad_is_named(tmp_path):
    path = tmp_path / "plain.dxf"
    ezdxf.new().saveas(path)
    with pytest.raises(DxfReadError, match="unknown CAD system"):
        dxf_adapter.prepare(str(path))


@pytest.mark.parametrize("pieces, text", [
    (["10", "", "", "-", "0,0", "0,2"], "10-0,2"),  # 03057789-1-01: 10 with 0 / −0.2
    (["%%c", "40", "h6", "", "-", "0,000", "0,016"], "Ø40h6"),  # a fit: its deviations are implied
    (["1", r"\U+044545", "%%d", r"2 \U+0444\U+0430\U+0441\U+043A\U+0438"], "1×45° 2 фаски"),
    ([r"\U+041C", "20", "-6g"], "М20-6g"),
    (["8", "+3"], "8+3"),
    (["10", "±1"], "10±1"),
])
def test_dimension_texts(pieces, text):
    assert dxf_adapter.dimension_text(pieces)[0] == text


def test_a_reference_dimension():
    assert dxf_adapter.dimension_text(["%%c", "8", "*"]) == ("Ø8", ["Ø8: a reference dimension (*)"])


@pytest.mark.parametrize("scale", [2.0, 10.0])
def test_a_solidworks_sheet_is_read_at_1_to_1(tmp_path, scale):
    path = tmp_path / "sw.dxf"
    sheet(scale).saveas(path)
    reader_path, adapted = dxf_adapter.prepare(str(path))
    assert reader_path.endswith(".kompas.dxf") and adapted.cad == dxf_adapter.SOLIDWORKS
    assert adapted.scale == scale and adapted.material == "EN 10025-2 S355J2+N"
    assert adapted.warnings[0] == f"read from a SolidWorks DXF (AC1015) drawn at {scale:g}:1, brought to 1:1"
    (part,) = dxf_input.part_results(dxf_reader.read_dxf(reader_path))
    data = part.data
    assert data.overall_length == 50
    sections = [(f.type, f.diameter, f.length) for f in data.features if f.type in ("od_turn", "thread")]
    assert sections == [("od_turn", 20.0, 30.0), ("thread", 20.0, 30.0), ("od_turn", 30.0, 20.0)]
    assert any(f.type == "chamfer" and f.length == 1 and f.face == "left" for f in data.features)
    assert next(f for f in data.features if f.type == "od_turn" and f.diameter == 30).tolerance == "h6"


def test_the_frame_and_title_block_are_not_the_contour(tmp_path):
    path = tmp_path / "sw.dxf"
    sheet().saveas(path)
    reader_path, _ = dxf_adapter.prepare(str(path))
    doc, contour, thin, axes, _ = dxf_reader.load(reader_path)
    assert len(contour) == 12 and len(thin) == 2 and len(axes) == 1


def test_a_half_section_with_flats_is_not_read_and_says_why(tmp_path):
    # 03057789-1-01 (in-sample): the view half shows the flats' edge on the collar, the section half the full Ø50
    path = tmp_path / "half.dxf"
    half_section_sheet().saveas(path)
    with pytest.raises(DxfReadError) as err:
        dxf_adapter.prepare(str(path))
    message = str(err.value)
    assert message.startswith("A half section: the main view is half a view and half a section (hatched, below the axis)")
    assert "differs from the section at x 10…20 (a line 15 mm from the axis), from the left end" in message
    assert "On the end view Ø50 is drawn as arcs, not a full circle: flats are possible (milling, not turning)" in message
    assert message.endswith("this drawing is not read: enter the part by hand.")
