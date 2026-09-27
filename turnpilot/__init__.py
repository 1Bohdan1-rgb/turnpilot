import os

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

    return app
