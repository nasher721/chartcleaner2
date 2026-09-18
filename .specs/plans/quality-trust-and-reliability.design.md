# Design: Quality & trust package + reliability fixes

Chart Cleaner currently says what it removed but never what it missed. This design adds a
post-run review layer, turns repeated findings into rule suggestions, and fixes three
reliability gaps. Single user, single machine — no onboarding or installer work.

## Goals

1. After every run, show what survived cleaning and deserves a second look.
2. When the same finding repeats across runs, propose a rule to catch it.
3. Make config saves recoverable, port conflicts self-resolving, and UI errors visible.
4. Protect cleaning behavior with tests so engine changes cannot silently alter output.

## Non-goals

- No new input formats, file batches, or folder watching (separate package).
- No local LLM, no network calls. Everything stays local.
- No auto-application of rules; every adoption requires confirmation.

## Part 1 — Post-run review layer

New module `chartcleaner/audit.py`. It runs after `Pipeline.run` and never modifies
output. Five checks over the cleaned text:

| Check | Detects |
|-------|---------|
| `long-digits` | Standalone runs of 6+ digits (MRN/account numbers) |
| `date-like` | DOB-style dates near labels like "DOB", "Born" |
| `phone-email` | Phone and email patterns that escaped regex and Presidio |
| `label-names` | Names after known labels (`Patient: John Smith`) |
| `residual-chrome` | Known Epic noise patterns (pager lines, version stamps) |

Each check is independently toggleable and tunable in `config.json` under a new
`"audit"` section, edited from a "Review checks" card on the Pipeline & Rules page.

The Clean page gains a review strip under the results: one chip per check with counts.
Clicking a chip expands a findings list (line number + excerpt, capped at 50 per run);
flagged regions highlight in the output diff pane. `clean-chart --audit` prints the same
findings after the existing CLI summary.

## Part 2 — Suggestions and one-click rule drafting

Each run appends finding signatures to `data/audit_hits.jsonl` (gitignored, short
excerpts only). A signature is a normalized pattern id — `digits-6plus`,
`label-name:Patient` — or a line-shape fingerprint for residual chrome.

The Pipeline page shows a Suggestions section. A card appears only when a signature has
survived 3+ runs in the last 30 days. Each card carries a pre-drafted regex and a live
match count against the current sample text. Actions: **Adopt** (opens the stage editor
pre-filled; the normal save path, with its existing backup, applies it), **Dismiss
forever** (recorded, never resurfaces), or ignore (auto-expires after 30 quiet days).

Every finding in the review strip has a "Build rule" button that opens the stage editor
pre-filled with a regex targeting that excerpt. Nothing is auto-saved.

## Part 3 — Reliability fixes

**Rotating config backups.** Every save writes `data/backups/config-<timestamp>.json`,
keeping the last 5; the existing `.bak` stays unchanged. Settings gains "Restore previous
config" listing timestamp + rule count delta. Restore validates before replacing.

**Port handling.** When 8765 is taken, probe whether the listener is Chart Cleaner (HTTP
marker on `/`). If yes: print the URL, open the browser to it, exit the new process. If
unknown: next free port (current behavior) plus a one-line terminal note.

**Guarded errors.** Shared UI guard around pipeline and audit calls: exceptions open a
dialog with the traceback and append to `data/app.log`. Audit checks, hit-file appends,
and suggestion aggregation each fail independently; none can break a clean run.

## Data flow

Run → `Pipeline.run` (unchanged) → `run_audit(output, config)` → findings render in
review strip → signatures append to `data/audit_hits.jsonl` → Pipeline page aggregates
30-day hits into suggestion cards → adopt flows through the existing editor and save
path (which already keeps a backup).

## Testing

New `tests/` suite (pytest as the only new dev dependency):

- Unit tests per audit check: synthetic positives and known-safe negatives (years in
  "2024 guidelines", order numbers).
- One failing check never fails the run.
- Backup rotation keeps 5 and drops the oldest; restore rejects corrupt files.
- Golden-file test: `sample_chart.txt` → committed expected output, so engine changes
  that alter cleaning results fail loudly.

## Open items

- Exact regex defaults per check — tune against `sample_chart.txt` during implementation.
- Where dismissed-suggestion state lives (`data/suggestions_state.json` is the current
  lean choice).
