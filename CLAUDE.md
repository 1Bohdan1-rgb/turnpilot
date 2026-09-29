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

## Status (2026-09-29)
- 507 tests pass. Features is the default mode (dimensions_first ~2× tokens, no clear win out of sample).
- The new features prompt `b423ae0e` stays. It replaces `d7924a66` and adds the taper-end rule, Rz,
  general tolerance, chamfer position and internal thread. Taper-rule eval, option B (11 calls):
  - 07 diameters 81→90%;
  - 06 lengths 50→58%;
  - no regression on 01–05.
  Reports: `instance/eval_results_taper_*.md`.
- Watch: on 03 the model recorded chamfer Ø50 as internal (Ø30). Is the new external/internal field the
  cause? Check on the next runs.
- Open problems, to work through on real drawings:
  - the baseline dimension chained over the groove (06: Ø48/thread 20 instead of 16);
  - Ra/Rz on a leader goes to the wrong feature (03, 05, real_02).
- Hex (commits 232e296 … ed561db):
  - Feature type `hex`: `diameter` across corners, `across_flats` S. If one is missing, the code
    computes it and marks it "check". With both given, D need not equal S/cos 30°: warn only if D < S
    or D < 1.10·S (corners cut off).
  - Planner: turn to D, then mill the flats with a `milling` (driven) tool, or a manual operation.
  - Hex bar stock: sizes on the Machine page, `Job.blank_shape` round/hex. A hex bar is suggested only
    when the hex is in stock and every other diameter is ≤ S. If S is tighter than h11, the flats are
    milled.
  - In the features prompt since f8da656, prompt version `b423ae0e` → `ae696380`:
    - hex type and `across_flats` (0 = none, converted to None on input);
    - a rule: one hex feature, not also an od_turn;
    - a chamfer given with the hex's S or corners is put on the corners by the code.
  - NOT MEASURED YET. The step-3 eval waits for the user's separate "так", and the user is still
    confirming the real_03 expected answer. Plan (20 calls):
    - 08–10 ×3;
    - real_03 ×3;
    - 01–07 ×1 and real_02 ×1 regression.
  - Success criteria:
    - hex found in ≥8/9 runs on 08–10 and S right;
    - hex recorded as od_turn 0 times on 08–10;
    - hex found on real_03;
    - lengths of 10 and real_03 on their own line in the report;
    - no regression over one run on 01–07 and real_02.
  - Synthetic hex drawings: 08_hex_s_only, 09_hex_d_and_s (D 19.4 ≠ S/cos 30°), 10_hex_middle.
    10 is built like real_03, so they are not independent evidence (noted in the eval report).
- Known limitation: dimensions_first does not know hex (its tool schema and prompt `e91adddb` have
  no hex); a hex drawing read in that mode loses the hex.
- Thread section: an external thread's od_turn is matched by diameter and length (a fitting has a Ø10
  collar and an M10 thread).
- Eval scorer:
  - a section with shoulders on both sides may be a groove or an od_turn (diameter, length, position
    count); such pairs are reported in a "Type mismatch" column;
  - new metric `across_flats`.
- real_03 (fitting, shtucer.pdf, extraction 24):
  - The expected answer has the hex as `hex` Ø13. The user is checking the drawing: don't change it.
  - Extraction 24 (old prompt): no hex, no relief Ø8 L1.5; the Ø8 recess read with L3.5 instead of 8.
