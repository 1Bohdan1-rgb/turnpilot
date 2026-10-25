"""The shop in 3D (shop_scene): the plan's elements and the placed machines as shapes in mm; the statuses and
conflicts are the 2D page's; the heights not given are conditional, and said so."""
from gcode_jobs import ready_machine
from test_shop_placement import EXAMPLE, NEW, lathe, place, upload_plan

from turnpilot import services, shop_plan, shop_scene
from turnpilot.models import Machine, db

PLAN = shop_plan.parse(EXAMPLE.read_text(encoding="utf-8"))


def _segments(plan):
    n = 0
    for w in plan.walls:
        n += len(w.points) - 1 + (1 if w.closed else 0)
    return n


def item(**kw):
    base = {"id": 1, "name": "Lathe 1", "kind": "cnc_lathe", "x": 9000, "y": 6000, "rotation": 90, "length": 4400,
            "width": 2200, "height": None, "status": "running", "alarm": False, "conflicts": [], "removed": False}
    base.update(kw)
    return base


def test_the_plan_in_3d():
    scene = shop_scene.build(PLAN, [])
    assert scene["floor"] == {"w": 72000, "d": 39200}
    assert len(scene["walls"]) == _segments(PLAN) and len(scene["columns"]) == 29 and len(scene["openings"]) == 7
    first = scene["walls"][0]
    assert (first["x0"], first["y0"], first["x1"], first["y1"], first["thickness"]) == (0, 0, 50400, 0, 300)
    closing = scene["walls"][9]  # the closed outline's last segment back to its start
    assert (closing["x0"], closing["y0"], closing["x1"], closing["y1"]) == (0, 30400, 0, 0)
    assert {a["role"] for a in scene["areas"]} == {"walkway", "zone", "storage", "fixture"}
    assert len(scene["areas"]) == 2 + 1 + 11 + 6 and len(scene["labels"]) == 7
    assert scene["columns"][0] == {"x": 0, "y": 0, "size": 400, "height": shop_scene.CONDITIONAL_WALL_HEIGHT_MM}


def test_wall_heights_from_the_plan_else_conditional():
    scene = shop_scene.build(PLAN, [])
    assert all(w["conditional"] and w["height"] == 3000 for w in scene["walls"])
    assert scene["conditional"]["walls"] and scene["conditional"]["wall_height"] == 3000
    data = PLAN.model_dump()
    data["wall_height_mm"] = 6000
    data["walls"][1]["height_mm"] = 2500
    scene = shop_scene.build(shop_plan.Plan.model_validate(data), [])
    assert {w["height"] for w in scene["walls"]} == {6000, 2500} and not scene["conditional"]["walls"]
    assert scene["columns"][0]["height"] == 6000  # as high as the walls


def test_a_machine_in_its_frame_with_its_footprint_and_height():
    scene = shop_scene.build(PLAN, [item(), item(id=2, name="Saw", kind="saw", height=1450, rotation=0)])
    lathe_3d, saw = scene["machines"]
    assert (lathe_3d["length"], lathe_3d["width"], lathe_3d["height"], lathe_3d["rotation"]) == (4400, 2200, 1900, 90)
    assert lathe_3d["height_source"] == "conditional" and saw["height_source"] == "machine" and saw["height"] == 1450
    assert scene["conditional"]["machines"] == ["Lathe 1"]
    plate, body = lathe_3d["parts"][:2]
    assert plate["role"] == "status" and (plate["sx"], plate["sy"]) == (4700, 2500)
    assert (body["sx"], body["sy"], body["sh"]) == (4400, 2200, 1900 * .9)
    for m in scene["machines"]:  # every shape inside the footprint and the height
        for p in m["parts"][1:]:
            half_x = p["sx"] / 2 if p["shape"] == "box" else (p["length"] / 2 if p["axis"] == "x" else p["r"])
            assert abs(p["x"]) + half_x <= m["length"] / 2 + 1e-6, (m["kind"], p)


def test_every_type_has_its_shape_and_an_unknown_one_a_grey_frame():
    for kind in shop_scene.SHAPES:
        m = shop_scene.machine(item(kind=kind))
        assert m["known"] and len(m["parts"]) >= 4 and m["height"] == shop_scene.CONDITIONAL_MACHINE_HEIGHT_MM[kind]
    unknown = shop_scene.machine(item(kind=None))
    assert not unknown["known"] and unknown["parts"][1:] == [] and unknown["height_source"] == "unknown"
    assert unknown["length"] == unknown["width"] == shop_scene.UNKNOWN_MACHINE_MM
    assert not shop_scene.machine(item(length=None))["known"]


def test_only_placed_machines():
    scene = shop_scene.build(PLAN, [item(), item(id=2, name="Not placed", x=None, y=None),
                                    item(id=3, name="Taken off", removed=True)])
    assert [m["name"] for m in scene["machines"]] == ["Lathe 1"]


def test_statuses_and_conflicts_are_the_2d_ones(app, client):
    machine = lathe(ready_machine())
    upload_plan(client)
    client.post("/machines", data=NEW)
    other = lathe(db.session.execute(db.select(Machine).filter_by(name="Lathe 2")).scalar_one())
    machine.height_mm = 2100
    db.session.commit()
    place(client, machine, 9000, 6000)
    place(client, other, 9000, 6000)  # on the first one
    record, plan = services.current_shop_plan()
    flat = services.shop_machines()
    scene = shop_scene.build(plan, flat)
    by_id = {m["id"]: m for m in scene["machines"]}
    for m in flat:
        assert (by_id[m["id"]]["status"], by_id[m["id"]]["alarm"], by_id[m["id"]]["conflicts"]) == (
            m["status"], m["alarm"], m["conflicts"])
    assert by_id[machine.id]["conflicts"] and by_id[machine.id]["height"] == 2100


def test_the_shop_page_has_the_3d_view(app, client):
    import json
    from pathlib import Path

    machine = lathe(ready_machine())
    upload_plan(client)
    place(client, machine, 9000, 6000)
    page = client.get("/shop").get_data(as_text=True)
    assert 'data-view="2d" aria-pressed="true"' in page and 'data-view="3d" aria-pressed="false"' in page
    assert '<div class="sf-3d" id="sf-3d" data-scene="#sf-scene" hidden>' in page and "/static/shop3d.js" in page
    start = page.index('<script type="application/json" id="sf-scene">') + len('<script type="application/json" id="sf-scene">')
    scene = json.loads(page[start:page.index("</script>", start)])
    assert [m["name"] for m in scene["machines"]] == [machine.name] and scene["conditional"]["machines"] == [machine.name]
    assert "The walls' height is conditional (3000 mm): the plan gives none." in page
    assert f"Conditional height by the type (*): {machine.name}" in page
    script = (Path(__file__).parent.parent / "turnpilot" / "static" / "shop3d.js").read_text(encoding="utf-8")
    assert 'from "./webgl.js"' in script and "new THREE.WebGLRenderer" not in script  # the page's one renderer
    assert "castShadow" not in script and "shadowMap" not in script  # no shadows
    assert "window.turnpilotShop.select(" in script  # a click: the 2D card
    assert "window.turnpilotShop = {select:" in page
