from flask_migrate import upgrade

from turnpilot import create_app
from turnpilot.seed import seed_database

app = create_app()

if __name__ == "__main__":
    with app.app_context():
        upgrade()  # apply pending migrations
        if seed_database():
            print("Empty database: seed data inserted.")
    app.run(debug=True)
