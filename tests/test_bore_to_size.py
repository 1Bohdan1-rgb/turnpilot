"""A bore of IT12 or coarser whose Ra the roughing bar's feed reaches: bored to size in one operation (Завіса 36's
Ø22+0,21); the operator may keep the finishing pass (by name), which stays for the calculations that follow."""
from datetime import datetime, timezone

from gcode_jobs import make_job
from test_drilling import _of, _ops
from test_gcode_boring import machine, program  # noqa: F401 (the fixture)

from turnpilot import planner, services
from turnpilot.models import Feature, db
from turnpilot.planner import FeatureSpec

BODY = FeatureSpec(1, "od_turn", diameter=34.8, length=80)


def _types(ops):
    return [(op.tool_type, op.mode) for op in ops]


def test_bored_to_size_with_both_numbers():
    (_, _, rough) = _of(_ops([BODY, FeatureSpec(2, "bore", diameter=22, length=32, tolerance="+0.21", ra=6.3)]), 2)
    assert rough.tool_type == "boring_rough" and planner.TO_SIZE_WARNING in rough.warnings
    note = next(n for n in rough.notes if n.startswith("bored to size in one operation"))
    assert "the tolerance +0.21 on Ø22 is IT12 or coarser" in note
    assert "the feed f 0.15 with rε 0.4 gives Ra ≈ 1.8 µm (f²·1000/(32·rε)) ≤ Ra 6.3 of the drawing" in note


def test_the_finishing_pass_stays_when():
    finish = ("boring", "finish")
    fine = FeatureSpec(2, "bore", diameter=22, length=32, tolerance="H9")  # finer than IT12
    smooth = FeatureSpec(2, "bore", diameter=22, length=32, tolerance="+0.21", ra=1.6)  # Ra 1.8 > 1.6
    kept = FeatureSpec(2, "bore", diameter=22, length=32, tolerance="+0.21", bore_finish_kept=True)
    flat = FeatureSpec(2, "bore", diameter=22, length=32, tolerance="+0.21", bore_bottom="flat")
    for bore in (fine, smooth, kept, flat):
        assert finish in _types(_of(_ops([BODY, bore]), 2)), bore
    chamfered = [BODY, FeatureSpec(2, "bore", diameter=22, length=32, tolerance="+0.21"),
                 FeatureSpec(3, "chamfer", diameter=22, length=1, location="internal")]
    assert finish in _types(_ops(chamfered))


def _bushing_job(machine):
    bore = Feature(type="bore", diameter=22, length=32, tolerance="+0.21", bore_open_end="right", bore_from_mm=0,
                   bore_bottom="drill_point", bore_position_source="dxf", bore_position_at=datetime.now(timezone.utc),
                   depth_mode="full_diameter", depth_decided_by="Bohdan", depth_decided_at=datetime.now(timezone.utc))
    job = make_job([Feature(type="face"), Feature(type="od_turn", diameter=34.8, length=80), bore,
                    Feature(type="parting")], free_end="right", blank=38, length=90, stickout=95, face_stock=1.0,
                   machine=machine)
    return job, bore


def test_program_bores_to_size_and_the_part_is_complete(machine):  # noqa: F811
    job, bore = _bushing_job(machine)
    prog, text = program(job, machine)
    titles = [b.title for b in prog.blocks]
    assert any(t.startswith("ROUGH BORE D22 2 X AP 0.5 FROM D20") for t in titles)
    assert not any(t.startswith("FINISH BORE") for t in titles) and "G41" not in text
    result = services.gcode_simulation(job, machine, text)
    assert result.ok, result.errors
    assert result.complete, result.incomplete


def test_operator_keeps_the_finishing_pass(client, machine):  # noqa: F811
    job, bore = _bushing_job(machine)
    page = client.get(f"/jobs/{job.id}").get_data(as_text=True)
    assert "bored to size in one operation" in page and "Keep the finishing pass" in page
    url = f"/jobs/{job.id}/features/{bore.id}/bore-finish"
    client.post(url, data={"decided_by": ""})
    assert bore.bore_finish_kept_by is None  # the name is required
    client.post(url, data={"decided_by": "Bohdan"})
    assert bore.bore_finish_kept_by == "Bohdan" and bore.bore_finish_kept_at
    readiness = services.gcode_readiness(job, machine)
    assert any("its finishing pass was kept by Bohdan after the calculation" in b for b in readiness.blockers)
    services.calculate_operations(job, machine)
    assert ("boring", "finish") in [(op.tool_type, op.rough_finish) for op in job.current_operations]
    services.calculate_operations(job, machine)  # and in the calculations that follow
    assert ("boring", "finish") in [(op.tool_type, op.rough_finish) for op in job.current_operations]
    assert "Finishing pass kept by the operator (Bohdan" in client.get(f"/jobs/{job.id}").get_data(as_text=True)
