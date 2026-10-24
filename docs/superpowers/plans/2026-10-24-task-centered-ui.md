# Task-centered UI Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reorganize Chart Cleaner around task-centered navigation, a calmer Clean workflow, unified clinical review, and consistent secondary screens without changing cleaning output or release behavior.

**Architecture:** Keep NiceGUI and the existing service, validation, provenance, and release modules. Add small presentation-only modules for navigation, workspace layout, review projection, and shared workflow components; page modules retain their existing orchestration and callbacks. Deliver each screen behind the current public test markers and verify responsive behavior with the existing Playwright helpers.

**Tech Stack:** Python 3.10+, NiceGUI/Quasar, existing CSS design tokens, standard-library dataclasses, pytest, NiceGUI `User` tests, and existing Playwright screenshot/overflow scripts. No new runtime dependency.

**Spec:** [Task-centered UI redesign](../specs/2026-10-24-task-centered-ui-design.md). Read it before execution.

## Global Constraints

- Keep every current feature, clinical safeguard, local-first behavior, keyboard workflow, and the Nothing-inspired visual identity.
- Do not change `release_decision`, copy guards, acknowledgment semantics, audit history, cleaning stages, validation rules, destination policies, AI grounding rules, or stored clinical data.
- Do not introduce another severity or release engine, a basic/advanced application mode, a new frontend framework, a new visual brand, or a runtime dependency.
- Preserve every existing public test marker on its equivalent control. New behavior may change which menu or disclosure contains that control; update tests to open the disclosed surface before finding the marker.
- Preserve `common.CONFIG_PATH`, `common.PREFS`, and `common.CUSTOM_DIR` access through `common`; do not import rebindable values directly into new page modules.
- Register no new page with `@ui.page`; routes remain in `app.ROUTES`.
- Tests must not touch `data/`; use existing fixtures and never call `monkeypatch.undo()`.
- Responsive requirements are exactly: two resizable columns at 1200 px and above, stacked panes from 768 through 1199 px, Input/Result views below 768 px, no horizontal page scrolling at 360 px, and functionality at 200% zoom.
- A blocked release banner remains visible across all Review views and filters. Review-rendering failure must not produce Ready.
- No issue-free copy may claim safety; use exactly “No issues detected by these checks—review before sharing.”
- Run focused tests after every task and commit only that task's files. Do not amend or discard unrelated user changes.

## File structure and ownership

| File | Responsibility |
|---|---|
| `app_pages/navigation.py` (new) | Pure navigation grouping and active-group lookup; no NiceGUI imports |
| `app_pages/workflow_ui.py` (new) | Shared section headers, summary counts, overflow menus, and empty states |
| `app_pages/workspace_layout.py` (new) | Clean mobile-view state and resizable split behavior; no cleaning state |
| `app_pages/review_model.py` (new) | Presentation-only finding projection, grouping, filtering, and default-view selection |
| `app_pages/review_workspace.py` (new) | Findings/Changes/Diagnostics views, blocking banner, and diff navigation |
| `app_pages/batch_view.py` (new) | Pure batch count and row projection |
| `app_pages/common.py` | Shell integration and existing diff renderer; preserve public helpers |
| `app_pages/design_system.py` | Existing tokens plus responsive/workspace/review CSS only |
| `app_pages/clean.py` | Existing run orchestration and callbacks; compose new presentation modules |
| `app_pages/batch.py`, `census.py`, `rules.py`, `settings.py`, `stats.py`, `doctor.py` | Page-specific adoption; no service or extractor changes |
| `scripts/overflow_check.py`, `scripts/screenshot_ui.py` | Add Census and required viewports for manual visual verification |
| `tests/test_task_centered_shell.py` | Navigation and shared-component contracts |
| `tests/test_clean_workspace_ui.py` | Clean disclosure, action bar, and responsive state |
| `tests/test_review_model.py` | Finding projection and filtering |
| `tests/test_review_workspace_ui.py` | Review views, fallback, and diff navigation |
| `tests/test_batch_view.py` | Batch projection and updated UI behavior |

## Phase sequence

| Phase | Tasks | Independently testable deliverable |
|---|---|---|
| Shell and Clean | 1–3 | Grouped shell, simplified Clean controls, responsive workspace |
| Review | 4–6 | Unified findings, changes, diagnostics, resilient diff navigation |
| AI and secondary workflows | 7–10 | Tabbed AI, clearer Batch/Census, consistent customization/system pages |
| Qualification | 11 | Full regression, visual/responsive evidence, finalized documentation |

### Task 1: Add grouped navigation and shared workflow primitives

**Files:**
- Create: `app_pages/navigation.py`, `app_pages/workflow_ui.py`, `tests/test_task_centered_shell.py`
- Modify: `app_pages/common.py:156-179,617-805`, `app_pages/design_system.py:186-278`

**Interfaces:**
- Produces: `NAV_GROUPS`, `NAV`, `SECTION_NUMBERS`, `group_for(active: str) -> str`, `workflow_section(title, description="")`, `summary_counts(items)`, `overflow_menu(actions)`, `workflow_state(title, body, kind="info")`, and `empty_state(title, body, icon="info")`.
- Consumes: existing `common.shell`, `_status_strip`, `status_tag`, and current route paths.

- [ ] **Step 1: Write failing navigation and component tests**

```python
# tests/test_task_centered_shell.py
from nicegui import ui
from app_pages import navigation
from app_pages.workflow_ui import empty_state, summary_counts, workflow_state


def test_navigation_groups_cover_every_existing_page_once():
    flattened = [item for _name, items in navigation.NAV_GROUPS for item in items]
    assert [item[0] for item in flattened] == [
        "/", "/batch", "/census", "/rules", "/pipeline", "/scripts",
        "/stats", "/settings", "/doctor",
    ]
    assert navigation.group_for("pipeline") == "Customize"
    assert navigation.group_for("clean") == "Work"
    assert navigation.group_for("missing") == ""


def test_summary_counts_uses_stable_labels_and_values():
    with ui.row() as row:
        summary_counts([("Processed", 4), ("Needs review", 2), ("Errors", 1)])
    text = " ".join(getattr(el, "text", "") for el in row.descendants())
    assert "Processed" in text and "4" in text
    assert "Needs review" in text and "2" in text


def test_empty_state_has_explicit_status_role():
    with ui.column() as col:
        empty_state("No beds", "Clean a bed-tagged chart first.", icon="bed")
    region = col.default_slot.children[0]
    assert region.props.get("role") == "status"
    assert region.props.get("aria-label") == "No beds"


def test_error_state_uses_alert_without_changing_release_semantics():
    with ui.column() as col:
        workflow_state("Export failed", "Try the download again.", kind="error")
    region = col.default_slot.children[0]
    assert region.props.get("role") == "alert"
```

- [ ] **Step 2: Run the tests and confirm the missing-module failure**

Run: `python -m pytest tests/test_task_centered_shell.py -q`
Expected: FAIL during collection because `app_pages.navigation` and `app_pages.workflow_ui` do not exist.

- [ ] **Step 3: Implement the pure navigation model and shared components**

```python
# app_pages/navigation.py
NAV_GROUPS = (
    ("Work", (
        ("/", "cleaning_services", "Clean"),
        ("/batch", "layers", "Batch"),
        ("/census", "bed", "Census"),
    )),
    ("Customize", (
        ("/rules", "highlight", "My text rules"),
        ("/pipeline", "tune", "Pipeline & Rules"),
        ("/scripts", "code", "Custom Scripts"),
    )),
    ("System", (
        ("/stats", "insights", "Statistics"),
        ("/settings", "settings", "Settings"),
        ("/doctor", "medical_services", "Doctor"),
    )),
)
NAV = tuple(item for _name, items in NAV_GROUPS for item in items)
SECTION_NUMBERS = {
    "clean": ("01", "Clean"), "batch": ("02", "Batch"),
    "census": ("03", "Census"), "rules": ("04", "My text rules"),
    "pipeline": ("05", "Pipeline & Rules"), "stats": ("06", "Statistics"),
    "scripts": ("07", "Custom Scripts"), "settings": ("08", "Settings"),
    "doctor": ("09", "Doctor"),
}


def group_for(active: str) -> str:
    slug = (active or "clean").strip("/") or "clean"
    path = "/" if slug == "clean" else f"/{slug}"
    for name, items in NAV_GROUPS:
        if any(item_path == path for item_path, _icon, _label in items):
            return name
    return ""
```

```python
# app_pages/workflow_ui.py
from contextlib import contextmanager
from nicegui import ui


@contextmanager
def workflow_section(title: str, description: str = ""):
    with ui.column().classes("w-full gap-2 cc-workflow-section") as holder:
        with ui.column().classes("gap-0"):
            ui.label(title).classes("cc-section")
            if description:
                ui.label(description).classes("cc-muted")
        yield holder


def summary_counts(items: list[tuple[str, object]]) -> None:
    with ui.row().classes("w-full gap-2 flex-wrap cc-summary-counts"):
        for label, value in items:
            with ui.element("div").classes("cc-summary-count"):
                ui.label(str(value)).classes("cc-summary-count-value")
                ui.label(label).classes("cc-summary-count-label")


def overflow_menu(actions, *, marker: str, label: str = "More actions"):
    with ui.button(icon="more_horiz").props(
            f"flat round aria-label='{label}'").mark(marker):
        with ui.menu():
            for action in actions:
                text, icon, callback, action_marker = action
                ui.menu_item(text, on_click=callback).props(f"icon={icon}").mark(action_marker)


def workflow_state(title: str, body: str, kind: str = "info") -> None:
    with ui.column().classes("w-full items-center gap-2 py-12 cc-workflow-state") as region:
        role = "alert" if kind == "error" else "status"
        region.props(f"role={role} aria-label='{title}'")
        if kind == "loading":
            ui.spinner("dots", size="md")
        else:
            icon = {"success": "check_circle", "error": "error", "empty": "info"}.get(kind, "info")
            ui.icon(icon).classes("text-2xl")
        ui.label(title).classes("cc-section")
        ui.label(body).classes("cc-page-sub text-center")


def empty_state(title: str, body: str, icon: str = "info") -> None:
    # Keep the custom icon for existing callers while sharing the same state layout.
    with ui.column().classes("w-full items-center gap-2 py-12 cc-workflow-state cc-empty-state") as region:
        region.props(f"role=status aria-label='{title}'")
        ui.icon(icon).classes("text-2xl")
        ui.label(title).classes("cc-section")
        ui.label(body).classes("cc-page-sub text-center")
```

Replace the literal `NAV` and `SECTION_NUMBERS` in `common.py` with `from app_pages.navigation import NAV, NAV_GROUPS, SECTION_NUMBERS`. In `shell`, render the three Work links directly, render Customize and System as labeled menus with `aria-current="page"` on the active menu item, and render all nine drawer buttons under group labels. Keep `NAV` flattened so `palette_commands` needs no behavioral change.

In `_status_strip`, retain the rules and AI pills; move watcher and encryption pills into a `Local & private` popover. Do not put release information in this strip. Add CSS for `.cc-summary-count`, `.cc-workflow-section`, `.cc-empty-state`, and active group menus using existing `--nd-*` tokens.

- [ ] **Step 4: Run shell, palette, and accessibility regressions**

Run: `python -m pytest tests/test_task_centered_shell.py tests/test_a11y.py tests/test_phase6_ui.py::test_command_palette_runs_a_command tests/test_phase6_ui.py::test_shortcut_matching -q`
Expected: PASS. The active page still has exactly one visible `aria-current="page"`, and palette navigation still lists all nine pages.

- [ ] **Step 5: Commit the shell increment**

```bash
git add app_pages/navigation.py app_pages/workflow_ui.py app_pages/common.py app_pages/design_system.py tests/test_task_centered_shell.py tests/test_a11y.py
git commit -m "feat(ui): group navigation around user tasks"
```

### Task 2: Simplify Clean input controls and consolidate result actions

**Files:**
- Modify: `app_pages/clean.py:680-768,2896-3155`, `app_pages/design_system.py`
- Modify: `tests/test_phase6_ui.py`, `tests/test_clean_review_ui.py`, `tests/test_ai_ui_lifecycle.py`
- Create: `tests/test_clean_workspace_ui.py`

**Interfaces:**
- Consumes: `overflow_menu` and `workflow_section` from Task 1; existing `copy_as`, `guarded_copy`, `release_for_copy`, `show_tab`, and selection callbacks.
- Produces: markers `clean-task-flow`, `input-overflow`, `advanced-input-tools`, `selection-overflow`, `result-actions`, `review-primary`, and `destination-summary`; existing markers remain attached to equivalent actions.

- [ ] **Step 1: Write failing Clean-workspace tests**

```python
# tests/test_clean_workspace_ui.py
import pytest
from nicegui import ui
from nicegui.testing import User
import app as cc_app
from chartcleaner.appstate import CLEAN_STATE
from tests.test_phase8_ui import _route, page  # noqa: F401

pytestmark = pytest.mark.nicegui_main_file("")


async def test_advanced_input_controls_are_disclosed(user: User, page):
    CLEAN_STATE.update(input="Plan: recheck\n", mode="clean", result=None,
                       result_text="", audit=None, summary=None, qa=[])
    await _route(user, "/workspace-input", cc_app.clean_page)
    await user.should_see(marker="clean-task-flow")
    await user.should_see(marker="run-clean")
    await user.should_see(marker="clean-mode")
    await user.should_see(marker="clean-destination")
    await user.should_not_see(marker="selection-mode")
    user.find(marker="advanced-input-tools").click()
    await user.should_see(marker="selection-mode")
    user.find(marker="input-overflow").click()
    await user.should_see("Sample")
    await user.should_see("Clear")


async def test_result_has_one_release_aware_primary_action(user: User, page):
    CLEAN_STATE.update(input="Plan: recheck\n", mode="clean", result=None,
                       result_text="", audit=None, summary=None, qa=[])
    await _route(user, "/workspace-output", cc_app.clean_page)
    user.find(marker="run-clean").click()
    await user.should_see(marker="result-actions", retries=200)
    await user.should_see(marker="copy-as")
    await user.should_see(marker="destination-summary")
    assert CLEAN_STATE["result"] is not None
```

Update existing tests that click Sample, Clear, Save, or hidden selection actions to open `input-overflow`, `result-overflow`, or `selection-overflow` first. Do not change their cleaning, copy-guard, or AI assertions.

- [ ] **Step 2: Run focused tests and verify the new controls are absent**

Run: `python -m pytest tests/test_clean_workspace_ui.py -q`
Expected: FAIL because the new disclosure and action markers do not exist.

- [ ] **Step 3: Recompose the existing controls without changing callbacks**

Above the workspace, add a compact noninteractive indicator marked `clean-task-flow` with exactly **1 Input → 2 Review → 3 Export**. Derive its visual current step from existing result/release state; do not add routes, clickable wizard steps, or hide either wide-screen pane.

In the input toolbar, keep Clean and Paste visible. Use Task 1's `overflow_menu` with `marker="input-overflow"` for Sample (`load_sample`), Clear (`clear_all`), and Auto-clean. Because Auto-clean is a switch rather than a menu callback, place that existing switch in a menu created directly with `ui.menu`; retain its preference callback.

Keep the existing mode toggle, preset select, destination select, and mode note in the run-configuration row. Put Selection settings and the undo-history button in `ui.expansion("Advanced input tools", icon="tune").mark("advanced-input-tools")`. Keep the immediate Undo button beside the chart only after an undo step exists.

In the contextual selection bar, leave Remove, Line, Replace…, Abbreviate…, and Rule… visible. Move `sel_keep`, `sel_collect`, `sel_more`, whole-word/case controls, and Close into a menu marked `selection-overflow`. Keep all context-menu entries and shortcuts unchanged.

Consolidate the existing release pill, character/token reduction, possible-identifier count, elapsed time, and detected note type into one result header. Keep `release-status` independent of later Review rendering and do not duplicate the persistent action bar's Copy/Review action in that header.

In `render_output`, wrap the copy/save/edit/more controls in `ui.row().mark("result-actions")`. Add a destination label using the existing detected note dictionary:

```python
destination = (CLEAN_STATE.get("note") or {}).get("destination")
ui.label(destination or "No destination override").classes("cc-muted").mark("destination-summary")
```

When `release_for_copy(result).status != "ready"`, add `ui.button("Review findings", icon="rule", on_click=lambda: show_tab(1)).mark("review-primary")` before Copy and render Copy outlined rather than unelevated. Keep `copy-as` and every `copy-*`, `save-*`, `edit-result`, `restore-reply`, `known-good`, and `daily-note` marker. Move Save into the More menu marked `result-overflow`; all download actions still call `guarded_copy` exactly as before.

- [ ] **Step 4: Run Clean, copy-guard, and AI lifecycle tests**

Run: `python -m pytest tests/test_clean_workspace_ui.py tests/test_phase6_ui.py tests/test_clean_review_ui.py tests/test_validation_review_ui.py tests/test_ai_ui_lifecycle.py -q`
Expected: PASS. Blocked copy remains blocked, acknowledgment still invalidates after edit, and stale AI responses remain discarded.

- [ ] **Step 5: Commit the Clean-workspace increment**

```bash
git add app_pages/clean.py app_pages/design_system.py tests/test_clean_workspace_ui.py tests/test_phase6_ui.py tests/test_clean_review_ui.py tests/test_validation_review_ui.py tests/test_ai_ui_lifecycle.py
git commit -m "feat(ui): simplify clean input and result actions"
```

### Task 3: Add the responsive and resizable Clean workspace

**Files:**
- Create: `app_pages/workspace_layout.py`
- Modify: `app_pages/clean.py:79-187,2958-3155`, `app_pages/design_system.py`
- Modify: `tests/test_clean_workspace_ui.py`, `scripts/overflow_check.py`, `scripts/screenshot_ui.py`

**Interfaces:**
- Produces: `set_mobile_view(layout, view: str) -> None`, `bind_split_handle(layout, handle) -> None`, and markers `mobile-workspace-switch`, `clean-split-handle`, `input-pane`, `result-pane`.
- Consumes: the existing `layout`, input column, `results_col`, and successful `do_clean_core` completion.

- [ ] **Step 1: Add failing layout-state and accessibility tests**

```python
from app_pages.workspace_layout import set_mobile_view


def test_mobile_view_switches_only_presentation_classes():
    with ui.element("div") as layout:
        pass
    set_mobile_view(layout, "result")
    classes = " ".join(layout._classes)
    assert "cc-mobile-show-result" in classes
    assert "cc-mobile-show-input" not in classes
    set_mobile_view(layout, "input")
    classes = " ".join(layout._classes)
    assert "cc-mobile-show-input" in classes


async def test_clean_has_mobile_switch_and_split_separator(user: User, page):
    CLEAN_STATE.update(input="Plan: recheck\n", mode="clean", result=None,
                       result_text="", audit=None, summary=None, qa=[])
    await _route(user, "/workspace-responsive", cc_app.clean_page)
    handle = next(iter(user.find(marker="clean-split-handle").elements))
    assert handle.props.get("role") == "separator"
    assert handle.props.get("aria-orientation") == "vertical"
    assert handle.props.get("tabindex") == "0"
    switch = next(iter(user.find(marker="mobile-workspace-switch").elements))
    with user.client:
        switch.set_value("result")
    layout = next(iter(user.find(marker="clean-workspace").elements))
    assert "cc-mobile-show-result" in layout._classes
```

- [ ] **Step 2: Run tests and confirm the missing layout behavior**

Run: `python -m pytest tests/test_clean_workspace_ui.py -q`
Expected: FAIL because `workspace_layout` and the responsive controls do not exist.

- [ ] **Step 3: Implement mobile state, split dragging, and CSS**

```python
# app_pages/workspace_layout.py
from nicegui import ui


def set_mobile_view(layout, view: str) -> None:
    selected = "result" if view == "result" else "input"
    layout.classes(remove="cc-mobile-show-input cc-mobile-show-result",
                   add=f"cc-mobile-show-{selected}")


def bind_split_handle(layout, handle) -> None:
    handle.props("role=separator aria-orientation=vertical tabindex=0 "
                 "aria-label='Resize input and result panes' aria-valuemin=25 aria-valuemax=75 "
                 "aria-valuenow=50")
    ui.run_javascript("""
    (() => {
      const root = document.querySelector('.cc-clean-workspace');
      if (!root) return;
      const grip = root.querySelector('.cc-split-handle');
      if (!grip) return;
      let pct = 50;
      const apply = value => {
        pct = Math.max(25, Math.min(75, value));
        root.style.gridTemplateColumns =
          `minmax(0,calc(${pct}% - 4px)) 8px minmax(0,calc(${100-pct}% - 4px))`;
        grip.setAttribute('aria-valuenow', String(Math.round(pct)));
      };
      grip.addEventListener('pointerdown', event => {
        if (window.innerWidth < 1200) return;
        grip.setPointerCapture(event.pointerId);
        const move = e => {
          const box = root.getBoundingClientRect();
          apply((e.clientX - box.left) / box.width * 100);
        };
        const up = () => {
          grip.removeEventListener('pointermove', move);
          grip.removeEventListener('pointerup', up);
        };
        grip.addEventListener('pointermove', move);
        grip.addEventListener('pointerup', up);
      });
      grip.addEventListener('keydown', event => {
        if (!['ArrowLeft', 'ArrowRight', 'Home'].includes(event.key)) return;
        event.preventDefault();
        apply(event.key === 'Home' ? 50 : pct + (event.key === 'ArrowLeft' ? -5 : 5));
      });
    })();
    """)
```

Give `layout` marker `clean-workspace` and classes `cc-clean-workspace cc-mobile-show-input`; mark the input and result columns `input-pane` and `result-pane`. Insert the 8 px handle between them and mark it `clean-split-handle`. Add a mobile `ui.toggle({"input": "Input", "result": "Result"}, value="input")` marked `mobile-workspace-switch`; its callback calls `set_mobile_view(layout, e.value)`. After `render_results()` succeeds in `do_clean_core`, set the switch to `result`; below 768 px this selects the Result view, and from 768 through 1199 px a small viewport-aware `scrollIntoView` brings the stacked result into view without clearing or replacing input. `on_mode_change` and `clear_all` set the switch to `input`. Do not call cleaning code from a resize or view switch.

Add these exact breakpoints:

```css
.cc-mobile-workspace-switch{display:none;}
.cc-clean-workspace{display:grid;width:100%;align-items:start;}
.cc-split-handle{display:none;}
@media (min-width:1200px){
  .cc-clean-workspace{grid-template-columns:minmax(0,calc(50% - 4px)) 8px minmax(0,calc(50% - 4px));}
  .cc-split-handle{display:block;min-height:420px;cursor:col-resize;}
  .cc-split-handle:focus-visible{outline:2px solid var(--nd-text-display);}
}
@media (min-width:768px) and (max-width:1199px){
  .cc-clean-workspace{grid-template-columns:minmax(0,1fr);row-gap:var(--nd-space-lg);}
}
@media (max-width:767px){
  .cc-mobile-workspace-switch{display:flex;}
  .cc-mobile-show-input .cc-result-pane,.cc-mobile-show-result .cc-input-pane{display:none !important;}
  .cc-clean-workspace{grid-template-columns:minmax(0,1fr);}
}
```

Update the manual scripts to include `/census` and widths `360, 768, 1199, 1200, 1440`.

- [ ] **Step 4: Run layout tests and manually verify actual breakpoints**

Run: `python -m pytest tests/test_clean_workspace_ui.py tests/test_a11y.py -q`
Expected: PASS.

Manual: in a first terminal run `python app.py --port 8899 --no-browser`; in a second terminal run `python scripts/overflow_check.py 360 768 1199 1200 1440` and `python scripts/screenshot_ui.py clean census`. Verify drag and ArrowLeft/ArrowRight/Home on the separator, automatic Result selection after clean, both themes, and no document-level horizontal overflow. Stop the temporary app with Ctrl+C in the first terminal and confirm its process exited before proceeding; do not leave a background server running.

- [ ] **Step 5: Commit the responsive increment**

```bash
git add app_pages/workspace_layout.py app_pages/clean.py app_pages/design_system.py tests/test_clean_workspace_ui.py scripts/overflow_check.py scripts/screenshot_ui.py
git commit -m "feat(ui): add responsive clean workspace layout"
```

### Task 4: Build a presentation-only Review finding model

**Files:**
- Create: `app_pages/review_model.py`, `tests/test_review_model.py`

**Interfaces:**
- Consumes: `chartcleaner.review_state.review_findings`, existing result/validation/audit objects, and `support_text`.
- Produces: frozen `FindingRow`, `build_finding_rows(result, audit, *, source: str, output: str) -> list[FindingRow]`, `filter_findings(rows, mode: str) -> list[FindingRow]`, and `default_review_view(rows) -> str`.
- Does not produce: release status, acknowledgment, clinical severity inference, or persisted data.

- [ ] **Step 1: Write failing model tests**

```python
from types import SimpleNamespace as NS
from app_pages.review_model import build_finding_rows, default_review_view, filter_findings


def _result(validation=None, fact_check=None):
    return NS(validation=validation, fact_check=fact_check, stages=[], text="output")


def test_blocking_findings_sort_first_and_duplicates_keep_occurrences():
    report = NS(losses=[
        NS(category="unexpected", display="K 6.1", stage_label="Rules", lines=["K 6.1"]),
        NS(category="unexpected", display="K 6.1", stage_label="Rules", lines=["K 6.1 again"]),
    ], meaning=[], introduced=[], implausible=[], implausible_in_source=set())
    rows = build_finding_rows(_result(fact_check=report), None, source="K 6.1", output="")
    assert rows[0].severity == "blocked"
    assert rows[0].count == 2
    assert len(rows[0].occurrences) == 2
    assert default_review_view(rows) == "findings"
    assert filter_findings(rows, "blocking") == rows


def test_validation_coverage_and_retained_edits_are_review_not_release_logic():
    issue = NS(blocking=False, code="scope_changed", stage_id="rules", rule_key="r1",
               message="Edit exceeds its declared scope", source_spans=[])
    proposal = NS(decision="retain_original", stage_id="rules", rule_key="r1",
                  mapping="exact", source_spans=(), before="Plan: aspirin", after="")
    coverage = NS(status="partial", reasons=())
    validation = NS(issues=[issue], coverage=coverage)
    result = _result(validation=validation)
    result.ledger = NS(proposals=[proposal])
    rows = build_finding_rows(result, None, source="Plan: aspirin", output="")
    assert all(row.severity == "review" for row in rows)
    assert {row.kind for row in rows} >= {"scope_changed", "edits_retained", "coverage_partial"}
    assert filter_findings(rows, "info") == []


def test_no_actionable_findings_defaults_to_changes():
    assert default_review_view([]) == "changes"
    assert default_review_view(filter_findings([], "all")) == "changes"


def test_same_validation_code_from_different_rules_never_merges():
    issues = [
        NS(blocking=False, code="scope_changed", stage_id="rules", rule_key=key,
           message="Edit exceeds its declared scope", source_spans=[])
        for key in ("rule-a", "rule-b")
    ]
    validation = NS(issues=issues, coverage=NS(status="complete", reasons=()))
    rows = build_finding_rows(_result(validation=validation), None,
                              source="Plan: aspirin", output="")
    scoped = [row for row in rows if row.kind == "scope_changed"]
    assert len(scoped) == 2
    assert {row.rule_key for row in scoped} == {"rule-a", "rule-b"}
    assert all(row.count == 1 for row in scoped)
```

- [ ] **Step 2: Run tests and confirm the model is missing**

Run: `python -m pytest tests/test_review_model.py -q`
Expected: FAIL during collection because `app_pages.review_model` does not exist.

- [ ] **Step 3: Implement finding projection and grouping**

```python
# app_pages/review_model.py
from dataclasses import dataclass
from chartcleaner.review_state import review_findings, support_text

_SEVERITY = {"blocked": 0, "review": 1, "info": 2}


@dataclass(frozen=True)
class FindingRow:
    kind: str
    severity: str
    title: str
    detail: str
    stage_id: str
    rule_key: str | None
    occurrences: tuple[dict, ...]

    @property
    def count(self) -> int:
        return len(self.occurrences)


def _group(items: list[dict]) -> list[FindingRow]:
    grouped = {}
    for item in items:
        key = (item["kind"], item["severity"], item["title"], item["detail"],
               item.get("stage_id", ""), item.get("rule_key"))
        grouped.setdefault(key, []).append(item)
    rows = [FindingRow(*key, tuple(values)) for key, values in grouped.items()]
    return sorted(rows, key=lambda row: (_SEVERITY[row.severity], row.title, row.detail,
                                         row.stage_id, row.rule_key or ""))


def build_finding_rows(result, audit, *, source: str, output: str) -> list[FindingRow]:
    items = []
    for finding in review_findings(result.fact_check, audit, source=source, output=output):
        items.append({**finding, "kind": "clinical_or_identifier",
                      "stage_id": "", "rule_key": None})
    validation = getattr(result, "validation", None)
    if validation is None:
        return _group(items)
    labels = {stage.id: stage.label for stage in result.stages}
    for issue in validation.issues:
        spans = tuple(span for span in issue.source_spans if span.end > span.start)
        before = source[spans[0].start:spans[0].end] if spans else ""
        items.append(dict(kind=issue.code,
                          severity="blocked" if issue.blocking else "review",
                          title=issue.message,
                          detail=labels.get(issue.stage_id, issue.stage_id),
                          stage_id=issue.stage_id, rule_key=issue.rule_key,
                          source_spans=spans, mapping="exact" if spans else "unavailable",
                          before=before, after=""))
    proposals = getattr(getattr(result, "ledger", None), "proposals", ())
    for proposal in proposals:
        if proposal.decision != "retain_original":
            continue
        items.append(dict(kind="edits_retained", severity="review",
                          title="Edit kept as original",
                          detail=labels.get(proposal.stage_id, proposal.stage_id),
                          stage_id=proposal.stage_id, rule_key=proposal.rule_key,
                          source_spans=proposal.source_spans, mapping=proposal.mapping,
                          before=proposal.before, after=""))
    coverage = validation.coverage
    if coverage.status != "complete":
        items.append(dict(kind=f"coverage_{coverage.status}", severity="review",
                          title=f"Validation coverage: {coverage.status}",
                          detail=support_text(result), stage_id="", rule_key=None,
                          source_spans=(), mapping="unavailable", before="", after=""))
    return _group(items)


def filter_findings(rows: list[FindingRow], mode: str) -> list[FindingRow]:
    if mode == "blocking":
        return [row for row in rows if row.severity == "blocked"]
    if mode == "info":
        return [row for row in rows if row.severity == "info"]
    if mode == "review":
        return [row for row in rows if row.severity in ("blocked", "review")]
    return list(rows)


def default_review_view(rows: list[FindingRow]) -> str:
    return "findings" if any(row.severity != "info" for row in rows) else "changes"
```

Grouping identity includes kind, severity, title, detail, stage ID, and rule identity; two rules with the same validation code never merge. Occurrences retain every original before/after pair, source span, and mapping quality already available. No chart content enters logs or persistence.

- [ ] **Step 4: Run model and existing release-policy tests**

Run: `python -m pytest tests/test_review_model.py tests/test_review_state.py tests/test_validation_release.py -q`
Expected: PASS. Release-policy tests are unchanged because this task adds no release code.

- [ ] **Step 5: Commit the Review model**

```bash
git add app_pages/review_model.py tests/test_review_model.py
git commit -m "feat(ui): project review findings for presentation"
```

### Task 5: Integrate Findings, Changes, Diagnostics, and a safe fallback

**Files:**
- Create: `app_pages/review_workspace.py`, `tests/test_review_workspace_ui.py`
- Modify: `app_pages/clean.py:205-250,335-410,869-1048,2444-2580`
- Modify: `tests/test_validation_review_ui.py`, `tests/test_phase6_ui.py`

**Interfaces:**
- Consumes: Task 4 model; existing `release_for_copy`, `locate_finding`, `review_diff_html`, `inspect_change`, `render_validation`, `render_fact_check`, `render_audit`, `render_removed`, and abbreviation/stage renderers.
- Produces: `ReviewSections` and `render_review_workspace(...)`; markers `review-findings-tab`, `review-changes-tab`, `review-diagnostics-tab`, `finding-filter`, `finding-row`, `blocking-banner`, and `review-fallback`.

- [ ] **Step 1: Write failing Review-workspace tests**

```python
import asyncio
import pytest
from nicegui import ui
from nicegui.testing import User
import app as cc_app
from app_pages import review_workspace
from chartcleaner.appstate import CLEAN_STATE
from tests.test_phase8_ui import _route, page  # noqa: F401
from tests.test_validation_review_ui import BLOCK_CFG, BLOCK_CHART, _write_cfg

pytestmark = pytest.mark.nicegui_main_file("")


async def _clean(user, route):
    CLEAN_STATE.update(input=BLOCK_CHART, mode="clean", result=None, result_text="",
                       audit=None, summary=None, qa=[], release_ack=None)
    await _route(user, route, cc_app.clean_page)
    user.find(marker="run-clean").click()
    for _ in range(300):
        if CLEAN_STATE.get("result") is not None:
            return
        await asyncio.sleep(0.02)
    raise AssertionError("clean did not finish")


async def test_blocking_banner_survives_view_and_filter_changes(user: User, page):
    _write_cfg(**BLOCK_CFG)
    await _clean(user, "/workspace-blocked")
    await user.should_see(marker="blocking-banner")
    user.find(marker="review-changes-tab").click()
    await user.should_see(marker="blocking-banner")
    user.find(marker="review-diagnostics-tab").click()
    await user.should_see(marker="blocking-banner")
    await user.should_see(marker="validation-panel")


async def test_review_failure_keeps_release_and_plain_diff(user: User, page, monkeypatch):
    _write_cfg(**BLOCK_CFG)
    monkeypatch.setattr(review_workspace, "render_review_workspace",
                        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("render failed")))
    await _clean(user, "/workspace-fallback")
    await user.should_see(marker="review-fallback")
    await user.should_see(marker="release-status")
    status = next(iter(user.find(marker="release-status").elements)).text
    assert "ready" not in status.lower()
```

Add a rendering test with only informational rows: the Findings filter must be `all`, the rows must remain visible, and switching to Needs review must show `No findings match this filter.` rather than a blank list. Add an actionable-row counterpart asserting the initial filter is `review`.

Update the existing validation-panel test to open `review-diagnostics-tab` before expecting `validation-panel`; retain all coverage and no-chart-excerpt assertions.

- [ ] **Step 2: Run the new tests and confirm missing integration**

Run: `python -m pytest tests/test_review_workspace_ui.py -q`
Expected: FAIL because the new module and markers do not exist.

- [ ] **Step 3: Implement the Review workspace and integration callbacks**

```python
# app_pages/review_workspace.py
from collections.abc import Callable
from dataclasses import dataclass
from nicegui import ui
from app_pages.review_model import build_finding_rows, default_review_view, filter_findings


@dataclass(frozen=True)
class ReviewSections:
    diff: Callable[[], None]
    removed: Callable[[], None]
    abbreviations: Callable[[], None]
    diagnostics: Callable[[], None]


def render_review_workspace(*, result, audit, source, output, decision,
                            sections: ReviewSections, locate) -> None:
    rows = build_finding_rows(result, audit, source=source, output=output)
    if decision.status == "blocked":
        with ui.row().classes("w-full items-center gap-2 cc-tint cc-tint--danger p-3") \
                .mark("blocking-banner"):
            ui.icon("report")
            ui.label("Release blocked — resolve the blocking findings before copying.")

    finding_count = sum(row.count for row in rows)
    with ui.tabs().props("dense align=left") as tabs:
        findings_tab = ui.tab(f"Findings ({finding_count})", icon="rule").mark("review-findings-tab")
        changes_tab = ui.tab("Changes", icon="difference").mark("review-changes-tab")
        diagnostics_tab = ui.tab("Diagnostics", icon="account_tree").mark("review-diagnostics-tab")
    initial = findings_tab if default_review_view(rows) == "findings" else changes_tab
    with ui.tab_panels(tabs, value=initial).props("keep-alive").classes("w-full"):
        with ui.tab_panel(findings_tab):
            initial_filter = "review" if filter_findings(rows, "review") else "all"
            filter_control = ui.toggle(
                {"all": "All", "blocking": "Blocking", "review": "Needs review", "info": "Informational"},
                value=initial_filter).props("dense no-caps").mark("finding-filter")
            listing = ui.column().classes("w-full gap-2")

            def draw_findings() -> None:
                listing.clear()
                visible = filter_findings(rows, filter_control.value)
                with listing:
                    if not visible:
                        message = ("No issues detected by these checks—review before sharing."
                                   if not rows else "No findings match this filter.")
                        ui.label(message).classes("cc-muted")
                    for row in visible:
                        with ui.card().classes("w-full gap-1").mark("finding-row"):
                            ui.badge({"blocked": "Blocked", "review": "Review", "info": "Info"}[row.severity],
                                     color={"blocked": "red", "review": "orange", "info": "grey"}[row.severity])
                            ui.label(row.title).classes("cc-section")
                            ui.label(f"{row.detail} · {row.count} occurrence(s)").classes("cc-muted")
                            for occurrence in row.occurrences:
                                ui.button("Locate", icon="my_location",
                                          on_click=lambda _e, item=occurrence: locate(item)) \
                                    .props("flat dense no-caps").mark("locate-finding")
            filter_control.on_value_change(lambda _e: draw_findings())
            draw_findings()
        with ui.tab_panel(changes_tab):
            with ui.tabs().props("dense align=left") as change_tabs:
                changed = ui.tab("Changed")
                removed = ui.tab("Removed")
                abbreviated = ui.tab("Abbreviations")
            with ui.tab_panels(change_tabs, value=changed).props("keep-alive").classes("w-full"):
                with ui.tab_panel(changed):
                    sections.diff()
                with ui.tab_panel(removed):
                    sections.removed()
                with ui.tab_panel(abbreviated):
                    sections.abbreviations()
        with ui.tab_panel(diagnostics_tab):
            sections.diagnostics()
```

In `clean.py`, construct `ReviewSections` with callbacks that render the existing diff, removed groups, abbreviation changes, and a Diagnostics callback containing `render_validation`, `render_fact_check`, `render_audit`, and the existing stage table. Pass `decision=release_for_copy(result)`. Do not call `render_findings` inside Diagnostics; the new queue replaces that duplicate list.

Wrap only the new workspace invocation in `try/except Exception`: log the exception through the existing redacted logger, show `ui.label("Detailed findings are unavailable. Release status is unchanged; use the plain diff below.").mark("review-fallback")`, and render the existing side-by-side diff through `common.review_diff_html`. Keep the independently rendered result header and `release-status` outside this `try` block.

- [ ] **Step 4: Run Review and copy-safety regressions**

Run: `python -m pytest tests/test_review_model.py tests/test_review_workspace_ui.py tests/test_validation_review_ui.py tests/test_clean_review_ui.py tests/test_phase6_ui.py::test_review_click_opens_rule_and_never_remove_keeps_text tests/test_phase6_ui.py::test_review_click_can_delete_the_rule -q`
Expected: PASS. Existing Locate dialogs, Never remove, Pause/Delete, and copy guards retain their behavior.

- [ ] **Step 5: Commit the unified Review increment**

```bash
git add app_pages/review_workspace.py app_pages/clean.py tests/test_review_workspace_ui.py tests/test_validation_review_ui.py tests/test_phase6_ui.py
git commit -m "feat(ui): unify clinical review workspaces"
```

### Task 6: Add diff context collapsing and Previous/Next navigation

**Files:**
- Modify: `app_pages/common.py:405-462`, `app_pages/review_workspace.py`, `app_pages/design_system.py:661-682`
- Modify: `tests/test_review_workspace_ui.py`, `tests/test_a11y.py`

**Interfaces:**
- Consumes: the existing `groups`, `flagged_lines`, cached `review_html`, `REVIEW_CLICK_JS`, and `REVIEW_KEY_JS`.
- Produces: `collapse_context_ranges(line_count, changed_lines, context=3) -> list[tuple[int, int]]` and markers `change-previous`, `change-next`, `change-position`, `diff-context`.

- [ ] **Step 1: Write failing pure range and UI tests**

```python
from app_pages.review_workspace import collapse_context_ranges


def test_context_ranges_cover_only_long_unchanged_runs():
    # Changed diff rows 5 and 12 keep rows 2–15 visible; only rows 16–20 collapse.
    ranges = collapse_context_ranges(20, {5, 12}, context=3)
    assert ranges == [(16, 20)]
    assert all(not (start <= n <= end) for start, end in ranges for n in (5, 12))
    assert collapse_context_ranges(6, {3}, context=3) == []


async def test_change_navigation_has_position_and_controls(user: User, page):
    _write_cfg(**BLOCK_CFG)
    await _clean(user, "/workspace-diff-nav")
    user.find(marker="review-changes-tab").click()
    await user.should_see(marker="change-previous")
    await user.should_see(marker="change-next")
    position = next(iter(user.find(marker="change-position").elements)).text
    assert "/" in position
```

Add an HTML test asserting equal rows carry `data-diff-line`, changed rows carry `cc-diff-change`, captions/column headers remain, and generated context controls are real `<button type="button">` elements with accessible labels.

- [ ] **Step 2: Run tests and confirm missing navigation/context behavior**

Run: `python -m pytest tests/test_review_workspace_ui.py tests/test_a11y.py -q`
Expected: FAIL because `collapse_context_ranges`, row addresses, and navigation controls do not exist.

- [ ] **Step 3: Implement deterministic context ranges and browser navigation**

```python
def collapse_context_ranges(line_count: int, changed_lines: set[int], context: int = 3) -> list[tuple[int, int]]:
    visible = set(changed_lines)
    for line in changed_lines:
        visible.update(range(max(1, line - context), min(line_count, line + context) + 1))
    ranges = []
    start = None
    for line in range(1, line_count + 2):
        if line not in visible and line <= line_count:
            start = line if start is None else start
            continue
        if start is not None:
            if line - start >= 2:
                ranges.append((start, line - 1))
            start = None
    return ranges
```

In `common.review_diff_html`, derive 1-based changed **diff-row** numbers directly from the existing `SequenceMatcher` opcodes while building rows; do not infer them from output-line numbers, because deletions have no output line:

```python
changed_lines: set[int] = set()
flagged_diff_lines: set[int] = set()
for tag, i1, i2, j1, j2 in sm.get_opcodes():
    row_count = (i2 - i1 if tag in ("equal", "delete")
                 else j2 - j1 if tag == "insert" else max(i2 - i1, j2 - j1))
    for offset in range(row_count):
        # Build the same left/right cells as the current renderer for this opcode/offset.
        rows.append("<tr>...</tr>")
        if tag != "equal":
            changed_lines.add(len(rows))
        output_line = j1 + offset + 1 if tag != "delete" and j1 + offset < j2 else None
        if output_line in flagged:
            flagged_diff_lines.add(len(rows))
```

Add `data-diff-line='<1-based diff row>'` to every `<tr>` and `cc-diff-change` to delete/insert/replace rows. Call `collapse_context_ranges(len(rows), changed_lines | flagged_diff_lines)` so audit-flagged output lines also remain visible; never mix output-line numbers directly with diff-row numbers. Keep one captioned table with its existing column headers, using multiple `<tbody>` elements: each collapsed range gets a hidden `<tbody id='diff-context-<start>-<end>'>` containing only its unchanged rows, preceded by a visible one-row `<tbody>` whose `<td colspan='2'>` contains a real `<button type='button' class='cc-diff-context' aria-expanded='false' aria-controls='diff-context-<start>-<end>'>Show N unchanged lines</button>`. The button toggles only that tbody's `hidden` property, its `aria-expanded`, and Show/Hide wording. Do not put `<details>` or nested tables between table rows. Preserve all existing escaping, clickable spans, and table semantics. The uncached path and worker-built `review_html` must call this same renderer so cached and uncached HTML match.

In `ReviewSections.diff`, add Previous and Next buttons and a position label. After the diff mounts, use this script to navigate only real change rows:

```javascript
(() => {
  const rows = Array.from(document.querySelectorAll('.cc-diff-change[data-diff-line]'));
  let index = -1;
  const label = document.querySelector('[data-marker="change-position"]');
  const show = next => {
    if (!rows.length) {
      if (label) label.textContent = '0 / 0';
      return;
    }
    index = (next + rows.length) % rows.length;
    rows[index].scrollIntoView({block: 'center', behavior: 'instant'});
    rows[index].setAttribute('tabindex', '-1');
    rows[index].focus({preventScroll: true});
    if (label) label.textContent = `${index + 1} / ${rows.length}`;
  };
  document.querySelector('[data-marker="change-previous"]')?.addEventListener('click', () => show(index - 1));
  document.querySelector('[data-marker="change-next"]')?.addEventListener('click', () => show(index + 1));
  show(0);
})();
```

Add CSS for context summaries, focused change rows, and expanded details using existing tokens. Do not change source or output strings when collapsing.

- [ ] **Step 4: Run diff, accessibility, and Review tests**

Run: `python -m pytest tests/test_review_workspace_ui.py tests/test_a11y.py tests/test_phase6_ui.py::test_review_diff_marks_removed_and_abbreviated_text tests/test_phase6_ui.py::test_review_click_opens_rule_and_never_remove_keeps_text -q`
Expected: PASS. Clickable spans remain keyboard-operable inside and outside collapsed context.

- [ ] **Step 5: Commit the diff-navigation increment**

```bash
git add app_pages/common.py app_pages/review_workspace.py app_pages/design_system.py tests/test_review_workspace_ui.py tests/test_a11y.py
git commit -m "feat(ui): make long review diffs scannable"
```


### Task 7: Reorganize Local AI into Summary and Ask chart tabs

**Files:**
- Modify: `app_pages/clean.py:1416-1505,2958-2970`, `app_pages/design_system.py`
- Modify: `tests/test_ai_ui_lifecycle.py`
- Create: `tests/test_ai_workspace_ui.py`

**Interfaces:**
- Consumes: existing `summary_refs`, `qa_refs`, `run_summarize`, `run_ask`, `render_summary_output`, `render_qa_log`, `toggle_ai`, and model preferences.
- Produces: markers `ai-assistant-button`, `ai-close`, `ai-summary-tab`, `ai-ask-tab`, and `ai-prompt-settings`; existing AI output, citation, and streaming markers remain unchanged.

- [ ] **Step 1: Write failing AI-workspace tests**

```python
import pytest
from nicegui.testing import User
import app as cc_app
from app_pages import clean as clean_mod
from tests.test_ai_ui_lifecycle import _open_ai_panel, _restore_state

pytestmark = pytest.mark.nicegui_main_file("")


async def test_ai_drawer_uses_summary_and_question_tabs(user: User, monkeypatch, tmp_path):
    before, auto_before, _ = await _open_ai_panel(user, monkeypatch, tmp_path)
    try:
        user.find(marker="ai-assistant-button").click()
        await user.should_see(marker="ai-summary-tab")
        await user.should_see(marker="ai-ask-tab")
        await user.should_see("Summarize")
        user.find(marker="ai-ask-tab").click()
        await user.should_see("Ask about this chart")
        user.find(marker="ai-summary-tab").click()
        await user.should_not_see("Custom prompt (replaces the preset)")
        user.find(marker="ai-prompt-settings").click()
        await user.should_see("Custom prompt (replaces the preset)")
    finally:
        _restore_state(before, auto_before)


async def test_ai_drawer_close_restores_invoker_focus(user: User, monkeypatch, tmp_path):
    scripts = []
    monkeypatch.setattr(clean_mod.ui, "run_javascript",
                        lambda script, *args, **kwargs: scripts.append(script))
    before, auto_before, _ = await _open_ai_panel(user, monkeypatch, tmp_path)
    try:
        user.find(marker="ai-assistant-button").click()
        user.find(marker="ai-close").click()
        assert any("ai-assistant-button" in script and ".focus(" in script
                   for script in scripts)
    finally:
        _restore_state(before, auto_before)
```

- [ ] **Step 2: Run the test and confirm the tabs are absent**

Run: `python -m pytest tests/test_ai_workspace_ui.py -q`
Expected: FAIL because `ai-assistant-button` and the tab markers do not exist.

- [ ] **Step 3: Wrap existing AI controls without changing model lifecycle**

Change the header psychology icon button to a labeled **AI assistant** button marked `ai-assistant-button`; retain the Alt+A shortcut and icon. Keep the drawer model selector and unavailable-model warning in the compact header. Add a drawer Close button marked `ai-close`, calling `toggle_ai(False)`. Register one `ai_drawer.on_value_change` handler that, only on a transition to closed, runs `document.querySelector('[data-marker="ai-assistant-button"]')?.focus()` through `ui.run_javascript`; this covers Close, Escape, outside-click, and Alt+A without focusing anything during initial rendering. Do not change drawer preferences or streaming lifetime.

Replace the Summary and Ask headings/separator with two tabs. The Summary panel contains an expansion marked `ai-prompt-settings` holding the existing preset selector and `custom_box`, followed by the existing Summarize button, spinner, output, marked-unsupported HTML, citations, grounding row, metadata, and Copy summary. The Ask panel contains the existing question input, Ask button, spinner, model explanation, stream, and Q&A log.

Use this structure and move the named existing controls into it unchanged:

```python
with ui.tabs().props("dense align=left") as ai_tabs:
    summary_tab = ui.tab("Summary", icon="psychology").mark("ai-summary-tab")
    ask_tab = ui.tab("Ask chart", icon="forum").mark("ai-ask-tab")
with ui.tab_panels(ai_tabs, value=summary_tab).props("keep-alive").classes("w-full"):
    with ui.tab_panel(summary_tab):
        with ui.expansion("Prompt settings", icon="tune").classes("w-full").mark("ai-prompt-settings"):
            preset_select = ui.select(
                dict(PRESET_LABELS), value=preset_key, label="Write",
                on_change=lambda e: (save_llm_pref("prompt_preset", e.value),
                                     custom_box.set_visibility(e.value == "custom")))
            preset_select.classes("w-full")
            custom_box = ui.textarea(
                "Custom prompt (replaces the preset)", value=str(opts["custom_prompt"]),
                on_change=lambda e: save_llm_pref("custom_prompt", e.value))
            custom_box.props("outlined").classes("w-full cc-mono")
            custom_box.set_visibility(preset_key == "custom")
        summary_refs["button"] = ui.button(
            "Summarize", icon="psychology", on_click=run_summarize)
        summary_refs["button"].props("unelevated color=primary")
```

Do not duplicate `custom_box` or the preset selector: the implementation moves the existing declarations into the expansion and retains their exact `save_llm_pref` callbacks. `render_summary_output()` runs after the Summary output refs exist; `render_qa_log()` runs after the Ask log ref exists. Keep both panels alive so an in-flight stream is not destroyed by a tab switch.

Add narrow-screen CSS making the right drawer `width:min(440px,100vw)` and overlaying rather than widening the page.

- [ ] **Step 4: Run all AI lifecycle and grounding UI tests**

Run: `python -m pytest tests/test_ai_workspace_ui.py tests/test_ai_ui_lifecycle.py tests/test_phase14.py tests/test_phase7_ui.py -q`
Expected: PASS. No model calls occur in tests; streaming, citations, unsupported values, and stale-response protection remain intact.

- [ ] **Step 5: Commit the AI-workspace increment**

```bash
git add app_pages/clean.py app_pages/design_system.py tests/test_ai_workspace_ui.py tests/test_ai_ui_lifecycle.py
git commit -m "feat(ui): clarify local AI summary and chat workflows"
```

### Task 8: Separate Batch queue, options, and summary-first results

**Files:**
- Create: `app_pages/batch_view.py`, `tests/test_batch_view.py`
- Modify: `app_pages/batch.py:15-333`, `app_pages/design_system.py`
- Modify: `tests/test_phase6_ui.py::test_batch_filters_and_retry`

**Interfaces:**
- Consumes: existing `BatchResult` objects and `publish`, `run_now`, `run_list`, `retry_failed`, and download callbacks.
- Produces: `batch_counts(results) -> list[tuple[str, int]]`, `batch_rows(results) -> list[dict]`, and markers `batch-queue`, `batch-run-options`, `batch-summary`, `batch-details-<safe stem>`.

- [ ] **Step 1: Write failing projection tests**

```python
from types import SimpleNamespace as NS
from app_pages.batch_view import batch_counts, batch_rows


def _result(name="one.txt", status="ok", review=False, error=""):
    return NS(name=name, path=name, status=status, needs_review=review,
              chars_before=100, chars_after=60, reduction=40.0, phi_total=2,
              findings=1, facts_status="ok", facts="4 / 4", note_type="progress",
              preset="daily", elapsed_ms=12, summary="Stable", error=error)


def test_counts_and_default_rows_keep_review_and_error_distinct():
    results = [_result(), _result("two.txt", review=True),
               _result("bad.txt", status="error", error="decode failed")]
    assert batch_counts(results) == [("Processed", 3), ("Needs review", 1), ("Errors", 1)]
    rows = batch_rows(results)
    assert list(rows[0]) == ["file", "reduction", "findings", "status", "actions"]
    assert rows[0]["reduction"] == "+40.0%"
    assert rows[2]["status"] == "error"
    assert rows[2]["findings"] == "—"


def test_row_projection_does_not_replace_exception_details():
    row = batch_rows([_result(status="error", error="decode failed")])[0]
    assert row["actions"] == "View details"
```

- [ ] **Step 2: Run tests and confirm the Batch projection is missing**

Run: `python -m pytest tests/test_batch_view.py -q`
Expected: FAIL during collection because `app_pages.batch_view` does not exist.

- [ ] **Step 3: Implement projection and recompose Batch**

```python
# app_pages/batch_view.py
def batch_counts(results) -> list[tuple[str, int]]:
    return [
        ("Processed", len(results)),
        ("Needs review", sum(bool(r.needs_review) for r in results)),
        ("Errors", sum(r.status == "error" for r in results)),
    ]


def batch_rows(results) -> list[dict]:
    rows = []
    for result in results:
        ok = result.status == "ok"
        rows.append({
            "file": result.name,
            "reduction": f"{result.reduction:+.1f}%" if ok else "—",
            "findings": str(result.findings) if ok else "—",
            "status": "review" if result.needs_review and ok else result.status,
            "actions": "View details",
        })
    return rows
```

Wrap upload/folder controls and pending label in `workflow_section("Queue", ...).mark("batch-queue")`. Put delta, note-type, patient-list AI, Clean, Clear, and progress in `workflow_section("Run options", ...).mark("batch-run-options")`. Keep patient-list splitting available in its existing expansion.

In `render_results`, call `summary_counts(batch_counts(results))` inside a container marked `batch-summary`. Keep the All/Needs review/Errors filter and Retry failed. Use the five default columns returned by `batch_rows`; retain `row_key="file"` and pagination.

Below the table, render one `ui.expansion` per visible result, marked `batch-details-<safe stem>`, containing every existing metric: characters, PHI, audit findings, clinical facts, note type/preset, elapsed time, AI one-liner, and full status/error. Add a Q-table `body-cell-actions` slot with keyboard-accessible **View details** and **Download .txt** buttons for each successful row; the download button calls the same existing per-file `download_file` callback and is not duplicated inside the expansion. `table.on("rowClick", ...)` and View details open the matching expansion by safe stem; guard malformed event args by ignoring anything without a row dictionary. This keeps per-file export in its row while preserving all detailed metrics.

Keep Download all and Open exports folder above the detail expansions. No change to `publish`, exports, history, retry, patient splitting, or worker/UI thread boundaries.

- [ ] **Step 4: Run Batch projection and UI regressions**

Run: `python -m pytest tests/test_batch_view.py tests/test_phase6_ui.py::test_batch_filters_and_retry tests/test_batch.py -q`
Expected: PASS. The result counts match filtered data, failed files retain their error text, and all export callbacks still work.

- [ ] **Step 5: Commit the Batch increment**

```bash
git add app_pages/batch_view.py app_pages/batch.py app_pages/design_system.py tests/test_batch_view.py tests/test_phase6_ui.py
git commit -m "feat(ui): clarify batch queue and results"
```

### Task 9: Add Census attention filtering and consistent card disclosure

**Files:**
- Modify: `app_pages/census.py:11-166`, `app_pages/design_system.py`
- Modify: `tests/test_census_ui.py`

**Interfaces:**
- Consumes: existing `report.beds`, each bed's `attention`, safety flags, conflicts, delta, to-dos, and export callbacks.
- Produces: markers `census-filter`, `census-export`, and `census-details-<bed tag>`; existing card/open/copy markers remain.

- [ ] **Step 1: Write failing Census filter and disclosure tests**

```python
async def test_census_attention_filter_preserves_safety_and_open(user: User, page, quiet_config):
    recent_charts.remember(CHART, "test", {}, tag="G20-1")
    await _route(user, "/census-filter", cc_app.census_page)
    await user.should_see(marker="census-bed-G20-1", retries=200)
    switch = next(iter(user.find(marker="census-filter").elements))
    with user.client:
        switch.set_value("attention")
    await user.should_see(marker="census-bed-G20-1")
    user.find(marker="census-export").click()
    await user.should_see(marker="census-copy")
    await user.should_see(marker="census-dl-md")
    user.find(marker="census-open-G20-1").click()
    assert CLEAN_STATE["tag"] == "G20-1"
```

Add a second bed-tagged chart with no attention flags and assert the Needs attention filter hides only that card; the summary continues to report total beds and total attention count. If the synthetic chart has attention flags, use two `SimpleNamespace` beds in a pure `visible_beds` test instead of weakening the assertion.

- [ ] **Step 2: Run tests and confirm filtering/export grouping are absent**

Run: `python -m pytest tests/test_census_ui.py -q`
Expected: FAIL because `census-filter` and `census-export` do not exist.

- [ ] **Step 3: Implement pure filtering and recompose cards**

Add this function to `census.py`:

```python
def visible_beds(beds, mode: str):
    if mode == "attention":
        return [bed for bed in beds if bed.attention]
    return list(beds)
```

Add `state["filter"] = "all"` and a `ui.toggle({"attention": "Needs attention", "all": "All beds"}, value="all").mark("census-filter")`. Its callback updates state and calls `draw(state["report"])` when a report exists. `draw` always computes the headline from all beds and passes `visible_beds(report.beds, state["filter"])` to card rendering. Show a shared `empty_state` when a filter has no matches without changing stored data.

Group Copy census, Copy Markdown, Download .txt, and Download .md into a menu marked `census-export`; keep all existing markers on their menu items. Refresh remains visible.

In `bed_card`, leave bed tag, cleaned time, Open, Copy, allergies, code status, safety flags, conflicts, antibiotics, and device review visible. Put bundle gaps, stale details, to-dos, and delta lines into `ui.expansion("More details", icon="expand_more").mark(f"census-details-{bed.tag}")`; do not collapse a card's active safety flag or contradiction. Keep existing extractor wording unchanged.

- [ ] **Step 4: Run Census and clinical-extractor regressions**

Run: `python -m pytest tests/test_census_ui.py tests/test_conflicts_ui.py tests/test_safety_stale_ui.py -q`
Expected: PASS. Census-to-Clean still transfers the original tagged input, and safety/conflict content is unchanged.

- [ ] **Step 5: Commit the Census increment**

```bash
git add app_pages/census.py app_pages/design_system.py tests/test_census_ui.py
git commit -m "feat(ui): prioritize census beds needing attention"
```

### Task 10: Standardize customization and system pages

**Files:**
- Modify: `app_pages/rules.py:327-381`, `app_pages/pipeline.py`, `app_pages/settings.py:19-26,1025-1062`, `app_pages/stats.py:130-321`, `app_pages/doctor.py:13-62`
- Modify: `app_pages/design_system.py`
- Create: `tests/test_secondary_consistency.py`
- Modify: `tests/test_phase6_ui.py::test_settings_search_filters_cards`

**Interfaces:**
- Consumes: existing Rules renderers, Settings search index/jump chips, Statistics summary/charts, and Doctor checks/fixes.
- Produces: `settings_category(title: str) -> str`, markers `rules-open-pipeline`, `pipeline-open-rules`, `stats-details`, `doctor-failing-checks`, and `doctor-ok-checks`.

- [ ] **Step 1: Write failing consistency tests**

```python
import pytest
from nicegui.testing import User
import app as cc_app
from app_pages.settings import settings_category
from tests.test_phase8_ui import _route, page  # noqa: F401

pytestmark = pytest.mark.nicegui_main_file("")


def test_settings_categories_are_deterministic():
    assert settings_category("Local AI") == "Output & AI"
    assert settings_category("Stored chart data") == "Privacy & data"
    assert settings_category("Folder watcher — auto-clean as you drop files") == "Automation"
    assert settings_category("Unrecognized custom card") == "System"


async def test_rules_link_to_advanced_pipeline(user: User, page):
    await _route(user, "/rules-pipeline-link", cc_app.text_rules_page)
    user.find(marker="rules-open-pipeline").click()
    await user.should_see("Pipeline")


async def test_pipeline_links_back_to_quick_rules(user: User, page):
    await _route(user, "/pipeline-rules-link", cc_app.pipeline_page)
    user.find(marker="pipeline-open-rules").click()
    await user.should_see("My text rules")


async def test_statistics_keep_summary_before_details(user: User, page, monkeypatch):
    from chartcleaner import store
    monkeypatch.setattr(store, "load_runs", lambda: [])
    await _route(user, "/stats-empty-summary", cc_app.stats_page)
    await user.should_see("No runs recorded yet.")
```

Add a Doctor test with injected checks: fail/warn rows are visible under `doctor-failing-checks`; ok rows are under the closed `doctor-ok-checks` expansion and become visible after clicking it. Use the existing `doctor.Check` type and monkeypatch `doctor.run_checks`, not a replacement health model.

- [ ] **Step 2: Run tests and confirm the new grouping is absent**

Run: `python -m pytest tests/test_secondary_consistency.py -q`
Expected: FAIL because the category helper, links, and Doctor groups do not exist.

- [ ] **Step 3: Implement category mapping, links, and summary-first details**

```python
def settings_category(title: str) -> str:
    text = title.casefold()
    if any(word in text for word in ("output", "local ai", "nlp", "converter")):
        return "Output & AI"
    if any(word in text for word in ("chart data", "recent charts", "known-good", "token maps", "retention")):
        return "Privacy & data"
    if any(word in text for word in ("watcher", "clipboard", "integration", "hotkey")):
        return "Automation"
    if any(word in text for word in ("rule", "validation", "abbreviation", "note type")):
        return "Rules & validation"
    return "System"
```

Keep the existing Settings search behavior and card ordering. Build `jump_row` chips in category order Output & AI, Privacy & data, Automation, Rules & validation, System; add a small group label before each non-empty category. Search still filters the original card index and does not create a second settings store.

Add `rules-open-pipeline` beside Full settings backup, navigating to `/pipeline`. Add `pipeline-open-rules` on Pipeline, navigating to `/rules`; label them Advanced Pipeline and My text rules respectively. Do not move rule editors between pages. Recompose both pages with the shared section/disclosure and feedback-state components while preserving their existing search fields, filters, row actions, destructive confirmations, rule-history Undo entry points, previews, and empty-state wording. My text rules remains quick-add-first; Pipeline & Rules remains the advanced editor. Add UI assertions that these existing controls still open and call their original callbacks after the visual recomposition.

On Statistics, keep the metric cards, average-reduction card, and Rule health summary visible. Wrap time-series charts, top-stage charts, PHI charts, stage-noise details, run table, evaluation, and history actions in `ui.expansion("Detailed activity", icon="insights", value=False).mark("stats-details")`. Keep the existing empty state.

On Doctor, compute `attention = [c for c in checks if c.status in ("fail", "warn")]` and `ordinary = [c for c in checks if c not in attention]`. Render attention in `doctor-failing-checks`; render ordinary in a closed expansion marked `doctor-ok-checks`. Preserve every `doctor-<id>` and `doctor-fix-*` marker and the existing `apply_fix` callback.

- [ ] **Step 4: Run secondary-page and settings regressions**

Run: `python -m pytest tests/test_secondary_consistency.py tests/test_phase6_ui.py tests/test_rules_page_ui.py tests/test_update_ui_lifecycle.py tests/test_phase7_ui.py::test_doctor_page -q`
Expected: PASS. Settings search still finds privacy cards, Rules render lazily, Doctor fixes still call the original service, and Statistics charts use existing data.

- [ ] **Step 5: Commit the consistency increment**

```bash
git add app_pages/rules.py app_pages/pipeline.py app_pages/settings.py app_pages/stats.py app_pages/doctor.py app_pages/design_system.py tests/test_secondary_consistency.py tests/test_phase6_ui.py
git commit -m "feat(ui): standardize customization and system pages"
```

### Task 11: Qualify the complete redesign and record maintenance guidance

**Files:**
- Modify: `AGENTS.md`, `docs/releases.md`, `docs/superpowers/specs/2026-10-24-task-centered-ui-design.md`
- Modify: `scripts/overflow_check.py`, `scripts/screenshot_ui.py` only if Task 3's viewport/page additions need correction
- Create: `tests/test_ui_redesign_contract.py`
- Test: full existing suite

**Interfaces:**
- Consumes: all previous tasks and their public markers.
- Produces: final regression evidence, maintenance notes for the new modules, and no product behavior beyond the approved redesign.

- [ ] **Step 1: Add a final marker and safety-contract audit**

Create `tests/test_ui_redesign_contract.py` with this executable audit:

```python
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REQUIRED_MARKERS = {
    "run-clean", "copy-as", "copy-text", "copy-epic", "edit-result",
    "result-output", "review-tab", "review-diff", "release-status",
    "validation-panel", "validation-coverage", "ai-panel", "ai-answer-stream",
    "batch-filter", "census-summary", "settings-search", "doctor-summary",
    "input-overflow", "result-actions", "mobile-workspace-switch",
    "review-findings-tab", "review-changes-tab", "review-diagnostics-tab",
    "blocking-banner", "review-fallback", "census-filter", "census-export",
}


def test_required_ui_markers_remain_in_source():
    sources = "\n".join(path.read_text(encoding="utf-8") for path in (ROOT / "app_pages").glob("*.py"))
    missing = sorted(marker for marker in REQUIRED_MARKERS if marker not in sources)
    assert not missing, f"Missing UI compatibility markers: {missing}"


def test_review_ui_does_not_define_release_policy():
    source = (ROOT / "app_pages" / "review_model.py").read_text(encoding="utf-8")
    assert "release_decision(" not in source
    assert "acknowledgement" not in source
```

- [ ] **Step 2: Run the contract audit and fix only actual regressions**

Run: `python -m pytest tests/test_ui_redesign_contract.py -q`
Expected: PASS after Tasks 1–10. If a marker is absent, restore it on the equivalent control; do not delete the audit expectation to excuse a regression.

- [ ] **Step 3: Run the complete automated suite and output-equivalence checks**

Run: `python -m pytest -q`
Expected: PASS with no tracked-file modifications caused by tests.

Then run: `git status --short`
Expected: only intentional Task 11 document/script changes, no changes under `data/`, `config.json`, golden fixtures, or encrypted stores.

Explicitly confirm these existing suites are included and passing: `test_golden*`, `test_pipeline_properties.py`, `test_validation_release.py`, `test_edit_enforcement.py`, `test_clean_review_ui.py`, `test_ai_ui_lifecycle.py`, `test_a11y.py`, and `test_error_boundary.py`.

- [ ] **Step 4: Perform and record visual/manual verification**

In a first terminal, start the temporary app without backgrounding it:

```bash
python app.py --port 8899 --no-browser
```

In a second terminal, run:

```bash
python scripts/overflow_check.py 360 768 1199 1200 1440
python scripts/screenshot_ui.py
python scripts/screenshot_ui.py --light
```

Verify all pages at 360 px and 200% browser zoom; Clean separator drag and keyboard resizing; blocked-banner persistence across every Review view/filter; diff expansion and Previous/Next; copy-guard acknowledgment; Batch retry/export; Census-to-Clean; AI summary/Ask; keyboard-only menus/dialogs and focus return; reduced motion. Stop the temporary app with Ctrl+C in the first terminal and confirm its process exited; do not leave any development server running.

Update `AGENTS.md` with one concise workspace fact naming `navigation.py`, `workflow_ui.py`, `workspace_layout.py`, `review_model.py`, `review_workspace.py`, and `batch_view.py`, including the rule that Review presentation never recomputes release decisions. Add a `docs/releases.md` entry describing the task-centered UI and unchanged cleaning/release behavior. Change the spec status to `Implemented and verified` only after every automated and manual check passes.

- [ ] **Step 5: Commit qualification evidence and maintenance notes**

```bash
git add AGENTS.md docs/releases.md docs/superpowers/specs/2026-10-24-task-centered-ui-design.md tests/test_ui_redesign_contract.py scripts/overflow_check.py scripts/screenshot_ui.py
git commit -m "docs(ui): record task-centered redesign verification"
```

## Execution notes for reviewers

- Review Tasks 4–6 especially for accidental release/severity duplication; the only release call belongs in the existing Clean callback and service surfaces.
- Review Task 6 with long synthetic charts and cached/uncached results; collapsed context must never alter source/output text or drop clickable occurrences.
- Review every moved marker with `user.find()` visibility in mind: tests must open the relevant menu, tab, or expansion before asserting the control.
- Do not treat a successful screenshot as proof of clinical correctness. Clinical correctness is established by the unchanged engine, golden/property tests, and existing validation/copy-guard suites.
