"""Rule builder dialog: turn a selection into exactly the rule you mean.

Opened from the Clean page's selection bar ("Rule…"), its right-click menu and
Alt+Shift+B. It offers the :func:`rule_shapes.generalize` candidates (exact
text, any line containing it, the same line with any date / time / number /
name, every line starting with…, a block up to a blank line or heading), lets
you edit the text and its placeholders, the kind, the matching options and a
section scope, and previews it before anything is saved
(:func:`rule_builder.preview` — matches here, recent charts, clinical values it
would delete, "never remove" clashes).

Saving is the caller's job (``on_save(shape, scope, note)``): the Clean page
applies the rule to the chart on screen and puts it on its undo stack.
"""

from __future__ import annotations

from app_pages import common
from app_pages.common import *  # noqa: F401,F403 — shared imports and helpers
from chartcleaner import recent_charts, rule_builder, rule_shapes
from chartcleaner.section_parser import SECTION_TAXONOMY

KIND_LABELS = {"text": "This text", "line": "Whole lines containing it",
               "line_start": "Lines starting with it", "until": "From it up to…"}
STOP_LABELS = {"blank_line": "the next blank line", "next_heading": "the next heading",
               "marker": "this text…"}
SCOPE_LABELS = {"all": "Everywhere", "only": "Only in these sections", "except": "Everywhere except"}
SECTION_LABELS = {key: key.replace("_", " ").capitalize() for key in SECTION_TAXONOMY}
PLACEHOLDER_HELP = "Placeholders: " + ", ".join(
    f"{{{name}}} = {help_}" for name, (_rx, help_) in rule_shapes.PLACEHOLDERS.items())


def open_rule_builder(selected: str, *, chart: str, on_save, recent: int = 20,
                      initial: dict | None = None, initial_scope: dict | None = None,
                      initial_note: str = "") -> None:
    """Show the builder for ``selected`` (text highlighted in ``chart``).

    ``initial`` (a shape, with ``initial_scope`` / ``initial_note``) opens an
    existing rule for editing: it is the first choice, followed by the
    generalizations of its text.
    """
    candidates = rule_shapes.generalize(selected) or [
        rule_shapes.Candidate(rule_shapes.normalize({"text": selected or " x"}), "Exactly this text", "")]
    if initial is not None:
        current = rule_shapes.normalize(initial)
        candidates = [rule_shapes.Candidate(current, "The rule as saved", rule_shapes.build(current)[0])] + [
            c for c in candidates if c.shape != current]
    cfg = load_config(common.CONFIG_PATH)
    scores = rule_builder.rank(cfg, [c.shape for c in candidates], chart)
    state = {"recent": None}

    with ui.dialog() as dlg, ui.card().classes("w-[820px] max-w-full gap-2").mark("rule-builder"):
        ui.label("Edit rule" if initial is not None else "Build a rule from this selection") \
            .classes("text-lg font-semibold")
        ui.label("Pick how much it should catch, check the preview, then save. The rule runs on "
                 "every future clean and can be paused, edited or undone later.") \
            .classes("text-sm opacity-70 -mt-1")

        options = {i: f"{c.label} — {scores[i]['hits']} here" for i, c in enumerate(candidates)}
        choice = ui.radio(options, value=0).props("dense").mark("builder-choice")

        with ui.row().classes("w-full items-end gap-2 no-wrap"):
            text_in = ui.input("Rule text", value=candidates[0].shape["text"]) \
                .props("outlined dense").classes("flex-grow cc-mono").mark("builder-text")
            kind = ui.select(KIND_LABELS, value=candidates[0].shape["kind"], label="Remove") \
                .props("outlined dense").classes("w-56").mark("builder-kind")
        ui.label(PLACEHOLDER_HELP).classes("text-xs opacity-60 -mt-1")
        with ui.row().classes("w-full items-end gap-2") as until_row:
            stop = ui.select(STOP_LABELS, value="blank_line", label="Stop at") \
                .props("outlined dense").classes("w-56").mark("builder-stop")
            marker = ui.input("Stop text").props("outlined dense").classes("flex-grow")
        with ui.row().classes("w-full items-end gap-2") as replace_row:
            replacement = ui.input("Replace with (empty removes it)").props("outlined dense") \
                .classes("flex-grow").mark("builder-replacement")
        with ui.row().classes("items-center gap-4"):
            case = ui.checkbox("Match case", value=False)
            whole = ui.checkbox("Whole words only", value=True)
            template = ui.checkbox("Use placeholders", value=candidates[0].shape["template"]) \
                .mark("builder-template")
        with ui.row().classes("w-full items-end gap-2"):
            scope_mode = ui.select(SCOPE_LABELS, value=(initial_scope or {}).get("mode", "all"),
                                   label="Where").props("outlined dense").classes("w-56") \
                .mark("builder-scope")
            sections = ui.select(SECTION_LABELS, multiple=True,
                                 value=[x for x in (initial_scope or {}).get("sections", [])
                                        if x in SECTION_LABELS],
                                 label="Sections").props("outlined dense use-chips").classes("flex-grow")
        note = ui.input("Note (optional, e.g. “Epic MyChart footer”)", value=initial_note or "") \
            .props("outlined dense").classes("w-full")

        ui.separator()
        headline = ui.label("").classes("text-sm font-medium").mark("builder-headline")
        warnings_box = ui.column().classes("w-full gap-1")
        samples_box = ui.column().classes("w-full gap-0")

        def shape_now() -> dict | None:
            spec = {"kind": kind.value, "text": text_in.value or "", "case_sensitive": bool(case.value),
                    "whole_words": bool(whole.value), "template": bool(template.value),
                    "replacement": (replacement.value or "") if kind.value == "text" else ""}
            if kind.value == "until":
                spec["until"] = {"marker": marker.value or ""} if stop.value == "marker" else stop.value
            shape, error = rule_builder.check_shape(spec)
            if shape is None:
                headline.set_text(error)
            return shape

        def scope_now() -> dict | None:
            if scope_mode.value in ("only", "except") and sections.value:
                return {"mode": scope_mode.value, "sections": list(sections.value)}
            return None

        def show(result) -> None:
            headline.set_text(result.headline)
            warnings_box.clear()
            samples_box.clear()
            with warnings_box:
                for warning in result.warnings:
                    with ui.row().classes("items-start gap-1 no-wrap"):
                        ui.icon("warning").classes("text-warning")
                        ui.label(warning).classes("text-sm").mark("builder-warning")
            with samples_box:
                samples = list(result.samples)
                if result.impact:
                    samples += [s for s in result.impact.get("samples", []) if s not in samples]
                for s in samples[:8]:
                    ui.label(f"− {s['before'][:160]}" + (f"   → {s['after'][:80]}" if s["after"] else "")) \
                        .classes("text-xs cc-mono opacity-80")

        def refresh() -> None:
            until_row.set_visibility(kind.value == "until")
            marker.set_visibility(stop.value == "marker")
            replace_row.set_visibility(kind.value == "text")
            whole.set_visibility(kind.value == "text")
            sections.set_visibility(scope_mode.value != "all")
            shape = shape_now()
            if shape is None:
                warnings_box.clear()
                samples_box.clear()
                return
            try:
                result = rule_builder.preview(load_config(common.CONFIG_PATH), shape, chart,
                                              scope=scope_now())
            except Exception as ex:  # an odd pattern must not break the dialog
                headline.set_text(f"Could not preview: {ex}")
                return
            state["recent"] = None
            show(result)

        def pick(index) -> None:
            cand = candidates[int(index)]
            with_shape = cand.shape
            text_in.set_value(with_shape["text"])
            kind.set_value(with_shape["kind"])
            template.set_value(with_shape["template"])
            case.set_value(with_shape["case_sensitive"])
            whole.set_value(with_shape["whole_words"])
            replacement.set_value(with_shape.get("replacement", ""))
            if with_shape["kind"] == "until":
                until = with_shape["until"]
                stop.set_value("marker" if isinstance(until, dict) else until)
                if isinstance(until, dict):
                    marker.set_value(until["marker"])
            refresh()

        async def check_recent() -> None:
            shape = shape_now()
            if shape is None:
                return
            headline.set_text("Checking your recent charts…")

            def work():
                return rule_builder.preview(load_config(common.CONFIG_PATH), shape, chart,
                                            recent_charts.texts(recent), scope=scope_now(),
                                            examples=rule_examples.load())
            try:
                result = await run.io_bound(work)
            except Exception as ex:
                headline.set_text(f"Could not check: {ex}")
                return
            show(result)

        def save() -> None:
            shape = shape_now()
            if shape is None:
                ui.notify(headline.text or "Fix the rule first.", type="warning")
                return
            if on_save(shape, scope_now(), (note.value or "").strip() or None):
                dlg.close()

        choice.on_value_change(lambda e: pick(e.value))
        if initial is not None:
            pick(0)
        for element in (text_in, kind, stop, marker, replacement, case, whole, template, scope_mode,
                        sections):
            element.on_value_change(lambda _e: refresh())
        refresh()

        with ui.row().classes("gap-2"):
            ui.button("Save rule", icon="save", on_click=save).props("unelevated color=primary") \
                .mark("builder-save")
            ui.button("Check my recent charts", icon="travel_explore", on_click=check_recent) \
                .props("flat").mark("builder-recent")
            ui.button("Cancel", on_click=dlg.close).props("flat")
    dlg.open()
