import os

import sqlalchemy as sa
from flask import Flask

from .models import db


def create_app(test_config=None):
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_mapping(
        SECRET_KEY=os.environ.get("TURNPILOT_SECRET_KEY", "dev"),
        SQLALCHEMY_DATABASE_URI="sqlite:///" + os.path.join(app.instance_path, "turnpilot.db"),
    )
    if test_config:
        app.config.update(test_config)

    os.makedirs(app.instance_path, exist_ok=True)
    db.init_app(app)

    from .routes import bp
    from .seed import seed_command

    app.register_blueprint(bp)
    app.cli.add_command(seed_command)

    with app.app_context():
        db.create_all()
        _add_missing_columns()

    return app


# Columns added after the first release. create_all() does not alter existing tables,
# so an older SQLite database gets them here.
_ADDED_COLUMNS = {
    "feature": {"is_deleted": "BOOLEAN NOT NULL DEFAULT 0"},
    "operation": {
        "calculation_version": "INTEGER NOT NULL DEFAULT 1",
        "is_archived": "BOOLEAN NOT NULL DEFAULT 0",
    },
}


def _add_missing_columns():
    inspector = sa.inspect(db.engine)
    with db.engine.begin() as conn:
        for table, columns in _ADDED_COLUMNS.items():
            existing = {c["name"] for c in inspector.get_columns(table)}
            for name, ddl in columns.items():
                if name not in existing:
                    conn.execute(sa.text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))
