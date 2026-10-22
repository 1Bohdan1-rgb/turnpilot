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
    assert "Лише зовнішній профіль — отвори й лиски не показано" in page
    assert "http" not in page.split('<script type="importmap">')[1].split("</script>")[0]
    data = client.get(f"/gcode/{record.id}/part3d.json").get_json()
    assert data["states"][0]["n"] is None and len(data["states"]) > 2
    assert data["bar_radius"] == 16 and data["z_jaws"] == -75 and data["final"] and data["computed_at"] is None
    assert data["stale"] == []
