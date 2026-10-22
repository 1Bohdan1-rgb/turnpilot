"""The 3D view of the part: the files it loads from the project (three.js, not from the internet)."""
import hashlib
from pathlib import Path


VENDOR = Path(__file__).parent.parent / "turnpilot" / "static" / "vendor" / "three"


def test_three_js_is_the_npm_files_unchanged():
    lines = (VENDOR / "VERSION.txt").read_text(encoding="utf-8").splitlines()
    sums = dict(reversed(line.split("  ")) for line in lines[lines.index("sha256:") + 1:] if line.strip())
    assert set(sums) == {"LICENSE", "OrbitControls.js", "three.module.min.js"}
    for name, digest in sums.items():
        assert hashlib.sha256((VENDOR / name).read_bytes()).hexdigest() == digest, name
