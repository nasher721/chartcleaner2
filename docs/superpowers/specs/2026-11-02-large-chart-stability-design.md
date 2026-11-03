# Large-chart editing and cleaning stability

Date: 2026-11-02
Status: Design approved; awaiting written-spec review

## Problem

On the Clean page, charts of roughly 100,000–500,000 characters can feel slow both while editing and while cleaning. During cleaning, the browser shows a reconnecting banner and then recovers the same page. The desired fix prioritizes fluid editing, responsive controls, and connection stability. The editor may remain locked during an explicit Clean, and a clean may take up to 30 seconds if progress remains visible.

The app already has a 30-second reconnect timeout, paged findings, lazy Review/Insights panels, bounded validation work, and speculative cleaning. These mitigations do not isolate CPU-heavy Python work from the websocket-serving process, and the editor still performs server-side work and whole-chart synchronization around input changes.

## Chosen approach

Use stability-first isolation. Measure the current workload, move CPU-heavy cleaning and result preparation into one reusable worker process, and bound browser/server editor traffic. Preserve the cleaning pipeline and clinical validation behavior.

Two alternatives were rejected for this phase:

- A reduced-work large-chart mode would be simpler but could weaken validation and review evidence on the hardest charts.
- Incremental note-level cleaning could improve long-term throughput, but cross-note deduplication, ordering, provenance, and validation make it too broad for the first stability fix.

## Goals

- Eliminate reconnect banners and page rebuilds during representative 100–500 KB edit-and-clean workloads.
- Keep typing visually fluid and avoid whole-chart websocket updates for every keystroke.
- Keep websocket and UI handling responsive during CPU-heavy cleaning.
- Show honest stage-level progress and elapsed time.
- Preserve cleaned text, stage order, validation results, provenance, release decisions, and existing clinical behavior.
- Keep chart content in memory and local to the app and its private worker pipe.

## Non-goals

- No reduced-validation or reduced-feature large-chart mode.
- No changes to cross-note cleaning, deduplication, or clinical extraction algorithms.
- No changes to Batch, REST, MCP, AI summarization, or Ask this chart execution paths.
- No character-delta editor synchronization protocol.
- No requirement to edit the input chart while an explicit Clean is running.
- No increase of the reconnect timeout as a substitute for removing event-loop stalls.

## Architecture

The Clean page has three execution paths:

1. **Browser:** immediate editing, character/word counts, selection handling, and progress display.
2. **Main app process:** websocket traffic, UI state, configuration, clipboard access, encrypted persistence, and the existing release gate. Short disk operations may continue using `run.io_bound`.
3. **One reusable cleaning worker process:** the CPU-heavy pipeline, audit, redaction analysis, and requested Review/Insights preparation. Python CPU work in this process cannot delay the main process's websocket heartbeats.

The worker is app-wide and reused across Clean-page jobs. It starts in the background for the Clean UI and warms the required NLP engine there, avoiding duplicate model warm-up in the UI process. Only one job executes at a time. A new explicit Clean supersedes stale speculative work; explicit jobs otherwise retain their current serialized execution semantics.

The worker receives an immutable snapshot containing the chart, mode, effective configuration, and relevant paths. It returns the result data consumed by the Clean page today. Chart content is exchanged only over the private local worker pipe and is not written to temporary files.

The existing 30-second reconnect timeout remains as a safety net.

## Component boundaries

### Editor synchronization

A small browser-side Clean-page module owns:

- Local character and word counts, updated at most once per animation frame.
- Selection-event filtering so ordinary typing emits no selection traffic.
- Debounced whole-snapshot synchronization after a 300 ms typing pause.
- Monotonically increasing editor revisions and acknowledgement tracking.
- Flushing the newest snapshot before any action that consumes chart text.

The main process remains responsible for authoritative `CLEAN_STATE` updates. The browser is authoritative for unsent editor changes until their revision is acknowledged. A reconnected tab re-synchronizes its unacknowledged editor value.

### Cleaning computation

Extract the existing nested Clean-page computation into a module-level, side-effect-free computation boundary. It produces the pipeline result, audit, requested extras, redaction candidates, change groups, finding rows, and non-lazy Review content without performing persistence or UI operations.

An optional pipeline progress observer reports stage boundaries without changing stage behavior. Progress contains no clinical text.

### Worker lifecycle and protocol

A worker manager owns process startup, NLP warm-up, request IDs, progress messages, cancellation, one retry after worker failure, and shutdown. It supports computation and lazy Review/Insights jobs through the same process boundary.

The main process never reads worker messages synchronously on the event loop. Progress messages contain only job ID, stage ID and label, stage number, total stages, and elapsed time.

The UI orchestration in `app_pages/clean.py` verifies job identity and input digest before publishing a result. `review_state.release_decision` remains the only release gate; presentation code does not recompute or bypass it.

## Editing data flow

1. The browser updates counts locally and filters selection events.
2. After a 300 ms typing pause, the browser sends the current full chart snapshot and its editor revision to the main process.
3. Clean, selection actions, upload, undo, and page-state restoration flush pending input and wait for acknowledgement before consuming chart text.
4. Ordinary typing does not send selection events or trigger server-side word splitting.
5. If a reconnect occurs with unacknowledged edits, the same page re-synchronizes its current editor value. Revision checks prevent an older snapshot from overwriting newer state.

Whole-snapshot synchronization is intentionally retained to avoid fragile character-delta merging.

## Cleaning data flow

1. Clean prepares the effective configuration and input digest as it does now.
2. The main process sends one immutable job to the worker and locks the editor.
3. The worker reports small stage-boundary messages. The main process immediately updates the progress label without transmitting chart text.
4. The worker prepares CPU-heavy result metadata. Review and Insights remain lazy at the existing size threshold and use the worker when first opened.
5. When computation finishes, the main process verifies that the job is current, persists history and encrypted records off the event loop, and publishes the result once.
6. The output textarea is transmitted only when the result changes. The previous result remains visible until a successful replacement is ready.
7. Speculative cleaning uses the same worker. Newer input invalidates older speculative work, and stale results are discarded.

## Progress, cancellation, and recovery

Progress displays `Stage X of Y: [stage name] · N seconds elapsed`. It does not display a fabricated percentage because stage durations vary. Progress appears within one second of pressing Clean and advances at stage boundaries.

Every job has a unique ID and input digest. Only the current job may update the page or replace the displayed result. Stale speculative jobs stop at stage boundaries and never affect current state.

If the websocket reconnects while a job is running, the page restores current progress and receives the result normally. A job lasting longer than 30 seconds is allowed and must not itself cause disconnection.

If the worker crashes or becomes unresponsive, the main process starts a fresh worker and retries the pure computation once. If the retry fails, the page shows a concise error, preserves the previous result, clears progress, and unlocks the editor. The app does not silently fall back to running a large clean on the UI thread.

Worker startup and result-transfer failures produce structured errors. Application shutdown closes the worker cleanly and leaves no orphan process. Failed or stale jobs do not modify run history, recent charts, token maps, or the previous displayed result.

Logs may contain job IDs, durations, stage names, and sanitized exception details, but no chart content.

## Verification

### Representative benchmarks

Use synthetic multi-note fixtures of 100 KB, 250 KB, and 500 KB. Measure baseline and optimized behavior on the same reference Mac, with cold worker startup reported separately from warm runs. Exercise repeated editing, cleaning, reconnecting, and opening Review/Insights rather than timing only the pipeline.

Record wall-clock and CPU time, event-loop responsiveness, browser input responsiveness, websocket message counts and sizes, worker memory, and cleaning duration. Wall-clock thresholds belong in scheduled benchmarks, not brittle per-PR assertions.

### Success criteria

- Zero reconnect banners or page rebuilds during 10 consecutive 500 KB edit-and-clean cycles, including cycles lasting at least 30 seconds.
- Main-process event-loop checks respond within one second at the worst observed point, with a 100 ms p95 target.
- Browser typing has a 100 ms p95 input-response target, with no whole-chart websocket update per keystroke.
- Progress appears within one second and advances at stage boundaries.
- Warm 500 KB cleaning completes within 30 seconds on the reference Mac; exceeding the target must not cause disconnection or hide progress.
- Worker and in-process execution produce identical cleaned text, validation reports, provenance summaries, and release decisions on the same fixtures.
- No worker remains after shutdown, and no chart text appears in temporary files or logs.

### Automated tests

- Compare worker and in-process results across golden charts and all validation modes.
- Instrument browser/server messages to prove typing and selection traffic is bounded.
- Simulate slow stages and verify websocket heartbeats continue.
- Test editor revision flush and acknowledgement, reconnect with unacknowledged edits, and stale revision rejection.
- Test worker crash, one retry, repeated failure, stale speculation, reconnect during Clean, lazy-panel loading, and shutdown.
- Verify packaged startup and NLP model loading on macOS and Windows.
- Run the existing full test suite to catch behavior regressions.

## Implementation planning constraint

Preserve the current clinical and release behavior while changing execution and transport boundaries. Any optimization that cannot reproduce the existing output and validation contract is outside this design and requires a separate design decision.
