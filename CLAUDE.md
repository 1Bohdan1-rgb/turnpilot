# turnpilot — notes for a new chat

Flask app that builds a lathe (turning) process sheet for one CNC machine, and reads part drawings
with Claude vision. Code, comments and README in English; the user writes in Ukrainian.

## Layout
- `turnpilot/` — app factory (`__init__.py`), `config.py`, `models.py`, `planner.py` (pure planning
  rules), `services.py`, `routes.py`, `templates/`.
- Drawing reading: `drawing_reader.py` (features mode, default), `dimensions_first.py`
  (`TURNPILOT_READ_MODE=dimensions_first`), `extraction_schema.py` (pydantic + strict tool schema).
- DXF input (no model): `dxf_reader.py` (profile + binding, ported from the prototype), `dxf_input.py`
  (rows + binding report). Tests build synthetic KOMPAS-like DXF files with `tests/dxf_drawings.py`.
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
- Відповідай українською (answer the user in Ukrainian); code, file names, commit messages, code comments and
  README stay in English.
- No paid API calls without the user's explicit "так" and the stated number of calls.
- Always unset `ANTHROPIC_BASE_URL` for API calls. Never print the API key; `.env` is gitignored.
- `real_*` drawings and expected answers stay local (gitignored, copyright). Eval reports go to `instance/`.
- Show a plan first when asked and wait for "ок"/"так". Separate commits per fix, push to `main`.
- Temporary scripts live in the scratchpad, never in git. Write files as UTF-8 with `\n` (Windows cp1252 breaks Ø).
- Never auto-correct lengths: warn only. Mark derived/uncertain values with a "check" badge.
- Label in-sample tuning honestly. Don't change the prompt unless asked.
- Say "operator" ("оператор"), never "machinist" ("машиніст"), in every text. Three comments in the files
  frozen by `dxf-blind-freeze` (dxf_reader.py ×1, dxf_input.py ×2) still say "machinist": change them after
  the stage 3 run.

## Project goal (set 2026-09-30)
Read ≥ 90% of the fields in each of 3 runs, on 3–6 part types (shaft, bushing, fitting, flange, screw),
with 2–3 real drawings per type that the system has not seen (hold-out). Tuning on a drawing makes it
in-sample: keep the hold-out drawings untouched until they are measured.

## Vision and roadmap (set 2026-10-07)
Product goal: TurnPilot reads the drawing correctly → chooses the cutting data and the tools correctly →
builds the machining route correctly → prepares the process for running. Running always comes after the
operator has checked it: a person presses "Start".

Principle: no invented number. Every number is stored with its source (machine documentation, tool
catalogue, handbook, ISO), and the operator or the process engineer confirms it.

Stages, in this order:
1. Edit and delete tools on the Machine page. **Done 2026-10-07** (a9bc72c, 4e9fce6): see "Tool library".
2. Check every placeholder constant (see "Placeholders and assumptions") against the catalogue and the
   operator's experience. **Done 2026-10-08 for steel P1.2** (Sandvik 2020): see the Status column; open
   rows stay open (other materials, machine data, rules not in the catalogue).
3. Blind check of the DXF input on new files (see "DXF blind check: stop rule"). **Code frozen 2026-10-08**
   (tag `dxf-blind-freeze`): waiting for the new drawings and the expected answers.
4. Machine documentation: upload the PDF → the model reads the data → the operator confirms it on a review
   screen. **Built 2026-10-08** (see "Stage 4: machine passport"): waiting for the real passport; its one paid
   reading only with the user's "так" and the number of calls.
5. Cutting data from a catalogue or handbook: the catalogue is uploaded once → the model extracts the table →
   a person confirms it → the planner takes its numbers only from that table, with the source in the note.
   **Built 2026-10-08** (see "Stage 5: cutting data from catalogues"): in-sample reading 66 / 66 numbers right
   (2 calls); the hold-out waits for the user's expected answer.
6. G-code from the part zero (Z0 on the end face, X0 on the axis) from the DXF contour coordinates: the outer
   profile first, one control, simulation required.
   **Built 2026-10-09** (see "Stage 6: G-code"): the operator checked the Zavisa 36 pin program as a turner; ready
   to run waits for the machine's real values (passport, the operator's programming values).
7. Probing the blank and sending the program to the machine. **7.3 (the part in 3D from the simulation) built
   2026-10-22** (see "Stage 7.3"). **7.1 (machines, orders, the shop page by hand) and 7.2
   (the shop plan in mm) built 2026-10-17** (see "Stage 7.1", "Stage 7.2"); the rest **planned 2026-10-10, not started**: it starts only
   once the machine's passport shows a probe and the custom macro option (Fanuc Custom Macro B).
   - At the program's start a probing block (Custom Macro B) measures the blank's real Ø and stick-out and sets
     Z0 on the face.
   - The measured blank is compared with the blank of the TurnPilot plan: too short, stuck out otherwise, or a Ø
     larger than planned (a cut deeper than ap max, speeds for another Ø) → alarm #3000 with the reason; no cut.
   - A tool setter probe: the tools' length offsets come from the measurement, not typed by hand.
   - The program is sent to the machine chosen on the shop map (Ethernet).

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

## Number check, stage 2: binding numbers to sections (prototype, 2026-10-01)
Option A: the code reads the dimension lines and the contour from the PDF geometry and binds each
number to the section it spans. The prototype is NOT in the repo: it lives in the session scratchpad
(`bind_prototype.py`, `bind_prototype_v2.py`).
- What works:
  - step 2 on all 4 drawings tried: dimension lines (thin horizontal line, arrow at both ends, number
    above) and the scale (line length / number, spread ≤ 0.07 pt/mm, matches the title block). This
    also holds on the 2 synthetic drawings by another author (not KOMPAS, other line widths).
  - real_02: profile and binding right (14 → Ø20, 16 → bore). On the 9 saved runs it catches 5/5
    wrong runs (incl. the mirrored reading that stage 1 misses) with 0/4 false alarms.
  - real_03 (after extending v2 while looking at it): 5/5 sections right, incl. the recess L8 and the
    relief L1.5 that the model never read, and the hex Ø15.01 across corners; 7/7 dimensions bound.
    Not done: the Ø5 through bore.
- What does not work (found on test_01/test_02 from another author, before they were looked at):
  - the axis is taken from the points where KOMPAS splits the end-face lines into two halves. Other CAD
    systems draw them whole, so there is no axis and the profile is never built;
  - short dimensions drawn with the arrows outside and the line extended (3, 4, 7 on test_02) are not
    recognised.
- In-sample caveat: v1 was written looking at real_02 and v2 at real_03, so their success there is
  in-sample. test_01/test_02 were the only independent check, and they failed at the axis. Once the
  general fixes (axis from the centre line / symmetry, outside arrows) are made looking at test_01/02,
  those become in-sample too.
- Version 3 (`bind_prototype_v3.py`, scratchpad), two general techniques plus two fixes inside them:
  - the axis comes from the dash-dot centre line (a dash pattern of 4+ numbers spanning the part).
    Without one (KOMPAS draws it as plain pieces) it comes from the contour's symmetry: midpoints of
    pairs of thick horizontal lines, with candidates closer than 0.3 pt merged;
  - a dimension runs between the tips of the two arrow heads on its line (tip = narrow end; touching
    filled pieces are merged first, since KOMPAS draws a head as two halves). This also finds short
    dimensions with the arrows outside;
  - result, with all four drawings now in-sample:
    - real_02, real_03, test_01: steps 2–4 right (real_02 still 5/5 caught, 0/4 false alarms);
    - test_02: 10/10 dimensions and 6 of 7 turned sections. Ø38 L50 is split 7 + 36 + 7 by the keyway
      seen in the upper half of the contour (an envelope of both halves would fix it; not done).
      Internal features shown with hidden lines are not found.
- Stop rule (from the user): on new drawings the prototype has not seen, if it breaks on the basic steps
  again (dimension lines, scale, axis, sections), option A is stopped and recorded as a conclusion.
- Option B (numbers with coordinates in the prompt) was not tried. It would not solve the binding by
  itself, since the model already reads the numbers right; it needs a prompt change and paid runs.
- **Option A is STOPPED (2026-10-01) by the stop rule.**
  - Run: `bind_prototype_v3.py` unchanged (md5 befbc789285eb7e1f1d9071421874848) on new drawings it had
    not seen. The basic steps broke on 2 of 3 drawings:

    | Drawing | Step 2: dims, scale | Axis | Step 3: sections | Step 4: binding |
    |---|---|---|---|---|
    | test_03 bushing | 4/4, 2:1 | centre line | outer right; inner without chamfers | 3/4 (15 not bound) |
    | test_04 axle | **0/6: open arrows (two lines)** | — | — | — |
    | test_05 screw | 5/5, 2:1 | symmetry | outer 4/4; **phantom inner Ø7.49 from the hex edge lines** | 3/5 (7 ambiguous, S13 on the other view) |

  - Long tail: each new drawing brought a new kind of failure (open arrows, inner chamfers, hex edge
    lines, a dimension on another view). Unambiguous binding: 0 of 3.
  - Caveat: test_03–05 are synthetic (generated in chat), not a hold-out of real drawings.
- What stays in the product: stage 1 only (`pdf_text.py` + `number_check.py`). The operator checks the
  binding on the review screen.
- Deferred: a narrow mode "warn only when everything is recognised unambiguously". On the new drawings
  it would have bound 0 of 3, i.e. stayed silent.
- `bind_prototype_v3.py` and `v3_new.txt` stay in the session scratchpad for reference only. They are
  not to be moved into the product.

## Run disagreement as an uncertainty signal (analysis, 2026-10-02)
- Hypothesis: read each drawing N=3 times and mark a section's length "check" when the runs disagree.
- Measured on saved runs only, no calls, nothing in the product (script and table in the session
  scratchpad: `run_disagreement.py`, `run_disagreement.md`).
  - Data: 24 groups of the same drawing, prompt and mode, with 2–3 runs.
  - Unit: one expected section with a length, in one run. Flagged when the group's runs give it
    different lengths (missing counts as its own value).
- Results:

  | Mode | Section×run | Wrong | Caught | Missed (runs agree) | False alarms | Flag precision |
  |---|---|---|---|---|---|---|
  | features | 253 | 102 | 70 (69%) | 32 | 34 (23% of right) | 67% |
  | dims | 108 | 52 | 43 (83%) | 9 | 14 (25% of right) | 75% |

- It catches unstable errors, i.e. what stage 1 cannot:
  - real_02's mirrored reading: 8/8 on prompts b423ae0e and dd7fd70a;
  - no flags where the model is stably right (08–11, real_02 on ae696380).
- It misses systematic errors, where every run is wrong in the same way (31% of wrong lengths in
  features):
  - 06 Ø48/thread L20 instead of 16;
  - real_03: the relief missing in 9/9 runs; thread L9.5.
  Agreement is not correctness.
- A "false alarm" here is a right run flagged because another run differs: the section is genuinely
  unstable, so "check" is not useless there.
- Caveats:
  - few groups and only 2–3 runs, so the rates are rough;
  - 3 real drawings, and the synthetic ones are simpler;
  - some real triples are assembled from several runs with the same prompt (real_03 b423ae0e includes
    extraction 24 from the app).
- Cost: N=3 is 3× the calls and tokens per drawing (real_03: ~60k output tokens instead of ~20k), and
  3× the time unless the runs are parallel.
- Status: a candidate "check" signal, complementary to stage 1 (which catches different errors). Not
  implemented; the next step (a paid measurement or a product change) needs the user's decision.

## Combined "check" signal: stage 1 OR run disagreement (analysis, 2026-10-02)
- Measured on the same 24 groups of saved runs, no calls, nothing in the product (scratchpad:
  `combined_signal.py`, `combined_signal.md`).
  - Stage 1 flag of a section = what the review row shows: "check: computed as a − b" or "length X is not
    on the drawing".
- **Criterion (≥ 85% caught, ≤ 30% false alarms) NOT met: 73% caught, 35% false alarms** (CAD PDFs with
  text: 88 of 120 wrong lengths caught; 72 false alarms on right lengths).
  - Counting the drawing-level "N on the drawing is not used" as a check on every length of the run gives
    98% caught, but 44% false alarms: almost every length of an unstable drawing gets flagged.
- Stage 1 at section level adds nothing to run disagreement: 0 wrong lengths caught by stage 1 alone.
  Its section flags fire mostly on right computed lengths (by design: computed values are to be checked).
- Run disagreement is the only working signal: 69% caught in features, 83% in dims.
- Not caught by any method (32 in CAD PDFs, 9 without text): systematic errors, the same in every run:
  - a real number of the drawing bound to the wrong section (06 Ø48/thread L20 instead of 16;
    real_03 thread L9.5 instead of 8);
  - a section left out (real_03: the relief Ø8 L1.5, the hex length, the bore Ø5 length).
  This is the stage 2 class (binding), and stage 2 option A is stopped.
- Decision: no paid measurement of N=3 runs. Nothing changed in the product.

## DXF instead of PDF (probe, 2026-10-05)
- Hypothesis: read the dimensions from DXF, where a dimension is a DIMENSION entity with its value and
  its anchor points (defpoints). The binding to the geometry is then already in the file.
- Data: 3 real KOMPAS DXF files in `real_dxf/` (gitignored since 0994e80, local only, copyright):
  `деталь 1`, `Завіса 36`, `НД 012`. They are new parts, not real_02/real_03; there are no expected
  answers and no model runs for them yet.
- Tools: `ezdxf` 1.4.4 is installed in the venv for the scratchpad only (not in requirements.txt).
  Scratchpad: `dxf_probe.py` / `dxf_probe.txt` (read-only probe), `dxf_render.py` (PNG previews saved
  next to the DXF files, gitignored).
- Findings:
  - Format: DXF AC1021, codepage ANSI_1251, mm ($INSUNITS 4). KOMPAS shows in appid `KOMPAS` and the
    linetypes K5LT_THIN / K5LT_BASIC / K5LT_AXLED. Model space is at 1:1.
  - Dimensions are kept as DIMENSION entities (not exploded): 10 / 26 / 19, 55 in all.
    - Lengths are linear dimensions at 0°; diameters are linear at 90° with Ø in the text (no DIAMETER
      type); there are a few radius dimensions; chamfers ("1×45°") are linear on the leg.
    - Defpoints are present everywhere: 13/14 in every linear dimension, 10/15 in every radius one.
  - Field 42 is −1 in every dimension, so the value must be computed from the defpoints: for a linear
    dimension, the projection of 13→14 on the angle (50); for a radius one, the distance from 10 to 15.
  - The dimension text is in the `*D…` block, as several TEXT pieces ("Ø" | "30", "М" | "22" | "×" |
    "1,5-6g") that must be joined. KOMPAS symbol codes:  = Ø,  = ×,  = ±. Threads
    use a Cyrillic М.
  - Computed value = text in 54 of 55 dimensions. The one exception: Завіса 36 `Ø22+0,21`, where the
    geometry is drawn at 21.8. Such a dimension is to be flagged, not trusted.
  - Centre lines: K5LT_AXLED, horizontal, in all three files (Завіса 36 has two). The contour is
    symmetric about them: 100% / 100% and 93% / 100% of the K5LT_BASIC lines.
  - All three are turned parts:
    - деталь 1: head Ø30 with a cone, Ø22, M22×1.5-6g, a spherical end R10, lengths from the right face;
    - Завіса 36: TWO different parts on one sheet, not two views: a bushing in section (blind bore
      Ø22+0,21 × 32, length 80) and a pin (Ø22−0,21, length 112). Dimensions repeat because the parts
      share diameters. It has to be split by the centre lines;
    - НД 012: a formed profile with arcs R12.5 (concave), R20.46 (convex), R2.5, and an Ø45 holding stub
      with no length dimension. The arcs do not fit the simple section types (closest: fillet / taper).
  - Rendering (ezdxf drawing add-on): the Ø, × and ° glyphs show as boxes, since the font lacks the KOMPAS
    codes (in the PNG only; the DXF text is right). Three INSERT blocks of Завіса 36 crash ezdxf
    (zero-length MTEXT direction): the title block, the general roughness 6.3 and the note "H12, h12,
    ±IT12/2". They are drawn entity by entity and skipped.
- What this means: the basic steps that broke option A on PDFs (finding arrows, dimension lines, scale,
  axis) come straight from the file, and binding by the x of defpoints 13/14 looks direct.
- Not yet shown:
  - only KOMPAS DXF so far;
  - no expected answers;
  - no catch / false-alarm measurement (that needs model runs on these parts, which are paid);
  - multi-part sheets and formed arcs need handling.
- Status: probe only. No prototype of the binding, nothing in the product. The next step needs the
  user's decision.

## DXF binding prototype (scratchpad, 2026-10-05)
- Goal: build the outer profile (sections: type, Ø, length) from a KOMPAS DXF and bind every DIMENSION to
  its section(s) by the x of its defpoints. The prototype is `dxf_bind.py` in the session scratchpad. It
  is not in the product; there are no API calls; nothing from `real_dxf/` is committed.
- Plan agreed with the user:
  - Development on `деталь 1` and `НД 012` only.
  - Control: `Завіса 36`, variant (a). It is PARTLY SEEN: its dimension list and a render were looked at
    during the DXF probe, so it is not blind. A new DXF that has not been opened becomes the main control.
  - Expected answers are written by the user by hand in `real_dxf/<name>.etalon.md`, not generated from
    the DXF.
  - Scope is the outer profile only. Inner dimensions are listed apart as "not bound (inner)" and are
    not counted.
  - Success on Завіса 36: for each of its two parts, ≥ 90% of the dimensions bound to the right section,
    and every "geometry ≠ text" dimension flagged.
  - Stop if any basic step breaks on Завіса 36 (axis, splitting the parts, sections, binding).
  - Arcs get the type "arc".
  - The script's md5 is fixed before the control run.
- `деталь 1` (development drawing, so IN-SAMPLE):
  - 6/6 sections match the expected answer: Ø30 L10, taper Ø30→Ø22 L5, groove Ø20 L2, thread M22 L21 with
    a 1×45° chamfer on the right, od_turn Ø20 L2, arc R10 L10.
  - 10/10 dimensions bound right: 50 / 40 / 35 / 2 / 12 to boundaries; Ø30, Ø22, Ø20 and M22 to sections;
    R10 to the arc. The thread type is set from the M in the dimension text. No "geometry ≠ text" flags.
- Two general fixes made during development:
  - contour connectivity also counts T-junctions: a groove bottom meets the steps in their middle;
  - a dimension belongs to the part whose x range covers its defpoints, and the nearest axis decides
    between several parts. KOMPAS starts the second extension line of a baseline dimension on the
    previous dimension line (e.g. y −27.4 on an Ø30 part), so "defpoints within the radius" rejected
    50 / 40 / 35. Without this fix the score was 7/10.
- Not exercised yet:
  - the "nearest axis" rule with several parts (Завіса 36 will be the first);
  - inner profiles (out of scope);
  - angular dimensions.
- `НД 012` (development drawing, so IN-SAMPLE). The expected answer was corrected by the user after the
  geometry showed it:
  - section 0 is a stock Ø50 (not machined, no length) and section 1 is Ø45 L5 with a transition R2.5 on
    the right;
  - Ø38,5 (left) is the start of the R12.5 arc.
  - Result: 19/19 dimensions bound right in the final run (scratchpad `dxf_bind_nd012.txt`). An earlier
    run gave 18/18, with Ø45 left out while its expected row still pointed at section 0 (the stock).
  - Sections by x: 5 of 6 match. Expected section 4 "arc R20,46" (x 69–89) is drawn as an arc R51.61
    (69–79) plus a taper Ø35→Ø30 (79–89).
- `деталь 1` rerun with the final code: still 6/6 sections and 10/10 dimensions.
- Drawing ≠ dimension (found in НД 012 and flagged by the code; not binding errors):
  - the point of R20,46 lies on a drawn arc of radius 51.61, followed by a straight taper;
  - Ø35 stands at x 77, where the contour is Ø36.03.
- General rules in `dxf_bind.py` (none tuned to a part):
  1. Contour connectivity counts T-junctions.
  2. A dimension belongs to the part whose x range covers its defpoints; the nearest axis decides
     between parts.
  3. Sections run face/step to face/step:
     - a 45° diagonal with a leg ≤ 3 mm next to a flat is a chamfer of that flat;
     - other diagonals are tapers;
     - a cylinder narrower than both neighbours is a groove.
  4. A transition arc (tangent to a flat at one end and, at the other, to a real vertical step off the
     axis) is a fillet R at the boundary, not a section. Other arcs (e.g. a spherical end on the axis)
     are sections of type "arc".
  5. Length: the x of both ends; an end inside a section is reported as "inside section k".
  6. Diameter: the section whose radius at the dimension's x equals the value, arcs included.
     - On a boundary: the closest radius; if equally close, the constant-diameter section; else
       ambiguous.
     - If the defpoints are not on the contour and there is no inner contour there: the outer section at
       x, flagged "Ø ≠ contour".
  7. Radius: the arc or fillet its point (defpoint 15) lies on, flagged when the drawn radius differs.
  8. Chamfer: the chamfer of the section under its defpoints. M in the text makes the section a thread.
  9. Comparison is by geometry: the expected boundaries are placed in x by the expected lengths;
     - the stock is left out;
     - a diameter/radius is right when our section lies within the expected one;
     - duplicate texts are paired by agreement first.
- Current script md5: 5a3b83768e9a20a82015999e0ae2fc13 (not frozen yet; it is frozen before the control
  run).
- Next: the control run on Завіса 36 (partly seen, see above). Nothing in the product.
- **Control run on Завіса 36 (2026-10-06), the official result:**
  - A single run, with code that did not change after it was frozen:
    - `dxf_bind.py` md5 5a3b83768e9a20a82015999e0ae2fc13 (binding, unchanged since development);
    - `dxf_compare.py` md5 486339cd95bbce56a85334422114a28e (comparison only; it reads expected answers
      with several parts, section lists and "inner" rows, and imports the binding unchanged);
    - the md5 of both is in the scratchpad file `control_md5.txt`.
  - Basic steps:
    - 2 axes, 2 parts. The "nearest axis" rule worked: the x ranges overlap, and all 26 dimensions
      went to the right part;
    - sections: 5/5 on the bushing and 7/7 on the pin, all boundaries coinciding with the expected ones.
  - External dimensions bound right:
    - bushing 9/9 (100%);
    - pin 12/13 (92%). The one miss is the comparison, not the binding: Ø22−0,21 was bound right
      (section 6, the Ø22 neck), but the expected answer writes the minus as "−" (U+2212) while the DXF
      has "-", so the texts did not pair.
  - Criterion ≥ 90% met on both parts.
  - "Geometry ≠ text": the bushing's Ø22+0,21 (drawn at 21.8) is flagged.
  - Inner dimensions, outside the criterion: Ø22+0,21 and Ø10+0,15 are in the "not bound (inner)" list.
    The bore depths 32 and 5 were bound as lengths ending inside a section.
  - Caveats:
    - the sheet was partly seen before (dimension list and render during the DXF probe);
    - the parts are simple (cylinders, grooves, chamfers, one taper);
    - all three files are from KOMPAS;
    - inner profiles are out of scope.
- `dxf_compare.py` normalises "−" (U+2212) to "-" for future runs (added after the control run;
  Завіса 36 was not rerun).
- Observations for the planner (from НД 012, the user's reading; not implemented):
  - a section marked "not machined" (the roughness sign with a circle) is the bar held in the chuck, not
    part of the part;
  - the part is parted off at the end of the overall dimension;
  - when turning the first diameter, the parting tool's width (~3 mm) is added to the length.

## DXF input in the product (2026-10-06)
DXF → sections → review → planner, as a second input next to the model. Commits 4e19eec … 5c1fafb.
- `dxf_reader.py` is the prototype `dxf_bind.py` (md5 5a3b8376…) steps 1–5 with the same rules and
  tolerances; SystemExit became `DxfReadError`. Equivalence, field by field against the prototype on
  the 4 parts (деталь 1, НД 012, Завіса 36 bushing and pin): 100%, no difference (product md5
  cb7a837c…, scratchpad `equivalence.py` / `equivalence.txt`, not in git).
- `dxf_input.py`: Ø / tolerance / pitch / class / chamfer / R from the bound dimension's text, else from
  the geometry with a note. Lengths = distance between section boundaries, never corrected;
  `length_derived` when no single dimension gives it. A Ø dimensioned once for several sections is taken
  with a note naming the section. Inner and unbound dimensions become warnings.
- One `DrawingExtraction` per part (`dxf_part`, `binding` JSON; migration db9bd888d36a; DB backup
  `instance/turnpilot.db.bak-2026-10-06-before-dxf`). `read_mode` "dxf", `model` empty,
  `prompt_version` = "dxf:" + hash of the two modules. No API call.
- Feature type `arc` (formed section: start Ø, end Ø, R, length), a manual operation in the planner.
  It is NOT in the model's tool schema: the prompt versions stay ae696380 / e91adddb (a test checks it).
- `ezdxf==1.4.4` pinned in requirements.txt.
- Measured only in-sample / partly seen (see the prototype sections above). Not yet blind.
- Two metrics, kept apart (also in the README):
  - binding accuracy against the expected answers (outer profile): деталь 1 10/10 and НД 012 19/19
    (both in-sample), Завіса 36 bushing 9/9 and pin 12/13 (control, partly seen; the miss is the "−" vs
    "-" in the expected answer, the binding itself is right);
  - equivalence product = prototype: all dimensions, 100%.
- Seen on the demo server: on real sheets most lengths carry "check" (computed from a chain), since
  KOMPAS drawings dimension from a base. Risk: the operator gets used to the badge and ignores it.
  Idea for later, NOT to implement now: two levels of warning, "length from a chain of dimensions" and
  "length from the geometry only, no dimension".
- The not-machined chucked section (НД 012 §1 Ø50) is a row to untick by hand.

## Roughing from the neighbouring section (2026-10-06)
Commits ab942d5, f80f261, b7b0c7d. Plan agreed with the user (all three defaults).
- Bug found while planning, fixed first: a DXF threaded section gave only a thread row, so the planner
  never turned the Ø under the thread (НД 012 M24, деталь 1 M22). Now od_turn + thread, like the model.
- `Job.axial_order_known` (migration d5838ef1ecf1; DB backup `instance/turnpilot.db.bak-2026-10-06-
  before-axial-order`): True on Confirm from a DXF, cleared by "Add feature" (a new feature goes to the
  end of the list), kept by delete. Jobs read by the model or entered by hand: False, planned as before.
- `planner.rough_starts`: with a known order, the largest Ø (the chuck side) is roughed from the bar;
  every other od_turn / hex from the nearest turned section towards it with Ø ≥ its own (by the drawing's
  Ø), starting at the Ø that section is cut to (a thread's d − 0.1·P). Grooves, tapers, arcs, fillets
  are passed over. A section narrower than both neighbours: "check", roughed from the bar (not guessed).
  A largest Ø between smaller sections: note "machined from both sides (re-chucking is not planned)".
  The start Ø sets passes, ap, ref_diameter and n. The planner computes no machining time.
- Roughing passes (seed turret T2 ap_max 4, finishing allowance 0.2, suggested blank):
  - НД 012 (Ø55): §2 Ø45 2 → 1, §4 Ø38.5 3 → 1, §8 M24 (newly turned) 4 → 2; total 10 → 5;
  - Завіса 36 bushing (Ø38): 3 → 3 (all Ø34.8, the largest);
  - Завіса 36 pin (Ø38): §6 Ø22 2 × 3.9 → 2 × 3.1, n 1675 → 1829; total 5 → 5;
  - деталь 1 (Ø32): §4 M22 2 → 1, §5 Ø20 2 → 1; total 5 → 3.
- Existing tests did not change (the rule is off without a known order); test_dxf_input changed for
  the thread row.
- Not done: the chuck end is inferred from the largest Ø (no fixture data); re-chucking for a part
  machined from both sides; a wide groove (wider than the insert) is still one plunge.

## Wide grooves (2026-10-06)
Commits 1ee88c5, c757e24. Plan agreed with the user (all three defaults).
- Before: one plunge whatever the width; the insert by turret position only. A groove narrower than the
  insert was cut wider without a warning (деталь 1: 2 mm with the 3 mm insert).
- `select_grooving_tool`: the widest insert not wider than the groove; narrower than all: no tool, warning.
- `groove_plunges`: 1 + ceil((W − w) / (0.8·w)), first and last at the walls; step ≤ 0.8·w
  (`GROOVE_STEP_FACTOR`, placeholder constant: overlap ≥ 20% of the insert). W = w (±0.01): one plunge.
- Ra ≤ 1.6 (`GROOVE_FINISH_RA`, placeholder): plunges leave 0.2 mm (`GROOVE_FINISH_ALLOWANCE_MM`) on the
  walls and the bottom, then a finishing operation (same insert, finishing data). No room for it: plunges
  in full, warning "finishing needs a narrower insert".
- Real files: НД 012 and Завіса 36 grooves are 3 mm = the insert: unchanged. деталь 1 groove 2 mm: now a
  warning. The real_03 fitting (in tests): recess W8 → 4 plunges (3 with Ra 0.8 + finishing), relief 1.5 →
  warning.
- Existing tests did not change.
- Deferred: a width tolerance as a finishing trigger. The groove's `tolerance` is the bottom diameter's,
  and the general tolerance fills it; it needs its own field (DXF could read it from the width dimension
  text; for the model it is a prompt change).

## Thread speed check, n·P (2026-10-06)
Commits b0393ad, 895848e. Plan agreed with the user.
- `Machine.max_thread_feed` (mm/min, optional; migration 13d9cb0e4528; DB backup
  `instance/turnpilot.db.bak-2026-10-06-before-thread-feed`). Seed: empty, the number is not guessed.
- `planner.limit_thread_speed` for an external thread, a tap (G84) and an internal threading bar, after
  the max RPM cap: above the limit n = floor(limit / P) with a note; within it a "Z feed n·P" note; no
  limit: warning "check n·P = … mm/min for your machine". Warnings do not block Approve (only a missing
  tool does). The tap drill is not checked.
- Real parts (seed turret, Vc 132.5): НД 012 M24×1.5 n 1757, n·P 2635.5; деталь 1 M22×1.5 n 1917,
  n·P 2875.5. Without a limit both get the warning; with an illustrative 2000 mm/min both go to 1333.
- Existing tests did not change.

## Spindle power check of roughing (2026-10-06)
Commits 7fd8d86, 661b3fb. Plan agreed with the user (all three defaults).
- Data: `Material.kc1 / mc / kc_source` (Materials section on the Machine page) and
  `Machine.drive_efficiency` (optional, empty in the seed); migration 09577df41fd1, DB backup
  `instance/turnpilot.db.bak-2026-10-06-before-power`. The migration also fills the seed materials of an
  existing DB by name.
- Seed kc1 / mc (C45 1600 / 0.25, AISI 304 2000 / 0.21, 6061 600 / 0.25) are catalogue-type values written
  from memory, marked PLACEHOLDER in kc_source and NOT verified against a catalogue. The user is to replace
  them with the values of the catalogue in use.
- `planner._check_rough_power` (turning_rough only): Pc = Vc·ap·f·kc/60000, kc = kc1·f^−mc (κr taken as 90°),
  Vc actual from n. Above power × efficiency: ap reduced (more passes), Vc / f unchanged, note; below the
  tool's ap_min: warning "reduce f or Vc". No kc / efficiency / power: warning, no change. `plan_job(...,
  power=None)` (direct planner calls) is not checked, so existing tests did not change.
- Example (C45 placeholder kc ≈ 2012 at f 0.4, Vc 200, illustrative η 0.8 → 8.8 kW): НД 012 §1 6.17,
  §2 6.17, §4 8.18, §8 Ø24 9.56 kW → 2 × 3.562 becomes 3 × 2.375 (6.37 kW); Завіса 36 Ø34.8 3.76 kW,
  pin §6 Ø22 8.32 kW (unchanged). With the seed (no efficiency) every roughing operation gets the warning.
- Not done: facing, boring, grooving and parting power; the entering angle per tool.

## Drilling before boring (2026-10-06)
Commits 3a7cfe6, c2bf00c. Plan agreed with the user (all three defaults).
- Before: a bore was bored from solid (no drilling, no centring); the tap drill was any drill in the turret.
- `Tool.diameter` (migration e057e67f3716; DB backup `instance/turnpilot.db.bak-2026-10-06-before-drilling`)
  and tool type `centre_drilling`; the seed turret has neither drills nor a centre drill (warnings).
- `planner.plan_holes`: drill ≤ D − 2·ap_min(boring tool); drill-only when a drill of D exists and the bore
  is IT ≥ 12 (`DRILL_IT_GRADE`) and Ra ≥ 6.3 or none (`DRILL_RA_UM`), placeholders; the general tolerance
  fills most bores, so "no tolerance" alone would never apply. A one-sided "+0.21" counts as a 0.21 band
  (IT12 on Ø22). G83 note above 3 × the drill (`PECK_DEPTH_FACTOR`, placeholder). Coaxial holes share a
  drill. New stage `drill` after `face`; one centring on the first hole's row; drills from the smallest.
- Deviation from the plan, on purpose: rough boring keeps n from the bore Ø (as before), not from the drill
  Ø; inside a hole the Ø grows during the pass, so the bore Ø is the conservative one.
- Examples (synthetic turret: centre drill, Ø6/8/10/16/20, seed boring bar ap 0.2–2): Завіса 36 bushing
  Ø22+0,21 L32 → Ø20, rough boring 1 × 0.8; pin Ø10+0,15 L5 → drill Ø10 only; Ø8 H7 L40 → Ø6 with G83.
- Tests updated as planned: the bore without tools now has four operations; the test drills got their
  diameter; the tap drill test sees the bore's drill too.

## Placeholders and assumptions (inventory 2026-10-06, status 2026-10-07)
Every constant and assumption the planner and the seed data rest on. Source: ISO = from the standard
(values typed from memory of it, not copied from the document); catalogue = tool makers' practice, from
memory; rule of thumb = workshop practice, from memory; guessed = chosen here; TT / SRT = Sandvik Coromant
Turning tools / Solid round tools 2020 (`docs/turnpilot_catalog_P1.2.md`). "Machine page" = the operator can
change it in the app; "code" = only in the code / config.
Status: **verified** = checked against the catalogue (page given); **partly** = verified in part, the rest open;
**operator decision** = set by the operator's choice, not by a catalogue number; **open** = not checked yet.
All catalogue values are for steel P1.2 (C45) with coolant.
Since stage 5 the planner takes the catalogue numbers of a job's material from the confirmed cutting data rows
(Cutting data page); the tool's own values are the fallback, marked "(check)" on the operation.

Machine and tools (seed, `turnpilot/seed.py`):

| Name | Value | Depends on it | Source | Change | Status |
|---|---|---|---|---|---|
| Seed machine `MACHINE` | 4000 rpm, 11 kW, Ø300 | n cap, power check, blank check | guessed (example) | Machine page, passport review | open (stage 4 built: closes when the real passport is read and confirmed) |
| Seed tools: ranges, ap rec, f rec, Vc(f) | T1 SCMT-PM, T2 CNMG-PM, T4 DNMG-PF, T5 CCMT-PM, T6 N123G2, T7 266RG VM, T8 QD-NG, T9 CCMT-PF, T10–T12 860-GM | every Vc, n, f, ap, passes | TT A41, A49, A160, A278–A279, A283, A285, A289, A290, B11, B53, B130–B131, B139, B144 (graphs), C5, C73; SRT B25–B26, B70–B71 | Machine page, tool library | verified (grades: the catalogue's ★, operator decision); T3 and VCGT (library) open |
| Seed materials kc1 / mc | C45 1600/0.25, AISI 304 2000/0.21, 6061 600/0.25 | spindle power check | C45 kc1: TT A278; the rest from memory | Machine page, Materials | partly (C45 kc1 verified; mc and the other materials open) |
| Seed: no centre drill, no threading feed limit, no drive efficiency | empty | centring, n·P, power: warnings until set | not guessed on purpose | Machine page / tool library | partly (drills added; centre drill not in TT / SRT: open by decision; n·P and efficiency: stage 4) |
| ISO groups of the checked tools | P only | M / N jobs: "no tool" | operator decision 2026-10-07 | tool library | operator decision |
| Coolant | `Machine.coolant`, empty (not known) | catalogue Vc: a warning when the machine has none | catalogue condition | Machine page, passport review | operator decision (assumed present until the passport says) |

Planner constants (`turnpilot/planner.py`):

| Name | Value | Depends on it | Source | Change | Status |
|---|---|---|---|---|---|
| `NEAR_MIN` / `NEAR_MAX` | 0.25 / 0.75 of a tool's range | rough / finish Vc, f, ap of a tool without catalogue values | guessed | code | verified where replaced: tools with f rec / ap rec / Vc(f) use them (operator decision (b)); still open for T3 and VCGT |
| finishing ap | the tool's ap rec (else ap_min) | finishing pass, allowance left by roughing | TT A285 (T4 0.4), A289 (T9 0.35) | tool data | verified |
| `DEFAULT_FINISH_ALLOWANCE_MM` | 0.5 | allowance when no finishing tool | guessed (PLACEHOLDER) | code | open |
| finish feed from Ra | f = √(Ra·32·rε/1000) | finishing feed | textbook formula | code | open |
| `DEFAULT_NOSE_RADIUS_MM` | 0.4 | feed when rε is not in the insert code | guessed | code (insert code) | open (fallback only: the seed insert codes give rε) |
| `RZ_PER_RA` | Ra ≈ Rz/4 | feed for an Rz on the drawing | GOST 2789 approximation, from memory | code | open |
| `GRINDING_IT_GRADE` / `GRINDING_RA_UM` | IT5 / Ra 0.4 | "may require grinding" warning | rule of thumb | code | open |
| `IT5_UM`, `IT11_UM`, `IT12_UM` | ISO 286 tables | grinding, hex bar h11, drill-only holes | ISO | code | open |
| `THREAD_MAJOR_REDUCTION` | d − 0.1·P | OD under an external thread | rule of thumb | code | open |
| `METRIC_THREAD_DEPTH_FACTOR` | 0.613·P | external thread depth | ISO 68-1 (h3), from memory | code | open (C77 depth is for the full profile, not used for VM: operator decision) |
| `INTERNAL_THREAD_DEPTH_FACTOR` | 0.541·P | internal thread depth | ISO 68-1 (H1), from memory | code | open (as above) |
| thread passes and infeed series | C77 count (P 1 / 1.5 / 2: 5 / 6 / 8; internal 1.5: 6); series ϕ 0.3, 1, x − 1 | thread passes | TT C77, C82 | code | verified (other pitches: the tool's ap range, open) |
| `RAPID_STOCK_MARGIN_MM` (`gcode/program.py`) | 1 mm | G00 over the material vs feed (roughing) | the operator's rule (2026-10-17) | code | open |
| `THREAD_RUN_IN_PITCHES` (`gcode/program.py`) | run-in ≥ 3·P (max with the machine's value) | G92 start Z | rule of thumb (the operator, 2026-10-16) | code | open |
| thread spring pass | one 0 mm pass | thread passes | rule of thumb | code | open |
| `TAP_HOLES_C157` / `TAP_HOLES_C158` | PHD (drill) and PHDX (max) for cutting taps: M3–M48 coarse (19), 17 fine; else d − P | tap drill Ø, the drills accepted | SRT C157, C158 | code | verified (other threads: d − P, open) |
| `COARSE_PITCH_MM` (`extraction_schema.py`) | ISO 261 | pitch when the drawing has none ("check") | ISO, from memory | code | partly (C157 confirms the pitch of M3–M48 in its table; the other sizes open) |
| `WRENCH_SIZES_MM` (`extraction_schema.py`) | ISO 272 | hex "not a wrench size" warning | ISO, from memory | code | open |
| `HEX_BAR_TOLERANCE` | h11 | hex bar flats left unmachined | GOST 8560 / EN 10278, from memory | code | open |
| `HEX_MIN_CORNERS_PER_FLATS` | 1.10 | hex corners warning | ISO 4032-like, from memory | code | open |
| `HEX_BAR_MATCH_MM` | 0.05 | hex = bar size | guessed | code | open |
| `GEOMETRY_TOLERANCE_MM` | 0.2 | "section lengths sum" warning | guessed | code | open |
| tap / internal threading Vc | middle of the range | n of a tap / bar | guessed | code | open (no tap in the catalogue file) |
| drill Vc / f | the drill's Vc 125 (start) and f rec | n, f of drills | SRT B70, B71 (860-GM) | tool data | verified |
| drill max depth | 3 × D (860-GM 3×D) → "drill too short" | warning | SRT (3×D series) | tool data | verified |
| chuck side (`rough_starts`) | the largest Ø | roughing start Ø, passes, ap, n (DXF jobs) | assumption | code | open |
| `GROOVE_STEP_FACTOR` | step ≤ 0.8 × insert | plunges of a wide groove | guessed (PLACEHOLDER) | code | open |
| `GROOVE_FINISH_RA` / `GROOVE_FINISH_ALLOWANCE_MM` | Ra 1.6 / 0.2 mm | groove finishing pass | guessed (PLACEHOLDER) | code | open |
| `GROOVE_WIDTH_TOL_MM`, `DRILL_SIZE_TOL_MM`, `TAP_DRILL_TOL_MM` | 0.01 / 0.01 / 0.05 | "same size" tests | guessed | code | open |
| power: κr | 90° (hm = f) | kc, so Pc | assumption (a 45° holder raises kc) | code | open |
| Vc between catalogue points | linear | Vc at f rec / from Ra | assumption (the catalogue gives 3 points) | code | open |
| `DRILL_IT_GRADE` / `DRILL_RA_UM` | IT12 / Ra 6.3 | hole made by the drill alone | rule of thumb (PLACEHOLDER) | code | open |
| `BAR_OVERHANG_FACTOR` | overhang > 4 × D (D = DMIN) | warning only | rule of thumb (the user, 2026-10-23) | code | open |
| boring bar's head | DMIN − (edge r) across the axis | room to step back, the simulation's bar check | assumption (DMIN as the head's room) | tool data | open |
| drill zone below a drill-point bottom | D/2 (points of 90° or more) | "cut beyond the bore" for a drill | assumption | code | open |
| `PECK_DEPTH_FACTOR` | 3 × drill Ø | G83 note | rule of thumb (PLACEHOLDER; carbide often 5×) | code | open (3×D of 860-GM is its length: "drill too short", operator decision) |
| boring allowance | the finishing boring tool's ap rec | drill Ø for a bore | TT A289 (T9 0.35) | tool data | verified |
| rough boring | `boring_rough` tool (T5) by its ap rec | rough boring passes | TT A41, A289, A279 | tool data | verified |
| parting note | feed −50% for the last 2 mm | note only | rule of thumb | code | open |

Blank and config (`turnpilot/config.py`):

| Name | Value | Depends on it | Source | Change | Status |
|---|---|---|---|---|---|
| `BAR_STOCK_DIAMETERS` | 10 … 200 | suggested blank Ø | guessed (PLACEHOLDER) | code / config | open |
| `HEX_BAR_SIZES` | 8 … 41 | suggested hex bar | ISO 272-like list | Machine page (bar stock) | open |
| `BLANK_DIAMETER_ALLOWANCE_MM` | 2.0 | suggested blank Ø | guessed | code / config | open |
| `BLANK_FACING_ALLOWANCE_MM` | 2.0 | suggested blank length | guessed | code / config | open |
| `DEFAULT_PARTING_WIDTH_MM` | 3.0 | blank length without a parting tool width | guessed | code / config (else the parting tool's width) | partly (T8 width 3 verified, TT B53; the default itself open) |

DXF reader (`turnpilot/dxf_reader.py`, what becomes a section on the review screen):

| Name | Value | Depends on it | Source | Change | Status |
|---|---|---|---|---|---|
| `GEOM_TOL` / `BIND_TOL` | 0.01 / 0.05 mm | joining the contour, binding dimensions | guessed (measured on 3 files) | code | open |
| `CHAMFER_MAX_LEG` | 3 mm | a 45° diagonal up to 3 mm is a chamfer, longer is a taper | guessed | code | open |
| `TANGENT_TOL_DEG`, `AXIS_OVERHANG`, `MIN_MIRRORED_SHARE` | 1°, 5 mm, 0.8 | fillet vs arc, parts on the sheet | guessed | code | open |

Check first (safety or scrap), in this order:
1. Machine max rpm (chuck / bar limit) and the threading feed limit n·P (Z axis following the thread): not set.
2. The thread rules still open: d − 0.1·P, depths 0.613 / 0.541·P, the spring pass (thread out of gauge);
   tap drills for threads outside C157 / C158 (d − P).
3. mc of C45, kc of AISI 304 / 6061, and the drive efficiency (spindle overload, stall).
4. Chuck side = the largest Ø in DXF jobs: if the part is held otherwise, roughing ap is larger than planned.
5. Drill-only holes (IT12 / Ra 6.3) and the groove finishing rule (Ra 1.6, 0.2 mm): size and finish of holes
   and grooves.
6. Finishing: Ra feed formula, rε default 0.4, Rz/4 (surface finish); linear Vc between catalogue points.
7. Bar stock list and blank allowances (a blank too small), hex bar h11.
8. The graph-read feeds of T6 / T8 (B139, B144): checked by eye only.

## Tool library (2026-10-07)
Commits a9bc72c, 4e9fce6. Plan agreed with the user, plus the "Source" field.
- Edit everything but the type (a new type would change the meaning of the operations made with it).
- Delete: refused while the tool is in the turret; deleted when no operation used it; otherwise retired
  (`Tool.is_retired`): out of the library and the turret's choices, the operations keep `tool_id` and name.
- `Tool.source` (optional text): where the Vc / f / ap ranges come from; shown in the library and as the
  note "cutting data ranges: …" on every operation of that tool. Seed tools: empty (NOT validated anyway).
- No automatic recalculation. `Tool.updated_at` > `Operation.created_at` → the process sheet names the
  changed (or retired) tools and asks for Calculate. Operations calculated before `created_at` existed are
  not compared.
- Migration 157413ef972d; DB backup `instance/turnpilot.db.bak-2026-10-07-before-tool-edit`.

## Stage 2: placeholders checked against Sandvik 2020, steel P1.2 (done for P1.2, 2026-10-08)
Plan agreed with the user (8 answers). Source file: `docs/turnpilot_catalog_P1.2.md` (numbers and pages only;
catalogue PDFs under `docs/catalogs/*.pdf` are git-ignored). Done so far:
- f13984e: the catalogue file in git; its section 4 corrected to the V-profile insert 266RG-16VM01A001M.
- 5feb133: `Tool.ap_rec / f_rec / vc_points` (migration 0558af27f2f6). With f rec the planner takes f rec
  (finishing: from Ra), ap rec, Vc from the points at that f (linear between them, flat outside); roughing
  passes by ap rec; finishing allowance = the finishing tool's ap rec. Tools without them: as before.
- 8aaac04: seed T1 SCMT120408-PM GC4325, T2 CNMG120408-PM GC4325, T4 DNMG150604-PF GC4315, T9
  CCMT09T304-PF GC4315 (pages A41, A49, A160, A278-A279, A283, A285, A289, A290), ISO P. Data migration
  3748554069af (only unchanged seed tools). Tests build the seed turret with `conftest.seed_turret()`.
- 144aab1: T6 N123G2-0300-0003-GM GC4325 (f from the B139 graph), T7 266RG-16VM01A001M GC1125 (Vc 195,
  pitch 1-2), T8 QD-NG-0300-0002-CM GC1125 (f from the B144 graph); T4 renamed "Finish turning DNMG (P)".
  Data migration 5fb0e3d2a1c4. The T7 ap range is still the placeholder infeed range.
- 1889cb6, 3522d05: tool type `boring_rough`; seed T5 = CCMT09T304-PM GC4325 (A41, A289, A279) roughs bores,
  T9 (PF) finishes them; VCGT (N) only in the library. Data migration 8c2d41e7b9a3 (T5 swapped only while it
  holds the unchanged seed VCGT). Ø30 from the Ø20 drill: 14 × 0.332 became 8 × 0.581.
- Coolant: the catalogue Vc are with coolant; assumed present (to check at stage 4, machine documentation).
- 9e8baff: thread passes from C77 (count; the VM insert takes the full profile's count, C82) and the C82
  infeed series (it was already the code's); depth stays 0.613·P / 0.541·P, "not in the catalogue".
- 7e648e7: `Tool.max_depth`; drills 860-GM Ø6 / Ø8 / Ø10 on T10–T12 (SRT B25–B26, B70, B71; 3×D, external
  coolant); deeper than the drill reaches → "drill too short". Migration 450337eecff1 (positions only if empty).
- The placeholder table has a Status column (verified / partly / operator decision / open).
- 451d344: tap drills from SRT C157 / C158 (PHD; a drill accepted from PHD up to PHDX, the nearest to PHD).
  The memory table matched for all 19 coarse sizes (M33 is in C157 too: 29.50 / 29.771) and 16 of 17 fine
  ones; M10 × 1.25 is 8.8 now (d − P gave 8.75). Other threads: d − P, "not in catalogue". Section 5a of the
  catalogue file was copied from the operator's updated notes, M33 added.
- Result (48 rows): verified 9, partly 4, operator decision 2, open 33. Stage 2 is closed for steel P1.2;
  other materials (AISI 304, aluminium) have no checked tools: their jobs get "no tool".
- DB backups: `instance/turnpilot.db.bak-2026-10-07-before-catalogue-data`, `…-before-t6-t8`,
  `…-before-rough-boring`, `…-before-drills`.

## DXF blind check: stop rule (set 2026-10-06, BEFORE the new files are received)
- Data: new KOMPAS DXF files the system has not seen (not деталь 1, НД 012, Завіса 36). Expected
  answers are written by the user by hand before the run.
- Code frozen before the run: md5 of `turnpilot/dxf_reader.py` and `turnpilot/dxf_input.py` recorded;
  one run, no changes between freezing and the run.
- Metric: the share of the outer-profile dimensions bound to the right section, summed over all the
  new parts (inner dimensions are not counted).
- ≥ 90%: DXF is confirmed as an input. Below 90%: DXF stays a prototype, and the code is NOT patched
  for these files (they would become in-sample).

## Stage 3: DXF blind check, frozen (2026-10-08)
Plan agreed with the user. Commits a707c6d (tool, template, tests) and the freeze commit, tag `dxf-blind-freeze`.
- Frozen (md5 of the files with LF line ends, in `tools/dxf_blind_freeze.json`; ezdxf 1.4.4):
  - `turnpilot/dxf_reader.py` cb7a837ca766dd852e808c075b74021f (the binding; unchanged since the port);
  - `turnpilot/dxf_input.py` 48cd70b73b2c2f25c5ba82462a57f5d6;
  - `tools/dxf_blind_check.py` 1da5e321f33373d3bbedcc82221fa73d;
  - `requirements.txt` cfec32af5793f15862929e8d5f1c5773.
  A run stops by itself if any of them differs. The user checks it with
  `git diff --stat dxf-blind-freeze -- turnpilot/dxf_reader.py turnpilot/dxf_input.py tools/dxf_blind_check.py
  tools/dxf_blind_freeze.json requirements.txt` (must print nothing). Do NOT change these files until the run
  is done.
- Files: `dxf_blind/<name>.dxf` + `<name>.etalon.md` (git-ignored, local only). Template:
  `docs/dxf_etalon_template.md`. Expected answers written by the user before the run.
- Blind: Claude does not open the new DXF files before the run (no render, no probe, no trial run). It may run
  `tools/dxf_blind_check.py dxf_blind --check-etalon` (expected answers only, no DXF opened).
- What to ask for: 8–10 real KOMPAS parts (DIMENSION entities kept, 1:1), not prepared for us, not деталь 1 /
  НД 012 / Завіса 36, ≥ 80 outer dimensions in all; stepped shafts, external threads, grooves, chamfers, a taper,
  a fillet, an arc end, bushings. Inner profile → "внутрішній"; hex, flats, keyways, angular dimensions, end
  views → "поза критерієм" (listed before the run); drawings with them are not dropped.
- Metric: outer-profile dimensions bound right / all, summed over all parts; ≥ 90% → DXF confirmed, below →
  stays a prototype, no patching. A broken sheet counts all its outer dimensions wrong. Check badges and
  "drawing ≠ dimension" flags are reported apart. An expected-answer error found after the run does not change
  the official number (a corrected number may be shown beside it, labelled).
- One run: `.venv/Scripts/python tools/dxf_blind_check.py dxf_blind --report instance/dxf_blind_report.md`.
  Only the numbers and the verdict go to CLAUDE.md / README, not the drawings' content.

## Stage 4: machine passport (built 2026-10-08)
Plan agreed with the user (all three defaults: S1 power for the check; the passport's max Z feed is not the
threading limit unless the operator ticks it; the operator picks the pages before a reading). Commits
c68d92e ("operator", not "machinist"), 1c81872, 9217c81, 2fe69ed, 463b9d9, 4bef39f. No new dependency
(pymupdf, anthropic); the files frozen by `dxf-blind-freeze` are untouched.
- `turnpilot/machine_spec.py`: one list of the machine's fields (Machine page, passport tool, review).
  New fields: min_rpm, power_s6_kw, max_turning_length, max_bar_diameter, turret_positions, max_z_feed,
  coolant, coolant_pressure_bar, live_tooling, c_axis, control (migration ccd02424ad60). Seed: empty.
- `MachineSpecSource`: per field the source (passport + page + quote + document, operator, "not in passport").
  An operator's change on the Machine page is recorded as the operator's.
- Planner (`MachineLimits`): n below min_rpm → warning; coolant = no → warning on catalogue-Vc operations;
  live_tooling / c_axis = no → hex milling manual. Job pages: blank longer than the turning length, round bar
  thicker than the spindle passes. Empty fields check nothing.
- Passport: `MachineDocument` (instance/machine_docs/, migrations c96d2f7a7dcc, ac2fb403c778), page texts by
  pymupdf, the operator picks ≤ 20 pages, "likely" marks; `passport_reader` tool `record_machine` (prompt
  version "passport:8e97abf8", separate from the drawing prompt): value, unit as written, page, exact quote.
  The code drops a value without a picked page or a quote, checks the quote on the page (text pages) and the
  number in the quote. The paid call needs the operator's tick; calls and input tokens are shown before.
- Review: only ticked values are written (passport source; a changed value is the operator's); "not in
  passport" recorded where the machine has no operator / passport source; a conflict writes nothing.
- Tests: synthetic passport (`tests/passport_pdfs.py`, one scanned page), fake client: no paid call so far.
- DB backups: `instance/turnpilot.db.bak-2026-10-08-before-machine-spec`, `…-before-passport`,
  `…-before-passport-reading`. The demo server's DB holds one test upload "synthetic passport (test).pdf"
  (MachineDocument 1, not read); documents have no delete yet.
- Limits: uploads up to 10 MB (MAX_CONTENT_LENGTH): a larger scanned passport has to be split; no torque
  curve; the control is shown only (stage 6).
- Next: the real passport, one reading with the user's "так" (1 call), then the Status column update.

## Stage 5: cutting data from catalogues (built 2026-10-08)
Plan agreed with the user: R1 (a) no confirmed row → the tool's own values with "(check)", Approve not blocked;
R2 geometry rows (ap / f by insert / drill code) apart from grade rows (Vc by grade, application, material group);
R3 catalogue PDFs up to 300 MB in `instance/catalogues/` (never in git, checked with `git check-ignore`), only
picked pages are read; R4 graph values are not read by the model, the operator enters them. Commits 6314be1 …
1f8fc4c and the docs commit. Migration 339c93bf0976 (DB backup `instance/turnpilot.db.bak-2026-10-08-before-
cutting-data`). The files frozen by `dxf-blind-freeze` are untouched.
- `CuttingDataRow` (kind, catalogue, insert code / grade + application, material group, ap / f / Vc, coolant,
  from_graph, page, PDF page, quote, origin hand_typed / model / operator, status read / confirmed / rejected /
  replaced, checks), `CatalogueDocument`, `Material.catalogue_group` (C45 = P1.2; the others empty).
- `catalogue_file.py`: import of `docs/turnpilot_catalog_*.md` (tables by their headers, the drill section's Vc
  sentence; "~" = graph → check). The P1.2 file gives 24 rows; the T5 row (CCMT 09 T3 04-PM) was added to the file
  on 2026-10-08 from the tool data and checked by the user against A41 / A289 / A279.
- `cutting_data.catalogue_values` + `services.turret_entries(machine, material)`: ap / f from the geometry row,
  Vc from the grade row (a group row before an ISO-letter row), note "cutting data for P1.2: …" with pages.
  Equivalence (tested): the confirmed P1.2 file gives every seed tool its own numbers, and a job's plan is the same.
- `catalogue_reader.py`: page search, `record_cutting_data` (prompt `catalogue:` + hash), each page as text + image,
  max 32000 output tokens; code checks: picked page + quote, groups / codes asked for, order, graph → not taken;
  quote and every number looked for on the page → "check". A wrong number that is written elsewhere on the page
  passes silently: only the operator's review catches it.
- Review screen `/cutting-data/review`: confirm / correct (→ origin operator, changes in the note) / reject; a
  confirmed row with other values is replaced only with the "replace" tick. Hand entry with catalogue and page.
- `tools/eval_catalogue.py`: `--read PDF PAGES` per catalogue (TT and SRT are two PDFs, so the
  8 P1.2 pages are 2 calls), expected = the hand-typed file's rows on the read pages, score per number, wrong
  numbers marked with the code's check or SILENT; `--estimate` makes no call. Criterion proposed: ≥ 95% numbers
  right.
- **Step 7 (a), in-sample, run 2026-10-08 with the user's "так" (2 calls, claude-sonnet-5, prompt catalogue:03f9554d):**
  TT 2020 pages A278, A279, A283, A285, A289, A290 (PDF 282, 283, 287, 289, 293, 294) and SRT 2020 B70, B71 (PDF 270,
  271); the PDFs have no page labels, so the pages were given as "282=A278". **66 / 66 numbers right (100%)**,
  14 of 14 expected rows read, no extra rows, nothing dropped by the code, 0 wrong numbers. The two Vc rows (GC4325,
  GC4315) got "the quote is not on page": the model's quote is not a contiguous run of the page's text layer (a
  table). Tokens: input 53 772 + 17 024, output 5 029 + 7 425 (≈ $0.27). The estimate shown before (~37 000
  input) was about half the real input: the page images cost more than estimated (to recalibrate).
  Report `instance/eval_catalogue_p12.md`, runs `instance/eval_catalogue_p12.runs.json`. In-sample: one run, one
  material group, the expected answer is the file the prompt's structure was written from. Hold-out (b) waits for
  the user's expected answer.
- Demo DB: the P1.2 file was imported once (24 rows "read", none confirmed): until the operator confirms them,
  every operation shows "(check)". The catalogue PDFs are uploaded (CatalogueDocument 1 TT, 2 SRT).

## Stage 6: G-code (built 2026-10-09)
Plan agreed with the user: R1 Fanuc 0i-T (system A) first; R2 explicit moves and G92 per pass, no canned cycles;
R3 every value not in a catalogue / passport is the operator's, no default; R4 no G41/G42 (chamfers, tapers:
check); R5 no confirmed max rpm, no program. The operator's additions: drilling only in G97 (G96 on the axis is an
error); the spindle direction for a right-hand tool is an operator value (and a checklist item); parting to the
axis in G96 is a check. Commits da80770 … f36e507 and the docs commit; migrations e304ff3c2963, 209c6b4145de,
d82ea01ebe01, d032e6444de3 (DB backups `instance/turnpilot.db.bak-2026-10-09-*`). Files frozen by
`dxf-blind-freeze` untouched (the profile comes from the confirmed rows, not from `dxf_reader`).
- `turnpilot/gcode/`: readiness (approved operations of a DXF job, unchanged tools, max rpm passport / operator /
  demo, programming values and job set-up present; an operation goes in only with cutting data from confirmed
  catalogue rows or the operator: `Operation.cutting_data_origin`), profile (r(z) turned / final), program
  (neutral commands, the builders), fanuc (print, strict read back), simulate, svg.
- The operator's remarks on the first real program (Zavisa 36 pin), all done:
  - the taper is roughed (steps from its larger end's neighbour, then along its line) and finished in the contour;
    the planner no longer makes a taper manual (fillets and arcs stay manual);
  - the planner reduces ap above the nominal power when the drive efficiency is not set;
  - a section is roughed from its neighbour as the neighbour's roughing leaves it (+ 2 × the finishing allowance);
  - the finishing contour does not go down towards the chuck steeper than `Tool.max_ramp_angle` (empty = not
    allowed); the back chamfer at the parted end is cut with the parting insert's corner after a slot;
  - parting past the axis (`parting_overshoot_mm`); an optional groove dwell (`groove_dwell_s`, G04 P ms).
- Checks the simulation added and the bugs they found: a turning cut deeper than ap max over more than ap max
  of its way (found the unroughed taper and a groove bridged at the nominal Ø instead of the turned one); a cut
  down steeper than the in-copying angle; PART NOT COMPLETE for material left; a thread's run-in over material
  above its root (деталь 1: M22 after the R10 sphere).
- Page: job → G-code (blockers, left-out operations, values with sources, programs), program page (SVG with a step
  slider, errors / PART NOT COMPLETE / checks, the text with flagged lines, checklist and name, Ready to run,
  download only when ready and not out of date). DEMO values: `flask gcode-demo [--job ID]`, source "demo",
  banner; Ready to run impossible until the operator saves them.
- Real files (local, `tools/gcode_real.py`, DEMO values, C45, all P1.2 rows confirmed, operations approved in a
  temporary DB): Zavisa 36 bushing and pin: 0 errors, complete; деталь 1: 0 errors, not complete (R10 sphere
  manual, groove 2 mm narrower than the insert, M22 left out: its run-in over the sphere); НД 012: 0 errors, not
  complete (the arcs R12.5 / R20.46 manual). Programs in `instance/gcode_*.nc`.
- Not done: a groove's finishing pass, boring and internal threads, other dialects (Haas / Sinumerik need their manuals), re-chucking. The in-copying angle of T4's holder and the real
  machine values are to be entered by the operator.
- In the user's DB the 3 graph rows (T6, T8 feeds) are not confirmed: grooving and parting are left out of a
  program until they are, so the Zavisa pin's back chamfer would also be left (PART NOT COMPLETE).

## Arcs G02/G03 and nose radius compensation G41/G42 (built 2026-10-10)
Plan of 8 commits agreed with the user: P1 I / K; P2 the convexity from a DXF is set by the code, otherwise by the
operator; P3 G41 / G42 in the finishing contour only; P4 the tip direction T only from the operator, without it no
compensation (CHECKs stay); P5 an arc whose drawn radius ≠ dimension is not generated until the operator chooses.
Commits a95492f, fbde05c, faa0310, 6731c10, 835d9a1 (arcs), e49ea07 + 7503246 (rε / T; rε read with spaces),
043c517 (G42), e647687 (nose simulation), 48f530f (fixes found on the real files). Migration 500ead6679f1 (rε, T).
- Generator: G42 only when the finishing tool has rε and T; the header and the checklist name the offset page's
  R / T; start-up on the move onto the contour (along the first segment's line; a sphere at X0 Z0 along the face);
  G40 on the move off it, or on the move back in Z at a larger section's face; a groove bridged before a step up
  shorter than rε is crossed straight.
- Simulation: a turning / facing insert with rε and T is its nose circle (T0–T4, T9; T5–T8 an error). Compensation
  errors (see README). A nose fillet in an inner corner (turn > 2°, at most rε thick) is a warning, not PART NOT
  COMPLETE. Start-up / cancel modelled as type A: the control's parameter is to be checked by the operator.
- Real files (local, `tools/gcode_real.py --tip-direction 4=3`, T3 typed there, DEMO values; 0 errors in all):
  - Zavisa 36 bushing and pin: complete; the pin differs from the operator-checked program only in the
    compensation lines (header, G42 X10. + along the taper, G40, the CHECKs on chamfers / taper gone);
  - деталь 1: not complete: the 2 mm groove (narrower than the 3 mm insert, no tool);
  - НД 012: not complete: the concave R12.5 (the in-copying angle is not set) and the R20.46 arc (drawn R51.61,
    waits for the operator's radius).
  Without T the real programs differ from e49ea07 only by the zero-length move after G02 / G03.
- A run of `gcode_real.py` on НД 012 hung for ~39 min before 48f530f: a message formatted for every nose position of
  a mass gouge; now one message per cell, 12 s per sheet.

## Copying tools, RMPX and the 2 mm groove (built 2026-10-10)
Plan agreed with the user (groove first; copy roughing last; PM roughs, PF finishes). Commits 8957b9c, da5c9a0,
4191fbd, e3a5de2, 89322c8, ee3838b. Migrations a7b45471d5b1 (holder code + 2 mm insert), c4e1f0a9b2d7 (RMPX source),
d81f3b6c0e45 (VNMG PF / PM); DB backups `instance/turnpilot.db.bak-2026-10-10-before-{groove-2mm,rmpx-source,vnmg}`.
- Library only (the turret is full: positions are the operator's): N123E2-0200-0002-GM GC4325 2 mm (TT B10-B11,
  B139 graph, B130, holder R/LF123E08 B29, max depth 8); VNMG 16 04 04-PF GC4315 and 16 04 08-PM GC4325 (★ A172
  checked by the operator on the image; A288; A278 / A279; holder DVJNR 2020K16 / 2525M16). Holder codes and RMPX
  are left empty for the operator.
- `Tool.holder_code` (CoroCut 1-2 seat check E / G), `Tool.max_ramp_source`; RMPX suggestions (planner.RMPX_TABLE:
  A204 DNMG 27°, A212 VNMG DVJNR 44° / PVJNR 41°) taken by the operator's click only.
- Planner: a concave arc (its angle is the same whichever end is free) goes to tools whose RMPX covers it (finishing;
  roughing by a roughing tool, else the finishing copying insert with a pass warning). Tapers and convex arcs going
  down are not reassigned (they depend on the free end, which the planner does not know).
- Generator: a contour block per finishing tool; copy roughing on concentric arcs placed for the nose centre (needs
  rε and T), after the other roughing, from the level it leaves. Simulation: a profile steeper than every finishing
  tool's RMPX is an error with the way out.
- Real files (local; typed in the temporary database: the 2 mm groove on T3, VNMG PM / PF on T10 / T11 instead of
  the drills, RMPX T4 27° / VNMG 44°, T3 on T4 / T10 / T11; DEMO values): Zavisa 36 bushing and pin complete;
  деталь 1 complete (the groove cut by the 2 mm insert, 0 errors); НД 012: 0 errors, R12.5 copy roughed in 2 passes
  of 1.25 from Ø39.3 and finished by the PF; left only the R20.46 arc (drawn R51.61, the operator's choice).

## The operator's decision on an arc's radius (built 2026-10-10 / 2026-10-14)
Plans agreed with the user. Commits acc4052 (decision), cd1238d (shape of an existing row), f1ee206 (change and
history). Migrations e5a2c7d91f03 (Feature.radius_dimension / radius_geometry / radius_source / radius_decided_at /
radius_note), f2b9d3a61c08 (RadiusDecision); DB backups `instance/turnpilot.db.bak-2026-10-10-before-radius-decision`,
`…-2026-10-14-before-radius-history`.
- Job page: conflict with its place (Z from / to, the end Ø), take the dimension / the geometry / an own value (checked
  at once: a circle of it through the section's ends with the arc's shape; else nothing saved, the smallest R = half
  the chord shown), an optional note, the operator's name required. No default.
- Until decided: a simulation error naming the place (never ready to run), not only PART NOT COMPLETE.
- RadiusDecision keeps every decision (R, source, both radii, note, name, date); never changed or deleted; the
  Feature fields are the last one's. Decisions taken before the history: name empty.
- "Change the decision": the same choices; a calculated job is recalculated (new version, the old one kept): the
  arc's own operations always, and others whose values changed, go back to "proposed"; the rest keep "approved". A
  program made before is out of date, its reasons name who changed which R and when.
- Jobs confirmed from a DXF before the shapes were kept (НД 012 job 4, 2026-10-06) have arcs without shape, side or
  drawn radius: the shape and side can be set on the row; the drawn radius only comes with a new upload and Confirm.
- НД 012 (job 7): the conflict is Z-35 to Z-45, Ø35 -> Ø38.5, convex; both R20.46 and R51.608 go through its ends.
  The operator's first decision: R20.46 (the dimension), note "за розміром на кресленні".

## SolidWorks DXF through an adapter (built 2026-10-15)
Plan agreed with the user (1–4; SolidWorks only; S355J2+N as P1.2). Commits 5e70664 (S355), 007bae0 (CAD detection),
1e4d48d (adapter), b58d08c (review page, material, arcs). Migration a3c6e81f4d27 (DB backup
`instance/turnpilot.db.bak-2026-10-15-before-s355`). The files frozen by `dxf-blind-freeze` are untouched.
- Data: three trial SolidWorks files from Denys in `real_dxf/denys/` (local, git-ignored): eb1_011 (Рукоятка, AISI
  304, 10:1), 03057789-1-01 (Втулка, S355J2+N, 2:1, A3), 03057789-2-01 (Шпилька, S355J2+N, 2:1). The user wrote no
  expected answers: they are trial files, NOT in the stage 3 metric, and the adapter was developed on them, so they
  are in-sample for it. A blind check of the SolidWorks path needs other files with expected answers first.
- What SolidWorks writes (probe): AC1015, ANSI_1251 with Cyrillic as \U+XXXX, layers 0 / FORMAT / Defpoints, all
  colour 7; contour Continuous 0.25, thin 0.18, CENTERX2 axes, frame 0.70, title block 0.50 / 0.35; real DIMENSION
  entities with points, in sheet units; text pieces in the `_D…` blocks.
- `turnpilot/dxf_adapter.py`: detect (KOMPAS: appid / K5LT_*; SolidWorks: SW_* blocks / FORMAT layer); scale from the
  dimensions (60% must agree within 1%; title block checked); the frame and what hangs on it dropped; contour = the
  heaviest Continuous weight along a horizontal CENTER* line; linear and radius dimensions rewritten (texts joined);
  diameter / angular ones, splines, circles, hatches not read (listed as warnings); a rewritten copy
  `<stored>.kompas.dxf` is what the frozen reader reads.
- S355J2+N (EN 10025-2): P1.2 (TT cross reference list PDF 682: S355J2G3 = P1.2.Z.AN), kc1 1600 (TT A278 / PDF 282),
  mc 0.25 not verified, hardness not given; recognised from the title block.
- Run on the three files (in-sample): eb1_011 read (Ø8 × 10, chamfers 0.6 both ends; the bore not read: inner);
  03057789-2-01 read (M20-6g × 24, chamfers 0.5 both ends; Ø10 inner; "16" and "4" on the other views not bound);
  03057789-1-01 NOT read: its main view is a half section with a lug (the end view shows Ø50 with a flat), so no
  contour is symmetric about the axis (the reader's basic rule). Not patched: a half-section rule would be a reader
  change or a guess in the adapter.
- 03057789-1-01 diagnosed (2026-10-22): the reader stops at `find_parts` ("no contour is symmetric"). The main view is
  half a view (above the axis) and half a section (hatched, below). Found in the bushing: two flats S40 on the collar
  Ø50 (x 10…20, running into the 45° cone; the "40" and Ø50 only on the end view) — milling, not turning; a through
  internal thread M20 (bore Ø17.5), its "М20-6g" dimensioned as a half (axis to r 10, so 10 if read as it is), and 6g is
  an external thread's class (6H expected?): to be checked, not corrected.
- Done (option A, the user's choice): the adapter says it in words instead of "no symmetric contour": a half section,
  where the view differs from the section, the end view's Ø drawn as arcs (flats possible), "enter the part by hand".
- Not now (the user's decision): B, reading a half section (the hatched half mirrored, a half dimension × 2, flats from
  the end view as a warning), goes with the "inner profiles" stage, once other half-section drawings exist for a blind
  check (on this file it would be in-sample); C, a feature type "flats" (count, S, Z range; milling by a driven tool
  with the C axis, else manual; left out of the G-code with the reason), after B.

## Turret set-up per job (built 2026-10-16)
Plan agreed with the user (1–5; the operator's name Bohdan). Commits 99d24e0 (model), 954c1d7 (job page), 0e943f5
(suggestions), f20f7c3 (G-code, stale). Migration c5d7a9e3b104 (DB backups
`instance/turnpilot.db.bak-2026-10-16-before-job-turret`, `…-before-turret-data`).
- `JobTurretChange`: job, position, the tool (or empty), what the machine had then, the operator's name (required),
  date, note; undone = removed_by / removed_at (never deleted). `services.job_turret(job, machine)` = the machine's
  slots with the job's active changes: the calculation, the library-drill hint, the operation edit, the G-code
  simulation and the program page use it.
- `services.turret_suggestions`: from the current calculation, library tools the job lacks (a drill of a bore's
  size; a turning tool with RMPX ≥ a concave arc's angle; a missing tool type; a narrower grooving insert), on an
  empty position first, else one whose tool the job does not use (lowest); "Apply" with the operator's name.
- G-code: the header lists the changes after the blank; the program page and a checklist item too; a program made
  before a change of the set-up is out of date.
- Data (2026-10-16, in the user's DB): the machine's T3 = Rough turning CNMG (M/N), T12 = Drill 860-GM Ø10; jobs/7
  (НД 012) set-up T3 -> Copy roughing VNMG (P), T12 -> Copy finishing VNMG (P) (Bohdan). Calculated again: jobs/7 v8
  (R12.5 roughed by T3, finished by T12), jobs/8 v2 (Ø10 drilled to size by T12, no boring); no suggestions left.
  Approval and the G-code are the operator's. Tool 4 (DNMG) has RMPX 27° but no holder code and no tip direction T
  (its contour goes without G42 until T is entered).

## Skipping an operation (built 2026-10-16)
Plan agreed with the user (carry the skip to new calculations: yes; the note required: yes). Commit 223e3f8.
Migration d6e8b0f4c215 (DB backup `instance/turnpilot.db.bak-2026-10-16-before-skip`).
- Operation status "skipped" + skipped_by / skipped_at / skipped_note / skip_carried_from; skip and unskip are
  recorded in the operation's Edit log; name and note required (unskip: the name).
- G-code readiness: a skipped operation is not pending and goes to `skipped` with "skipped by the operator (name):
  note" (listed in the program's header under NOT IN THIS PROGRAM); a skipped cutting operation leaves material
  (PART NOT COMPLETE).
- `calculate_operations` carries a skip to the operation of the new version with the same feature, type and mode.
- Why: jobs/8 had a centring operation with no tool (no centre drill in TT / SRT, catalogue file section 6); the
  operator's decision is no centring before the 860-GM drill (140°, self-centring). To be skipped on jobs/8 by the
  operator.

## Axial order and the G-code link (2026-10-16)
Commits 657eca3, this one. Adding a bore, an internal thread or an internal chamfer keeps `axial_order_known` (not part
of the outer profile); other features reset it. jobs/8 restored to 1 (DB backup
`instance/turnpilot.db.bak-2026-10-16-before-axial-order-job8`; operations v4 and the centring skip untouched). The
"G-code" button is always on the job page and the process sheet; the G-code page names what blocks a program.

## Bore depth and the thread run-in (2026-10-16)
Plan agreed with the user. Commit 480652f (bore depth) and the run-in commit. Migration e8a1b2c3d4f5 (Tool.point_angle_deg /
point_angle_source, Feature.depth_mode / depth_decided_by / depth_decided_at). The dev server's reloader applied it
before a backup was taken: `instance/turnpilot.db.bak-2026-10-16-after-bore-depth-migration` is AFTER it; the last
backup before it is `…-before-axial-order-job8` (differs only by jobs/8's axial order flag).
- Drill point angle: 860-GM Ø8 / Ø10 SIG 140° (SRT 2020 B26, PDF 226, the user; the point of Ø10 1.8 mm matches
  D/2/tan 70°). Ø6 (B25) left empty: not checked.
- A bore's depth: "to the full Ø" (the point goes D/2/tan(SIG/2) deeper: jobs/8 Ø10 × 5 → Z-6.82) or "to the drill's
  point" (Z-5, the full Ø 3.18 deep); the operator's choice with a name on the job page, no default. Without it no
  program; to the full Ø without a point angle: no program. The program's comment names the decision; a later
  decision makes a program out of date. Every decision is a `BoreDepthDecision` row (mode, name, date, optional
  note; migration a7c3e9d1f2b4, the earlier jobs/8 decision copied as its first row); the Feature fields are the
  last one's. The planner's "drill too short" stays on the drawing's depth; the G-code warns when the point goes past
  the drill's max depth.
- Thread run-in: max(the machine's value, 3·P), the comment says which (M20×2.5 with 4.5 → 7.5). A run-in over a
  larger section: the thread is left out, the reason names both values.
- jobs/8: the depth decision is the operator's: Bohdan, to the full Ø (2026-10-16).

## Small fixes package (2026-10-16)
Plan 1–7 agreed with the user. Commits cb18301 … and the history commit. DB backups
`instance/turnpilot.db.bak-2026-10-16-before-kc-sources`, `…-before-depth-history` (taken before the migration files).
- Program page: one row per reason (messages equal apart from their numbers), the lines' range and count, differing
  numbers as min…max (`gcode/report.py`). НД 012 program 1: 241 error lines → 4 rows.
- A convex arc whose top is above the larger end's Ø: shown at each radius choice and after the decision (НД 012:
  R20.46 tops at D38.62 > D38.5 at Z-45); not a blocker.
- `Material.kc1_source` / `mc_source` (migration f1a2b3c4d5e6, `kc_source` dropped): C45 and S355 kc1 TT A278 (PDF
  282), mc "not verified"; AISI 304 / 6061 placeholders. The power note names both.
- Thread notes: "V-profile: N passes from TT C77 (PDF 535), the full-profile count as C82 (PDF 540) allows" and "depth
  from the ISO thread profile"; C77 is PDF 535 (the user wrote 540: that is C82, where the V-profile sentence is).
- Process sheet: the unconfirmed-data note lists only operations with a tool whose cutting data is not from a
  confirmed catalogue row or the operator.
- Review screen of a DXF: a fillet is a boundary, not added to the section lengths (НД 012 extractions 28 / 30:
  116.5 → no warning). Model readings unchanged. The old НД 012 extraction 27 still sums to 92 (another cause, not
  looked at). Frozen files untouched.

## Cycle time (built 2026-10-17)
Plan 1–4 agreed with the user. Commits fb401ae (fields), 5125f10 (estimate), 8cae788 (page) and the docs commit.
Migration b8d4f0a2c6e1 (DB backup `instance/turnpilot.db.bak-2026-10-17-before-cycle-time`, taken before the file).
- `Machine.rapid_x_m_min / rapid_z_m_min / tool_change_s`: in `MACHINE_FIELDS`, so in the passport reading too (prompt
  version passport:4a869bb9; no call made). The user's DB: empty (no passport).
- `gcode/cycle_time.py` reads the program's text (fanuc.parse): G01/G02/G03 ∫ dl/(f·n) in steps ≤ 0.1 mm (G96 n from
  the diameter, ≤ G50), G92 per pass |ΔZ|/(n·P) plus its rapids, G00 max(|ΔX/2|/vX, |ΔZ|/vZ), G04 P, T × the tool
  change time. Not counted: acceleration, M01, loading, the moves to / from the reference point. Works on programs
  made before (computed when the page opens, from the current machine values; no staleness).
- Program page: a table by block N, the part, the order (job.quantity); "without the passport — an estimate"; DEMO
  banner when a value's source is "demo".
- Measured (scratchpad `cycle_check.py`): user's DB, cutting + threading only (rapids / changes not set): jobs/8
  program 7 12.1 s, jobs/7 program 5 94.7 s. A temporary copy with DEMO values (rapid X 20, Z 24 m/min, change 1.5 s;
  not this machine's): jobs/8 22.5 s, jobs/7 121.4 s. Not compared with a real run.

## Stage 7.1: machines, orders, the shop page (built 2026-10-17, by hand, no connection)
Plan 1–5 agreed with the user, the five defaults taken (operations / time from the machine's program; library,
materials, cutting data shared; DXF jobs show the program's profile; one running order per machine; names required).
Commits 8a3df08 (machines), 30dc433 (job → machine), 7a320fc (orders), edea248 (shop) and the docs commit.
Migrations c9e5a1b3d7f2 (Job.machine_id / calculated_machine_id, GcodeProgram.machine_id, JobTurretChange.machine_id;
existing rows → Lathe 1) and d1f6b2c4e8a3 (Order, OrderEvent, OrderOperationMark, MachineStatus, Machine.shop_row /
shop_col). DB backups `instance/turnpilot.db.bak-2026-10-17-before-job-machine`, `…-before-orders` (taken before the
migration files).
- `services.job_machine(job)` replaces the first machine everywhere a job is concerned; the program page, ready and
  download take the program's own machine. A calculation made for another machine blocks the G-code ("calculate the
  job for …"); a program made for another machine is out of date.
- Byte check of commit 2 (asked by the user): jobs 7 and 8 regenerated from a copy of the user's DB, and the 4 real
  parts by `tools/gcode_real.py` (Zavisa 36 parts 1 / 2 --blank 38x120, НД 012 --blank 55x110, деталь 1 --blank 32x60;
  plain and --tip-direction 4=3: 8 files), before and after: 10 / 10 the same, byte for byte. `gcode_real.py` needs
  --blank now (the review form has no blank for these sheets).
- Orders take the job's newest program for their machine (re-taken by a button); blocks N and the time from
  `cycle_time`; time left = (quantity − done) × the part's time, "incomplete" while rapids / tool change are not set.
- Shop: a CSS grid (max 20 × 12 cells; a taken cell swaps the two machines), drag and drop by fetch, a row / column
  form as the fallback; the panel is a fragment in a <dialog>.
- Check in a temporary copy (scratchpad `shop_check.py`, Lathe 2 with DEMO values): jobs/8 on Lathe 1 program 7,
  12.1 s a part (incomplete), 200 − 35 → 2000 s left; jobs/8 on Lathe 2: no program; jobs/7 on Lathe 1 program 5,
  94.7 s, out of date (the peck depth changed since). The user's DB: no order, no second machine, nothing placed.

## Shop panel fixes, tool groups, rapids over the material (2026-10-17)
Plan agreed with the user (with the corrections: T word kept in every block; G00 ≥ 1 mm from the material; the
transition between blocks X then Z). Commits 5f76667 (order panel), 27c843d (B4 tool groups), 0fef871 (B5 rapids)
and the docs commit. No migration; the files frozen by `dxf-blind-freeze` untouched.
- Order / shop panel: one form, one operator field (from the machine's status when set; empty is an error); a mark
  shows ✓, the name and the time, "Cancel the mark" adds a row; "left" counts the finished parts only (explained);
  the shop panel opens again after an action; `back` accepts local paths only.
- B4: `fanuc.render` leaves out G28 and M01 between blocks with the same tool; the T word stays; the simulation and
  the cycle time take the same T as no change.
- B5: the builder keeps the stock its straight passes leave (`_stock_r`, an upper bound); a pass with no material
  is left out, an air part of a pass is a rapid when the material is ≥ `RAPID_STOCK_MARGIN_MM` (1 mm, the operator's
  rule) below and before it, the way in to a taper / arc pass uses `_approach` (up first, the last step fed).
  A first version recomputed the start Ø over the section's own span only and dropped a whole operation whose
  passes also cleared a neighbour (a synthetic taper + groove part; the finishing then cut 5.4 mm): replaced by the
  pass-by-pass rule before the commit. Not done (later, the user's decision): the operations' order (НД 012's taper
  still roughs its neighbours' material, from D55).
- Cause found (НД 012 gcode/8): the taper is roughed from the bar D55 (the planner passes over the arc next to it)
  and its steps run to the Ø50 face, so the Ø45, Ø38.5 and arc roughing that follow fed through air (N3 70 mm, N5
  108 mm, N6 41 mm; 275 mm in all, measured by replaying the program over the stock).
- Measured (B4 → B5; scratchpad `b_metrics.py`, `b_compare.py`, `air_cuts.py`; jobs from a copy of the user's DB):
  jobs/7 342 lines, cutting 94.7 → 78.1 s, DEMO total 113.9 → 97.9 s, air 274.5 → 45.9 mm; jobs/8 the same byte
  for byte; real НД 012 air 220 → 23 mm, деталь 1 52 → 15 mm; Zavisa 36 only the X / Z split. Before B4 → after B4:
  jobs/7 341 → 326 lines, 14 → 9 tool changes, DEMO total 121.4 → 113.9 s. jobs/7 and jobs/8: 0 simulation errors.
- НД 012 extraction 27 ("lengths sum to 92 ≠ 114"): read by an older DXF version (dxf:4809a076, 2026-10-06) that gave a
  threaded section a thread row only (no od_turn); a thread is not an axial section, so its 22 mm is missing
  (114 − 22 = 92). Fixed the same day (ab942d5: od_turn + thread); extractions 28 (job 4) and 30 (job 7) sum to 114.
  Not a bug now: 27 is a stale reading never confirmed (no job).

## Stage 7.2: the shop plan (built 2026-10-17, by hand, no connection)
Plan 1–6 agreed with the user (the mock-up committed as a reference; Lathe 1's type left to the user; a footprint
running into a wall / column / walkway / machine kept with a yellow warning). Commits 8003903 (plan file, schema),
6ecceb5 (type, footprint), 2cc8e1a (plan page), 057db90 (drag, rotate, conflicts), 0569fe7 (Upload drawing from the
card), a fix (machines listed without a plan) and the docs commit. Migration e2a7c3d5f9b1 (ShopPlan, MachinePlacement,
Machine.machine_kind / footprint_length_mm / footprint_width_mm); DB backup
`instance/turnpilot.db.bak-2026-10-17-before-shop-plan` (taken before the migration file, the server stopped).
- `turnpilot/shop_plan.py`: pydantic `Plan` (extra keys refused, every point on the sheet), `parse` (PlanError with
  the field's path), footprint rectangles (rotation 0 / 90 / 180 / 270), `conflicts` (walls with their thickness by
  Liang-Barsky, columns, walkways, other machines; touching is not a conflict). `turnpilot/shop_svg.py`: the plan as
  SVG in mm (viewBox = sheet + 1.5 m; lines non-scaling; label height = the longer side / 80).
- `services.place_machine` (name required, a plan required, snapped to 100 mm, inside the sheet, rotation one of four;
  a new MachinePlacement row each time), `placement_conflicts`, `shop_machines` (the page's data: status class from
  7.1's statuses, alarm = stopped + "!", the order running and its progress, conflicts).
- The footprint fields are in `MACHINE_FIELDS`: the passport reading's prompt version is passport:1b575a6b (no call).
- The 7.1 grid (`shop_row / shop_col`) is not shown and not converted to mm (that would invent a place); the columns
  stay in the DB. The status / order panel of 7.1 opens from the card.
- The operator's name is typed on the page for each move (not remembered, not filled in).
- Checked in a copy of the user's DB (port 5002, the example plan, two DEMO machines): drag without a name refused,
  with a name saved and shown with a yellow outline over the walkway; dark theme; phone width (the page 407 px, the
  plan scrolls in a 310 px box). The user's DB: no plan uploaded, Lathe 1 not placed (listed under the plan), its
  order 1 (1 of 50, 12.1 s a part, incomplete) and status running (Bohdan) in the card.
- Later fixes (2026-10-17 / 21): dragging from the not-placed list by pointer events and X / Y / rotation in the card
  (2b4c607); + / − / Fit, Ctrl + wheel, background pan, fits 1920 × 1080, labels under a machine fade (3bdfd25); after a
  placement the page keeps its scale / scroll and says where the machine went (9deda0a: the drop point was right, the
  page came back fitted); the card's queue and the time left of all orders (the docs commit's predecessor).
- 2026-10-22 (fae8b33; migration f4b8d2e6a1c9 `MachinePlacement.removed`, DB backup
  `instance/turnpilot.db.bak-2026-10-22-before-unplace`): the office places the machines, so the page's field is
  "Хто змінює" (required, kept in sessionStorage until the tab is closed, never filled in); without it a drag does not
  start (red "Впишіть, хто змінює", the field focused, nothing sent); the card shows "змінив/змінила <name>"; "Прибрати
  з плану" adds a removed row (the last place kept) and the machine goes back to the not-placed list. Statuses and
  operation marks keep their operator field.
- pytest took 15+ min once: the computer was switched off during that run; a timed run with durations: 1121 tests in
  4 min, the slowest 7.8 s (test_gcode_nose), nothing waits on a server.

## Stage 7.3: the part in 3D (built 2026-10-22)
Plan 1–5 agreed with the user, plus: the shop card shows the finished part (profile.final) transparent with the stock by
the marks over it and the next operation highlighted; it takes the order's own program; the caption "лише зовнішній
профіль"; states computed for an old program are saved. Commits 7707dc7 (states), bf1b9da (three.js), 16817ca (program
page), c1c7e2c (shop / order) and the docs commit. No migration (the states are in the program's simulation JSON); the
G-code text and the golden files unchanged; the files frozen by `dxf-blind-freeze` untouched.
- `simulate.py` keeps `initial_stock` and the stock at the end of every block N (`SimResult.blocks`);
  `gcode/stock3d.py`: `profile_after(result, k)`, Douglas-Peucker to 0.01 mm, `states` (in the JSON as `stock_by_block`).
  `services.stock_states` simulates an old program from its text once and saves the states with the time.
- `static/part3d.js` + `static/vendor/three` (three@0.160.0 from npm, integrity checked; three.module.min.js 670 681 B,
  OrbitControls.js 29 868 B, LICENSE; sha256 in VERSION.txt, tested; `-text` in .gitattributes). Import map in base.html.
- Shop / order: blocks marked done from the first without a gap; a gap is named ("N5 marked done, but N3 is not: shown up
  to N2"), nothing mixed.
- Measured on a copy of the user's DB (scratchpad `stock3d_check.py`): jobs/7 program 11: 14 blocks, k=0 the bar,
  k=14 = final_stock, no block adds material, simulation 7.7–8.9 s, states 0.2 s, 10.5 KB JSON, 2…75 points a state;
  jobs/8 program 10: 6 blocks, the same checks, inside the part |Δr| ≤ 0.0005 mm from profile.final, 1.1 KB.
- Found on jobs/7 after N4 (T03 copy roughing R12.5): the simulated stock 1.54 mm below the finished profile from
  Z-46.98 to Z-54.28, with no error. Resolved the same day, see "The insert's trailing edge (2026-10-22)".

## The insert's trailing edge (2026-10-22)
Plan 1–4 agreed with the user (tolerance 0.01 mm, the drawing's tolerance shown for reference; without κr the flat
edge, cautious; κr of J = 93° from the Sandvik Turning tools catalogue, KAPR 93.0°, the user). Commits ac1bd5b (error),
24bc4ca (κr), a6b4df2 (trailing edge) and the docs commit. Migration a9d3f5b7c1e2 (Tool.entering_angle_deg / _source;
DB backup `instance/turnpilot.db.bak-2026-10-22-before-entering-angle`, the server stopped). G-code text unchanged.
- The question: does the VNMG 16 04 08 (T03, DVJNR, RMPX 44°) cut the Ø38.5 neck after N4? **No.** By hand (scratchpad
  `n4_edge_calc.py`): its nose centre runs on arcs about the R12.5 centre (Z-57.5, r 29.25), radii 10.05 / 11.30; the
  trailing edge rises at 180 − 93 − 35 = 52° and clears the profile by ≥ +0.535 mm (+0.490 even at the RMPX 44°), the
  nose by +0.400 (the finishing allowance). The simulation's flat edge (ap max 4 mm at the nose's lowest point, any move
  with X) gave −1.540, exactly the dip. The generator needs no change.
- `simulate.py`: every lowering of the stock (`_lower`) keeps the first line taking a cell below the finished profile
  (the lower of its neighbours: a step face is between cells); `_below_final` reports runs not already reported as
  errors (BELOW_FINAL_MM 0.01, the depth, the tool, the drawing's tolerance from `Section.tolerance`).
- `_edge`: the trailing edge tangent to the nose (or from the tip point) at 180° − κr − ε, the insert's edge long
  (`planner.insert_shape`: ε by the ISO shape letter, length = the size digits); no κr or no shape: the old flat, and the
  error says "κr of the holder is not set".
- Tests: five tests with V / D inserts going down got the error from the flat model in ac1bd5b and are clean again with
  κr 93° in a6b4df2; the back chamfer test now gives its DNMG κr 72.5° (52.5° edge) for its RMPX 50°: in a 93° holder
  its 32° edge really cuts a 45° chamfer (the catalogue's RMPX for it is 27°).
- Measured on a copy of the user's DB (scratchpad `edge_measure.py`): jobs/7 program 13 without κr: 1 error (the dip,
  1.54 mm, T03); with κr 93° on tools 4, 18, 19 (as the operator would take it): 0 errors, nowhere below the profile;
  jobs/8 program 10: 0 errors both ways. The 4 real parts (`tools/gcode_real.py`, plain and --tip-direction 4=3, seed tools
  without κr): the 8 programs byte for byte the same as at 1d9ae2c, the same simulation summaries (Zavisa 36 parts 1 / 2
  complete, 0 errors; НД 012 2 errors, not complete; деталь 1 not complete, as before).
- In the user's DB κr waits for the operator: tools 19 / 18 (VNMG PM / PF, DVJNR) and 4 (DNMG, DDJNR) have the J
  suggestion; 1 (SCMT), 2 and 3 (CNMG), 5 (VCGT) have no holder code.

## Turret set-up from the library (built 2026-10-22)
The operator-technologist's decision: the program composes the job's turret set-up from the library, the operator sets
it up by the card. Plan 1–6 agreed with the user (answers: empty turret in the operations' order; jobs/7's earlier
changes need the marks too; accepting calculates again; a tool on Lathe 1 may be proposed for Lathe 2: the library is
tool types, not stock), plus "installed, X / Z offsets measured" and the "also on Lathe 1" hint. Commits 8e0b12c
(proposal), 2db3f43 (models), 5599545 (card), 3fb08c4 (marks), b9f1ead (copy) and the docs commit. Migration
b3e7c9d1f5a4 (TurretSetup, JobTurretChange.setup_id, TurretInstall, MachineTurretCopy; DB backup
`instance/turnpilot.db.bak-2026-10-22-before-turret-setup`, the server stopped). The files frozen by `dxf-blind-freeze`
untouched; the G-code generator untouched.
- `services.turret_setup_proposal`: three planner runs (the job's turret; + the checked library tools at positions from
  1000; the check with the set-up), nothing written. Needs per (feature, tool type): no tool, a bore not drilled to size,
  a concave arc steeper than the tool's RMPX; skipped operations need none. Missing: "the library has no X tool for ISO P
  checked by the catalogue for P1.2: …" or "X: in the library, but its cutting data are not from a confirmed catalogue
  row …". `accept_turret_setup` checks the signature of the set-up shown ("1:5,2:7").
- `_turret_setup.html` (job page, process sheet), `_turret_installs.html` (job page, program page, order panel), copy on
  the machine page. The one-by-one suggestions (`turret_suggestions`, Apply) are gone.
- Checked on copies of the user's DB (scratchpad `setup_bytes.py`, `job9_check.py`): jobs/7 and jobs/8 on Lathe 1,
  Calculate + G-code (approved in the copy) at 239528c and at HEAD: 507 lines, byte for byte the same (480 G-code lines);
  jobs/9 (Завіса 36 part 1) on Lathe 2: 10 operations without a tool → proposal T1 SCMT, T2 CNMG (P), T3 DNMG (T0303,
  offset page R0.4 T3), T4 grooving 3 mm, T5 parting 3 mm, each "also on Lathe 1"; accepted → v2, 0 without a tool,
  0 "No tool" warnings, 5 waiting for "installed".
- In the user's DB nothing was accepted or copied; jobs/7's two earlier changes (T3, T12) now wait for the marks before
  Ready to run.

## Design package (built 2026-10-23)
Plan 1–11 agreed with the user (10: only the filled-in values are copied; 1: generated messages in English without a
mark). Commits 3ce4451 … and the docs commit; migrations c4d8e2f6a3b7, d5e9f3a7b4c8 (DB backups
`instance/turnpilot.db.bak-2026-10-23-before-copy-programming`, `…-before-stickout-hint`, `…-before-stock-states`).
G-code text and golden files unchanged; the files frozen by `dxf-blind-freeze` untouched. Not checked in a browser.
- Language: `turnpilot/i18n.py`, one dictionary (English key → `UK`), `LABELS` for stored codes (English shows the code
  as before), `JS_KEYS` for the scripts (`t(key, {name: value})` in base.html). Default Ukrainian (cookie `lang`, the
  UA / EN switch). Templates call `_()` (the text is markup, values escaped; `link()`, `strong()` for markup values).
  Generated messages stay English. Tests run in English (`conftest.client` sets the cookie); `tests/test_i18n.py`
  checks every text asked for, the scripts' keys, the codes, no `"` added in a translation, the glossary.
- One style: the shop plan's palette as root tokens, dark under the system preference and `html[data-theme]`; the
  theme button in the top bar (localStorage `tp-theme`, the old `sf-theme` read once).
- Process sheet: notes in a popover (ⓘ), 11 columns. Touch buttons: class `touch` (44 × 44 px).
- `services.next_step(job)` (`NEXT_STEP_LABELS`): job page, process sheet, G-code page, program page.
- 3D (`static/part3d.js`): one WebGL renderer per page, each view copies into its own canvas; `fitCamera` to the
  part's length and the bar's Ø; "Вмістити" button; the shop card mounts the running order's view; views whose
  element left the page are freed.
- Drawing review: the planned-for line inside the form.
- `flask stock-states [--dry-run] [--program ID]`. Run on the user's DB 2026-10-23: 15 programs, 9 without states,
  all 9 computed (jobs/7: 10–15 states, 9.5–18.2 s each; jobs/8: 7 states, 1.2–1.3 s).
- Turret copy: the other machine's filled-in programming values too (source `copied`, quote "<machine> (<name>)";
  DEMO stays DEMO); `MachineTurretCopy.programming` keeps {name: [before, taken]}.
- Stick-out hint (`services.stickout_hint`): length of the profile sections + the parting insert's width (the job's
  parting operation, else the job's turret) + `Machine.stickout_margin_mm` (a programming value with
  `in_program=False`: not in a program's settings, so no program goes out of date). The operator takes it with the
  name (`Job.stickout_note` keeps the sum, name, time; typing the stick-out clears it). In the user's DB the margin is
  empty: no hint until the operator enters it.

## Fixes after the browser check of the design package (2026-10-23)
Plan agreed with the user (stick-out option (a); the UK time rule written out; both order hints; the actual Vc). No
migration; G-code text and golden files unchanged; the files frozen by `dxf-blind-freeze` untouched.
- Stick-out: measured from the jaws to the raw end face. The simulation and the drawings put the jaws at
  Z = stock beyond Z0 − stick-out (`services.jaws_below_z0`); programs made before have no `stickout_reference` row
  in their settings: out of date ("Stick-out measured to"), their drawings keep the old jaws
  (`program_jaws_below_z0`).
- Stick-out hint: length + stock beyond Z0 + parting width + max(margin, chuck safety distance); when the safety
  distance is taken the page says so (НД 012: 114 + 2 + 3 + max(2, 5) = 124).
- Time: `turnpilot/localtime.py`, Europe/London by the UK rule (BST from 01:00 UTC on the last Sunday of March to
  01:00 UTC on the last Sunday of October), tested at the changes; filter `|local` on every page; the database keeps
  UTC; times in the G-code text unchanged. Later: a "shop time zone" setting (this Windows has no zoneinfo data and
  requirements.txt is frozen, so no tzdata).
- Language: `i18n.generated` (`|gen`) translates by shape what the code writes as labels and states (value
  sources, out-of-date reasons, blockers, skip reasons, the checklist, turret set-up lines, order events, units);
  `i18n.dxf_text` (`|dxf`) the DXF binding table's words without touching the frozen files. Stay English: the
  planner's notes and warnings, the simulation's messages, the DXF row notes, G-code block titles, data names.
- Upload page names DXF (read at once, no model, no paid call). "Ready to run" said once (the banner).
- Touch size also for Unskip, Cancel the mark, Take the latest program (dark when a newer program exists or the
  order's is out of date), Close the order.
- Machine page: the tool library and the turret in five columns, sources behind ⓘ; measured at 768 px: no
  horizontal scroll on /machines/1 and /machines/2.
- Orders: `running_without_order` on the shop card and the panel; closing the running order names the machine's
  other open orders, nothing switched. In the user's DB the running order of Lathe 1 was lost on 2026-10-22: order 4
  was made running (order 1 stopped being), then closed; "Take the latest program" changes no running state.
- Marks: a mark counts only when its stored title equals the block's title now (`valid_done_blocks`); taking a newer
  program cancels the marks of changed operations (new rows, event `marks_cancelled`, a flash).
- Process sheet: when n is capped, "→ Vc on ØD" = π·D·n/1000 on the reference diameter (facing: the blank's Ø, e.g.
  Ø22 at 4000 rpm: 276.5 m/min). The "289" in the plan was an example not computed; corrected here.

## Inner profile: bores (built 2026-10-23 / 24)
Plan 1–7 agreed with the user, plus: a flat bottom faced by the bar from the centre outwards (κr ≥ 90°, else the
reason), a cone left on it = PART NOT COMPLETE; the spindle direction per boring bar (the operator's, no default,
no boring without it, a checklist item); an overhang above 4 × D (DMIN as D) a warning only. Commits 493deb0 (fields),
a610d56 (DXF), 9708157 (planner), e87b2c3 (simulation), 33b58ae (G-code), cae1779 (3D), 120d905 (a bar as wide as
the hole) and the docs commit. Migration e6f0a4b8c5d9 (DB backup `instance/turnpilot.db.bak-2026-10-23-before-
inner-profile`, the server stopped). The files frozen by `dxf-blind-freeze` untouched (`git diff` empty).
- `Feature.bore_open_end / bore_from_mm / bore_bottom` ("flat" / "drill_point" / "through") / `bore_cone_angle` /
  `bore_position_source` ("dxf" / "operator") / `_by` / `_at`; `Tool.spindle_direction` (+ source),
  `Tool.max_depth_source` (a boring bar's max depth = its overhang as clamped).
- `turnpilot/dxf_inner.py` (IN-SAMPLE, written on Завіса 36): on the real files the bushing's Ø22+0,21 × 32 (drill
  point 120°, the "geometry 21.8 ≠ text 22" flag kept) and the pin's Ø10+0,15 × 5 (flat bottom), dims bound;
  НД 012, деталь 1: no rows; the SolidWorks trial files: eb1_011 Ø3.3 × 8 (118°), 03057789-2-01 Ø10 L1.996 from the
  geometry (its jobs/8 bore was typed as Ø10 × 5: to the drill's point?). A through bore splits the contour into two
  halves: the frozen reader finds no part (a limit, not patched).
- A bore placed by the operator after the calculation blocks the G-code until Calculate.
- Check on copies of the user's DB (scratchpad `jobs_bytes.py`, `bushing_check.py`): jobs/7 and jobs/8 calculated
  again and approved, at c21e890 (before this stage, the backup) and at HEAD: 323 / 157 lines, byte for byte the same
  (jobs/8's bore has no place: its drill keeps "the inner profile is not simulated"). Завіса 36 bushing as a new job
  on Lathe 2 from the DXF (copy only, DEMO values marked: a Ø20 860-GM drill on T11, T5 / T9 DMIN 20 by the TT
  suggestion A16R, overhang 50, M03, T2, κr 95; depth to the full Ø): 0 errors, complete; warning: the nose radius
  in the corner at Z-32; rough 2 × 0.325 from D20, finishing G41 X22.
- In the user's DB the boring bars have no DMIN, overhang, spindle direction, T, κr: boring is left out of programs
  until the operator enters them; no Ø20 drill in the library (the bushing's Ø22 needs one).
- Not done: internal threads and grooves in the G-code, a second set-up (a bore at the chuck end), chips; the bar is
  its head's DMIN only (no shank geometry); not checked in a browser.

## Tools from the catalogues (built 2026-10-24)
Plan 1–4 agreed with the user. Commits 0133de1 (search), 09401c3 (add to the library), 0db1a34 (set-up card), b2b17c5
(the card says why an added tool is not taken) and the docs commit. Migration f7a1b5c9d2e3 (`Tool.catalogue_ref`; DB
backup `instance/turnpilot.db.bak-2026-10-24-before-catalogue-tools`, the server stopped). No model, no paid call. The
files frozen by `dxf-blind-freeze` untouched.
- `turnpilot/catalogue_tools.py`: written on SRT B35 / B26 and TT A102 / B6 (in-sample for the parser); on the whole
  Sandvik 2020 pair 1980 drills, 40 bars, 79 grooving inserts taken (1 / 2 / 152 rows not taken: they do not check).
  SIG on the 860-PM page (B35) is 147°, on 860-GM (B26) 140°: taken as printed. The library's 860-GM Ø8 / Ø10 have max
  depth 24 / 30 (3 × D), the catalogue's LU is 25.4 / 31.8: not changed (the user's to decide).
- Check on a copy of the user's DB (scratchpad `cat_check.py`, catalogues' page texts copied): Завіса 36 bushing from
  the DXF on Lathe 2 plans a Ø10 drill; the card: a Ø22 drill (drill-only possible) not found; a drill up to Ø21.3:
  636 fit, first the six Ø20 (460.1-2000-060A0 / A1 / 090A0 / A1 / 150A1-XM, 860.1-2000-055A1-PM). 860.1-2000-055A1-PM
  added (Ø20, LU 63, SIG 147°, grade 4234 with their quotes; Vc / f typed as DEMO in the copy): the set-up does not
  take it: no confirmed catalogue ap / f for its code in P1.2.
- 860-PM cutting data: B76 (PDF 276) gives Vc only (4234, P1.2.Z.AN 140-180-250 m/min); the feeds fn by Ø are on B77
  (PDF 277). Estimate (code, images not counted, and the 2026-10-08 reading was about 2 × its estimate): B76 alone
  6 039 input tokens, B76 + B77 9 743, one call either way. The user approved one call on B76 for P1.2: waiting for
  the decision B76 alone (Vc only: the drill stays not taken) or B76 + B77.

## Deferred
- A "shop time zone" setting (now Europe/London written out in `turnpilot/localtime.py`).
- SVG preview of a DXF on the review screen (plan commit 7): postponed until the decision on 2026-10-09.
- Decision (2026-10-06): TurnPilot is developed further; the SVG preview of a DXF stays deferred.
- dimensions_first on real_02 ×3 (3 calls; 9 for a fair comparison: 6 vs 6 with features). The
  question: does the code's solver remove the end-face confusion? Expectation: probably not. The
  mirrored reading 16 / 28 / 14 / 30 sums to 44 like the right one, so a consistent wrong binding
  gives no conflict. The value would be in seeing which boundaries the model binds each dimension to.
  Compare with the 9 features runs already made (right reading 5/9). Not approved; do not run.

- 1261 tests pass (2026-10-24). Features is the default mode (dimensions_first ~2× tokens, no clear win out of sample).
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
