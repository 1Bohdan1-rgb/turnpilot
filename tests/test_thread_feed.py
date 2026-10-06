"""Thread speed check: the Z axis moves at n·P when threading; the machine's limit, or a warning without one."""
from turnpilot import services
from turnpilot.models import db

PROFILE = dict(action="profile", name="Lathe 1", max_rpm="4000", power_kw="11", max_diameter="300")


def test_seed_machine_has_no_thread_feed_limit(app):
    assert services.get_machine().max_thread_feed is None  # not guessed


def test_machine_page_saves_and_clears_the_limit(client):
    client.post("/machine", data={**PROFILE, "max_thread_feed": "2000"})
    assert services.get_machine().max_thread_feed == 2000
    assert 'name="max_thread_feed" type="number" step="any" min="0" value="2000.0"' in client.get("/machine").get_data(as_text=True)
    client.post("/machine", data={**PROFILE, "max_thread_feed": ""})
    assert services.get_machine().max_thread_feed is None


def test_machine_page_rejects_a_negative_limit(client):
    client.post("/machine", data={**PROFILE, "max_thread_feed": "-5"})
    db.session.expire_all()
    assert services.get_machine().max_thread_feed is None
