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

## Project goal (set 2026-09-30)
Read ≥ 90% of the fields in each of 3 runs, on 3–6 part types (shaft, bushing, fitting, flange, screw),
with 2–3 real drawings per type that the system has not seen (hold-out). Tuning on a drawing makes it
in-sample: keep the hold-out drawings untouched until they are measured.

## Number check, stage 1 (2026-09-30)
- `pdf_text.py` reads the numbers of a CAD PDF with vector text:
  - KOMPAS Symbol_A codes are mapped (Ç = Ø, Å = °), × is written as •, and Cyrillic 7Н becomes 7H;
  - duplicated spans are read once and the title block is skipped;
  - an image or a scan gives None.
- `number_check.py` compares the answer with the drawing both ways, shows the result on the review
  screen (Geometry check, "check: computed as 44 − 14" badges) and in the eval ("Number check" column
  and section).
- Works ONLY for CAD PDFs with vector text. It does NOT check which section a number belongs to: that
  is stage 2 (binding by the dimension lines' coordinates), not started, to be planned after these
  results.
- Measured on all saved runs, no calls (report `instance/eval_results_number_check.md`):
  - real_02: the mirrored reading 16/28/14/30 is not caught (0 of 5 wrong runs), since 28 = 44 − 16
    and 30 = 44 − 14;
  - real_03: 8 of 9 wrong runs warned, always indirectly ("11.5 / 8 on the drawing is not used"); no
    right run exists yet;
  - no false alarms on right runs. But the right answers themselves warn in two cases: real_03 if the
    computed lengths are not marked (11.5 unused), and 06 always (40 = 90 − 30 − 20 needs three
    numbers);
  - the difference/sum rule lets through 0–27 values per drawing (real_02: 5, real_03: 14). One real
    coincidence hid an error: 06 Ø60 L60 = 90 − 30 (expected 40), and that run became silent.
- real_02 and real_03 expected answers mark their computed lengths (length_derived: real_02 L30 and L28,
  real_03 L8 and L1.5); these files are local only.

## Deferred
- dimensions_first on real_02 ×3 (3 calls; 9 for a fair comparison: 6 vs 6 with features). The
  question: does the code's solver remove the end-face confusion? Expectation: probably not. The
  mirrored reading 16 / 28 / 14 / 30 sums to 44 like the right one, so a consistent wrong binding
  gives no conflict. The value would be in seeing which boundaries the model binds each dimension to.
  Compare with the 9 features runs already made (right reading 5/9). Not approved; do not run.

- 565 tests pass. Features is the default mode (dimensions_first ~2× tokens, no clear win out of sample).
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
  - Ra/Rz on a leader goes to the wrong feature (03, 05, real_02);
  - real_02: which end face the lengths are measured from. The model switches between the right reading
    (Ø20 L14, Ø25 L30, bore L16, thread L28) and 16 / 28 / 14 / 30, on every prompt;
  - real_03: the dimension chain (3.5 / 8 / 3.5 / 1.5 / 8) is not read, and the relief Ø8 L1.5 is
    missing in 9/9 runs on all prompts.
- Hex (commits 232e296 … ed561db):
  - Feature type `hex`: `diameter` across corners, `across_flats` S. If one is missing, the code
    computes it and marks it "check". With both given, D need not equal S/cos 30°: warn only if D < S
    or D < 1.10·S (corners cut off).
  - Planner: turn to D, then mill the flats with a `milling` (driven) tool, or a manual operation.
  - Hex bar stock: sizes on the Machine page, `Job.blank_shape` round/hex. A hex bar is suggested only
    when the hex is in stock and every other diameter is ≤ S. If S is tighter than h11, the flats are
    milled.
  - In the features prompt since f8da656, current version `ae696380` (b0b174d):
    - hex type and `across_flats` (0 = none, converted to None on input);
    - a rule: one hex feature, not also an od_turn;
    - a chamfer given with the hex's S or corners is put on the corners by the code.
  - Measured (reports: `instance/eval_results_hex_*.md`, `instance/eval_results_regr_summary.md`):
    - hex works: found in 12/12 runs on 08–11 and recorded as od_turn 0 times. 08–10 and the 01–07
      regression were measured on `dd7fd70a`, which is `ae696380` plus the chamfer-circle sentence;
    - the chamfer-circle sentence ("a Ø on the circle tangent to the flats is S") did not help: S right
      on 11 in 1/3 runs with it and 0/3 without; on real_03 0/3 on both. It was removed (b0b174d).
      real_03 was not an independent check of it anyway (written after reading real_03);
    - the code check "hex Ø..: probably S.. on the chamfer circle" with the "this is S" button on the
      review screen flags every misread run (11 and real_03) and no correct one;
    - no real_02 regression: the old prompt `b423ae0e` also reads the wrong lengths in 2/3 runs. This
      is an old instability of that drawing, not the hex rule;
    - output tokens: the answer is 5–13% longer (the across_flats field). Thinking varies from run to
      run on every prompt; the doubling seen in single runs did not hold up over 3 runs.
  - Synthetic hex drawings: 08_hex_s_only, 09_hex_d_and_s (D 19.4 ≠ S/cos 30°), 10_hex_middle,
    11_hex_chamfer_circle (S16 only as Ø16 on the chamfer circle).
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
  - Expected answer confirmed by the user:
    - the hex is `hex` across_flats 13, diameter null (Ø13 on the end view is on the circle tangent to
      the flats, i.e. S13; the code computes D 15.01, marked check);
    - brass, and S13 is in the bar list, so the expected blank is hex bar S13 with the flats not
      machined.
  - Extraction 24 (old prompt): no hex, no relief Ø8 L1.5; the Ø8 recess read with L3.5 instead of 8.
