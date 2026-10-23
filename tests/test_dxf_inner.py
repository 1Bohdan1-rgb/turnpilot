"""The inner profile of a DXF part (dxf_inner): bore rows beside the frozen reader, their place on the drawing."""
import io
import json

import dxf_drawings as dd
from dxf_drawings import DIA, DEG, TIMES
from turnpilot import dxf_inner, dxf_input, dxf_reader
from turnpilot.models import DrawingExtraction, Feature, Job, db


def draw_bushing(sheet):
    """Bushing, 40 long, Ø30: a blind bore Ø16 depth 20 from the right face with a 1×45° chamfer at its mouth and
    a 120° drill point below it."""
    s = sheet
    s.axis(-3, 43)
    s.vertical(0, 0, 15)
    s.line(0, 15, 40, 15)
    s.vertical(40, 9, 15)  # right face, around the chamfer
    s.line(39, 8, 40, 9)  # chamfer 1×45° at the mouth
    s.line(20, 8, 39, 8)  # the wall
    s.vertical(20, 0, 8)  # where the cone meets the wall (an edge line)
    s.line(20 - 8 / 3 ** 0.5, 0, 20, 8)  # the drill point, 120°
    s.length(0, 40, "40", r_from=15)
    s.length(20, 40, "20", r_from=8, offset=-35)
    s.diameter(30, 30, f"{DIA}30h12")
    s.diameter(30, 16, f"{DIA}16H9", offset=-20)
    s.length(39, 40, f"1{TIMES}45{DEG}", r_from=8.5, offset=-45)


def draw_stepped(sheet):
    """Sleeve, 30 long, Ø24: a stepped blind bore from the left face, Ø14 L10 then Ø8 L8 with a flat bottom. (A
    through bore splits the contour into two halves, which the frozen reader does not take for a part.)"""
    s = sheet
    s.axis(-3, 33)
    s.vertical(0, 7, 12)
    s.line(0, 12, 30, 12)
    s.vertical(30, 0, 12)
    s.line(0, 7, 10, 7)
    s.vertical(10, 4, 7)  # the step
    s.line(10, 4, 18, 4)
    s.vertical(18, 0, 4)  # the flat bottom
    s.length(0, 30, "30", r_from=12)
    s.length(0, 10, "10", r_from=7, offset=-35)
    s.diameter(20, 24, f"{DIA}24")
    s.diameter(5, 14, f"{DIA}14", offset=-20)
    s.diameter(14, 8, f"{DIA}8", offset=-15)


def _read(tmp_path, *drawers):
    reading = dxf_reader.read_dxf(dd.write(tmp_path / "inner.dxf", *drawers))
    results = dxf_input.part_results(reading)
    dxf_inner.add_inner(reading, results)
    return results


def _bores(result):
    return [(f, row) for f, row in zip(result.data.features, result.report["rows"]) if row["section"] == "inner"]


def test_shaft_blind_bore_with_a_flat_bottom(tmp_path):
    (result,) = _read(tmp_path, dd.draw_shaft)
    ((bore, row),) = _bores(result)
    assert (bore.type, bore.diameter, bore.tolerance, bore.length, bore.length_derived) == ("bore", 10, "H11", 15, False)
    assert row["inner"] == {"open_end": "left", "from": 0, "bottom": "flat", "cone_angle": None}
    assert row["dims"] == ["Ø10H11", "15"] and dxf_inner.IN_SAMPLE_NOTE in row["notes"]
    assert not any("inner profile" in w for w in result.data.warnings)  # the bore is read: no "add it by hand"
    assert [d for d in result.report["dims"] if d["inner"]][0]["inner_row"] is True


def test_bushing_drill_point_and_mouth_chamfer(tmp_path):
    (result,) = _read(tmp_path, draw_bushing)
    (bore, row), (chamfer, chamfer_row) = _bores(result)
    assert (bore.diameter, bore.tolerance, bore.length) == (16, "H9", 20)
    assert row["inner"] == {"open_end": "right", "from": 0, "bottom": "drill_point", "cone_angle": 120.0}
    assert any("drill point drawn at 120°" in n for n in row["notes"])
    assert (chamfer.type, chamfer.location, chamfer.face, chamfer.length, chamfer.diameter) == (
        "chamfer", "internal", "right", 1, 16)
    assert chamfer_row["dims"] == [f"1×45°"]


def test_stepped_blind_bore(tmp_path):
    (result,) = _read(tmp_path, draw_stepped)
    (wide, wide_row), (narrow, narrow_row) = _bores(result)
    assert (wide.diameter, wide.length, wide_row["inner"]["bottom"], wide_row["inner"]["from"]) == (14, 10, "flat", 0)
    assert (narrow.diameter, narrow.length, narrow.length_derived) == (8, 8, True)
    assert narrow_row["inner"] == {"open_end": "left", "from": 10, "bottom": "flat", "cone_angle": None}


def test_confirm_keeps_the_place_from_the_dxf(client, tmp_path):
    data = dd.write(tmp_path / "sheet.dxf", draw_bushing).read_bytes()
    client.post("/jobs/upload", data={"drawing": (io.BytesIO(data), "Втулка.dxf")}, content_type="multipart/form-data")
    extraction = db.session.execute(db.select(DrawingExtraction)).scalar_one()
    parsed = json.loads(extraction.parsed)
    form = {"name": "Bushing", "material_id": "1", "quantity": "1", "blank_diameter": "32", "blank_length": "50",
            "blank_shape": "round", "feature_count": str(len(parsed["features"]))}
    for i, f in enumerate(parsed["features"]):
        form[f"f{i}-include"] = "1"
        form[f"f{i}-type"] = f["type"]
        for key in ("diameter", "length", "tolerance"):
            if f.get(key) is not None:
                form[f"f{i}-{key}"] = str(f[key])
        form[f"f{i}-position"] = " ".join(p for p in (f.get("location"), f.get("face")) if p)
    client.post(f"/extractions/{extraction.id}/confirm", data=form)
    bore = db.session.execute(db.select(Feature).filter_by(type="bore")).scalar_one()
    assert (bore.bore_open_end, bore.bore_from_mm, bore.bore_bottom, bore.bore_cone_angle) == (
        "right", 0, "drill_point", 120)
    assert bore.bore_position_source == "dxf" and bore.bore_position_by is None and bore.bore_position_at
    page = client.get(f"/jobs/{bore.job_id}").get_data(as_text=True)
    assert "opens at the right end face, from 0 mm, bottom: the drill&#39;s point (drawn 120°)" in page


def test_operator_places_a_bore_typed_by_hand(client):
    job = Job(name="J", material_id=1, quantity=1, blank_diameter=40, blank_length=60)
    bore = Feature(type="bore", diameter=12, length=20)
    job.features = [bore]
    db.session.add(job)
    db.session.commit()
    page = client.get(f"/jobs/{job.id}").get_data(as_text=True)
    assert "Not bored by a program until it is set" in page
    url = f"/jobs/{job.id}/features/{bore.id}/bore-position"
    client.post(url, data={"open_end": "left", "from_mm": "0", "bottom": "flat", "decided_by": ""})
    assert bore.bore_open_end is None  # the name is required
    client.post(url, data={"open_end": "left", "from_mm": "0", "bottom": "", "decided_by": "Bohdan"})
    assert bore.bore_open_end is None  # a blind bore needs its bottom
    client.post(url, data={"open_end": "left", "from_mm": "0", "bottom": "through", "decided_by": "Bohdan"})
    assert (bore.bore_open_end, bore.bore_bottom, bore.bore_position_source, bore.bore_position_by) == (
        "left", "through", "operator", "Bohdan")
    client.post(url, data={"open_end": "right", "from_mm": "5", "bottom": "drill_point", "decided_by": "Bohdan"})
    assert (bore.bore_open_end, bore.bore_from_mm, bore.bore_bottom) == ("right", 5, "drill_point")
