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

1. creates a job (material, quantity, blank size) by hand, or uploads a drawing and lets Claude
   read it (see [Reading drawings](#reading-drawings-with-claude));
2. enters or checks the part features: face, OD turning, groove, thread, bore, chamfer, parting;
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
  The OD section under an external thread (an od_turn with the thread's nominal diameter) is
  roughed and finished to `d - 0.1 * pitch` (Ø19.85 for M20x1.5), noted "major diameter for thread".
  The feature keeps the nominal diameter from the drawing.
- **Grinding check:** a finishing operation whose tolerance is IT5 or finer (fit grade such as `h5`,
  or a numeric band within IT5 for the diameter, ISO 286) or whose Ra ≤ 0.4 µm gets the warning
  "may require grinding — not guaranteed by turning".
- **Spindle speed:** `n = 1000 * Vc / (pi * D)`, capped at the machine max RPM. Roughing uses the
  diameter before the pass (the blank diameter). Facing and parting are marked
  "G96 constant surface speed, capped at max RPM".

> The seed cutting data are **placeholders** and have not been validated. Replace them with values
> from your tool catalogue before real use.

## Reading drawings with Claude

**Upload drawing** (`/jobs/upload`) accepts PNG, JPG and PDF up to 10 MB. The file type is checked
by extension *and* content, and the name is sanitized. For a PDF the first page is rendered to PNG
(pymupdf); images larger than 2576 px on the long edge are downscaled.

`turnpilot/drawing_reader.py` sends the image to Claude (model from `ANTHROPIC_MODEL`, default
`claude-sonnet-5`) with one strict tool, `record_part`, whose JSON schema matches our models:
material, blank size, overall length, quantity and features (type, diameter, start diameter,
length, tolerance, Ra, pitch) with a confidence per feature, plus warnings. The model is told to
use `null` for anything not visible on the drawing instead of guessing. The tool input is
validated with pydantic (`turnpilot/extraction_schema.py`); invalid output is rejected.
The request is streamed with an output budget of 64000 tokens (thinking included: a detailed drawing
used all of the earlier 16000 on thinking). If the budget still runs out, the user sees
"Drawing too complex, try again" and the token usage is logged and kept with the upload.

Nothing becomes a job until a person checks it. The review screen shows the drawing next to an
editable form:

- the model also classifies the part (`part_type`: turned / not_turned / unclear). For anything but
  `turned` a banner says "This does not look like a lathe part" and Confirm stays disabled until
  "I understand, create anyway" is ticked (checked on the server as well);
- features with confidence below 0.7 are highlighted; model warnings are listed on top;
- drawing conventions the model follows: a roughness symbol without a leader in the top-right
  corner is the general Ra (`general_ra`), shown on every row without its own mark as *general*;
  section lengths may be derived from chain/baseline dimensions (confidence ≤ 0.8 and the warning
  "length derived from chain dimensions"); a narrow step next to a thread below its minor diameter
  is a thread relief groove;
- tapers (start Ø, end Ø, length) and fillets (radius) are recognised and shown; the planner
  lists them as "manual operation" without a tool;
- the material is matched to the materials list by name and common aliases;
- if the drawing has no blank size, one is **suggested** (marked as such): largest external Ø +
  2 mm rounded up to the next bar size from `BAR_STOCK_DIAMETERS` in `turnpilot/config.py`,
  length = overall length + 2 mm facing + the parting tool width;
- tolerances of IT5 or finer and Ra ≤ 0.4 get the grinding warning;
- when there is a bore without Ra and an OD with Ra, both rows get "Ra may belong to the bore —
  check" (the model tends to put a bore's Ra on the OD in sectioned views);
- facing and parting are added unless unticked (drawings rarely show them); if the model already
  returned a face or parting row, the checkbox starts unticked and never adds a duplicate.

Known limitations of the extraction are listed at the end of `docs/eval_results.md`.

**Confirm** creates the job and its features; then the usual **Calculate**. For the audit trail
every upload is kept in `DrawingExtraction`: the original file and the image sent to the model
(`instance/drawings/`), the model name, the full raw API response, the validated result, errors,
and the job it became.

**No repeated API calls.** The "Read drawing" button is disabled after the first click and shows
"Reading…". On the server, a file (same SHA-256) already read by the same model with the same
prompt version is not sent again. The prompt version (`drawing_reader.prompt_version()`) is a hash
of the prompts and the tool's JSON schema, stored with every upload, so any change to the prompt or
the schema makes the next upload call the API again instead of reusing an old result:
a new upload record reuses the saved result and is marked *cached*, so a second job can still be
made from it. **Read again** on the review screen makes a new API call on purpose. A second upload of
a file that is still being read (within 2 minutes) is rejected.

### Setup

```bash
copy .env.example .env           # Windows (cp on Linux / macOS), then set ANTHROPIC_API_KEY
```

### Test drawings and accuracy

`tools/generate_drawings.py` (needs `pip install -r requirements-dev.txt`) draws five parts with
matplotlib: a stepped shaft, a shaft with groove, chamfer and M20x1.5 thread, a bushing with a
THRU bore, a shaft with h6/f7 fits and Ra 0.8/1.6, and an ambiguous bushing whose bore length is
not on the drawing (checks that the model does not guess). Each comes as PNG, PDF and a "photo" JPG
(rotated 1–3°, lower resolution, JPEG artefacts, noise), plus `<name>.expected.json` with the
correct answer, all in `tests/fixtures/drawings/`.

The eval also includes **real drawings** (`tests/fixtures/drawings/real_*`, each with a hand-written
`real_*.expected.json`), reported as a separate *real* group. They are **not published** in this
repository because of copyright: `real_*` is in `.gitignore` and the files stay on the local machine.
A clone of the repository runs the eval on the generated drawings only.

`tools/eval_extraction.py` runs all of them through the **real API** and reports the share of
correct diameters, lengths, tolerances, Ra values and materials, separately for clean, photo and
real drawings, in the console and in `docs/eval_results.md`. It costs money (one API call per
drawing variant, 15 for the generated set), asks for confirmation (`--yes` to skip) and is not part
of pytest.

```bash
python tools/eval_extraction.py
```

pytest never calls the API: `tests/test_drawing_reader.py`, `tests/test_upload.py` and
`tests/test_eval_scoring.py` use a fake client.

## Stack

Python, Flask, SQLAlchemy (Flask-SQLAlchemy), Flask-Migrate (Alembic), SQLite, Jinja2 with plain CSS,
Anthropic Python SDK, pydantic, pymupdf, python-dotenv, pytest. matplotlib and Pillow for the
test drawing generator only.

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
  planner.py       pure planning logic (incl. blank suggestion, grinding check)
  drawing_reader.py  Claude vision: image preparation, request, response validation
  extraction_schema.py  record_part tool schema + pydantic models
  config.py        settings: upload limit, model, bar sizes, allowances
  services.py      ORM <-> planner glue, edit logging, drawing upload
  routes.py        pages
  seed.py          seed data and `seed` CLI command
  templates/, static/
migrations/        Alembic migrations (Flask-Migrate)
tools/             generate_drawings.py, eval_extraction.py
docs/              eval_results.md
tests/             pytest suite; fixtures/drawings/ test drawings + expected answers
```

## Seed data

- Machine "Lathe 1": 4000 rpm, 11 kW, max Ø300 mm.
- Materials: Steel 45 (P), AISI 304 (M), Aluminium 6061 (N).
- Turret T1–T9: facing, two roughing, two finishing, grooving, threading, parting, boring bar.
  The grooving tool intentionally does not cover ISO M, so an AISI 304 job with a groove
  shows the "tool missing" warning.
