import os

from flask import Flask, flash, redirect, url_for
from flask_migrate import Migrate
from werkzeug.exceptions import RequestEntityTooLarge

from .config import DefaultConfig
from .models import db

# render_as_batch lets Alembic alter SQLite tables (SQLite has limited ALTER TABLE support).
migrate = Migrate(render_as_batch=True)


def create_app(test_config=None, instance_path=None):
    app = Flask(__name__, instance_relative_config=True, instance_path=instance_path)
    app.config.from_object(DefaultConfig)
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///" + os.path.join(app.instance_path, "turnpilot.db")
    if test_config:
        app.config.update(test_config)

    os.makedirs(app.instance_path, exist_ok=True)
    db.init_app(app)
    # The schema is managed by migrations (migrations/ folder), not by db.create_all().
    migrate.init_app(app, db, directory=os.path.join(os.path.dirname(app.root_path), "migrations"))

    from .routes import bp
    from .seed import seed_command

    app.register_blueprint(bp)
    app.cli.add_command(seed_command)

    @app.errorhandler(RequestEntityTooLarge)
    def file_too_large(_error):
        limit_mb = app.config["MAX_CONTENT_LENGTH"] / (1024 * 1024)
        flash(f"The file is too large (limit {limit_mb:g} MB).", "error")
        return redirect(url_for("main.upload_drawing"))

    return app
