# turnpilot — notes for a new chat

Flask app that builds a lathe (turning) process sheet for one CNC machine, and reads part drawings
with Claude vision. Code, comments and README in English; the user writes in Ukrainian.

## Layout
- `turnpilot/` — app factory (`__init__.py`), `config.py`, `models.py`, `planner.py` (pure planning
  rules), `services.py`, `routes.py`, `templates/`.
- Drawing reading: `drawing_reader.py` (features mode, default), `dimensions_first.py`
  (`TURNPILOT_READ_MODE=dimensions_first`), `extraction_schema.py` (pydantic + strict tool schema).
- `tools/generate_drawings.py` (synthetic drawings 01–07 + expected answers),
  `tools/eval_extraction.py` (real API eval, not part of pytest).
- `migrations/` (Flask-Migrate, `render_as_batch=True`; name every FK), `tests/`.

## Commands
- Tests: `.venv/Scripts/python -m pytest` (live API tests are marked `live` and deselected).
- Migrations: `flask db migrate -m "..."` then `flask db upgrade`. Stop the dev server first.
- Demo server: `.claude/launch.json` (port 5000), runs with
  `env -u ANTHROPIC_BASE_URL -u TURNPILOT_READ_MODE`.
- Eval: `env -u ANTHROPIC_BASE_URL -u TURNPILOT_READ_MODE PYTHONIOENCODING=utf-8 .venv/Scripts/python
  tools/eval_extraction.py --only <name> --groups <g> --mode features --output instance/<file>.md --yes`.
  Also `--repeat`, `--compare LABEL SOURCE`, `--rescore` (re-score saved `*.runs.json`, no calls).

## Rules (from the user)
- Відповідай українською (answer the user in Ukrainian); code, comments and README stay in English.
- No paid API calls without the user's explicit "так" and the stated number of calls.
- Always unset `ANTHROPIC_BASE_URL` for API calls. Never print the API key; `.env` is gitignored.
- `real_*` drawings and expected answers stay local (gitignored, copyright). Eval reports go to `instance/`.
- Show a plan first when asked and wait for "ок"/"так". Separate commits per fix, push to `main`.
- Temporary scripts live in the scratchpad, never in git. Write files as UTF-8 with `\n` (Windows cp1252 breaks Ø).
- Never auto-correct lengths: warn only. Mark derived/uncertain values with a "check" badge.
- Label in-sample tuning honestly. Don't change the prompt unless asked.

## Status (2026-09-28)
- 445 tests pass. Features is the default mode (dimensions_first ~2× tokens, no clear win out of sample).
- real_02 (bushing, Vtulka.pdf), 1 call features after the latest fixes: everything 100% except
  roughness 3/5 — Rz 20 was put on the bore Ø16 instead of the thread M14×2-7H.
- Open: eval plan for the taper-end rule in features — option A (6 calls) or B (11 calls), not approved.
