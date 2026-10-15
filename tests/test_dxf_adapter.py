"""DXF files from other CAD systems: the producer is told from the file, before the KOMPAS reader (frozen)."""

import ezdxf
import pytest
from dxf_drawings import new_doc

from turnpilot import dxf_adapter
from turnpilot.dxf_reader import DxfReadError


def solidworks_doc():
    """What SolidWorks writes around a drawing: the FORMAT layer and SW_* blocks, plain Continuous lines."""
    doc = ezdxf.new("R2000")
    doc.layers.add("FORMAT")
    doc.blocks.new("SW_SFSYMBOL_0")
    doc.modelspace().add_line((0, 0), (10, 0))
    return doc


def test_detect_the_cad():
    assert dxf_adapter.detect_cad(new_doc()) == dxf_adapter.KOMPAS
    assert dxf_adapter.detect_cad(solidworks_doc()) == dxf_adapter.SOLIDWORKS
    assert dxf_adapter.detect_cad(ezdxf.new()) == dxf_adapter.UNKNOWN


def test_a_solidworks_file_is_named(tmp_path):
    path = tmp_path / "sw.dxf"
    solidworks_doc().saveas(path)
    with pytest.raises(DxfReadError, match=r"A SolidWorks DXF \(DXF version AC1015\). Only KOMPAS-3D DXF"):
        dxf_adapter.check_supported(str(path))
