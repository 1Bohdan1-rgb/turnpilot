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

Planning rules (`turnpilot/planner.py`, pure functions without Flask):

- **Order:** face → roughing → finishing (incl. chamfers) → grooves → threads → parting.
- **Tool selection:** only tools loaded in the turret, matched by operation type and material ISO
  group (P/M/N). If none fits, the operation is kept with a clear warning and cannot be approved.
- **Roughing:** Vc near `vc_min`, f and ap near the top of their ranges.
  Passes = `ceil((blank_diameter - diameter) / 2 / ap)`.
- **Finishing:** Vc near `vc_max`, ap = `ap_min`, feed from the target roughness
  `f = sqrt(Ra * 32 * r_eps / 1000)` (Ra in µm, nose radius r_eps from the insert code),
  clamped to the tool's `f_min..f_max`.
- **Threading:** feed = thread pitch.
- **Spindle speed:** `n = 1000 * Vc / (pi * D)`, capped at the machine max RPM. Roughing uses the
  diameter before the pass (the blank diameter). Facing and parting are marked
  "G96 constant surface speed, capped at max RPM".

> The seed cutting data are **placeholders** and have not been validated. Replace them with values
> from your tool catalogue before real use.

## Stack

Python, Flask, SQLAlchemy (Flask-SQLAlchemy), SQLite, Jinja2 with plain CSS, pytest.

## Run

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # Linux / macOS
pip install -r requirements.txt

flask --app turnpilot seed       # create tables and seed data (optional: run.py seeds an empty DB)
python run.py                    # http://127.0.0.1:5000
```

The SQLite database is stored at `instance/turnpilot.db`.

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
tests/             pytest suite
```

## Seed data

- Machine "Lathe 1": 4000 rpm, 11 kW, max Ø300 mm.
- Materials: Steel 45 (P), AISI 304 (M), Aluminium 6061 (N).
- Turret T1–T9: facing, two roughing, two finishing, grooving, threading, parting, boring bar.
  The grooving tool intentionally does not cover ISO M, so an AISI 304 job with a groove
  shows the "tool missing" warning.
