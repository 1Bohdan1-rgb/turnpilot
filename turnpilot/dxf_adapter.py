"""DXF files from other CAD systems than KOMPAS-3D, before the KOMPAS reader (dxf_reader, frozen for the blind check
of roadmap stage 3, so it is not changed here).

detect_cad tells the producer of a file from what it writes:
- KOMPAS-3D: the application id "KOMPAS" or its linetypes K5LT_*; the file goes to dxf_reader as it is;
- SolidWorks: its blocks SW_* (surface finish symbols, centre marks) or its "FORMAT" layer;
- anything else: not supported, said in words.
"""
from __future__ import annotations

import ezdxf

from .dxf_reader import DxfReadError

KOMPAS, SOLIDWORKS, UNKNOWN = "KOMPAS-3D", "SolidWorks", "unknown"


def detect_cad(doc) -> str:
    """The CAD system that wrote the document (KOMPAS, SOLIDWORKS or UNKNOWN)."""
    appids = {a.dxf.name.upper() for a in doc.appids}
    linetypes = {lt.dxf.name.upper() for lt in doc.linetypes}
    if "KOMPAS" in appids or any(name.startswith("K5LT_") for name in linetypes):
        return KOMPAS
    blocks = {b.name.upper() for b in doc.blocks}
    layers = {layer.dxf.name.upper() for layer in doc.layers}
    if any(name.startswith("SW_") for name in blocks) or "FORMAT" in layers:
        return SOLIDWORKS
    return UNKNOWN


def check_supported(path) -> str:
    """The CAD of a DXF the reader can take; DxfReadError naming the CAD otherwise."""
    try:
        doc = ezdxf.readfile(path)
    except (OSError, ezdxf.DXFError) as exc:
        raise DxfReadError(f"The DXF file cannot be read: {exc}") from None
    cad = detect_cad(doc)
    if cad == KOMPAS:
        return cad
    if cad == SOLIDWORKS:
        raise DxfReadError(f"A SolidWorks DXF (DXF version {doc.dxfversion}). Only KOMPAS-3D DXF files are read so "
                           "far.")
    raise DxfReadError("A DXF from an unknown CAD system (no KOMPAS-3D or SolidWorks marks). Only KOMPAS-3D DXF files "
                       "are read so far.")
