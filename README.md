# TurnPilot

A small web app that builds a turning process sheet (operation list) for a single CNC lathe.

## Problem

Planning a turning job is repetitive: for every part the planner orders the operations again,
picks tools that are actually loaded in the turret, looks up cutting data and converts it to
spindle speed. Mistakes are common: a tool that is not in the turret, a speed that exceeds the
machine's max RPM, or a finishing feed that cannot reach the required surface roughness.

## Solution

TurnPilot works for one machine. The machine profile and the turret (T1..T12) are set up once.
After that the user:

1. creates a job (material, quantity, blank size);
2. enters the part features: face, OD turning, groove, thread, bore, chamfer, parting;
3. presses **Calculate**, and the planner proposes operations, tools and cutting data;
4. approves or edits each operation. Every change is written to an edit log (old value, new value, time).

Nothing is ever deleted. Pressing **Calculate** again archives the current operations as a numbered
calculation version and creates a new one; archived versions and their edit log are available on
the job's *Calculation history* page (read-only). Deleted features are only flagged, so history
still shows what they were.

Planning rules (`turnpilot/planner.py`, pure functions without Flask):

- **Order:** face → roughing → finishing (incl. chamfers) → grooves → threads → parting.
- **Tool selection:** only tools loaded in the turret, matched by operation type and material ISO
  group (P/M/N). If none fits, the operation is kept with a clear warning and cannot be approved.
- **Roughing:** Vc near `vc_min`, f near `f_max`. Roughing leaves the finishing allowance
  (the finishing tool's ap): radial stock = `(blank_diameter - diameter) / 2 - ap_finish`,
  split into equal passes: `passes = ceil(stock / ap_max)`, `ap = stock / passes`.
- **Finishing:** Vc near `vc_max`, ap = `ap_min`, feed from the target roughness
  `f = sqrt(Ra * 32 * r_eps / 1000)` (Ra in µm, nose radius r_eps from the insert code),
  clamped to the tool's `f_min..f_max`.
- **Chamfers** are not separate operations: they are machined in the finishing pass of the OD
  (or bore) with the same diameter, noted "incl. chamfer". A chamfer without such a feature
  gets its own finishing pass.
- **Grooves:** n is calculated on the start (larger) diameter, not the groove bottom. The table
  shows insert width and groove depth per side instead of ap. Feature fields: Diameter = bottom,
  Start Ø = diameter the groove is cut from (blank diameter if empty).
- **Parting:** insert width and depth per side instead of ap. Depth = diameter / 2 (the parting
  feature's diameter, or the blank), or down to the smallest bore if the part has one.
  Note: "reduce feed ~50% for last 2 mm before center" for a solid part, or
  "reduce feed ~50% for last 2 mm before breakthrough into bore" when parting to a bore.
- **Threads** (external metric): feed = pitch, profile depth per side `h = 0.613 * pitch`.
  Radial infeed that never increases from pass to pass: decreasing depth by the modified
  constant chip area method (first pass within the tool's `ap_max`). If that series would need a
  pass thinner than `ap_min`, the whole depth is split into equal passes instead. A final spring
  pass follows. The table shows h and the number of passes; the infeed schedule is in the notes,
  together with "G97 constant RPM — required for threading".
- **Spindle speed:** `n = 1000 * Vc / (pi * D)`, capped at the machine max RPM. Roughing uses the
  diameter before the pass (the blank diameter). Facing and parting are marked
  "G96 constant surface speed, capped at max RPM".

> The seed cutting data are **placeholders** and have not been validated. Replace them with values
> from your tool catalogue before real use.

## Stack

Python, Flask, SQLAlchemy (Flask-SQLAlchemy), Flask-Migrate (Alembic), SQLite, Jinja2 with plain CSS, pytest.

## Run

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # Linux / macOS
pip install -r requirements.txt

flask --app turnpilot db upgrade # create / update the database schema
flask --app turnpilot seed       # insert seed data into an empty database
python run.py                    # http://127.0.0.1:5000
```

`python run.py` also applies pending migrations and seeds an empty database on start,
so the two `flask` commands are optional for local use.

The SQLite database is stored at `instance/turnpilot.db`.

## Database migrations

The schema is managed by Flask-Migrate; the app never calls `db.create_all()` on a real database.
After changing a model:

```bash
flask --app turnpilot db migrate -m "describe the change"   # generate a migration in migrations/versions/
flask --app turnpilot db upgrade                           # apply it; existing data is kept
```

Review the generated file before committing it. SQLite changes are rendered in batch mode, so
columns can be altered or dropped. `tests/test_migrations.py` fails if the models and the
migrations drift apart. Useful extras: `flask --app turnpilot db current`, `db history`, `db downgrade`.

## Tests

```bash
pytest
```

## Project layout

```
turnpilot/
  __init__.py      app factory
  models.py        SQLAlchemy models
  planner.py       pure planning logic
  services.py      ORM <-> planner glue, edit logging
  routes.py        pages
  seed.py          seed data and `seed` CLI command
  templates/, static/
migrations/        Alembic migrations (Flask-Migrate)
tests/             pytest suite
```

## Seed data

- Machine "Lathe 1": 4000 rpm, 11 kW, max Ø300 mm.
- Materials: Steel 45 (P), AISI 304 (M), Aluminium 6061 (N).
- Turret T1–T9: facing, two roughing, two finishing, grooving, threading, parting, boring bar.
  The grooving tool intentionally does not cover ISO M, so an AISI 304 job with a groove
  shows the "tool missing" warning.
