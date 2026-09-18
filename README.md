# Chart Cleaner

Clean **Epic-style EMR exports** for safer sharing with LLMs or documentation — now as a **local desktop app** for macOS and Windows, with the original CLI still included.

- **Clean page** — paste or drop a chart, clean it, and inspect a **side-by-side diff**, per-run stats (characters/words/PHI/duration), and a table showing exactly what each cleaning stage did. **Drop .docx or .pdf files** — they're converted automatically (Word via the built-in reader or markitdown; PDFs via PyMuPDF with OCR fallback and page header/footer removal).
- **Post-run review** — after every clean, an audit scans the *surviving* text for leftovers (long digit runs, DOB-style lines, phones/emails, identity labels, Epic chrome) and lists them as review chips, with flagged lines highlighted in the diff and a one-click **Build rule** for any finding.
- **Rule suggestions** — findings that keep surviving run after run surface as suggestion cards on the Pipeline page, with a pre-drafted regex and live match counts. Adopt, or dismiss forever.
- **Rule packs** — curated rule sets shipped with the app: *HIPAA Safe Harbor (strict)* and *Philter core PHI* (ported from the published UCSF pipeline). Install as a preset or apply directly from the Pipeline page.
- **Reversible tokenization** — optionally swap PHI for stable `[[T1]]`-style codes instead of deleting it. Value→token maps are saved locally and can restore the original text later (Settings → Token maps, or `clean-chart --untoken`).
- **Pipeline & Rules page** — every cleaning rule is editable in the app: enable/disable, reorder, add/edit regex patterns (with live match counts against a sample), tune NLP redaction entities and thresholds, tune the review checks, and save any rule set as a named **preset** (import/export as JSON).
- **Evaluation report card** — generate synthetic charts with known PHI and measure how much your current rules actually catch (per-type recall), right on the Statistics page or with `clean-chart --evaluate`.
- **Folder watcher** — point it at a folder; every chart file dropped in is cleaned automatically to an output folder (Settings page, or `clean-chart --watch DIR`).
- **Custom Scripts** — write your own cleaning stage in Python (`clean(text, ctx)`) in the built-in editor; anything is possible: block removal, redactions, restructuring, counters for the stats tracker.
- **Statistics dashboard** — every run is recorded locally: total characters removed, average reduction, PHI redactions by type, a per-day chart, and **which stages clean the most**.
- **Config backups** — every rules save keeps a timestamped copy (`data/backups/`, newest 5) with a restore list in Settings.
- **100% local** — the app binds to `127.0.0.1` only, stores everything under the project folder (`data/`), and makes no network calls.

**Not** a guarantee of de-identification under HIPAA or other rules — review output before sharing.

---

## Quick start

### macOS

```bash
cd /path/to/chart-cleaner
./install.sh          # once: creates .venv, installs deps (needs internet)
./run-app.command     # the app — opens in your browser
```

`run-app.command` is double-clickable in Finder (it runs the install automatically on first use).

### Windows

1. Install Python 3.10+ from [python.org](https://www.python.org/downloads/) for **your user only**, with **"Add python.exe to PATH"** checked.
2. Double-click **`Run Chart Cleaner.bat`** — first run installs everything into `.venv`, then the app opens in your browser.

(Alternatively run `install.bat` once, then `Run Chart Cleaner.bat`.)

The app picks the first free port from 8765 and prints the URL; close the terminal window (or Ctrl+C) to quit.

### CLI (still available)

```bash
./clean-chart                 # macOS/Linux: clipboard in → cleaned out
clean-chart.cmd               # Windows
./clean-chart -f chart.txt    # single file — .txt/.md/.docx/.pdf (-d dir, -o out, --no-wrap)
./clean-chart --audit         # also print post-run review findings
./clean-chart --evaluate 50   # synthetic benchmark: recall report card for current rules
./clean-chart --watch ~/Inbox/charts   # auto-clean every file dropped in the folder
./clean-chart --untoken       # restore [[Tn]] tokens using the newest saved map
```

The CLI prints the same per-stage statistics as the app.

---

## The cleaning pipeline

Stages run top-to-bottom (you can reorder them in the app):

| # | Stage | What it does |
|---|-------|--------------|
| 1 | EMR line metadata | Deletes whole lines matching regexes (author/pager/version lines, Epic chrome). |
| 2 | Boilerplate blocks | Deletes multi-line blocks (disclaimers, empty SmartSections). |
| 3 | Structured PHI patterns | Regex→replacement pairs (MRN, DOB, phone lines…). |
| 4 | NLP redaction (Presidio) | NLP-based redaction of names/phones/emails, with allow-list and confidence threshold. |
| 5 | Literal replacements | Abbreviations and text fixes (e.g. *hypertension* → *HTN*). |
| 6 | Whitespace cleanup | Trims trailing spaces, collapses blank-line runs. |
| 7 | Duplicate note folding | Folds near-duplicate Epic note blocks by body similarity. |
| 8 | Fuzzy paragraph dedup | Collapses copy-forwarded paragraphs. |
| 9 | Header promotion | Turns known section headers into `## Header` — or lets medspaCy's sectionizer detect them (`headers_engine: medspacy`). |
| 10 | Bullet normalization | Normalizes •, *, - bullets. |
| 11+ | **Your custom scripts** | Any `custom_rules/*.py` file — see below. |

Off by default, between stages 2 and 3: **Reversible tokenization** — swaps structured PHI for stable `[[T1]]` codes (same value ⇒ same token) and saves the value→token map so the text can be restored later.

Output is wrapped in `<patient_chart>…</patient_chart>` unless disabled (Settings page or `--no-wrap`).

---

## Customizing — as deep as you want

### 1. Edit the built-in rules (Pipeline & Rules page)

Every stage is a card: toggle it, reorder it with the arrow buttons, and edit its patterns, replacements, or thresholds inline. Each pattern shows a live **hit count** against the test text so you can see exactly what a regex will catch before saving. Save writes `config.json` (a `.bak` of the previous version is kept); invalid regexes are caught before saving.

`config.json` remains fully documented and hand-editable — the app simply edits it for you.

### 2. Presets

Save the current rules as a named preset (e.g. *"Neuro-ICU strict"*, *"Light clean"*), switch presets from the Clean page, share them as JSON files (export/import). Factory defaults can be restored with one click.

### 3. Custom Python rules (Custom Scripts page)

Drop a `.py` file into `custom_rules/` (or create it in the app). It becomes a reorderable pipeline stage:

```python
LABEL = "Redact long ID numbers"
DESCRIPTION = "Masks standalone 6+ digit numbers."
PLACEHOLDER = False

import re

def clean(text: str, ctx) -> str:
    new_text, n = re.subn(r"\b\d{6,}\b", "[REDACTED_NUMBER]", text)
    ctx.count("numbers_redacted", n)   # shows up in the Statistics tracker
    ctx.log(f"Masked {n} long number(s).")
    return new_text
```

- `ctx.count(key, n)` — counters for the stats tracker; `ctx.log(...)` — notes in run details; `ctx.config` — read-only view of `config.json`.
- Use any stdlib or installed package (thefuzz, presidio, …).
- The editor validates syntax, runs the script against the test text, and reports errors.
- **Scripts run with your user's full privileges** — only add code you wrote or reviewed.
- Two worked examples ship in `custom_rules/`.

---

## Where your data lives

| Path | Contents |
|------|----------|
| `config.json` | Cleaning rules (presets in `presets/`, packs in `chartcleaner/packs/`) |
| `data/stats.jsonl` | One line per cleaning run — the statistics history |
| `data/audit_hits.jsonl` | One line per run — which leftover patterns the audit saw |
| `data/backups/` | Timestamped config backups (newest 5, restorable in Settings) |
| `data/tokens/` | Reversible-tokenization maps (newest 10) — **these undo your cleaning** |
| `data/evaluation.json` | The last recall report card from the evaluation harness |
| `data/watch.json` | Folder-watcher configuration |
| `data/exports/` | Downloaded results (auto-pruned after 24 h) |
| `custom_rules/` | Your Python cleaning stages |

Everything stays on this machine. Delete `data/` to reset all history.

---

## Files in, files out (v2.2)

The Clean page upload, batch folders, and `-f` all accept `.txt`, `.md`, `.docx`, and `.pdf`:

- **.docx** — built-in zero-dependency reader; if `markitdown` is installed it is preferred (better tables/headings).
- **.pdf** — text extracted with PyMuPDF; repeated page headers/footers and stray `#` heading markers are stripped automatically. Scanned PDFs (no text layer) fall back to OCR when `ocrmypdf` is available, and are flagged as needing OCR otherwise.
- Optional engines (`markitdown`, `docling`, `ocrmypdf`, `medspacy`) are **auto-detected** — install any of them and the Settings page shows them light up. The app works without them.

## The evaluation harness

`--evaluate` (CLI) or the Statistics page generates N synthetic Epic-style charts with *known* planted PHI — names, MRNs, DOBs, phones, emails, SSNs, URLs, addresses, ages > 89 — runs your current rules, and reports **recall**: how many PHI items are actually gone from the output, per type. Missed items are listed so you know exactly which rules to tighten (the shipped packs are a good next step). Deterministic per seed, so scores are comparable over time.

---

## The post-run review (audit)

Cleaning rules remove what they match; the review tells you what *survived*. After each
run, five read-only checks scan the cleaned output:

| Check | What it flags |
|-------|---------------|
| Long digit runs | Standalone numbers of 6+ digits (MRN / accession style) |
| Label-adjacent dates | DOB-style dates on birth-date-labelled lines |
| Phone / email | Phone-shaped numbers and email addresses |
| Names after labels | Values after `Patient:`, `Next of Kin:` … that are not placeholders |
| Residual EMR chrome | Known Epic noise lines (editor, pager, version stamps) |

Findings appear as an expandable review strip on the Clean page and as `--audit` output
in the CLI. Every check is toggleable on the Pipeline & Rules page ("Review checks"),
and none of them can change the cleaned text — they only look.

Findings feed the **Suggestions** section on the Pipeline page: once a finding type has
survived 3+ runs in the last 30 days, a card proposes a ready-made rule (live match count
included). Adopting opens the rule in the stage editor — nothing is saved without your
confirmation. "Dismiss forever" silences a suggestion for good.

Config saves are also protected: every save drops a timestamped backup into
`data/backups/` (newest 5), restorable from Settings. And when the app starts and its
default port is taken by **another copy of itself**, it simply opens the running one
instead of starting a second instance.

---

## How the tracker works

Each run records before/after counts of characters, words and lines, per-stage match counts and character deltas, PHI redactions by type, and duration. The Statistics page aggregates: total characters removed, average reduction %, top stages by contribution, PHI breakdown, and a per-day chart. The CLI prints the same summary per file.

---

### Standalone app (no Python needed)

Build a self-contained app bundle once (on each platform), then share or copy it like any other app:

```bash
./build_app.sh        # macOS  → dist/Chart Cleaner.app
build_app.bat         # Windows → dist\ChartCleaner\ChartCleaner.exe
```

The bundle includes Python, Presidio, and the spaCy model. On first launch it creates `config.json`, `custom_rules/`, and `data/` **next to the app**, so your rules and history stay there. Quit via Settings → "Quit app"; logs land in `data/app.log`.

## Requirements

| Requirement | Notes |
|-------------|-------|
| **Python 3.10+** (3.11–3.12 recommended) | Presidio/spaCy wheels are most reliable there. |
| **Internet (first install only)** | pip downloads packages and the spaCy model wheel. |
| **Write access** to this folder | The venv lives in `.venv/`. |

Clipboard mode uses [`pyperclip`](https://pypi.org/project/pyperclip/). The app binds to `127.0.0.1` only and strips external font CDNs so it works fully offline.

> **Note:** older versions of `requirements.txt` listed `en-core-web-sm` from PyPI, which does not exist — it now installs the official spaCy model wheel directly.

---

## Options & troubleshooting

| Symptom | Fix |
|---------|-----|
| Port already in use | The app auto-picks the next free port; or run `python app.py --port 9000`. |
| Don't want a browser window | `python app.py --no-browser`, then open the printed URL. |
| "NLP redaction skipped" warning | Presidio or the spaCy model is missing — re-run `install.sh` / `install.bat`. |
| Regex errors on save | The Pipeline page validates before saving; fix the highlighted pattern. |
| Windows "cannot run scripts" | Use `Run Chart Cleaner.bat` / `install.bat` (no PowerShell policy needed). |
| No admin rights | Everything installs under this folder with per-user Python — no elevation needed. |

`python app.py --help` for all launch options.

---

## Roadmap — further enhancements

Ideas that would take this tool further (implemented ideas live in Settings → "Ideas"):

- **Folder watcher** — auto-clean files dropped into a watched directory.
- **.docx / .pdf input** via python-docx / pdfplumber.
- **Rule packs** — community presets with a review workflow.
- **Recent runs** — reopen or re-run a previous cleaned output in one click.
- **Weekly self-report** — scheduled summary of cleaning statistics.

---

## Tests

```bash
python -m pytest        # from this folder (needs the venv active)
```

The suite covers every audit check (positives and known-safe negatives), store
persistence (suggestions, dismissals, backup rotation and restore), a golden-file test
so engine changes can't silently alter cleaning output (`CHARTCLEANER_REGEN_GOLDEN=1`
regenerates it), and a build test that renders every app page.

---

## Project layout

```
app.py                  # the desktop app (NiceGUI, local web UI)
medical_cleaner.py      # CLI entrypoint (same engine)
chartcleaner/
  engine.py             # stage pipeline + per-stage statistics + custom-rule loader
  audit.py              # post-run review: leftover-PHI checks + rule suggestions
  store.py              # history, preferences, presets, exports, backups
  default_config.json   # factory-default rules (restore from Settings)
config.json             # your current rules (hand-editable)
custom_rules/           # your Python cleaning stages (examples included)
data/                   # run history + exports (gitignored)
presets/                # named rule presets (gitignored)
tests/                  # pytest suite + golden file

install.sh / clean-chart            # macOS/Linux venv setup + CLI launcher
run-app.command                     # macOS app launcher (double-click)
install.bat / install.ps1 / clean-chart.cmd / Run Chart Cleaner.bat   # Windows
Clean_Medical_Chart.*               # clipboard-only helpers (unchanged)
sample_chart.txt        # synthetic demo chart — try "Load sample chart" in the app
```

## Privacy and compliance

This tool **reduces** obvious PHI and EMR noise; it is **not** a guarantee of de-identification under HIPAA or other rules. **You** are responsible for what you paste, store, or send to third parties. Review output before sharing. Adjust rules and Presidio settings for your institution's policy.

## License

Use and modify for your own workflow. If you share forks publicly, keep compliance and privacy warnings prominent.
