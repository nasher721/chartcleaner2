# Task-centered UI redesign

Date: 2026-10-24
Status: Implemented and verified

## Summary

Reorganize Chart Cleaner's existing NiceGUI interface around three user tasks: **Work**, **Customize**, and **System**. Keep every current feature, clinical safeguard, local-first behavior, keyboard workflow, and the Nothing-inspired visual identity. Reduce screen complexity through progressive disclosure rather than basic/advanced modes or feature removal.

The main workflow remains a two-column Clean workspace on wide screens, expressed as **Input → Review → Export**. The Review tab becomes a unified, prioritized experience with **Findings**, **Changes**, and **Diagnostics** views. Batch, Census, Rules, Pipeline, Settings, Statistics, and Doctor adopt the same summary-first and primary-action patterns.

## Goals

- Make paste → clean → review → copy faster and calmer.
- Make existing features easier to discover without showing every control at once.
- Preserve fast access to detailed clinical and technical audit evidence.
- Establish consistent action hierarchy, empty states, loading states, errors, filters, and responsive behavior.
- Improve visual refinement while preserving the current design identity.
- Deliver the redesign incrementally without changing cleaning output or release semantics.

## Non-goals

- No new cleaning stages, clinical inference, validation rules, or destination policies.
- No change to `release_decision`, copy guards, acknowledgment semantics, audit history, or AI grounding rules.
- No basic/advanced application mode.
- No new frontend framework or replacement of NiceGUI.
- No new visual brand, external assets, font service, or network dependency.
- No removal of existing features, export formats, integrations, or keyboard workflows.

## Current design constraints

The application already has a shared token-based design system, self-hosted fonts, light and dark themes, keyboard shortcuts, a command palette, responsive navigation, accessibility landmarks, and a page error boundary. The Clean page already provides a two-column workspace, contextual selection tools, Output/Review/Insights tabs, and an optional AI drawer.

The redesign addresses density and organization rather than replacing those foundations. Existing cleaning, validation, provenance, review, export, and local-AI modules remain the sources of truth.

## 1. Application shell and navigation

### Grouped navigation

Organize the nine pages as follows:

- **Work:** Clean, Batch, Census
- **Customize:** My text rules, Pipeline & Rules, Custom Scripts
- **System:** Statistics, Settings, Doctor

On desktop, Clean, Batch, and Census remain directly visible. Customize and System are labeled menus containing their pages. The active group is visually identified when one of its pages is open, and every page retains `aria-current="page"` where applicable.

On narrow screens, the navigation drawer lists all pages under the three group headings. The command palette continues to expose every page and command directly.

### Global status and actions

The default header status strip shows only current rules and local-AI availability. Clipboard monitoring and stored-data encryption move into a **Local & private** popover. This popover describes local settings and storage; it must not imply output safety or guaranteed de-identification.

Release status remains attached to the current result, never the global header. The command palette, keyboard-shortcut help, theme toggle, page-specific actions, privacy footer, and version display remain available.

## 2. Clean workspace

### Task flow

Use a compact, informational **1 Input → 2 Review → 3 Export** indicator. It communicates workflow state but does not create separate routes or hide the result behind a wizard. Input and result remain visible together on wide screens.

### Input column

The primary toolbar contains Clean, Paste, and an overflow menu for Sample, Clear, and Auto-clean. Clean remains the dominant action and retains its existing shortcut and running state.

The Full clean / Abbreviations only / Expand abbreviations mode selector, rule preset, and destination stay visible in a compact run-configuration row. The current explanatory mode note remains, with destination-specific descriptions taking precedence exactly as they do now.

Move Selection settings and rule-history controls into an **Advanced input tools** disclosure. The selection bar remains contextual. Its initial actions are Remove, Line, Replace, Abbreviate, and Rule…; Never remove, Collect, matching options, and More move into its overflow menu. Right-click actions and existing selection shortcuts remain intact.

The input footer combines character/word count, bed tag, file drop, and Batch entry. Upload behavior and supported formats do not change.

### Result column

Use one result header containing:

- Primary state: Ready, Review needed, or Blocked
- Cleaning summary: character/token reduction, possible identifiers redacted where applicable, and elapsed time
- Note-type information when detected
- One dominant next action: Review findings when required; otherwise Copy, supplied by the persistent result action bar defined in section 4 rather than a duplicate button

Retain Output, Review, and Insights tabs. Display finding counts consistently. Show Insights only when the current mode supports it.

The empty result state remains useful and concise: explain where the cleaned chart will appear, how to run the cleaner, and what Output, Review, and Insights provide. First-run onboarding remains available but uses the same shared presentation patterns.

### Responsive behavior

- At viewport widths of 1200 px and above, use two resizable input/result columns.
- From 768 through 1199 px, stack input above result and move the result into view after a clean without discarding input state.
- Below 768 px, provide adjacent Input and Result views with a persistent segmented switch. Open Input before the first run and Result after a successful run; the user can switch at any time.
- The AI drawer overlays content on narrow screens rather than forcing horizontal page scrolling.

No responsive transition may trigger a clean, clear state, or change output.

## 3. Unified Review experience

Review has three views: **Findings**, **Changes**, and **Diagnostics**. A persistent blocking banner remains visible across all three whenever the release decision is blocked, regardless of filter or selected view.

### Findings

Findings is the default Review view when actionable issues exist; Changes is the default when none exist. Findings combines user-facing findings from release and validation decisions, clinical-fact and meaning changes, introduced or implausible values, possible remaining identifiers, validator-rejected edits, and incomplete coverage.

Each row contains severity, plain-language title, source or stage, occurrence count when grouped, and applicable Locate or Edit output actions. Severity labels are Blocked, Review, and Info.

Group only findings whose existing finding type, severity, and stage/rule identity match. Preserve every source span and make all occurrences reachable; grouping must not discard evidence.

Filters are All, Blocking, Needs review, and Informational. The default shows unresolved actionable findings. Blocking findings remain represented by the persistent banner even when excluded by a filter.

Selecting a finding opens the existing style of source/output detail surface. Exact source links and nearest-line fallback remain honestly distinguished. Reformatted text must never be presented as an exact correspondence.

When no issues are detected, say **“No issues detected by these checks—review before sharing.”** Never claim that output is safe or fully de-identified.

### Changes

Keep the interactive before/after diff as the centerpiece. Provide Changed, Removed, and Abbreviations views.

Preserve clickable spans and existing actions: keep text, pause or delete rules, change abbreviations, copy source, and open Pipeline. Add Previous/Next change navigation and a compact position counter.

Collapse long runs of unchanged lines by default, showing three context lines around a change. Users can expand any collapsed region. Collapsing affects presentation only and never changes source or output text.

### Diagnostics

Diagnostics contains validation coverage and support level, intentional reductions, detailed edits retained as original, per-stage match/character/timing/error information, and technical validation codes and grouping details.

Findings may summarize retained-original edits and link to Diagnostics; Diagnostics retains their full technical evidence.

## 4. Output, export, and local AI

### Result action bar

Use a persistent result action bar. Copy is primary when release is ready; Review is primary when attention is required. Copy as… retains Epic-safe text, Markdown, prompts, and all other current formats.

Place Download, Save known-good, Restore names, Daily note, and other lower-frequency actions in overflow. Show the selected destination policy beside the action bar so users can confirm the intended destination before copying.

All guarded-copy, release acknowledgment, and destination-policy behavior remains unchanged.

### Local AI drawer

Use Summary and Ask chart tabs. Keep model/status information in a compact header and place prompt presets and custom prompts under Prompt settings.

Display grounding, citations, and unsupported-value warnings adjacent to the generated content. Preserve streaming, restore-names, copy, conversation history, and model-unavailable recovery behavior.

## 5. Secondary workflows

### Batch

Separate Queue, Run options, and Results. Move delta view, note-type presets, and patient-list AI into Run options.

Lead results with processed, needs-review, and error counts. The default table shows File, Reduction, Findings, Status, and Actions. Preserve all other metrics in expandable row details. Consolidate per-file downloads into each row and keep Download all prominent. Retry failed, patient-list mode, progress, and export-folder access remain available.

### Census

Keep allergies, code status, contradictions, and safety flags at the top of each card. Add Needs attention and All beds filters. Use a consistent collapsed state for lower-priority details while never hiding active safety flags.

Group Copy and Download actions into one export menu. Preserve existing extractors, deltas, to-dos, and the principle that “not mentioned” never means “not done.”

### Rules and Pipeline

My text rules remains the simple quick-add experience. Pipeline & Rules remains the advanced editor. Add clear contextual cross-links between them and standardize search, filters, row actions, confirmations, undo entry points, previews, and empty states.

### Settings, Statistics, and Doctor

Organize Settings into labeled categories with a local search field that filters existing setting labels and descriptions. Do not add a second settings store.

Statistics and Doctor use summary-first presentation: headline state and key metrics first, detailed charts and diagnostics on demand. Existing repair and diagnostic actions remain available.

## 6. Visual system and accessibility

Retain the Nothing-inspired tokens, self-hosted Space Grotesk, Space Mono, and Doto fonts, current color semantics, restrained motion, and existing aliases.

Use Space Grotesk for explanations and clinical prose. Reserve Space Mono for labels, data, buttons, navigation, and compact technical readouts, and Doto for metric numerals. Avoid introducing additional colors or status systems.

Each region has at most one visually dominant primary action. Shared components cover action bars, section headers, disclosures, finding rows, and empty/loading/error/success states.

Preserve skip navigation, landmarks, keyboard activation, accessible names, visible focus, reduced motion, and screen-reader status updates. Drawers and dialogs restore focus to their invoking control when closed. New view switches and finding navigation are keyboard-operable.

The interface remains usable at 200% zoom and has no horizontal page scrolling at a 360 px viewport. Data tables may become horizontally scrollable within their own labeled region or use row cards; they must not force the whole page to scroll sideways.

## 7. Component and data architecture

### Ownership

- `app_pages/common.py`: grouped shell, navigation, quieter global status, and shared shell behavior.
- `app_pages/design_system.py`: tokens and reusable presentational components only.
- `app_pages/clean.py`: cleaning orchestration, current-run state, selection actions, undo, copy, and AI lifecycle.
- A focused Review UI module under `app_pages/`: Findings, Changes, and Diagnostics rendering, with explicit inputs and callbacks.
- Existing secondary-page modules: adoption of shared components without changes to service or clinical-extractor behavior.

The Review UI consumes existing result objects, `release_decision`, `review_state`, validation results, fact checks, and audit findings. It does not implement another severity or release engine. Presentation-only grouping and filtering are separate from release computation.

### State and data flow

`CLEAN_STATE`, result caching, provenance, acknowledgments, run history, and existing service payloads remain unchanged. The existing clean run populates the same result and review data; the redesigned UI derives its view state from those values.

Selected views and filters are session-local UI state. No new persisted configuration group is required. Existing preferences continue to control theme, AI drawer, selection mode, presets, destinations, and onboarding.

### Failure behavior

A Review rendering failure must never produce a Ready state. Preserve and display the independently computed release status, retain Output, render the existing side-by-side diff through a separate minimal fallback path, and report that detailed findings are unavailable.

Expected failures remain in their panels with actionable recovery. Unexpected page failures continue through `common.guarded`, the recovery screen, and the redacted rotating log. Empty, unavailable, and failed validation states remain distinct.

## 8. Verification

### Automated tests

- Unit-test finding order, grouping identity, filters, counts, empty states, and failure fallback.
- Preserve every existing public test marker on its equivalent control; add new markers for the grouped navigation, Review views, and responsive Input/Result switch.
- Retain NiceGUI coverage for Clean, Review, AI lifecycle, copy guards, Batch, Census, Rules, accessibility, and error boundaries.
- Add tests for grouped navigation, responsive view switching, Review view selection, blocking-banner persistence, and focus restoration.
- Run golden and property tests to confirm byte-identical cleaning behavior and unchanged provenance/release semantics.

### Manual checks

Verify these journeys in both themes:

1. First-run sample clean and onboarding dismissal.
2. Paste → clean → review → copy.
3. Blocked copy → finding location → output edit → acknowledgment.
4. Create, pause, and undo a rule from a reviewed change.
5. Batch queue, progress, filtering, retry, per-file download, and zip export.
6. Census filter and Census-to-Clean handoff.
7. AI summary, chart question, unsupported values, and restore-names flow.
8. Keyboard-only operation, reduced motion, 200% zoom, and 360 px layout.

## 9. Incremental rollout

1. Shared components and grouped shell.
2. Clean workspace and Output actions.
3. Unified Review experience.
4. Batch, Census, and remaining-page consistency.
5. Responsive, keyboard, and accessibility polish.

Each increment must remain independently usable and tested. An increment may change presentation and component boundaries but must not change cleaning-engine behavior, clinical-safety decisions, or stored clinical data.

## Success criteria

- Routine Clean and Copy require no additional clicks.
- Advanced tools remain available with at most one additional disclosure.
- The default Clean screen has one clear primary action and fewer persistently visible controls than the current implementation.
- Blocking findings cannot be hidden by filters or view selection.
- No feature, destination policy, audit trail, keyboard workflow, or safety gate is removed.
- No horizontal page scrolling occurs at 360 px, and functionality remains available at 200% zoom.
- Existing golden output and safety behavior remain unchanged.
