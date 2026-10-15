"""The Rules page's "Remove & replace" editor.

One place to find, add, preview, edit and delete the user-facing regex rules:
learned rules, text replacements, whole-line removals and boilerplate blocks.

The on-disk shapes stay backwards compatible: pair lists hold
``[regex, replacement]`` and removal lists hold plain regex strings. New
plain-text rules are made by :mod:`chartcleaner.highlight_rules` (line removals
wrap that pattern in ``^[ \\t]*<text>.*$\\n?``); existing regexes are shown and edited
as regexes, so opening this page never changes a rule.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from nicegui import ui

from .engine import validate_config
from .highlight_rules import describe_rule, make_rule
from .regex_risk import risks as regex_risks
from .stage_core import get_stage_options


ConfigLoader = Callable[[], dict]
ConfigSaver = Callable[[dict], Any]

__all__ = ["RULE_SETS", "RuleSet", "build_entry", "preview_rule", "render_learned_editor"]


@dataclass(frozen=True)
class RuleSet:
    key: str          # config key
    stage: str        # pipeline stage id (stage_options / run history)
    label: str
    singular: str
    pairs: bool       # entries are [pattern, replacement]
    flags: int        # flags the stage runs this list with
    line: bool        # plain-text rules remove the whole line
    blurb: str


RULE_SETS: dict[str, RuleSet] = {s.key: s for s in (
    RuleSet("learned_rules", "learned_rules", "Learned rules", "learned rule", True, re.IGNORECASE,
            False, "Rules learned from highlighted text run on every future Clean."),
    RuleSet("literal_replacements", "literal_replacements", "Text replacements", "text replacement",
            True, re.IGNORECASE, False,
            "Built-in find → replace fixes and short forms (Hemoglobin → Hgb)."),
    RuleSet("emr_line_metadata", "metadata_lines", "Line removals", "line removal", False,
            re.IGNORECASE | re.MULTILINE, True,
            "Whole lines of EMR chrome to delete (Author:, Cosign Required:, …)."),
    RuleSet("boilerplate", "boilerplate", "Boilerplate", "boilerplate rule", False,
            re.IGNORECASE | re.MULTILINE | re.DOTALL, False,
            "Blocks of template text to delete wherever they appear."),
)}

_LINE_HEAD = "^[ \\t]*"
_LINE_TAIL = ".*$\\n?"
_PREVIEW_CHARS = 200_000
_SAMPLE_CHARS = 240


def _clip(text: str) -> str:
    text = text.strip()
    return text if len(text) <= _SAMPLE_CHARS else text[:_SAMPLE_CHARS - 1] + "…"


def _entry_pair(entry) -> list[str] | None:
    """An entry of any rule list as ``[pattern, replacement]`` (None if malformed)."""
    if isinstance(entry, str):
        return [entry, ""]
    if isinstance(entry, (list, tuple)) and len(entry) == 2 and all(isinstance(x, str) for x in entry):
        return list(entry)
    return None


def _pairs(config: dict, key: str = "learned_rules") -> list[list[str]]:
    """Return valid-looking entries while leaving validation to the engine."""
    rset = RULE_SETS.get(key)
    out = []
    for entry in config.get(key, []) or []:
        pair = _entry_pair(entry)
        if pair is not None and (rset is None or rset.pairs or isinstance(entry, str)):
            out.append(pair)
    return out


def _describe(pair: list[str], rset: RuleSet | None = None) -> dict | None:
    """Decode a plain-text rule made by this editor; None for hand-written regexes."""
    pattern, replacement = pair
    if rset is not None and rset.line:
        if not (pattern.startswith(_LINE_HEAD) and pattern.endswith(_LINE_TAIL)):
            return None
        pattern = pattern[len(_LINE_HEAD):-len(_LINE_TAIL)]
    return describe_rule([pattern, replacement])


def _label(pair: list[str], rset: RuleSet | None = None) -> tuple[str, str, bool]:
    """Return (source, replacement, generated-literal) for display."""
    detail = _describe(pair, rset)
    if detail is None:
        return pair[0], pair[1], False
    return str(detail["text"]), str(detail["replacement"]), True


def build_entry(rset: RuleSet, text: str, replacement: str = "", *, regex: bool = False,
                case_sensitive: bool = False, whole_words: bool = True) -> list[str]:
    """The ``[pattern, replacement]`` pair the editor saves for this input.

    Plain text is escaped (and, for line removals, widened to its whole line);
    a regex is kept as typed, with its replacement taken literally as a regex
    template (``\\1`` refers to a group).
    """
    if not isinstance(text, str) or not text.strip():
        raise ValueError("Enter text or a regular expression first.")
    if regex:
        return [text, replacement if rset.pairs else ""]
    pattern, repl = make_rule(text, replacement if rset.pairs else "",
                              case_sensitive=case_sensitive, whole_words=whole_words)
    if rset.line:
        pattern = _LINE_HEAD + pattern + _LINE_TAIL
    return [pattern, repl]


def _stored(rset: RuleSet, pair: list[str]):
    return list(pair) if rset.pairs else pair[0]


def _flags(rset: RuleSet, config: dict) -> int:
    flags = rset.flags
    if get_stage_options(config, rset.stage).get("case_sensitive"):
        flags &= ~re.IGNORECASE
    return flags


def preview_rule(pattern: str, replacement: str, flags: int, text: str, *,
                 limit: int = 3) -> dict:
    """What one rule does to ``text``: match count and a few before → after lines.

    Returns ``{"count", "samples": [(before, after)], "error", "risks",
    "empty"}``. A pattern with backtracking risks is not run (``count`` None):
    a preview must never freeze the page.
    """
    out: dict = {"count": 0, "samples": [], "error": None, "risks": [], "empty": False}
    try:
        rx = re.compile(pattern, flags)
    except re.error as e:
        out["error"] = f"Not a valid pattern: {e}"
        return out
    out["risks"] = regex_risks(pattern)
    if out["risks"]:
        out["count"] = None
        return out
    out["empty"] = rx.match("") is not None
    text = (text or "")[:_PREVIEW_CHARS]
    count = 0
    for m in rx.finditer(text):
        if m.start() == m.end():
            continue
        count += 1
        if len(out["samples"]) < limit:
            try:
                after = m.expand(replacement)
            except (re.error, IndexError) as e:
                out["error"] = f"Replacement problem: {e}"
                return out
            # The whole line(s) the match touches; a match that ends with its
            # line's newline ends on that line, not the next one.
            ls = text.rfind("\n", 0, m.start()) + 1
            le = text.find("\n", m.end() - 1 if m.group(0).endswith("\n") else m.end())
            le = len(text) if le == -1 else le
            out["samples"].append((text[ls:le], text[ls:m.start()] + after + text[m.end():le]))
    out["count"] = count
    return out


def _ensure_enabled(config: dict, key: str = "learned_rules") -> bool:
    """Enable learned rules when a config explicitly carries stage options."""
    if key != "learned_rules":
        return False
    options = config.setdefault("stage_options", {})
    current = options.setdefault("learned_rules", {})
    changed = current.get("enabled") is not True
    current["enabled"] = True
    return changed


def _save(config: dict, save_current: ConfigSaver) -> tuple[bool, str | None]:
    errors, _ = validate_config(config)
    if errors:
        return False, errors[0]
    save_current(config)
    return True, None


def _current_index(pairs: list, original_index: int, target: list[str]) -> int | None:
    """Find a dialog's target after another page may have changed the list.

    ``pairs`` may be raw config entries (strings or pairs); they are compared
    in their ``[pattern, replacement]`` form.
    """
    norm = [_entry_pair(p) for p in pairs]
    if original_index < len(norm) and norm[original_index] == list(target):
        return original_index
    try:
        return norm.index(list(target))
    except ValueError:
        return None


def _hit_text(row: dict | None) -> tuple[str, str] | None:
    """(label, css class) for a rule's run-history chip."""
    if not row or row.get("status") == "not enough runs yet":
        return None
    if row["status"] == "never matched":
        return f"Never matched in {row['runs']} runs", "text-orange-700"
    text = f"{row['hits']} hit{'s' if row['hits'] != 1 else ''} in {row['runs']} runs"
    if row["status"] in ("very broad", "slow"):
        return f"{text} · {row['status']}", "text-orange-700"
    return text, "opacity-60"


def render_learned_editor(load_current: ConfigLoader, save_current: ConfigSaver, *,
                          sample_text: Callable[[], str] | None = None,
                          load_runs: Callable[[], list] | None = None,
                          key: str = "learned_rules"):
    """Render the rule editor and return its root NiceGUI element.

    ``load_current`` is called for every mutation.  This matters when another
    page changes settings while the editor is open: the editor never writes a
    stale snapshot over those changes.  ``save_current`` owns atomic writing
    and backup creation. ``sample_text`` returns the chart used for live
    previews; ``load_runs`` returns run history for per-rule hit counts.
    """
    state: dict = {"key": key if key in RULE_SETS else "learned_rules", "selected": set(),
                   "undo": None}

    def rset() -> RuleSet:
        return RULE_SETS[state["key"]]

    def counts() -> dict[str, str]:
        cfg = load_current()
        return {k: f"{s.label} ({len(_pairs(cfg, k))})" for k, s in RULE_SETS.items()}

    with ui.column().classes("w-full max-w-5xl gap-3") as root:
        heading = ui.label(rset().label).classes("text-2xl font-semibold")
        blurb = ui.label("").classes("text-sm opacity-70")
        set_select = ui.toggle(counts(), value=state["key"]).props("no-caps dense") \
            .classes("flex-wrap").mark("rule-set")

        with ui.row().classes("w-full items-end gap-2"):
            search = ui.input("Search rules", placeholder="text or replacement").props(
                "outlined dense clearable"
            ).classes("grow")
            filter_select = ui.select(
                {"all": "All", "remove": "Removals", "replace": "Replacements",
                 "never": "Never matched"},
                value="all",
                label="Show",
            ).props("outlined dense")
            add_button = ui.button("Add rule", icon="add").props("unelevated color=primary")

        undo_box = ui.row().classes("w-full items-center gap-2")
        bulk_box = ui.row().classes("w-full items-center gap-2")
        list_box = ui.column().classes("w-full gap-2")

    def refresh_header() -> None:
        heading.set_text(rset().label)
        blurb.set_text(rset().blurb + (
            " New rules use plain-text matching unless you choose Pattern."
            if state["key"] == "learned_rules" else ""))
        set_select.set_options(counts(), value=state["key"])

    def write(mutate: Callable[[list], str | None], success: str | None) -> bool:
        """Apply ``mutate`` to a fresh copy of the current list and save it."""
        fresh = load_current()
        raw = list(fresh.get(state["key"]) or [])
        problem = mutate(raw)
        if problem:
            ui.notify(problem, type="warning")
            return False
        fresh[state["key"]] = raw
        enabled = _ensure_enabled(fresh, state["key"])
        try:
            ok, error = _save(fresh, save_current)
        except Exception as exc:  # keep UI responsive on save failures
            ui.notify(f"Could not save: {exc}", type="negative")
            return False
        if not ok:
            ui.notify(f"Cannot save rule: {error}", type="negative")
            return False
        if success:
            if enabled:
                success += " Learned rules were enabled in Pipeline settings."
            ui.notify(success, type="positive")
        return True

    def render_preview(box, pattern_fn: Callable[[], list[str] | str]) -> None:
        box.clear()
        with box:
            try:
                built = pattern_fn()
            except ValueError as e:
                if str(e) and not str(e).startswith("Enter text"):
                    ui.label(str(e)).classes("text-xs text-negative")
                return
            pattern, repl = built
            sample = sample_text() if sample_text else ""
            result = preview_rule(pattern, repl, _flags(rset(), load_current()), sample)
            if result["error"]:
                ui.label(result["error"]).classes("text-xs text-negative").mark("rule-preview-error")
                return
            if result["risks"]:
                ui.label(f"⚠ Could hang on long charts: {result['risks'][0]}. Preview skipped.") \
                    .classes("text-xs text-orange-700").mark("rule-preview-risk")
                return
            if result["empty"]:
                ui.label("⚠ This pattern also matches empty text.").classes("text-xs text-orange-700")
            if not sample.strip():
                ui.label("Paste a chart on the Clean page to preview this rule.").classes(
                    "text-xs opacity-60")
                return
            n = result["count"]
            ui.label(f"Matches {n} time{'s' if n != 1 else ''} in the preview chart."
                     if n else "No matches in the preview chart.").classes(
                "text-sm font-medium").mark("rule-preview-count")
            for before, after in result["samples"]:
                with ui.column().classes("w-full gap-0 cc-panel rounded p-2"):
                    ui.label(_clip(before) or "(blank line)").classes(
                        "text-xs cc-mono line-through opacity-70 break-all")
                    ui.label(_clip(after) or "(removed)").classes("text-xs cc-mono break-all")

    def edit_dialog(index: int | None = None) -> None:
        current_set = rset()
        config = load_current()
        raw = list(config.get(current_set.key) or [])
        existing = _entry_pair(raw[index]) if index is not None and index < len(raw) else None
        detail = _describe(existing, current_set) if existing is not None else None
        legacy = existing is not None and detail is None
        title = ("Edit " if existing is not None else "Add ") + current_set.singular

        with ui.context.client.content, ui.dialog() as dialog, ui.card().classes("w-full max-w-xl gap-3"):
            ui.label(title).classes("text-lg font-semibold")
            if legacy:
                ui.label("Regex rule — its pattern is kept as a regular expression.").classes(
                    "text-xs opacity-70")
                mode = None
            else:
                mode = ui.toggle({"text": "Plain text", "regex": "Pattern (regex)"},
                                 value="text").props("no-caps dense").mark("rule-mode")
            source = ui.textarea(
                "Regular expression" if legacy else (
                    "Lines starting with" if current_set.line else "Text to match"),
                value=(existing[0] if legacy else (detail["text"] if detail else "")),
            ).props("outlined input-style='min-height: 80px'").classes(
                "w-full" + (" font-mono" if legacy else ""))
            options_row = ui.row().classes("gap-4")
            with options_row:
                case_sensitive = ui.checkbox(
                    "Case-sensitive", value=bool(detail and detail.get("case_sensitive"))
                )
                whole_words = ui.checkbox(
                    "Whole words", value=bool(detail is None or detail.get("whole_words", True))
                )
            options_row.set_visibility(not legacy)

            replacement = None
            if current_set.pairs:
                replacement = ui.input(
                    "Replacement (leave empty to remove)",
                    value=(detail["replacement"] if detail else (existing[1] if existing else "")),
                ).props("outlined dense").classes("w-full")
            hint = ui.label("").classes("text-xs opacity-60")

            preview_box = ui.column().classes("w-full gap-1")

            def is_regex() -> bool:
                return legacy or (mode is not None and mode.value == "regex")

            def build() -> list[str]:
                return build_entry(
                    current_set, str(source.value or ""),
                    str(replacement.value or "") if replacement is not None else "",
                    regex=is_regex(), case_sensitive=bool(case_sensitive.value),
                    whole_words=bool(whole_words.value))

            def refresh(_=None) -> None:
                regex = is_regex()
                options_row.set_visibility(not regex)
                source.props["label"] = ("Regular expression" if regex else
                                         "Lines starting with" if current_set.line else "Text to match")
                source.update()
                if regex:
                    hint.set_text(r"Python regex. In the replacement, \1 inserts group 1."
                                  if current_set.pairs else "Python regex; every match is deleted.")
                else:
                    hint.set_text("Removes each whole line that starts with this text."
                                  if current_set.line else "")
                render_preview(preview_box, build)

            watched = [source, case_sensitive, whole_words] + ([replacement] if replacement else [])
            for element in watched:
                element.on_value_change(refresh)
            if mode is not None:
                mode.on_value_change(refresh)
            refresh()

            def commit() -> None:
                try:
                    pair = build()
                except ValueError as e:
                    ui.notify(str(e), type="warning")
                    return
                if is_regex():
                    try:
                        re.compile(pair[0])
                    except re.error as e:
                        ui.notify(f"Not a valid pattern: {e}", type="warning")
                        return

                def mutate(entries: list) -> str | None:
                    norm = [_entry_pair(e) for e in entries]
                    if any(c == pair and (index is None or pos != index) for pos, c in enumerate(norm)):
                        return "That exact rule already exists; no unrelated rule was changed."
                    if index is None:
                        entries.append(_stored(current_set, pair))
                        return None
                    target = _current_index(entries, index, existing or [])
                    if target is None:
                        return "The rule list changed; reopen this dialog and try again."
                    entries[target] = _stored(current_set, pair)
                    return None

                if write(mutate, "Rule saved."):
                    dialog.close()
                    render_list()

            with ui.row().classes("justify-end gap-2"):
                ui.button("Save", icon="save", on_click=commit).props("unelevated color=primary")
                ui.button("Cancel", on_click=dialog.close).props("flat")
        dialog.open()

    def delete(indexes: list[int]) -> None:
        """Delete rules right away; the undo bar puts them back."""
        config = load_current()
        raw = list(config.get(rset().key) or [])
        targets = [(i, _entry_pair(raw[i])) for i in sorted(set(indexes)) if i < len(raw)]
        removed: list[tuple[int, Any]] = []

        def mutate(entries: list) -> str | None:
            positions = []
            for original, pair in targets:
                pos = _current_index(entries, original, pair or [])
                if pos is None or pos in positions:
                    return "The rule list changed; reload the page and try again."
                positions.append(pos)
            for pos in sorted(positions, reverse=True):
                removed.append((pos, entries.pop(pos)))
            return None

        n = len(targets)
        if not n or not write(mutate, None):
            return
        state["undo"] = {"key": rset().key, "removed": sorted(removed)}
        state["selected"].clear()
        ui.notify(f"Deleted {n} rule{'s' if n != 1 else ''}.", type="positive")
        render_list()

    def undo() -> None:
        info = state.get("undo")
        if not info or info["key"] != rset().key:
            return

        def mutate(entries: list) -> str | None:
            for pos, entry in info["removed"]:
                if entry not in entries:
                    entries.insert(min(pos, len(entries)), entry)
            return None

        if write(mutate, "Restored."):
            state["undo"] = None
            render_list()

    def render_undo() -> None:
        undo_box.clear()
        info = state.get("undo")
        if not info or info["key"] != rset().key:
            return
        n = len(info["removed"])
        with undo_box, ui.row().classes("w-full items-center gap-2 cc-panel rounded px-3 py-1"):
            ui.icon("delete").classes("opacity-60")
            ui.label(f"Deleted {n} rule{'s' if n != 1 else ''}.").classes("text-sm grow")
            ui.button("Undo", icon="undo", on_click=undo).props("flat dense").mark("rule-undo")

    def health_rows(config: dict) -> dict[int, dict]:
        if load_runs is None:
            return {}
        try:
            from .rule_health import report
            runs = load_runs()
            return {r["index"]: r for r in report(config, runs) if r["key"] == rset().key}
        except Exception:
            return {}

    def render_bulk(shown_indexes: list[int]) -> None:
        bulk_box.clear()
        selected = state["selected"] & set(shown_indexes)
        if not shown_indexes:
            return
        with bulk_box:
            def toggle_all(e) -> None:
                if e.value:
                    state["selected"].update(shown_indexes)
                else:
                    state["selected"].difference_update(shown_indexes)
                render_list()
            ui.checkbox(f"Select all shown ({len(shown_indexes)})",
                        value=bool(shown_indexes) and selected == set(shown_indexes),
                        on_change=toggle_all).props("dense").mark("rule-select-all")
            if selected:
                ui.button(f"Delete selected ({len(selected)})", icon="delete_sweep",
                          on_click=lambda: delete(sorted(selected))) \
                    .props("unelevated dense color=negative").mark("rule-delete-selected")

    def render_list() -> None:
        refresh_header()
        render_undo()
        list_box.clear()
        current_set = rset()
        config = load_current()
        raw = list(config.get(current_set.key) or [])
        health = health_rows(config)
        query = str(search.value or "").strip().casefold()
        kind = filter_select.value or "all"
        shown: list[tuple[int, list[str]]] = []
        for index, entry in enumerate(raw):
            pair = _entry_pair(entry)
            if pair is None:
                continue
            source, repl, _ = _label(pair, current_set)
            mode = "replace" if pair[1] else "remove"
            if kind == "never" and (health.get(index) or {}).get("status") != "never matched":
                continue
            if kind in ("remove", "replace") and mode != kind:
                continue
            if query and query not in f"{source} {repl} {pair[0]}".casefold():
                continue
            shown.append((index, pair))
        state["selected"] &= {i for i, _ in shown}
        render_bulk([i for i, _ in shown])
        with list_box:
            if not raw:
                with ui.column().classes("w-full items-start gap-3 py-6"):
                    ui.element("div").classes("cc-dotgrid")
                    ui.label(f"No {current_set.label.lower()} yet. Add one here"
                             + (" or highlight text on Clean." if current_set.key == "learned_rules"
                                else ".")).classes("cc-page-sub")
                return
            if not shown:
                with ui.column().classes("w-full items-start gap-3 py-6"):
                    ui.element("div").classes("cc-dotgrid")
                    ui.label("No rules match this search or filter." if kind != "never" or health
                             else "Hit counts appear after a few cleans.").classes("cc-page-sub")
                return
            for index, pair in shown:
                source, repl, generated = _label(pair, current_set)
                mode = "Replacement" if pair[1] else "Removal"
                with ui.card().classes("w-full gap-1 p-3"):
                    with ui.row().classes("w-full items-start gap-3 no-wrap"):
                        ui.checkbox(value=index in state["selected"],
                                    on_change=lambda e, i=index: (
                                        state["selected"].add(i) if e.value else state["selected"].discard(i),
                                        render_bulk([j for j, _ in shown]))) \
                            .props("dense aria-label='Select rule'").mark(f"rule-select-{index}")
                        with ui.column().classes("min-w-0 gap-0 grow"):
                            ui.label(mode).classes(
                                "text-xs font-semibold uppercase tracking-wide "
                                + ("text-blue-700" if pair[1] else "text-orange-700")
                            )
                            ui.label(source).classes(
                                "text-sm break-all" + ("" if generated else " font-mono"))
                            ui.label(f"→ {repl}" if pair[1] else "Removed").classes(
                                "text-sm opacity-70 break-all"
                            )
                            with ui.row().classes("gap-3"):
                                ui.label(("Whole line · plain text" if current_set.line else "Plain text")
                                         if generated else "Regex").classes("text-xs opacity-50")
                                chip = _hit_text(health.get(index))
                                if chip:
                                    ui.label(chip[0]).classes(f"text-xs {chip[1]}")
                        with ui.row().classes("shrink-0"):
                            ui.button(icon="edit", on_click=lambda i=index: edit_dialog(i)).props(
                                "flat round dense aria-label='Edit rule'"
                            ).tooltip("Edit rule").mark(f"rule-edit-{index}")
                            ui.button(icon="delete", on_click=lambda i=index: delete([i])).props(
                                "flat round dense color=negative aria-label='Delete rule'"
                            ).tooltip("Delete rule (undo available)").mark(f"rule-delete-{index}")

    def switch_set(e) -> None:
        if e.value and e.value != state["key"]:
            state["key"] = e.value
            state["selected"].clear()
            render_list()

    set_select.on_value_change(switch_set)
    search.on_value_change(lambda _: render_list())
    filter_select.on_value_change(lambda _: render_list())
    add_button.on_click(lambda _: edit_dialog())
    render_list()
    return root
