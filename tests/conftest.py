import pytest

from turnpilot import create_app
from turnpilot.models import db
from turnpilot.seed import seed_database


@pytest.fixture
def app():
    app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    with app.app_context():
        db.create_all()  # in-memory test DB; migrations are checked in test_migrations.py
        seed_database()
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()
