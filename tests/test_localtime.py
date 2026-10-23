"""London's time for what a person reads (GMT / BST by the UK rule); the database keeps UTC."""
from datetime import datetime, timezone

from turnpilot import localtime


def test_bst_starts_and_ends_at_01_00_utc_on_the_last_sundays():
    # 2026: BST from Sunday 29 March 01:00 UTC to Sunday 25 October 01:00 UTC
    assert not localtime.is_bst(datetime(2026, 3, 29, 0, 59))
    assert localtime.is_bst(datetime(2026, 3, 29, 1, 0))
    assert localtime.is_bst(datetime(2026, 10, 25, 0, 59))
    assert not localtime.is_bst(datetime(2026, 10, 25, 1, 0))
    # 2027: 28 March and 31 October (October's last day is a Sunday)
    assert localtime.is_bst(datetime(2027, 3, 28, 1, 0)) and not localtime.is_bst(datetime(2027, 3, 28, 0, 30))
    assert localtime.is_bst(datetime(2027, 10, 31, 0, 30)) and not localtime.is_bst(datetime(2027, 10, 31, 1, 0))
    assert not localtime.is_bst(datetime(2026, 1, 15, 12, 0)) and not localtime.is_bst(datetime(2026, 12, 31, 23, 0))


def test_the_wall_clock():
    assert localtime.local(datetime(2026, 10, 23, 17, 48)) == "2026-10-23 18:48"  # the user's "ready to run" case
    assert localtime.local(datetime(2026, 3, 29, 0, 30)) == "2026-03-29 00:30"  # GMT, before the change
    assert localtime.local(datetime(2026, 3, 29, 1, 30)) == "2026-03-29 02:30"  # BST: 01:00 to 02:00 is skipped
    assert localtime.local(datetime(2026, 10, 25, 0, 30)) == "2026-10-25 01:30"  # BST
    assert localtime.local(datetime(2026, 10, 25, 1, 30)) == "2026-10-25 01:30"  # GMT: 01:00–02:00 comes twice
    assert localtime.local(datetime(2026, 12, 31, 23, 30), "%Y-%m-%d") == "2026-12-31"
    assert localtime.local(datetime(2026, 6, 30, 23, 30), "%Y-%m-%d") == "2026-07-01"  # the date moves too
    assert localtime.local(datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)) == "2026-07-01 13:00"
    assert localtime.local(None) == ""


def test_the_pages_show_london_time(client):
    from gcode_jobs import make_job, ready_machine
    from turnpilot import services
    from turnpilot.models import db

    machine = ready_machine()
    job = make_job()
    _, record = services.generate_gcode(job, machine)
    record.created_at = datetime(2026, 7, 1, 9, 5)
    db.session.commit()
    page = client.get(f"/gcode/{record.id}").get_data(as_text=True)
    assert "2026-07-01 10:05" in page and "2026-07-01 09:05" not in page
    assert "(UTC)" not in client.get("/jobs/upload").get_data(as_text=True)
