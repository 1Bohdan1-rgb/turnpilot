"""The 3D view of the part (static/part3d.js): the program page's data and the files it loads from the project."""
import hashlib
from pathlib import Path

from gcode_jobs import make_job, ready_machine
from test_gcode_golden import pin

from turnpilot import services

VENDOR = Path(__file__).parent.parent / "turnpilot" / "static" / "vendor" / "three"


def test_three_js_is_the_npm_files_unchanged():
    lines = (VENDOR / "VERSION.txt").read_text(encoding="utf-8").splitlines()
    sums = dict(reversed(line.split("  ")) for line in lines[lines.index("sha256:") + 1:] if line.strip())
    assert set(sums) == {"LICENSE", "OrbitControls.js", "three.module.min.js"}
    for name, digest in sums.items():
        assert hashlib.sha256((VENDOR / name).read_bytes()).hexdigest() == digest, name


def test_program_page_offers_the_3d_view_from_the_project_files(app, client):
    machine = ready_machine()
    job = make_job(**pin())
    _, record = services.generate_gcode(job, machine)
    page = client.get(f"/gcode/{record.id}").get_data(as_text=True)
    assert f'data-src="/gcode/{record.id}/part3d.json"' in page and 'data-mode="program"' in page
    assert "/static/vendor/three/three.module.min.js" in page and "/static/part3d.js" in page
    assert "Outer profile only: bores and flats are not shown." in page
    uk = app.test_client().get(f"/gcode/{record.id}").get_data(as_text=True)  # the default language
    assert "Лише зовнішній профіль: отвори й лиски не показано." in uk
    assert "http" not in page.split('<script type="importmap">')[1].split("</script>")[0]
    data = client.get(f"/gcode/{record.id}/part3d.json").get_json()
    assert data["states"][0]["n"] is None and len(data["states"]) > 2
    assert data["bar_radius"] == 16 and data["z_jaws"] == -75 and data["final"] and data["computed_at"] is None
    assert data["stale"] == []


def test_order_view_follows_the_operator_marks_on_the_order_program(app, client):
    machine = ready_machine()
    job = make_job(**pin())
    _, first = services.generate_gcode(job, machine)
    order = services.create_order(job, machine, 10, "Bohdan")
    _, newer = services.generate_gcode(job, machine)  # the order keeps the program it took
    assert order.program == first and newer.id != first.id
    data = client.get(f"/orders/{order.id}/part3d.json").get_json()
    blocks = [s["n"] for s in data["states"][1:]]
    assert data["program"] == first.id and (data["done"], data["next"], data["note"]) == (0, blocks[0], None)
    assert data["final"] and data["bar_radius"] == 16
    services.mark_order_block(order, blocks[0], True, "Ivan")
    services.mark_order_block(order, blocks[1], True, "Ivan")
    data = services.order_part3d(order)
    assert (data["done"], data["next"], data["note"]) == (2, blocks[2], None)
    services.mark_order_block(order, blocks[3], True, "Ivan")
    data = services.order_part3d(order)
    assert data["done"] == 2 and data["note"] == f"N{blocks[3]} marked done, but N{blocks[2]} is not: shown up to N{blocks[1]}."
    services.mark_order_block(order, blocks[0], False, "Ivan")
    assert services.order_part3d(order)["note"].endswith("shown up to the bar.")


def test_shop_panel_shows_the_part_by_the_marks_with_its_captions(app, client):
    machine = ready_machine()
    job = make_job(**pin())
    services.generate_gcode(job, machine)
    order = services.create_order(job, machine, 10, "Bohdan")
    services.make_order_current(order, "Bohdan")
    panel = client.get(f"/shop/machines/{machine.id}?fragment=1").get_data(as_text=True)
    assert f'data-src="/orders/{order.id}/part3d.json"' in panel and 'data-mode="order"' in panel
    assert "by the operator's marks, not from the machine." in panel
    uk = app.test_client().get(f"/shop/machines/{machine.id}?fragment=1").get_data(as_text=True)
    assert "за відмітками оператора, не з верстата" in uk and "Лише зовнішній профіль: отвори й лиски не показано" in uk
    assert "turnpilotPart3d.mountAll(body)" in client.get("/shop").get_data(as_text=True)


def test_an_order_without_a_program_says_so(app, client):
    machine = ready_machine()
    job = make_job(**pin())
    order = services.create_order(job, machine, 10, "Bohdan")
    assert order.program is None
    assert "No program taken by order" in client.get(f"/orders/{order.id}/part3d.json").get_json()["error"]


def test_one_webgl_renderer_per_page_and_the_camera_at_the_part():
    """Every view renders with the page's one WebGL renderer (copied into its own canvas); the camera is set to the
    part's length and Ø, and the Fit button puts it there again."""
    source = (Path(__file__).parent.parent / "turnpilot" / "static" / "part3d.js").read_text(encoding="utf-8")
    assert source.count("new THREE.WebGLRenderer(") == 1
    assert "if (!shared) shared = new THREE.WebGLRenderer(" in source
    assert "ctx.drawImage(renderer.domElement, 0, 0)" in source
    assert "function fitCamera(camera, controls, span, radius)" in source
    assert 'fit.textContent = t("Fit")' in source and "fitCamera(camera, controls, span, bar); render();" in source


def test_the_shop_card_shows_the_part_without_the_panel(client):
    page = client.get("/shop").get_data(as_text=True)
    assert "part3d: id => `/orders/${id}/part3d.json`" in page
    assert 'class="part3d sf-part3d" data-src="${URLS.part3d(o.id)}" data-mode="order"' in page
    assert 'window.turnpilotPart3d.mountAll(document.getElementById("sf-card"))' in page
