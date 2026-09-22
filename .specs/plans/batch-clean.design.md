# Design: Batch page — promote and upgrade interactive batch cleaning

Batch cleaning today lives in a small Clean-page expansion (folder path →
timestamped exports folder, table of char counts). That's easy to miss and
missing the useful numbers. Promote it to a dedicated **/batch page** (nav:
"Batch") and make it the one place for multi-file cleaning:

- **Two inputs**: multi-file upload (`.txt/.md/.docx/.pdf`, same accept list as
  Clean) *or* a folder path — charts often arrive as downloads where typing the
  folder is still the fastest path.
- **Richer per-file results**: chars before → after, reduction %, PHI
  redactions, post-run audit findings, elapsed ms, status/error.
- **Downloads**: per-file "Download .txt" and **Download all (.zip)** — the zip
  is written to `data/exports/` (already served by the `/exports` static route);
  `store.prune_exports` learns to prune `*.zip` alongside `*.txt`.
- Every cleaned file still lands in `data/exports/batch_<ts>/` on disk and in
  run history (`store.append_run`), exactly like the old expansion.

Decided (2026-09-21): batch loop extracted to `chartcleaner/batch.py` so it is
unit-testable without NiceGUI and the page stays thin. The Clean-page expansion
is removed (replaced by a pointer line to the new page) — one way to batch, not
two. Rejected: parallel file processing (NLP stages aren't thread-safe-cheap and
10 files run in seconds sequentially), recursive folder walking (Epic exports
usually sit flat; surprises users with hidden subfolders).

## Module: `chartcleaner/batch.py`

- `@dataclass BatchResult: name, path, status ("ok"|"error"), error, chars_before,
  chars_after, reduction, phi_total, findings, elapsed_ms, cleaned` — `cleaned`
  holds the cleaned text so the page can export without re-running.
- `run_batch(paths, cfg, custom_dir=None) -> list[BatchResult]` — for each path:
  `ingest.load_file` → `Pipeline(cfg, custom_dir).run` → `audit.run_audit`;
  any per-file exception becomes `status="error"` with the message (one bad file
  never kills the batch). Order follows input order.

## Page: `/batch`

`shell("Batch clean", "/batch")`. Upload row (multiple, auto-upload) or folder
input; Run button + spinner; results table (pagination 20); under the table a
download row per ok file plus "Download all (.zip)"; "Open exports folder"
button. Files ≤ 500 per run (same cap the old expansion had). Uploads are read
into a temp dir first so `ingest.load_file` (path-based) works unchanged.

## Tests

`tests/test_batch.py`: mixed dir of two `.txt` files + one `.xyz` → two ok
results in order with real stats, one error result isolating the bad file;
empty file → ok with zero reduction. `tests/test_pages.py`: `/batch` builds and
shows its heading.
