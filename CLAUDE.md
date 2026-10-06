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
- What stays in the product: stage 1 only (`pdf_text.py` + `number_check.py`). The machinist checks the
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
  - Result: 18/18 dimensions bound right (Ø45 left out: its row still points at section 0, the stock).
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
- Seen on the demo server: on real sheets most lengths carry "check" (computed from a chain), since
  KOMPAS drawings dimension from a base. The not-machined chucked section (НД 012 §1 Ø50) is a row to
  untick by hand.

## DXF blind check: stop rule (set 2026-10-06, BEFORE the new files are received)
- Data: new KOMPAS DXF files the system has not seen (not деталь 1, НД 012, Завіса 36). Expected
  answers are written by the user by hand before the run.
- Code frozen before the run: md5 of `turnpilot/dxf_reader.py` and `turnpilot/dxf_input.py` recorded;
  one run, no changes between freezing and the run.
- Metric: the share of the outer-profile dimensions bound to the right section, summed over all the
  new parts (inner dimensions are not counted).
- ≥ 90%: DXF is confirmed as an input. Below 90%: DXF stays a prototype, and the code is NOT patched
  for these files (they would become in-sample).

## Deferred
- SVG preview of a DXF on the review screen (plan commit 7): postponed until the decision on 2026-10-09.
- dimensions_first on real_02 ×3 (3 calls; 9 for a fair comparison: 6 vs 6 with features). The
  question: does the code's solver remove the end-face confusion? Expectation: probably not. The
  mirrored reading 16 / 28 / 14 / 30 sums to 44 like the right one, so a consistent wrong binding
  gives no conflict. The value would be in seeing which boundaries the model binds each dimension to.
  Compare with the 9 features runs already made (right reading 5/9). Not approved; do not run.

- 591 tests pass. Features is the default mode (dimensions_first ~2× tokens, no clear win out of sample).
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
