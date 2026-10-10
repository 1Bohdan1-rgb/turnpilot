"""An existing arc or fillet (e.g. confirmed before the shape was kept): the operator sets its shape and side."""
from gcode_jobs import make_job

from turnpilot.models import Feature


def test_set_the_shape_of_existing_rows(app, client):
    job = make_job([Feature(type="od_turn", diameter=45, length=5), Feature(type="fillet", radius=2.5),
                    Feature(type="arc", start_diameter=38.5, diameter=38.5, length=15, radius=12.5)], approve=False)
    fillet, arc = (next(f for f in job.features if f.type == t) for t in ("fillet", "arc"))
    assert "shape…" in client.get(f"/jobs/{job.id}").get_data(as_text=True)
    client.post(f"/jobs/{job.id}/features/{arc.id}/shape", data={"arc_shape": "concave"})
    assert arc.arc_convex is False
    response = client.post(f"/jobs/{job.id}/features/{fillet.id}/shape", data={"arc_shape": "concave"},
                           follow_redirects=True)
    assert "Fillet side: left or right" in response.get_data(as_text=True) and fillet.arc_convex is None
    client.post(f"/jobs/{job.id}/features/{fillet.id}/shape", data={"arc_shape": "concave", "face": "right"})
    assert (fillet.arc_convex, fillet.face) == (False, "right")
    od = next(f for f in job.features if f.type == "od_turn")
    assert client.post(f"/jobs/{job.id}/features/{od.id}/shape", data={"arc_shape": "convex"}).status_code == 404
