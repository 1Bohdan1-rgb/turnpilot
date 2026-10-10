"""Programs for real KOMPAS DXF files, for a local look (the drawings are not in git, nor are their programs).

Each part of the sheet goes the app's whole way in a temporary database (the app's own database is not touched):
upload, confirm the rows as read (arc shapes as the review page fills them; an arc drawn with another radius than
dimensioned stays for the operator to choose), calculate, approve every operation (in this temporary database only), the
P1.2 catalogue file confirmed, DEMO programming values (`flask gcode-demo`), generate, simulate. The programs go to
instance/gcode_<file>_part<n>.nc with a summary; they are drafts for the operator, never ready to run (DEMO).
--tip-direction 4=3 gives the tool at turret position 4 the tip direction T3 in the temporary database (a value typed
here, not the operator's), so its finishing contour is written with nose radius compensation; the files then end in
_comp.nc.

    python tools/gcode_real.py "real_dxf/Zavisa 36.dxf" [--blank 38x120] [--material "Steel 45 (C45)"]
        [--tip-direction 4=3]
"""
from __future__ import annotations

import argparse
import io
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from conftest import confirm_p12_catalogue  # noqa: E402

from turnpilot import create_app, services  # noqa: E402
from turnpilot.models import DrawingExtraction, Job, Material, TurretSlot, db  # noqa: E402
from turnpilot.seed import seed_database  # noqa: E402


def confirm_form(extraction, material_id, blank, instance_path):
    data = json.loads(extraction.parsed)
    diameter, length = blank or (data.get("blank_diameter"), data.get("blank_length"))
    form = {"name": data.get("part_name") or Path(extraction.original_filename).stem, "material_id": str(material_id),
            "quantity": "1", "blank_diameter": str(diameter or ""), "blank_length": str(length or ""),
            "blank_shape": "round", "feature_count": str(len(data["features"])), "add_face": "1", "add_parting": "1"}
    for i, f in enumerate(data["features"]):
        form[f"f{i}-include"] = "1"
        form[f"f{i}-type"] = f["type"]
        for key in ("diameter", "start_diameter", "length", "tolerance", "pitch", "radius"):
            if f.get(key) is not None:
                form[f"f{i}-{key}"] = str(f[key])
        form[f"f{i}-position"] = " ".join(p for p in (f.get("location"), f.get("face")) if p)
    for i, shape in services.dxf_arc_shapes(extraction, instance_path).items():  # as the review page fills them
        form[f"f{i}-arc_shape"] = shape["arc_shape"]
        if shape.get("face"):
            form[f"f{i}-position"] = f"external {shape['face']}"
    return form


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("dxf", nargs="+")
    parser.add_argument("--blank", help="DxL, e.g. 38x120 (else the blank suggested on the review page)")
    parser.add_argument("--material", default="Steel 45 (C45)")
    parser.add_argument("--tip-direction", action="append", default=[], metavar="POSITION=T",
                        help="the tip direction T of the tool at a turret position (typed here, not the operator's)")
    args = parser.parse_args(argv)
    blank = tuple(float(v) for v in args.blank.lower().split("x")) if args.blank else None
    tips = {int(k): int(v) for k, v in (item.split("=") for item in args.tip_direction)}
    out_dir = ROOT / "instance"
    with tempfile.TemporaryDirectory() as tmp:
        app = create_app({"SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp}/check.db", "ANTHROPIC_MODEL": "none"},
                         instance_path=tmp)
        with app.app_context():
            db.create_all()
            seed_database()
            confirm_p12_catalogue()
            for position, tip in tips.items():
                slot = db.session.execute(db.select(TurretSlot).filter_by(position=position)).scalar_one()
                slot.tool.tip_direction = tip
                print(f"T{position:02d} {slot.tool.name}: tip direction T{tip} (typed here, not the operator's)")
            db.session.commit()
            material = db.session.execute(db.select(Material).filter_by(name=args.material)).scalar_one()
            client = app.test_client()
            for path in map(Path, args.dxf):
                client.post("/jobs/upload", data={"drawing": (io.BytesIO(path.read_bytes()), path.name)},
                            content_type="multipart/form-data")
                extractions = db.session.execute(db.select(DrawingExtraction).filter_by(
                    original_filename=path.name)).scalars().all()
                for n, extraction in enumerate(extractions, start=1):
                    response = client.post(f"/extractions/{extraction.id}/confirm",
                                           data=confirm_form(extraction, material.id, blank, tmp))
                    db.session.refresh(extraction)
                    if extraction.job_id is None:
                        print(f"{path.name} part {n}: not confirmed (HTTP {response.status_code}; no blank on the "
                              "drawing? give --blank)")
                        continue
                    job = db.session.get(Job, extraction.job_id)
                    machine = services.get_machine()
                    services.calculate_operations(job, machine)
                    for op in job.current_operations:
                        op.status = "approved"  # in this temporary database only
                    db.session.commit()
                    services.set_gcode_demo(machine, job)
                    readiness, record = services.generate_gcode(job, machine)
                    stem = "".join(c if c.isalnum() else "_" for c in path.stem)
                    if record is None:
                        print(f"{path.name} part {n}: no program: {'; '.join(readiness.blockers)}")
                        continue
                    target = out_dir / f"gcode_{stem}_part{n}{'_comp' if tips else ''}.nc"
                    target.write_text(record.text, encoding="ascii", newline="\n")
                    sim = json.loads(record.simulation)
                    print(f"{path.name} part {n}: {target.relative_to(ROOT)}: {len(record.text.splitlines())} lines, "
                          f"{len(sim['errors'])} error(s), {'complete' if not sim['incomplete'] else 'NOT COMPLETE'}"
                          f", skipped {len(sim['skipped'])}")
                    for line, message in sim["errors"]:
                        print(f"  error line {line}: {message}")
                    for line, message in sim["warnings"]:
                        if not line:
                            print(f"  {message}")
                    for message in sim["incomplete"]:
                        print(f"  {message}")
                    for op_id, reason in sim["skipped"].items():
                        print(f"  operation {op_id}: {reason}")
            db.session.remove()
            db.engine.dispose()  # Windows keeps the file locked otherwise
    return 0


if __name__ == "__main__":
    sys.exit(main())
