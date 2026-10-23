import os

from flask import Flask, flash, redirect, request, url_for
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

    from .commands import gcode_demo_command, stock_states_command
    from .routes import bp
    from .seed import seed_command

    app.register_blueprint(bp)
    from . import i18n

    app.jinja_env.globals["_"] = i18n.template_gettext
    app.jinja_env.filters["label"] = i18n.label
    app.jinja_env.globals.update(link=i18n.link, strong=i18n.strong)

    @app.context_processor
    def _language():
        return {"lang": i18n.current_language(), "js_strings": i18n.js_strings}
    app.cli.add_command(seed_command)
    app.cli.add_command(gcode_demo_command)
    app.cli.add_command(stock_states_command)

    @app.errorhandler(RequestEntityTooLarge)
    def file_too_large(_error):
        catalogue = request.endpoint == "main.catalogues"
        limit_mb = app.config["MAX_CATALOGUE_LENGTH" if catalogue else "MAX_CONTENT_LENGTH"] / (1024 * 1024)
        flash(i18n.gettext("The file is too large (limit {mb} MB).", mb=f"{limit_mb:g}"), "error")
        return redirect(url_for("main.catalogues" if catalogue else "main.upload_drawing"))

    return app
