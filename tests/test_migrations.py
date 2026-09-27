from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from flask_migrate import upgrade

from turnpilot import create_app
from turnpilot.models import db
from turnpilot.seed import seed_database


def test_migrations_match_models(tmp_path):
    """Fails when a model changes without a matching migration (`flask db migrate`)."""
    app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'test.db'}"})
    with app.app_context():
        upgrade()
        with db.engine.connect() as conn:
            diff = compare_metadata(MigrationContext.configure(conn), db.metadata)
        assert diff == []
        assert seed_database()  # the migrated schema accepts the seed data
        db.session.remove()
        db.engine.dispose()
