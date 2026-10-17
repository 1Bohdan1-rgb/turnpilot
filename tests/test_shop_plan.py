"""The shop's plan from a JSON file in mm (stage 7.2): checked, kept as uploaded, the newest shown."""
import io
import json
from pathlib import Path

import pytest

from turnpilot import services, shop_plan
from turnpilot.models import ShopPlan, db

EXAMPLE = Path(__file__).resolve().parent.parent / "docs" / "shop_plan_example.json"


def test_the_example_plan_is_valid():
    plan = shop_plan.parse(EXAMPLE.read_text(encoding="utf-8"))
    assert (plan.width_mm, plan.height_mm) == (72000, 39200)
    assert plan.walls[0].closed and len(plan.columns) == 29 and plan.walkways[0].label == "MAIN WALKWAY 2.4 m"


@pytest.mark.parametrize("change, problem", [
    (lambda d: d.update(width_mm=-1), "width_mm: Input should be greater than 0"),
    (lambda d: d["walls"][1]["points"].append([80000, 0]), "walls[1]: (80000, 0) is outside the plan"),
    (lambda d: d["openings"][0].update(kind="gate"), "openings.0.kind"),
    (lambda d: d.update(machines=[]), "machines: Extra inputs are not permitted"),
])
def test_a_wrong_plan_says_what_is_wrong(change, problem):
    data = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    change(data)
    with pytest.raises(shop_plan.PlanError) as e:
        shop_plan.parse(json.dumps(data))
    assert problem in str(e.value)


def test_upload_keeps_every_plan_and_shows_the_newest(app, client):
    data = EXAMPLE.read_bytes()
    response = client.post("/shop/plan", data={"plan": (io.BytesIO(data), "shop.json")},
                           content_type="multipart/form-data", follow_redirects=True)
    assert "name is required" in response.get_data(as_text=True) and services.current_shop_plan() == (None, None)
    client.post("/shop/plan", data={"plan": (io.BytesIO(b"{not json"), "bad.json"), "uploaded_by": "Bohdan"},
                content_type="multipart/form-data")
    assert db.session.execute(db.select(ShopPlan)).first() is None
    for _ in range(2):
        client.post("/shop/plan", data={"plan": (io.BytesIO(data), "shop.json"), "uploaded_by": "Bohdan"},
                    content_type="multipart/form-data")
    record, plan = services.current_shop_plan()
    assert record.id == 2 and record.uploaded_by == "Bohdan" and plan.name.startswith("Example shop")
    page = client.get("/shop/plan").get_data(as_text=True)
    assert page.count("<tr><td>Example shop (invented, from the mock-up)") == 2 and page.count("(shown)") == 1
