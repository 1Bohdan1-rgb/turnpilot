"""The entering angle κr of a turning tool with its source: the holder style's value is a suggestion the operator
takes by a click (J = 93° from the Sandvik catalogue, the other letters to be checked); never filled by itself."""
from turnpilot.models import Tool, db
from turnpilot.planner import entering_angle_suggestion


def finishing_tool():
    return db.session.execute(db.select(Tool).filter_by(insert_code="DNMG150604-PF")).scalar_one()


def test_suggestion_by_the_holder_style():
    letter, angle, source = entering_angle_suggestion("DVJNR 2020K16")
    assert (letter, angle) == ("J", 93.0) and source.startswith("Sandvik Turning tools catalogue: KAPR 93.0°")
    assert entering_angle_suggestion("ddjnr2525m15")[:2] == ("J", 93.0)
    letter, angle, source = entering_angle_suggestion("PCLNR 2525M12")
    assert (letter, angle) == ("L", 95.0) and "from memory: check" in source
    assert entering_angle_suggestion("RF123E08-2525B") is None  # a parting / grooving holder
    assert entering_angle_suggestion(None) is None and entering_angle_suggestion("") is None


def test_the_operator_takes_it_or_types_it(app, client):
    tool = finishing_tool()
    assert tool.entering_angle_deg is None
    page = client.get(f"/tools/{tool.id}/edit").get_data(as_text=True)
    assert "Entering angle κr" in page and "Take κr" not in page  # no holder code: no suggestion
    tool.holder_code = "DDJNR 2525M15"
    db.session.commit()
    page = client.get(f"/tools/{tool.id}/edit").get_data(as_text=True)
    assert "DDJNR 2525M15: style J, κr 93.0°" in page and "Take κr 93.0°" in page
    assert tool.entering_angle_deg is None  # never filled by itself
    client.post(f"/tools/{tool.id}/entering-angle", data={"holder_style": "J"})
    assert tool.entering_angle_deg == 93.0
    assert tool.entering_angle_source.startswith("Sandvik Turning tools catalogue: KAPR 93.0°")
    assert tool.entering_angle_source.endswith("; holder DDJNR 2525M15 (taken by the operator)")
    response = client.post(f"/tools/{tool.id}/entering-angle", data={"holder_style": "L"}, follow_redirects=True)
    assert "No entering angle for this holder code." in response.get_data(as_text=True)
    assert "κr 93.0°" in client.get("/machine").get_data(as_text=True)
