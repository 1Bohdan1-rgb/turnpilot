"""S355J2+N (EN 10025-2): Sandvik group P1.2, kc1 from TT A278, mc not verified."""
from conftest import confirm_p12_catalogue

from turnpilot import services
from turnpilot.models import Feature, Job, Material, db


def test_s355_is_p12_with_its_sources(app):
    s355 = db.session.execute(db.select(Material).filter_by(name="S355J2+N (EN 10025-2)")).scalar_one()
    assert (s355.iso_group, s355.catalogue_group, s355.kc1, s355.mc, s355.hardness_hb) == ("P", "P1.2", 1600, 0.25,
                                                                                         None)
    assert "A278 (PDF 282)" in s355.kc_source and "(PDF 682)" in s355.kc_source and "mc 0.25: not verified" in \
        s355.kc_source


def test_a_s355_job_takes_the_p12_catalogue_data(app):
    confirm_p12_catalogue()
    s355 = db.session.execute(db.select(Material).filter_by(name="S355J2+N (EN 10025-2)")).scalar_one()
    job = Job(name="Bushing", material=s355, quantity=1, blank_diameter=45, blank_length=50)
    job.features = [Feature(type="od_turn", diameter=40, length=30)]
    db.session.add(job)
    db.session.commit()
    services.calculate_operations(job, services.get_machine())
    finish = next(op for op in job.current_operations if op.tool_type == "turning_finish")
    assert finish.cutting_data_origin == "catalogue" and "P1.2" in finish.note
