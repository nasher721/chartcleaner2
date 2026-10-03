# Improvement plan — abbreviations, cleaning, integrations

Status: **done** (Phases 0–4 built; see Manual checks in docs/integrations.md) · Branch: `claude/app-improvements-brainstorm-esuv9a`

This plan covers all 26 ideas from the improvement brainstorm. They are grouped
into five phases. Every item lists the files it touches, the config it adds,
the tests that prove it, and what "done" means.

## Ground rules (apply to every item)

1. **Rules stay in JSON.** New behavior is driven by config groups in
   `config.json` / `default_config.json` and validated in
   `chartcleaner/config_validator.py`. Python holds defaults (`DEFAULT_*`
   constants in `stages.py`), never site-specific rules.
2. **Defaults reproduce today's output.** Every new stage ships
   `enabled: false` (or an option default that is a no-op), so
   `tests/golden` stays byte-identical. Each new option group gets a test in
   `tests/test_options.py` (or a dedicated test file) that turns it on.
3. **New stages and stage order.** `Pipeline._build` appends any builtin
   missing from a user's saved `stage_order` to the *end*. That is wrong for
   new mid-pipeline stages. **Foundation F1** fixes this before any new stage
   lands.
4. **Chart text never leaves the machine.** Every integration listens on
   loopback only (same stance as the `LocalLlmClient` loopback guard), needs a
   per-install token, and logs no chart text.
5. **Mac first, Windows parity.** Anything launched by the user also gets a
   `.cmd` path and works without admin rights. This matches `install.bat` and
   `Clean_Medical_Chart.cmd`.
6. **Each item is one PR-sized commit** with tests, and an `AGENTS.md`
   "Learned Workspace Facts" line when it adds a module or entry point.

---

## Phase 0 — Shared foundations

These unblock several later items. Do them first.

### F1. Stable insertion of new builtin stages
- **Files:** `chartcleaner/engine.py` (`Pipeline._build`), `tests/test_options.py`.
- **Change:** add `STAGE_ANCHORS = {"lab_compaction": "sections", ...}` so a
  builtin missing from a saved `stage_order` is inserted right after its
  anchor, not at the end. Fall back to the end when the anchor is also missing.
- **Tests:** a saved order missing a new stage resolves it after its anchor.
  The full default order is unchanged.

### F2. Change provenance (spans)
- **Why:** needed by A5 (click an abbreviation), C7 (removed-content panel)
  and C8 (rule health).
- **Files:** `chartcleaner/stages.py`, `chartcleaner/abbreviations.py`,
  `chartcleaner/engine.py` (`StageStat`).
- **Change:** with `CleanContext.track_spans=True`, regex and abbreviation
  stages record `{"rule": <pattern or term>, "before": <text>, "after": <text>,
  "line": n}` in `StageStat.details["changes"]`. Cap the list at 2,000 entries
  per stage. Leave it off for CLI, batch and watcher runs so performance stays
  the same, and turn it on for the Clean page.
- **Rule IDs:** a stable `rule_id = sha1(pattern)[:10]` so stats can be
  grouped per rule (C8).
- **Tests:** `tests/test_provenance.py`: spans are recorded when tracking is on
  and absent when it is off, and output text is identical either way.

### F3. Service layer — one entry point for every integration
- **Why:** REST (I2), MCP (I4), the hotkey/Services (I3) and the clipboard
  watcher (I5) must all behave exactly like the app.
- **Files:** new `chartcleaner/service.py`.
- **API (pure functions, no UI):**
  `clean(text, *, preset=None, fmt="text", delta=False) -> dict`,
  `abbreviate(text) -> dict`, `expand(text) -> dict` (A8),
  `prompt(text, template) -> str` (I6), `ask(text, question) -> dict`
  (wraps `chart_qa.ask_chart`). Each one loads config through `store`, runs
  `Pipeline`, and appends a run record (`source="api:<caller>"`) so the
  Statistics page counts it.
- **Tests:** `tests/test_service.py`, which checks parity with `clean_text`
  for the same config.

### F4. Config validator coverage
- Add schemas for every new group introduced below (`abbreviations.*`
  additions, `lab_compaction`, `med_normalize`, `vitals_summary`,
  `imaging_impression`, `hospital_day`, `note_profiles`, `prompt_templates`,
  `integrations`). Add each one to `config_validator.py` as the item that
  needs it lands, not all at once.

---

## Phase 1 — Abbreviation quick wins

### A1. Highlight → "Always abbreviate as…"
- **Files:** `app.py` (Clean page highlight menu near `apply_highlight`),
  `chartcleaner/abbreviations.py`.
- **Flow:** select text in the result → a third highlight mode,
  **Abbreviate**, opens a dialog prefilled with the selection and a suggested
  short form (the CSV hit if one exists, else the initials) → save writes to
  `abbreviations.custom` through `normalize_settings`, re-applies to the
  current result, and offers **Undo** (same pattern as `highlight["undo"]`).
- **Edge cases:** if the selection is already an abbreviation, offer
  "Expand instead" (uses A8). If the term is in `disabled`, re-enable it.
- **Tests:** extend `tests/test_highlight_ui.py`: adding, undoing, and adding
  a duplicate term (which updates the replacement).

### A2. Live preview before saving
- **Files:** `chartcleaner/abbreviation_editor.py`, `chartcleaner/abbreviations.py`.
- **Change:** new `preview(text, cfg, candidate) -> {"count", "samples": [...]}`
  that runs `abbreviate` with and without the candidate rule and diffs the
  counts. The editor shows "37 changes in the current chart" plus up to 5
  before/after snippets. It previews against `CLEAN_STATE["input"]` when one
  exists, else `sample_chart.txt`.
- **Tests:** `tests/test_abbreviations.py::test_preview_counts`.

### A3. Conflict and safety warnings
- **Files:** new `chartcleaner/abbreviation_safety.py`, plus data file
  `chartcleaner/abbreviation_do_not_use.json` (Joint Commission "Do Not Use"
  list plus the ISMP error-prone list: U, IU, QD, QOD, MS, MSO4, MgSO4,
  trailing zero / missing leading zero, µg, cc, SC/SQ, HS, TIW, D/C, AD/AS/AU,
  OD/OS/OU…).
- **Checks:** (a) the replacement is on the do-not-use list, which **blocks
  saving** unless "I understand" is ticked; (b) two different terms map to the
  same abbreviation, which warns; (c) the abbreviation already appears
  verbatim in the chart with a different meaning (a CSV row whose
  `Abbreviation` matches but whose `Expanded version` differs), which warns.
- **Where:** A1 dialog, the A2 editor, A6 import, and a "Safety report"
  button on the Abbreviations tab that scans the whole bundled CSV and custom
  set.
- **Tests:** `tests/test_abbreviation_safety.py`.

### X1. Export abbreviations to text expanders
*(Integration item, scheduled here because it is small and uses the same dictionary.)*
- **Files:** new `chartcleaner/expander_export.py`; Abbreviations tab button
  "Export for…"; CLI `clean-chart --export-abbreviations FORMAT [-o PATH]`.
- **Formats:**
  - macOS Text Replacements `.plist` (drag into System Settings → Keyboard →
    Text Replacements). Array of `{phrase, shortcut}`.
  - Espanso `match/chartcleaner.yml` (`trigger: ";sah"`, `replace: "..."`).
  - TextExpander `.csv` (abbreviation, snippet, label).
  - AutoHotkey `.ahk` (`::;sah::subarachnoid hemorrhage`).
- **Options:** direction (short→long is the usual typing direction, or
  long→short), trigger prefix (default `;` to avoid firing on normal words),
  include custom only / bundled + custom, and skip do-not-use entries (default on).
- **Tests:** `tests/test_expander_export.py` (round-trip parse for plist via
  `plistlib`, YAML shape, escaping of quotes/backslashes).

---

## Phase 2 — Abbreviation depth

### A4. Suggested abbreviations ("phrase miner")
- **Files:** new `chartcleaner/phrase_miner.py` (same structure as
  `rule_miner.py`); Abbreviations tab panel "Suggestions".
- **Algorithm:** tokenize recent cleaned outputs (the Clean page result, the
  batch results in memory, or a picked folder; nothing is persisted beyond
  term counts). Count 2–6-word n-grams with frequency ≥ 3 that do not cross
  line breaks or punctuation. Score = `frequency × (len(phrase) −
  len(short))`. The short form is the existing CSV abbreviation when the
  phrase matches an `Expanded version` row that is currently not firing (for
  example a plural or hyphen variant), else initials (`SAH`). Drop phrases
  containing digits, PHI tokens (`[[T\d+]]`), or `nlp_allow_list` names.
- **UI:** accept (adds to `custom`, then runs the A3 check), reject (stored in
  `abbreviations.rejected_suggestions` so it never comes back), edit the
  short form.
- **Tests:** `tests/test_phrase_miner.py` with a synthetic chart.

### A5. Click an abbreviated span to inspect it
- **Depends on:** F2.
- **Files:** `app.py` diff/result rendering (`diff_html`), `abbreviations.py`.
- **Change:** in Abbreviations-only and Full-clean results, wrap each
  abbreviation replacement in `<span class="abbr" data-term=… data-rule=…>`.
  Click opens a popover: original term → abbreviation, rule source (bundled /
  custom / pack), and buttons **Disable this rule** (adds to `disabled`),
  **Change abbreviation**, and **Keep this one only** (a one-off revert in the
  current result, no rule change).
- **Tests:** `tests/test_pages.py`: spans are rendered, and the disable action
  updates config.

### A6. Bulk CSV import/export and abbreviation packs
- **Files:** `abbreviation_editor.py`, `chartcleaner/rulepacks.py`, new
  `chartcleaner/packs/abbreviations/*.json`, `rule_sharing.py`.
- **CSV:** import or export `custom` (and optionally `disabled`) using the
  same column headers as `medical_abbreviations.csv`. Import shows a preview
  table (new / changed / conflict / do-not-use) before applying, reusing the
  `preview_rules_import` merge/replace semantics.
- **Packs:** `_pack.kind = "abbreviations"`, body `{"abbreviations":
  {"custom": [...], "disabled": [...]}}`. Ship **Neuro ICU**, **Critical Care**,
  **Cardiology**, **Medicine** and **Nursing**. Installing merges into the user's `custom`, and each entry
  keeps a `"pack": "<name>"` field so it can be uninstalled cleanly.
  `normalize_settings` must keep that field.
- **Sharing:** include abbreviation packs in the existing rule-sharing bundle
  export/import.
- **Tests:** `tests/test_rulepacks.py`, `tests/test_rule_sharing.py`, and
  `tests/test_abbreviations.py` for the pack field round-trip.

### A7. Section-scoped abbreviation rules
- **Files:** `abbreviations.py`, `section_parser.py` (reuse
  `parse_clinical_sections`), editor.
- **Config:** `abbreviations.scope = {"mode": "all"|"only"|"except",
  "sections": ["Assessment and Plan", ...]}` globally, plus an optional
  per-custom-entry `"sections"` list.
- **Implementation:** when scope is not `all`, split the text into section
  spans, run `abbreviate` only on the spans that are in scope, and re-join.
  When the parser finds no sections, fall back to the whole text with a stage
  note.
- **Tests:** a chart with Medications + A/P sections; only A/P gets shortened.

### A8. Reverse mode — expand abbreviations
- **Files:** `abbreviations.py` (`expand(text, cfg)`), `engine.py` (new
  Pipeline mode `"expand"`), Clean page mode toggle (third option **Expand
  abbreviations**), CLI `--expand`.
- **Ambiguity handling:** an abbreviation with several CSV expansions
  (`a`, `MS`, `2/2`…) is expanded only when the "Context / qualification"
  column gives a usable context regex in the new optional CSV column
  `Expand when` (for example `\bradial\b` near `a`). Otherwise it stays
  unchanged and is listed under "ambiguous — not expanded" in stats.
  One-letter abbreviations are never expanded unless a context rule exists.
- **Optional:** "Expand before AI" on the Summarize/Ask panels (feeds the
  expanded text into `summarizer`/`chart_qa` for better grounding while the
  displayed result stays abbreviated).
- **Tests:** `tests/test_abbreviation_expand.py`: round-trip
  `expand(abbreviate(x)) ≈ x` for unambiguous terms, and ambiguous terms are
  left alone.

---

## Phase 3 — Cleaning improvements

All new stages ship `enabled: false` and use the F1 anchors shown.

### C1. Lab table compaction — stage `lab_compaction` (anchor: `sections`)
- **Files:** new `chartcleaner/compactors/labs.py`, wired in `stages.py`
  (`RUNNERS["labs"]`), `engine.py` (`BUILTIN_STAGE_IDS`, `STAGE_LABELS`,
  `STAGE_KINDS`), Pipeline editor options card.
- **Input shapes to support (fixture-driven):** Epic "Recent Labs" grid
  (column per time), "Lab Results" vertical (`Sodium 138 135 - 145 mmol/L`),
  and the component/value/ref/flag 4-column table.
- **Output:** `BMP 10/02 05:12: Na 138, K 4.1 (L), Cl 102, CO2 24, BUN 18,
  Cr 0.9, Glu 132 (H)`, with one line per panel per draw. Option
  `style: "line"|"fishbone"` (fishbone renders the BMP and CBC ASCII
  skeletons). Option `keep_reference_ranges: false`. Option
  `latest_only: false|N`.
- **Name map:** `lab_compaction.aliases` (`"Sodium": "Na"`,
  `"Potassium": "K"`…) in JSON.
- **Safety:** a line is only rewritten when every value on it was parsed. A
  partial parse leaves the original lines untouched and counts as `skipped`
  in details.
- **Tests:** `tests/test_compact_labs.py` with 3 fixture shapes, plus a
  golden fixture `tests/golden/labs_chart.txt` → `.cleaned.txt` with the stage on.

### C2. Medication list normalization — stage `med_normalize` (anchor: `lab_compaction`)
- **Files:** `chartcleaner/compactors/meds.py`.
- **Output:** `drug dose route freq [PRN reason]`, one per line. Strips
  `Dispense:`, `Refills:`, `Start/End date`, `Ordering provider`, NDC, and
  "Taking/Not taking" columns (configurable `drop_fields`). Marks
  `(held)` / `(discontinued)` from status columns. Optional
  `group_by: "none"|"scheduled_prn_infusion"`.
- **Abbreviations:** frequency/route normalizations come from
  `med_normalize.frequency_map` (`"twice daily": "BID"`…) and are checked
  against the A3 do-not-use list (for example, it will never emit `QD`).
- **Tests:** `tests/test_compact_meds.py`.

### C3. Vitals and I/O summary — stage `vitals_summary` (anchor: `med_normalize`)
- **Files:** `chartcleaner/compactors/vitals.py`.
- **Output:** `Vitals (24h): T 36.8–38.4 (last 37.1), HR 72–118, BP 98/54–162/90,
  RR 14–22, SpO2 92–99% | I/O: 2,450 in / 1,800 out (net +650)`.
  `window_hours` is set in config. When timestamps cannot be parsed it falls
  back to min/max/last over all rows.
- **Tests:** `tests/test_compact_vitals.py`.

### C4. Imaging — keep Impression only — stage `imaging_impression` (anchor: `sections`)
- **Files:** `chartcleaner/compactors/imaging.py`, reusing
  `section_parser` header detection.
- **Behavior:** inside a block detected as a radiology report (CT/MRI/XR/US
  title + Findings/Impression headers), keep `title + date + Impression`.
  Option `keep: ["impression"]` can add `"findings"`. If there is no
  Impression header, leave the block unchanged.
- **Tests:** `tests/test_compact_imaging.py`.

### C5. "What's new since the last note" on the Clean page
- **Files:** `app.py` (Clean page results tabs), `delta_engine.py` (already
  has `extract_note_deltas` / `format_delta_timeline`).
- **UI:** a new results tab, **Changes over time**, appears when
  `notes_found > 1`. It shows the timeline with "% copy-forward removed", plus
  a "Use this as result" button that swaps `CLEAN_STATE["result_text"]` (for
  copy, Summarize and Ask).
- **Also:** a Batch page checkbox "Delta view", and `service.clean(delta=True)`.
- **Tests:** `tests/test_pages.py`: the tab appears only with multiple notes.

### C6. Auto-detect note type → preset
- **Files:** new `chartcleaner/note_type.py`; Clean page preset selector;
  `batch.py`.
- **Detection:** keyword/header scoring from JSON
  (`note_profiles.detect = {"discharge_summary": ["Discharge Diagnosis",
  "Hospital Course"], "h_and_p": [...], "progress": [...], "consult": [...],
  "nursing": [...]}`), using the highest score above a threshold.
- **Mapping:** `note_profiles.presets = {"discharge_summary": "<preset
  name>"}`. On the Clean page this is a chip "Detected: Discharge summary →
  using preset X (change)". Applying it is opt-in through `note_profiles.auto_apply`.
- **Tests:** `tests/test_note_type.py`.

### C7. Removed-content review panel
- **Depends on:** F2.
- **Files:** `app.py` (new results tab **Removed**), `highlight_rules.py`.
- **UI:** removed blocks grouped by stage → rule, each with its text, a count,
  and two actions: **Restore here** (one-off, re-inserts into the current
  result at its line) and **Never remove this** (adds an exception). The
  exception is stored as `stage_options.<sid>.exceptions` (a list of literal
  strings); `run_regex_list` skips matches whose text contains an exception.
- **Tests:** `tests/test_removed_panel.py` (exception honored, golden unchanged
  when the list is empty).

### C8. Rule health report on the Statistics page
- **Depends on:** F2 rule IDs.
- **Files:** `store.py` (`append_run` stores per-rule hit counts from
  `details`, no text), `app.py` `stats_page`.
- **Report:** for every regex in `emr_line_metadata`, `boilerplate`,
  `literal_replacements`, `learned_rules` and `epic_phi_patterns`, show runs
  seen, total hits, last hit date, and mean time (time per rule measured only
  when `stage_options.<sid>.profile=true`). Flags: **never matched in last N
  runs**, **matches >X% of lines** (too broad), **slow**. One-click "Disable"
  and "Open in Pipeline editor".
- **Tests:** `tests/test_rule_health.py` over synthetic run records.

### C9. Hospital-day labels — stage `hospital_day` (anchor: before `timestamps`)
- **Files:** `chartcleaner/stages.py` (`run_hospital_day`).
- **Config:** `hospital_day = {"enabled": false, "admit_date": "auto"|"YYYY-MM-DD",
  "surgery_dates": [], "style": "append"|"replace"}`. With `auto`, the admit
  date comes from the earliest `Admission Date:`/`Admit:` header.
  `append` → `10/02/2026 (HD#3, POD#1)`; `replace` → `HD#3`.
- **Tests:** in `tests/test_options.py`.

### C10. Highlight-learned rules become regression tests
- **Files:** `app.py` `apply_highlight`, new `store.save_rule_example`,
  `tests/test_learned_examples.py`.
- **Behavior:** each saved highlight rule also stores a small example (the
  surrounding line before → after, already de-identified because it comes
  from the result pane) in `data/rule_examples.jsonl`. The test file
  replays each example through the current pipeline config, so a rule change
  that silently undoes an earlier lesson fails. A Settings button
  "Check my rules" runs the same check from inside the app.
- **Privacy:** an explicit setting `learned_examples.enabled` (default **on**,
  only one line stored per rule, deletable from Settings).

---

## Phase 4 — Integrations

### I1. Local REST endpoint
- **Depends on:** F3.
- **Files:** `app.py` (FastAPI routes on the existing NiceGUI `app`),
  `chartcleaner/api_auth.py`.
- **Routes:** `POST /api/v1/clean`, `/api/v1/abbreviate`, `/api/v1/expand`,
  `/api/v1/prompt`, `GET /api/v1/health`. JSON `{"text": ..., "preset": ...}`.
- **Security:** reject non-loopback `request.client.host`; require
  `Authorization: Bearer <token>` where the token lives in
  `data/api_token` (created on first run, `0600`, shown/rotatable in
  Settings); `Origin` header must be absent or `http://127.0.0.1:<port>` (this
  blocks browser CSRF from other sites); body limit 5 MB; chart text is never
  logged.
- **Tests:** `tests/test_api.py` with FastAPI TestClient (auth, loopback,
  origin, parity with service).

### I2. Global hotkey / macOS Services / Windows hotkey
- **Files:** `clean-chart` (new subcommands
  `--clipboard clean|abbreviate|expand|prompt:<name>`), new
  `integrations/macos/` (Shortcuts `.shortcut` export + Automator Quick
  Action workflows "Clean Chart", "Abbreviate Selection", "Expand Selection"
  that call `clean-chart --stdin --stdout`), new `integrations/windows/`
  (AutoHotkey v2 script binding `Ctrl+Alt+C/A/E`, which copies the selection,
  calls `clean-chart.cmd`, and pastes the result).
- **CLI additions:** `--stdin`, `--stdout`, `--mode abbreviations|expand`,
  `--preset NAME`.
- **Install:** `install.sh` gains an optional `--services` flag that copies
  the Quick Actions into `~/Library/Services` (no admin needed). Document
  how to assign a keyboard shortcut in System Settings.
- **Tests:** CLI tests for `--stdin/--stdout/--mode`. The macOS workflow is
  smoke-tested by hand (checklist in `docs/integrations.md`).

### I3. "Copy as prompt" templates
- **Files:** new `chartcleaner/prompt_templates.py`, Clean page split-button
  next to Copy, `service.prompt`, CLI `--prompt NAME`.
- **Config:** `prompt_templates = [{"name": "Neuro ICU progress note",
  "template": "...{chart}...", "format": "xml"|"markdown"|"text"}]`. Ship 4
  defaults: Progress note, Sign-out / handoff, A&P only, Discharge summary.
  `{chart}`, `{delta}` (C5 output) and `{date}` are the only placeholders.
- **Editor:** a Settings card with template CRUD and preview.
- **Tests:** `tests/test_prompt_templates.py`.

### I4. Local MCP server
- **Depends on:** F3.
- **Files:** new `chartcleaner/mcp_server.py` (official `mcp` Python SDK,
  **stdio transport only**, so there is no network listener), launcher
  `clean-chart-mcp` / `clean-chart-mcp.cmd`, `docs/integrations.md` snippets
  for Claude Desktop (`claude_desktop_config.json`) and Claude Code
  (`claude mcp add chart-cleaner -- /path/clean-chart-mcp`).
- **Tools:** `clean_chart(text, preset?, format?, delta?)`,
  `abbreviate(text)`, `expand_abbreviations(text)`,
  `render_prompt(text, template)`, `list_presets()`, `list_templates()`,
  `ask_chart(text, question)` (local Ollama only). Tool descriptions state
  that output is de-identified per the active config, and that the caller is
  responsible for what it does with the text next.
- **Dependency:** `mcp` added to `requirements.txt` as an *optional* extra
  (the import is guarded; the launcher prints install instructions if it is
  missing).
- **Tests:** `tests/test_mcp_server.py` (calls the tool functions directly;
  one in-process stdio round-trip).

### I5. Clipboard watcher mode
- **Files:** new `chartcleaner/clipboard_watcher.py` (polls `pyperclip`
  every 0.75 s), Settings card toggle, CLI `--watch-clipboard`.
- **Toggle:** `prefs.clipboard_watcher = {"enabled": false, "action":
  "auto"}`; switches in Settings and the app menu, and `--watch-clipboard`
  on the CLI.
- **Detection:** reuses C6 scores plus Epic chrome signatures (≥2
  `emr_line_metadata` hits, or a note header) and a minimum length.
  It ignores text the cleaner itself wrote (hash of the last output).
- **Action:** `auto` (replace the clipboard with the cleaned text and show
  a notification with an "Undo" that restores the original), or `notify`.
  Defaults to `auto`. Pauses automatically when the app window is focused.
- **Tests:** `tests/test_clipboard_watcher.py` with an injected fake clipboard.

### I6. Export formats + SmartPhrase-safe paste
- **Files:** new `chartcleaner/exporters.py`, Clean/Batch download menus,
  CLI `--format docx|md|obsidian|smartphrase`.
- **docx:** use `python-docx` if present, else the existing stdlib
  `_docx_stdlib` writer pattern (zip + XML). Headers become Heading 2.
- **md / obsidian:** front-matter (`date`, `preset`, `note_type`), with
  sections as `##`. For Obsidian, an optional vault folder in settings is
  written directly.
- **smartphrase:** ASCII-only (after `unicode_normalize`), lines ≤ 80
  characters, no tabs, `***` placeholders kept, Epic-safe bullets (`-`).
  "Copy for Epic" button.
- **Tests:** `tests/test_exporters.py`.

### I7. Browser extension (optional, last)
- **Depends on:** I1.
- **Files:** new `integrations/browser-extension/` (Manifest V3, Chrome +
  Edge).
- **Behavior:** a context menu on selected text → "Clean with Chart
  Cleaner" → `fetch("http://127.0.0.1:<port>/api/v1/clean")` with the token
  (pasted once into the extension options) → the result is copied to the
  clipboard and a toast is shown. Host permissions are limited to
  `http://127.0.0.1/*`. No content scripts run on Epic pages until the user
  invokes the menu.
- **Gate:** build this only if I2/I5 don't cover the Epic Web/Haiku workflow.
  Hospital browser policies may block unpacked extensions.
- **Tests:** a manual checklist plus a unit test for the request builder.

---

## Order of work and dependencies

| # | Item | Size | Depends on |
|---|------|------|-----------|
| 1 | F1 stage anchors | S | — |
| 2 | F2 provenance | M | — |
| 3 | F3 service layer | S | — |
| 4 | A3 safety list | S | — |
| 5 | A2 live preview | S | — |
| 6 | A1 highlight → abbreviate | S | A2, A3 |
| 7 | X1 text-expander export | S | A3 |
| 8 | A6 CSV import/export + packs | M | A3 |
| 9 | A8 expand mode | M | — |
| 10 | A5 click-to-inspect | M | F2 |
| 11 | A7 section scope | M | — |
| 12 | A4 phrase miner | M | A3 |
| 13 | C4 imaging impression | S | F1 |
| 14 | C9 hospital day | S | F1 |
| 15 | C5 delta tab | S–M | — |
| 16 | C1 labs | M | F1 |
| 17 | C2 meds | M | F1, A3 |
| 18 | C3 vitals/I/O | M | F1 |
| 19 | C7 removed panel | M | F2 |
| 20 | C8 rule health | S–M | F2 |
| 21 | C10 learned examples | S | — |
| 22 | C6 note-type detect | M | — |
| 23 | I3 prompt templates | S | F3 |
| 24 | I2 hotkey / Services | S–M | F3, A8 |
| 25 | I1 REST endpoint | S | F3 |
| 26 | I6 exporters | S–M | — |
| 27 | I4 MCP server | M | F3, I3 |
| 28 | I5 clipboard watcher | M | F3, C6 |
| 29 | I7 browser extension | L | I1 |

Suggested PR groups: **PR 1** = F1–F3 · **PR 2** = A1–A3 + X1 ·
**PR 3** = A4–A8 · **PR 4** = C4, C5, C9, C10 · **PR 5** = C1–C3 ·
**PR 6** = C6–C8 · **PR 7** = I1–I3, I6 · **PR 8** = I4, I5 · **PR 9** = I7.

## Definition of done (per item)
- `python -m pytest` is green, including `tests/test_golden.py` unchanged.
- New config keys exist in `chartcleaner/default_config.json`, are validated,
  and have editor UI where the item says so.
- The Windows `.cmd` path works for any new launcher or CLI flag.
- `README.md` usage section and an `AGENTS.md` workspace fact are updated.
- No new network listener except loopback; no chart text in logs or run records.

## Decisions (2026-10-03)
1. **Do-not-use abbreviations:** block saving, with an "I understand" override
   checkbox (A1, A2, A6, X1 as written).
2. **Clipboard watcher:** the action is **auto** (replace the clipboard with
   the cleaned text and notify). The watcher has an on/off toggle in Settings,
   in the app menu, and on the CLI (`--watch-clipboard`), remembered in prefs.
   It is off on a fresh install until it is turned on once.
3. **Abbreviation packs:** Neuro ICU, Critical Care, Cardiology, Medicine,
   Nursing.
4. **MCP:** allowed on the work machine. I4 ships for Mac and Windows.

## Progress
- [x] F1 stage anchors — `STAGE_ANCHORS` in `engine.py`
- [x] F2 provenance — `Pipeline.run(..., track_changes=True)`;
      `details["rule_hits"]` always recorded; `details["changes"]` is
      in-memory only and stripped from history
- [x] F3 service layer — `chartcleaner/service.py` (`clean`, `abbreviate`,
      `ask`, `format_output`); the CLI now uses `format_output`
- [ ] F4 validator coverage (lands with each new config group; `acknowledged`/`pack` done)
- [x] A3 safety — meaning-aware Do Not Use list; bundled DNU rows blocked by
      default (decision 1); compound parts checked; Safety report
- [x] A2 live preview — Abbreviations tab and the highlight dialog
- [x] A1 highlight → abbreviate — "Abbreviate mode" with undo ("Expand
      instead" waits for A8)
- [x] X1 text-expander export — Abbreviations tab + `--export-abbreviations`
      (short → long only: expanders need short triggers)
- [x] A8 expand mode — Clean page, `--mode expand`, `service.expand`;
      ambiguity resolved via `expand_prefer` ("Choose meanings") instead of a
      CSV context column; ALL-CAPS lines skipped
- [x] A7 section scope — global `abbreviations.scope` (per-entry sections not
      built); with no headers, "only" leaves text unchanged (safer than the
      planned fallback)
- [x] A6 CSV import/export + five specialty packs (Neuro ICU, Critical Care,
      Cardiology, Medicine, Nursing); packs exclude bundled terms and conflicts
- [x] A4 phrase suggestions — current chart; distinct-line counting, names skipped
- [x] A5 inspect — built as the result's **Abbreviations** tab (Change /
      Disable + re-run) because the result box is a plain text area
- [x] C4 imaging impression and C9 hospital day — opt-in stages, Pipeline-page
      forms (declarative `STAGE_FORMS`)
- [x] C5 changes over time — Clean tab + Batch option. **Also fixed the delta
      engine**, which could drop new facts (paragraph-level ≥85% similarity
      pruning, lines <30 chars skipped); it now prunes only sentences that
      literally repeat
- [x] C10 learned-rule examples — `rule_examples.py`, Settings check, test
      (no separate privacy toggle: examples hold the same text the rule
      already stores)
- [x] C1 labs, C2 meds, C3 vitals/I/O — opt-in stages; blocks are rewritten
      only when every line parses (`window_hours` for vitals not built: the
      summary covers the readings shown)
- [x] C6 note type — detection + Settings mapping (in prefs, not config,
      because presets replace config.json); the preset applies to that
      clean only. Batch detection not built
- [x] C7 removed panel — "Removed" tab with Copy / Never remove this
      (`stage_options.<sid>.exceptions`); "Restore here" replaced by Copy
- [x] C8 rule health — Statistics section; "slow rule" timing not built
- [x] I3 prompt templates — Clean "Copy as prompt", Settings editor, `--prompt`
- [x] I1 local REST API — `/api/v1/*`, token in Settings
- [x] I6 exports — .docx (stdlib), .md + notes folder, "Copy for Epic"
- [x] I2 hotkeys — `--stdin/--stdout/--preset`; macOS Quick Action generator
      (untested on a real Mac here — see manual checks); Windows AHK v2 script
      (uses clipboard mode rather than stdin)
- [x] I4 MCP server — mcp 2.x `MCPServer`, stdio; now a regular dependency
- [x] I5 clipboard watcher — Settings toggle (off by default), auto action,
      Undo, macOS notification; "pause while the app window is focused" not
      built (the server can't see window focus)
- [x] I7 browser extension — MV3 context menu → local API → clipboard

---

# Phase 5 (2026-10-03) — trust, privacy, structure, neuro ICU

Order: 1 → 4 → 5 → 2 → 3. Branch `claude/magical-volta-w468nq`.

- [x] **P1 "Nothing clinical lost" check** — `chartcleaner/fact_check.py`. Facts =
  numbers with units, labelled lab/vital/score values, BPs, drug names (whitelist +
  generic stems), allergy/code-status words. `Pipeline.run(fact_check=None)` snapshots
  fact counts after every stage that changed the text and attributes each loss to a
  stage: *unexpected* (reformatting stages), *rule* (regex/learned/custom/PHI), *by
  design* (sections, imaging, vitals/neuro/lab summaries; dedup only when the value
  is gone from the output). `result.fact_check` (FactReport) holds chart text; history
  keeps `summary()` counts. Clean page badge + list (“Never remove this” for regex
  stages), Batch column, CLI stderr, `service.clean` payload. Config `fact_check.enabled`.
- [x] **P4 Encrypted PHI + retention** — `chartcleaner/secure_store.py` (Fernet;
  key in macOS Keychain / Windows DPAPI / 0600 `data/.datakey`;
  `CHARTCLEANER_KEY_BACKEND` forces one). Token maps are `tokens-*.enc`; legacy
  `.json` maps load and are re-saved encrypted. `store.purge_old_data()` (startup +
  hourly) deletes token maps, exports/batch output and `data/watched_out` older than
  `prefs.retention_days` (default 14). Settings → *Stored chart data*. Also fixed:
  prefs not in `DEFAULT_PREFS` were dropped on load; `--tokens-file`; tests writing
  the real `data/prefs.json`.
- [x] **P5 Split app.py** — `app_pages/` (one module per page, `common.py`,
  `updates.py`); routes in `app.ROUTES`; rebindable names read as `common.X`
  (enforced by a test).
- [x] **P2 Trends** — `chartcleaner/trends.py`: per-note lab values (lab-compaction
  aliases, case rules for short names) and med-list changes between notes; Clean
  page *Trends* tab, `--trends`, API/MCP `trends`.
- [x] **P3 Neuro ICU condensers** — stage `neuro_summary` (anchor `vitals_summary`,
  off by default): neuro checks, EVD/ICP (`icp_threshold`), serial sodium, drip
  titrations; each part switchable.
