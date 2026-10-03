"""The Pipeline page (/pipeline): every stage, its options and rules."""

from __future__ import annotations

from app_pages import common
from app_pages.common import *  # noqa: F401,F403 — shared imports and helpers


STAGE_EDITORS = {
    "metadata_lines": ("regex_lines", "emr_line_metadata"),
    "boilerplate": ("regex_lines", "boilerplate"),
    "phi_patterns": ("regex_pairs", "epic_phi_patterns"),
    "clinical_identifiers": ("clinical_identifiers", "clinical_identifiers"),
    "literal_replacements": ("regex_pairs", "literal_replacements"),
    "medical_abbreviations": ("abbreviations", None),
    "learned_rules": ("regex_pairs", "learned_rules"),
    "headers": ("headers", "clinical_headers"),
    "duplicate_notes": ("dedup_notes", "duplicate_note_detection"),
    "fuzzy_dedup": ("fuzzy", "fuzzy_dedup"),
    "nlp_redaction": ("nlp", None),
    "tokenize_phi": ("tokenize", "tokenization"),
    "unicode_normalize": ("unicode", "unicode_normalize"),
    "timestamps": ("timestamps", "timestamp_removal"),
    "hospital_day": ("form", "hospital_day"),
    "imaging_impression": ("form", "imaging_impression"),
    "lab_compaction": ("form", "lab_compaction"),
    "med_normalize": ("form", "med_normalize"),
    "vitals_summary": ("form", "vitals_summary"),
    "sections": ("sections", "section_filter"),
    "whitespace": ("whitespace", "whitespace"),
    "caps_normalize": ("caps", "caps_normalize"),
    "bullets": ("bullets", "bullets"),
    "line_length": ("line_length", "line_length"),
}


# Declarative option forms for the opt-in condensing stages:
# (option, label, control, choices) — control is switch | text | select | multi | dates.
STAGE_FORMS: dict[str, list[tuple]] = {
    "hospital_day": [
        ("enabled", "Label dates with hospital day (HD#) and post-op day (POD#)", "switch", None),
        ("admit_date", 'Admission date ("auto" finds "Admission Date:" in the chart, or YYYY-MM-DD)',
         "text", None),
        ("surgery_dates", "Surgery dates for POD# (YYYY-MM-DD, comma-separated)", "dates", None),
        ("style", "Style", "select", {"append": "Keep the date: 10/02/2026 (HD#3)",
                                      "replace": "Replace the date: HD#3"}),
    ],
    "lab_compaction": [
        ("enabled", "Condense lab tables into one line per panel", "switch", None),
        ("style", "Style", "select", {"line": "One line per panel (BMP: Na 138, K 4.1, …)",
                                      "fishbone": "Fishbone diagrams for BMP and CBC"}),
        ("latest_only", "Show only the latest value from multi-day grids", "switch", None),
        ("keep_reference_ranges", "Keep reference ranges [135 - 145]", "switch", None),
    ],
    "med_normalize": [
        ("enabled", "Clean up medication lists (drug, dose, route, frequency)", "switch", None),
    ],
    "vitals_summary": [
        ("enabled", "Summarize vitals flowsheets and intake/output", "switch", None),
    ],
    "imaging_impression": [
        ("enabled", "Keep only the Impression of radiology reports", "switch", None),
        ("keep", "Also keep", "multi", {"findings": "Findings", "indication": "Indication / history",
                                        "technique": "Technique", "comparison": "Comparison"}),
    ],
}

STAGE_DESCRIPTIONS = {
    "metadata_lines": "Whole lines to delete (author/pager/version lines, Epic chrome). Case-insensitive regex per line.",
    "boilerplate": "Blocks to delete anywhere (disclaimers, empty SmartSections). Dot matches newlines.",
    "tokenize_phi": "Reversible tokenization: swaps structured PHI for [[T1]]-style codes and saves the value→token map (Settings → Token maps). Runs before redaction, so enable it instead of — not on top of — the PHI patterns you want tokenized.",
    "phi_patterns": "Regex → replacement pairs for structured PHI (MRN, DOB, phone lines).",
    "clinical_identifiers": "Off by default. Algorithmically recognizes and redacts verified National Provider IDs (NPI via Luhn checksum), DEA numbers (checksum verified), and UDI medical device barcodes.",
    "nlp_redaction": "Presidio NLP redaction: entity types, replacements, confidence threshold and allow-list.",
    "literal_replacements": "Regex → replacement pairs for abbreviations and text fixes.",
    "medical_abbreviations": "Shortens full medical terms using the bundled CSV dictionary. "
                             "Matches whole terms, longest phrases first, without cascading replacements.",
    "learned_rules": "Rules you taught the app by highlighting text on the Clean page (remove text, remove whole lines, or replace). Each row is [regex, replacement]; an empty replacement removes the match. New rules can also be added here by hand.",
    "unicode_normalize": "Off by default. Turn any of these on to replace curly quotes, en/em dashes, non-breaking spaces, zero-width characters, ellipses and ligatures with plain equivalents — great before LLM use.",
    "hospital_day": "Off by default. Adds hospital day (HD#1 = admission day) and post-op day (POD#0 = surgery day) next to each date, so timelines read at a glance. Runs before timestamp removal.",
    "lab_compaction": "Off by default. Turns lab result tables and Recent Labs grids into one line per panel (BMP, CBC, LFT, Coags) with H/L flags, or fishbone diagrams. A table is only rewritten when every line is a recognized lab; anything else stays as written.",
    "med_normalize": "Off by default. Inside medication sections, reduces each line to drug, dose, route and frequency: drops brand names, dispense/refill counts, dates and provider names, and marks held or discontinued meds. Never produces Do Not Use abbreviations.",
    "vitals_summary": "Off by default. Turns vitals flowsheets (several readings per row) into one line of ranges and last values, and Intake/Output totals into one line. Single-reading vitals lines are left alone.",
    "imaging_impression": "Off by default. In radiology reports (FINDINGS followed by IMPRESSION), removes findings, technique, comparison and indication, keeping the title and the impression. Reports without an impression are left alone.",
    "timestamps": "Off by default. Removes dates (ISO, US, 'Mar 5, 2024') and optionally bare clock times, replacing them with configurable text.",
    "sections": "Off by default. Drop only the listed sections, or keep only the listed ones. Section boundaries come from your header list unless you supply boundary headers.",
    "whitespace": "Trims trailing spaces and collapses 3+ blank lines by default; seven further switches (CRLF, leading indent, tabs, double spaces, edge trim…).",
    "duplicate_notes": "Folds near-duplicate Epic note blocks (same note pasted twice) by comparing bodies.",
    "fuzzy_dedup": "Collapses paragraphs that are nearly identical (copy-forwarded text).",
    "headers": "Lines matching one of these names become Markdown headings. Choose the heading level, bold style, colon retention, or restrict to ALL-CAPS lines. Optionally let medspaCy detect section titles instead.",
    "caps_normalize": "Off by default. Rewrites long ALL-CAPS lines into sentence case, preserving chosen acronyms.",
    "bullets": "Normalizes •, * and - bullet prefixes to a marker you choose. Optionally converts numbered lists and drops empty bullets.",
    "line_length": "Off by default. Truncates or wraps lines longer than a set width (wrap keeps indentation).",
}

STAGE_FLAGS = {
    "metadata_lines": re.IGNORECASE | re.MULTILINE,
    "boilerplate": re.IGNORECASE | re.MULTILINE | re.DOTALL,
    "phi_patterns": re.IGNORECASE,
    "literal_replacements": re.IGNORECASE,
    "learned_rules": re.IGNORECASE,
}


def pipeline_page():
    try:
        draft = load_config(common.CONFIG_PATH)
    except ConfigError as e:
        def restore_defaults() -> None:
            try:
                save_config_with_backup(load_default_config())
                ui.notify("Factory default rules restored.", type="positive")
                ui.navigate.to("/pipeline")
            except Exception as ex:
                ui.notify(str(ex), type="negative")

        with shell("Pipeline & Rules", "pipeline"):
            ui.label(f"Cannot load config: {e}").classes("text-red-600")
            ui.button("Restore factory defaults", on_click=restore_defaults).props("outline")
        return

    customs = list_custom_rules(common.CUSTOM_DIR)

    # ---- audit draft rules + suggestions (defined before the UI) -----------

    def render_pending_card() -> None:
        pending_holder.clear()
        if not PENDING_RULE:
            return
        with pending_holder:
            with ui.card().classes("w-full border-amber-400 gap-2"):
                ui.label("Draft rule from an audit finding — review, then add it to a stage") \
                    .classes("font-semibold text-amber-600")
                pat_in = ui.input("Regex pattern", value=PENDING_RULE.get("pattern", "")) \
                    .props("outlined dense").classes("w-full cc-mono")
                repl_in = None
                if PENDING_RULE.get("replacement") is not None:
                    repl_in = ui.input("Replace with", value=PENDING_RULE["replacement"]) \
                        .props("outlined dense").classes("w-full")
                target_sel = ui.select(
                    options={"phi_patterns": "Structured PHI patterns (regex → replacement)",
                             "emr_line_metadata": "EMR line metadata (delete whole lines)",
                             "literal_replacements": "Literal replacements",
                             "learned_rules": "Learned rules (your highlight-taught rules)"},
                    value=PENDING_RULE.get("stage") or "phi_patterns",
                    label="Add to stage",
                ).classes("w-full max-w-[460px]")
                count_lbl = ui.label("").classes("text-xs opacity-80")

                def sync_count() -> None:
                    flags = re.IGNORECASE
                    if target_sel.value == "emr_line_metadata":
                        flags |= re.MULTILINE
                    n = count_matches(pat_in.value, PIPE_TEST["text"], flags)
                    count_lbl.set_text(f"{n} hit(s) in the current test text" if n >= 0 else "bad regex")

                pat_in.on_value_change(lambda e: sync_count())
                target_sel.on_value_change(lambda e: sync_count())
                sync_count()

                def add_to_stage() -> None:
                    key = {"phi_patterns": "epic_phi_patterns",
                           "literal_replacements": "literal_replacements",
                           "learned_rules": "learned_rules",
                           "emr_line_metadata": "emr_line_metadata"}[target_sel.value]
                    if key == "emr_line_metadata":
                        draft.setdefault(key, []).append(pat_in.value)
                    else:
                        draft.setdefault(key, []).append(
                            [pat_in.value, repl_in.value if repl_in is not None else ""])
                    PENDING_RULE.clear()
                    render_pending_card()
                    refresh_stages()
                    ui.notify(f"Pattern added to “{target_sel.label or target_sel.value}” — "
                              "check the hit counts, then Save changes.", type="positive")

                with ui.row().classes("gap-2"):
                    ui.button("Add to stage", icon="add", on_click=add_to_stage) \
                        .props("unelevated color=primary")
                    ui.button("Discard", icon="close",
                              on_click=lambda: (PENDING_RULE.clear(), render_pending_card())) \
                        .props("flat")

    def render_suggestions() -> None:
        suggestions_holder.clear()
        try:
            suggestions = store.get_suggestions()
        except Exception:
            suggestions = []
        if not suggestions:
            return
        with suggestions_holder:
            ui.label("Suggestions from your recent runs").classes("font-semibold")
            ui.label("Findings that keep surviving run after run. Adopting opens a draft rule "
                     "below — nothing is saved until you confirm.").classes("text-xs opacity-60 -mt-2")
            for s in suggestions[:6]:
                payload = suggestion_for_signature(s["signature"])
                with ui.card().classes("w-full gap-1"):
                    ui.label(f"{payload['title']} — survived {s['runs']} recent run(s)").classes("font-medium")
                    ui.label(payload["detail"]).classes("text-xs opacity-70 -mt-2")
                    if payload["regex"]:
                        ui.label(payload["regex"]).classes("text-xs cc-mono opacity-80")
                    with ui.row().classes("gap-2"):
                        ui.button("Adopt", icon="rule",
                                  on_click=lambda p=payload: (
                                      PENDING_RULE.clear(),
                                      PENDING_RULE.update(pattern=p["regex"], replacement=p["replacement"],
                                                          stage=p["stage"]),
                                      render_pending_card(),
                                  )).props("outline dense")
                        ui.button("Dismiss forever", icon="block",
                                  on_click=lambda sig=s["signature"]: dismiss_suggestion_clicked(sig)) \
                            .props("flat dense")

    def dismiss_suggestion_clicked(sig: str) -> None:
        try:
            store.dismiss_suggestion(sig)
        except Exception as e:
            ui.notify(f"Could not record dismissal: {e}", type="warning")
        render_suggestions()

    def render_audit_card() -> None:
        audit_holder.clear()
        with audit_holder:
            section = ensure_audit_section(draft)
            checks = section.get("checks", {})
            ui.switch("Run the post-run review after every clean",
                      value=bool(section.get("enabled", True)),
                      on_change=lambda e: section.update(enabled=e.value))
            ui.label("Checks scan the cleaned output for leftovers. They never change the result — "
                     "findings appear in a review strip on the Clean page and feed rule suggestions.") \
                .classes("text-xs opacity-60")
            with ui.grid().classes("grid-cols-2 gap-2 w-full"):
                for cid in CHECK_LABELS:
                    c = checks.get(cid, {})
                    ui.switch(CHECK_LABELS[cid], value=bool(c.get("enabled", True)),
                              on_change=lambda e, k=cid: checks.setdefault(k, {}).update(enabled=e.value)) \
                        .tooltip(CHECK_DESCRIPTIONS.get(cid, ""))
            ld = checks.setdefault("long_digits", {})
            ui.number("min digits for long-number check", value=int(ld.get("min_digits", 6)),
                      min=4, max=20, format="%.0f",
                      on_change=lambda e: ld.update(min_digits=int(e.value or 6))).classes("w-72")

            ui.label(CHECK_DESCRIPTIONS["residual_chrome"] +
                     " Extra patterns below (an empty list uses the built-in defaults).") \
                .classes("text-xs opacity-60 mt-1")
            patterns = checks.setdefault("residual_chrome", {}).setdefault("patterns", [])

            def chrome_row(pos: int, pattern: str) -> None:
                with ui.row().classes("w-full items-center gap-2"):
                    p_in = ui.input(value=pattern).props("outlined dense").classes("flex-grow cc-mono")
                    count_lbl = ui.label("").classes("text-xs opacity-70 w-24")

                    def sync() -> None:
                        try:
                            patterns[pos] = p_in.value
                        except IndexError:
                            return
                        n = count_matches(p_in.value, PIPE_TEST["text"], re.IGNORECASE | re.MULTILINE)
                        count_lbl.set_text(f"{n} hits" if n >= 0 else "bad regex")

                    p_in.on_value_change(lambda e: sync())
                    sync()

                    def remove() -> None:
                        if 0 <= pos < len(patterns):
                            del patterns[pos]
                        render_audit_card()

                    ui.button(icon="delete", on_click=remove).props("flat dense round color=grey")

            for i, p in enumerate(list(patterns)):
                if isinstance(p, str):
                    chrome_row(i, p)

            def add_chrome_pattern() -> None:
                patterns.append("")
                render_audit_card()

            ui.button("Add chrome pattern", icon="add", on_click=add_chrome_pattern).props("outline dense")

    def resolve_order() -> list[str]:
        resolved = [s.id for s in Pipeline(draft, custom_dir=common.CUSTOM_DIR).stages]
        draft["stage_order"] = resolved
        return resolved

    def stage_enabled(sid: str) -> bool:
        if sid.startswith("custom:"):
            return bool((draft.get("custom_rules") or {}).get(sid.split(":", 1)[1], {}).get("enabled", True))
        if sid == "nlp_redaction":
            return bool((draft.get("nlp_redaction") or {}).get("enabled", True))
        if sid == "tokenize_phi":
            return bool((draft.get("tokenization") or {}).get("enabled", False))
        if sid == "duplicate_notes":
            return bool((draft.get("duplicate_note_detection") or {}).get("enabled", True))
        if sid == "fuzzy_dedup":
            return bool((draft.get("fuzzy_dedup") or {}).get("enabled", True))
        so = (draft.get("stage_options") or {}).get(sid)
        if isinstance(so, dict) and "enabled" in so:
            return bool(so["enabled"])
        return True

    def move_stage(sid: str, delta: int) -> None:
        lst = draft["stage_order"]
        i = lst.index(sid)
        j = i + delta
        if 0 <= j < len(lst):
            lst[i], lst[j] = lst[j], lst[i]
            refresh_stages()

    def set_stage_enabled(sid: str, flag: bool) -> None:
        if sid.startswith("custom:"):
            draft.setdefault("custom_rules", {}).setdefault(sid.split(":", 1)[1], {})["enabled"] = flag
        elif sid == "nlp_redaction":
            draft.setdefault("nlp_redaction", {})["enabled"] = flag
        elif sid == "tokenize_phi":
            draft.setdefault("tokenization", {})["enabled"] = flag
        elif sid == "duplicate_notes":
            draft.setdefault("duplicate_note_detection", {})["enabled"] = flag
        elif sid == "fuzzy_dedup":
            draft.setdefault("fuzzy_dedup", {})["enabled"] = flag
        else:
            draft.setdefault("stage_options", {}).setdefault(sid, {})["enabled"] = flag
        refresh_stages()

    def refresh_stages() -> None:
        stages_container.clear()
        with stages_container:
            render_stages()

    def render_stages() -> None:
        total = len(draft["stage_order"])
        for idx, sid in enumerate(draft["stage_order"]):
            is_custom = sid.startswith("custom:")
            label = STAGE_LABELS.get(sid) or (sid.split(":", 1)[1] if sid.startswith("custom:") else sid)
            with ui.expansion(f"{idx + 1}. {label}", icon="code" if is_custom else "rule").classes("w-full"):
                with ui.row().classes("w-full items-center gap-2 flex-wrap"):
                    ui.switch("Enabled", value=stage_enabled(sid),
                              on_change=lambda e, s=sid: set_stage_enabled(s, e.value))
                    ui.button(icon="arrow_upward", on_click=lambda s=sid: move_stage(s, -1)) \
                        .props("flat dense round").set_enabled(idx > 0)
                    ui.button(icon="arrow_downward", on_click=lambda s=sid: move_stage(s, +1)) \
                        .props("flat dense round").set_enabled(idx < total - 1)
                    if is_custom:
                        ui.button("Edit script", icon="edit", on_click=lambda: ui.navigate.to("/scripts")).props("flat")
                    else:
                        ui.label(STAGE_DESCRIPTIONS.get(sid, "")).classes("text-xs opacity-60 flex-grow")
                if stage_enabled(sid) and not is_custom:
                    render_stage_editor(sid)

    def list_set(lst: list, pos: int, value) -> None:
        if 0 <= pos < len(lst):
            lst[pos] = value

    def list_del(lst: list, pos: int) -> None:
        if 0 <= pos < len(lst):
            del lst[pos]

    def pattern_row(lst: list, pos: int, pattern: str, replacement: str | None = None,
                    flags: int = re.IGNORECASE, sid: str | None = None) -> None:
        with ui.row().classes("w-full items-center gap-2"):
            p_in = ui.input(value=pattern).props("outlined dense").classes("flex-grow cc-mono")
            r_in = None
            if replacement is not None:
                r_in = ui.input(value=replacement).props("outlined dense label='→ replace with'") \
                    .classes("flex-grow")
            case_sw = None
            if sid:
                case_sw = ui.switch("Aa", value=bool(((draft.get("stage_options") or {})
                                                      .get(sid, {}) or {}).get("case_sensitive", False))) \
                    .tooltip("Case sensitive matching for this stage")
            count_lbl = ui.label("").classes("text-xs opacity-70 w-24")

            def current_flags() -> int:
                if sid and case_sw is not None and case_sw.value:
                    return flags & ~re.IGNORECASE
                return flags

            def sync() -> None:
                try:
                    if replacement is not None:
                        lst[pos] = [p_in.value, r_in.value]
                    else:
                        lst[pos] = p_in.value
                except IndexError:
                    return
                n = count_matches(p_in.value, PIPE_TEST["text"], current_flags())
                count_lbl.set_text(f"{n} hits" if n >= 0 else "bad regex")

            p_in.on_value_change(lambda e: sync())
            if r_in is not None:
                r_in.on_value_change(lambda e: sync())
            if case_sw is not None:
                def set_case(e) -> None:
                    draft.setdefault("stage_options", {}).setdefault(sid, {})["case_sensitive"] = e.value
                    sync()
                case_sw.on_value_change(set_case)
            sync()

            def remove() -> None:
                list_del(lst, pos)
                refresh_stages()

            ui.button(icon="delete", on_click=remove).props("flat dense round color=grey")

    def render_nlp_editor() -> None:
        try:
            import presidio_analyzer  # noqa: F401
            nlp_ok = True
        except Exception:
            nlp_ok = False
        if not nlp_ok:
            ui.label("Presidio / spaCy model is not available in this environment — this stage will be "
                     "skipped. Run install.sh (macOS) or install.bat (Windows) to enable it.") \
                .classes("text-orange-600 text-xs")

        n = draft.setdefault("nlp_redaction", {})
        ents = n.setdefault("entities", {
            "PERSON": "[REDACTED_NAME]",
            "PHONE_NUMBER": "[REDACTED_PHONE]",
            "EMAIL_ADDRESS": "[REDACTED_EMAIL]",
        })
        with ui.grid().classes("grid-cols-2 gap-2 w-full"):
            for etype in list(ents):
                with ui.row().classes("items-center gap-1 flex-nowrap w-full"):
                    ui.switch(etype, value=True,
                              on_change=lambda e, k=etype: (ents.pop(k, None) if not e.value else None,
                                                            refresh_stages()))
                    ui.input(value=ents[etype],
                             on_change=lambda e, k=etype: ents.update({k: e.value})) \
                        .props("outlined dense").classes("flex-grow")
        new_ent = {"v": ""}

        def add_entity() -> None:
            k = (new_ent["v"] or "").strip().upper()
            if k:
                ents.setdefault(k, "[REDACTED]")
                refresh_stages()
            new_ent["v"] = ""

        with ui.row().classes("items-center gap-2"):
            ui.input("Add entity type (e.g. LOCATION, US_SSN)",
                     on_change=lambda e: new_ent.update(v=e.value)) \
                .props("outlined dense").classes("w-80")
            ui.button("Add", on_click=add_entity).props("outline dense")
        ui.number("Minimum confidence (0–1, empty = Presidio default)",
                  value=n.get("score_threshold"), min=0, max=1, step=0.05,
                  on_change=lambda e: n.update(score_threshold=e.value)).classes("w-96")

        allow = draft.setdefault("nlp_allow_list", [])
        ui.label("Allow-list (words NLP must never redact, e.g. drug names that look like people)") \
            .classes("text-xs opacity-70")
        with ui.row().classes("flex-wrap gap-1 items-center"):
            for w in list(allow):
                ui.badge(w).props("outline")
                ui.button(icon="close",
                          on_click=lambda ww=w: (allow.remove(ww) if ww in allow else None,
                                                 refresh_stages())).props("flat dense round")
            add_word = {"v": ""}

            def add_allow() -> None:
                if add_word["v"].strip():
                    allow.append(add_word["v"].strip().lower())
                    refresh_stages()
                add_word["v"] = ""

            w_in = ui.input("add word + Enter", on_change=lambda e: add_word.update(v=e.value)) \
                .props("outlined dense").classes("w-44")
            w_in.on("keydown.enter.prevent", add_allow)

    def render_form(key: str) -> None:
        o = draft.setdefault(key, {})
        for opt, label, control, choices in STAGE_FORMS[key]:
            if control == "switch":
                ui.switch(label, value=bool(o.get(opt)),
                          on_change=lambda e, k=opt: o.update({k: e.value}))
            elif control == "text":
                ui.input(label, value=str(o.get(opt, "auto") or ""),
                         on_change=lambda e, k=opt: o.update({k: e.value.strip()})) \
                    .props("outlined dense").classes("w-full")
            elif control == "dates":
                ui.input(label, value=", ".join(o.get(opt) or []),
                         on_change=lambda e, k=opt: o.update(
                             {k: [d.strip() for d in e.value.split(",") if d.strip()]})) \
                    .props("outlined dense").classes("w-full")
            elif control == "select":
                ui.select(choices, label=label, value=o.get(opt) or next(iter(choices)),
                          on_change=lambda e, k=opt: o.update({k: e.value})).classes("w-full")
            elif control == "multi":
                ui.select(choices, label=label, multiple=True, value=list(o.get(opt) or []),
                          on_change=lambda e, k=opt: o.update({k: list(e.value or [])})) \
                    .props("use-chips").classes("w-full")

    def render_stage_editor(sid: str) -> None:
        kind, key = STAGE_EDITORS[sid]

        if kind == "form":
            render_form(key)
            return

        if kind == "abbreviations":
            ui.label("Your abbreviation dictionary is also used by Abbreviations only on the Clean page. "
                     "Add, edit or disable terms in My text rules.") \
                .classes("text-sm opacity-70")
            ui.button("Edit abbreviations", icon="edit",
                      on_click=lambda: ui.navigate.to("/rules")).props("flat")

        elif kind in ("regex_lines", "regex_pairs"):
            lst = draft.setdefault(key, [])
            flags = STAGE_FLAGS.get(sid, re.IGNORECASE)
            is_pairs = kind == "regex_pairs"

            def add_row() -> None:
                lst.append(["", ""] if is_pairs else "")
                refresh_stages()

            for i, item in enumerate(list(lst)):
                if is_pairs and isinstance(item, list) and len(item) == 2:
                    pattern_row(lst, i, pattern=item[0], replacement=item[1], flags=flags, sid=sid)
                elif not is_pairs and isinstance(item, str):
                    pattern_row(lst, i, pattern=item, flags=flags, sid=sid)
                else:
                    ui.label(f"⚠ malformed entry #{i}").classes("text-red-500 text-xs")
            ui.button("Add " + ("pair" if is_pairs else "pattern"), icon="add", on_click=add_row) \
                .props("outline dense")

        elif kind == "tokenize":
            t = draft.setdefault("tokenization", {})
            ui.label("Swaps structured PHI (the patterns below in 'Structured PHI patterns', "
                     "plus identity-label values) for reversible [[T1]]-style codes. Same value "
                     "⇒ same token, so repeated names stay consistent. Value→token maps are saved "
                     "under data/tokens and can restore the text later (Settings → Token maps, or "
                     "clean-chart --untoken).").classes("text-xs opacity-70")
            ui.input("Token prefix", value=str(t.get("prefix") or "T"),
                     on_change=lambda e: t.update(prefix=(e.value or "T").strip())) \
                .props("outlined dense").classes("w-40")
            ui.label("Tokens are issued before redaction runs — while this stage is on, the "
                     "pattern stages will simply find nothing left to replace.").classes("text-xs opacity-60")

        elif kind == "headers":
            lst = draft.setdefault(key, [])
            try:
                from chartcleaner.nlp_medspacy import medspacy_available
                ms_ok = medspacy_available()
            except Exception:
                ms_ok = False
            eng = draft.get("headers_engine", "regex")
            eng_sel = ui.select(options={"regex": "Regex header list", "medspacy": "medspaCy sectionizer"},
                                value=eng if (ms_ok or eng == "regex") else "regex",
                                label="Header detection engine").classes("w-72")
            eng_sel.on_value_change(lambda e: draft.update(headers_engine=e.value))
            if not ms_ok:
                ui.label("medspaCy is not installed — choosing its engine will fall back to this "
                         "regex list. pip install medspacy to enable.").classes("text-xs opacity-60")

            ho = draft.setdefault("header_options", {})
            with ui.grid().classes("grid-cols-2 gap-3 w-full mt-2"):
                ui.select(options={1: "H1 (# Header)", 2: "H2 (## Header)", 3: "H3 (### Header)",
                                   4: "H4 (#### Header)", 5: "H5 (##### Header)", 6: "H6 (###### Header)"},
                          value=int(ho.get("level") or 2), label="Heading level",
                          on_change=lambda e: ho.update(level=int(e.value))).classes("w-full")
                ui.switch("Bold style (**Header**)", value=bool(ho.get("bold")),
                          on_change=lambda e: ho.update(bold=e.value))
                ui.switch("Keep trailing colon", value=bool(ho.get("keep_colon")),
                          on_change=lambda e: ho.update(keep_colon=e.value))
                ui.switch("Only ALL-CAPS lines", value=bool(ho.get("uppercase_only")),
                          on_change=lambda e: ho.update(uppercase_only=e.value))

            def add_header() -> None:
                lst.append("New Header")
                refresh_stages()

            for i, h in enumerate(list(lst)):
                with ui.row().classes("w-full items-center gap-2"):
                    ui.input(value=h, on_change=lambda e, p=i: list_set(lst, p, e.value)) \
                        .props("outlined dense").classes("flex-grow")
                    ui.button(icon="delete",
                              on_click=lambda p=i: (list_del(lst, p), refresh_stages())) \
                        .props("flat dense round color=grey")
            ui.button("Add header", icon="add", on_click=add_header).props("outline dense")

        elif kind == "whitespace":
            o = draft.setdefault("whitespace", {})
            ui.label("Trailing-space trim, blank-line collapse and final trim are on by default "
                     "(the long-standing behavior); everything else is opt-in.") \
                .classes("text-xs opacity-70")
            with ui.grid().classes("grid-cols-2 gap-2 w-full"):
                for opt, desc in (("crlf_to_lf", "Convert CRLF / CR line endings to LF"),
                                  ("trim_trailing", "Trim trailing spaces/tabs per line"),
                                  ("collapse_blank_lines", "Collapse 3+ blank lines to one blank line"),
                                  ("collapse_spaces", "Collapse runs of 2+ spaces to one space"),
                                  ("strip_leading", "Strip leading indentation on every line"),
                                  ("normalize_tabs", "Convert tabs to four spaces"),
                                  ("final_trim", "Trim blank lines at start/end of the chart")):
                    ui.switch(desc, value=bool(o.get(opt, DEFAULT_WHITESPACE[opt])),
                              on_change=lambda e, k=opt: o.update({k: e.value}))

        elif kind == "bullets":
            o = draft.setdefault("bullets", {})
            with ui.grid().classes("grid-cols-2 gap-3 w-full"):
                ui.select(options={"- ": "- (dash)", "• ": "• (bullet)", "* ": "* (asterisk)", "": "(strip marker)"},
                          value=str(o.get("style") if o.get("style") is not None else "- "),
                          label="Replacement marker",
                          on_change=lambda e: o.update(style=e.value)).classes("w-full")
                ui.input("Extra bullet glyphs (e.g. ›,»)",
                         value="".join(o.get("extra_glyphs") or []),
                         on_change=lambda e: o.update(extra_glyphs=list(e.value or ""))) \
                    .props("outlined dense").classes("w-full")
                ui.switch("Normalize numbered lists (1. / 1) → marker)",
                          value=bool(o.get("normalize_numbered")),
                          on_change=lambda e: o.update(normalize_numbered=e.value))
                ui.switch("Drop empty bullet lines", value=bool(o.get("skip_empty")),
                          on_change=lambda e: o.update(skip_empty=e.value))

        elif kind == "unicode":
            o = draft.setdefault("unicode_normalize", {})
            ui.label("All off by default — flip on what your charts need. Runs before the "
                     "regex stages, so patterns can assume plain characters.") \
                .classes("text-xs opacity-70")
            with ui.grid().classes("grid-cols-2 gap-2 w-full"):
                for opt, desc in (("quotes", "Curly quotes → straight (\u201c \u201d \u2019 …)"),
                                  ("dashes", "En/em dashes & minus → hyphen (– — −)"),
                                  ("nbsp", "Non-breaking spaces → normal spaces"),
                                  ("zero_width", "Strip zero-width characters & BOM"),
                                  ("ellipsis", "… → ..."),
                                  ("ligatures", "Ligatures → letters (ﬁ → fi)")):
                    ui.switch(desc, value=bool(o.get(opt)),
                              on_change=lambda e, k=opt: o.update({k: e.value}))

        elif kind == "timestamps":
            o = draft.setdefault("timestamp_removal", {})
            ui.switch("Remove dates/times", value=bool(o.get("enabled")),
                      on_change=lambda e: o.update(enabled=e.value))
            ui.input("Replace with (empty = delete)", value=str(o.get("replacement") or ""),
                     on_change=lambda e: o.update(replacement=e.value)) \
                .props("outlined dense").classes("w-full")
            with ui.row().classes("w-full items-center gap-4 flex-wrap"):
                ui.switch("Include built-in date patterns (ISO, US, 'Mar 5, 2024')",
                          value=bool(o.get("builtin_patterns", True)),
                          on_change=lambda e: o.update(builtin_patterns=e.value))
                ui.switch("Also remove clock times (12:30, 9:45 pm)",
                          value=bool(o.get("remove_clock_times")),
                          on_change=lambda e: o.update(remove_clock_times=e.value))
            ui.label("Extra patterns (one regex per row, run after the built-ins):") \
                .classes("text-xs opacity-70 mt-1")
            pats = o.setdefault("extra_patterns", [])

            for i, p in enumerate(list(pats)):
                if not isinstance(p, str):
                    continue
                with ui.row().classes("w-full items-center gap-2"):
                    ui.input(value=p, on_change=lambda e, k=i: list_set(pats, k, e.value)) \
                        .props("outlined dense").classes("flex-grow cc-mono")
                    ui.button(icon="delete",
                              on_click=lambda k=i: (list_del(pats, k), refresh_stages())) \
                        .props("flat dense round color=grey")

            def add_ts_pattern() -> None:
                pats.append("")
                refresh_stages()

            ui.button("Add pattern", icon="add", on_click=add_ts_pattern).props("outline dense")

        elif kind == "sections":
            o = draft.setdefault("section_filter", {})
            ui.select(options={"off": "Off", "drop": "Drop listed sections",
                               "keep": "Keep only listed sections"},
                      value=str(o.get("mode") or "off"), label="Mode",
                      on_change=lambda e: o.update(mode=e.value)).classes("w-64")
            ui.label("Section header names, one per row:") \
                .classes("text-xs opacity-70 mt-1")
            names = o.setdefault("sections", [])
            for i, h in enumerate(list(names)):
                with ui.row().classes("w-full items-center gap-2"):
                    ui.input(value=h, on_change=lambda e, k=i: list_set(names, k, e.value)) \
                        .props("outlined dense").classes("flex-grow")
                    ui.button(icon="delete",
                              on_click=lambda k=i: (list_del(names, k), refresh_stages())) \
                        .props("flat dense round color=grey")

            def add_section() -> None:
                names.append("")
                refresh_stages()

            ui.button("Add section name", icon="add", on_click=add_section).props("outline dense")

            ui.label("Section boundaries: text between one boundary header and the next is one "
                     "section. Leave empty to reuse the Header stage's header list. 'Keep text "
                     "before the first section' preserves the preamble.") \
                .classes("text-xs opacity-70 mt-2")
            ui.switch("Keep text before the first section", value=bool(o.get("keep_preamble", True)),
                      on_change=lambda e: o.update(keep_preamble=e.value))
            bounds = o.setdefault("boundary_headers", [])
            for i, h in enumerate(list(bounds)):
                with ui.row().classes("w-full items-center gap-2"):
                    ui.input(value=h, on_change=lambda e, k=i: list_set(bounds, k, e.value)) \
                        .props("outlined dense").classes("flex-grow")
                    ui.button(icon="delete",
                              on_click=lambda k=i: (list_del(bounds, k), refresh_stages())) \
                        .props("flat dense round color=grey")

            def add_boundary() -> None:
                bounds.append("")
                refresh_stages()

            ui.button("Add boundary header", icon="add", on_click=add_boundary).props("outline dense")

        elif kind == "caps":
            o = draft.setdefault("caps_normalize", {})
            ui.select(options={"off": "Off", "sentence": "Rewrite to sentence case"},
                      value=str(o.get("mode") or "off"), label="Mode",
                      on_change=lambda e: o.update(mode=e.value)).classes("w-64")
            ui.number("Minimum line length (shorter ALL-CAPS lines like headings are kept)",
                      value=int(o.get("min_chars") or 40), min=1, format="%.0f",
                      on_change=lambda e: o.update(min_chars=int(e.value or 40))).classes("w-96")
            ui.input("Acronyms to preserve (comma-separated, e.g. MRI, ICU, SAH)",
                     value=", ".join(o.get("preserve_words") or []),
                     on_change=lambda e: o.update(
                         preserve_words=[w.strip() for w in (e.value or "").split(",") if w.strip()])) \
                .props("outlined dense").classes("w-full")

        elif kind == "line_length":
            o = draft.setdefault("line_length", {})
            ui.select(options={"off": "Off", "truncate": "Truncate long lines",
                               "wrap": "Wrap long lines"},
                      value=str(o.get("mode") or "off"), label="Mode",
                      on_change=lambda e: o.update(mode=e.value)).classes("w-64")
            ui.number("Max characters per line", value=int(o.get("max_chars") or 200),
                      min=10, format="%.0f",
                      on_change=lambda e: o.update(max_chars=int(e.value or 200))).classes("w-72")
            ui.input("Truncation marker", value=str(o.get("marker") or "…"),
                     on_change=lambda e: o.update(marker=e.value)).props("outlined dense").classes("w-40")

        elif kind == "dedup_notes":
            d = draft.setdefault("duplicate_note_detection", {})
            with ui.grid().classes("grid-cols-2 gap-3 w-full"):
                ui.number("min body chars", value=d.get("min_body_chars", 400), format="%.0f",
                          on_change=lambda e: d.update(min_body_chars=int(e.value or 400)))
                ui.number("similarity threshold %", value=d.get("similarity_threshold", 90),
                          min=50, max=100, format="%.0f",
                          on_change=lambda e: d.update(similarity_threshold=int(e.value or 90)))
            ui.input("split pattern (regex)", value=d.get("split_pattern", ""),
                     on_change=lambda e: d.update(split_pattern=e.value)) \
                .props("outlined dense").classes("w-full cc-mono")

        elif kind == "fuzzy":
            f = draft.setdefault("fuzzy_dedup", {})
            with ui.grid().classes("grid-cols-2 gap-3 w-full"):
                ui.number("similarity threshold %", value=f.get("threshold", 95),
                          min=50, max=100, format="%.0f",
                          on_change=lambda e: f.update(threshold=int(e.value or 95)))
                ui.number("min paragraph chars", value=f.get("min_chars", 100), format="%.0f",
                          on_change=lambda e: f.update(min_chars=int(e.value or 100)))

        elif kind == "clinical_identifiers":
            ci = draft.setdefault("clinical_identifiers", {})
            ui.label(
                "Detects verified clinical identifiers using algorithmic checksums (Luhn-24 for NPI, "
                "checksum digit for DEA) and FDA UDI device barcode specifications."
            ).classes("text-xs opacity-70 mb-2")
            with ui.row().classes("gap-4 items-center"):
                ui.switch("Redact NPI numbers", value=bool(ci.get("redact_npi", True)),
                          on_change=lambda e: ci.update(redact_npi=e.value))
                ui.switch("Redact DEA numbers", value=bool(ci.get("redact_dea", True)),
                          on_change=lambda e: ci.update(redact_dea=e.value))
                ui.switch("Redact UDI device codes", value=bool(ci.get("redact_udi", True)),
                          on_change=lambda e: ci.update(redact_udi=e.value))
            ui.input("Custom replacement (leave empty for [REDACTED_<TYPE>])",
                     value=str(ci.get("replacement") or ""),
                     on_change=lambda e: ci.update(replacement=e.value if e.value else None)).classes("w-96")

        elif kind == "nlp":
            render_nlp_editor()

        else:
            ui.label("This stage has no parameters.").classes("text-xs opacity-60")

    # ---- toolbar / io actions --------------------------------------------------

    def save_all() -> None:
        errs, warns = validate_config(draft)
        if errs:
            ui.notify("Cannot save — " + str(len(errs)) + " problem(s). First: " + errs[0], type="negative")
            return
        try:
            save_config_with_backup(draft)
        except Exception as e:
            ui.notify(f"Save failed: {e}", type="negative")
            return
        save_btn.set_text("Save changes")
        msg = "Saved. " + (f"Warnings: {'; '.join(warns[:2])}" if warns else "")
        ui.notify(msg, type="positive")
        ui.navigate.to("/pipeline")

    def restore_defaults() -> None:
        try:
            save_config_with_backup(load_default_config())
            ui.notify("Factory default rules restored.", type="positive")
            ui.navigate.to("/pipeline")
        except Exception as e:
            ui.notify(str(e), type="negative")

    def run_tests() -> None:
        text = PIPE_TEST["text"]
        counts: list[str] = []
        for sid in draft["stage_order"]:
            ed = STAGE_EDITORS.get(sid)
            if not ed:
                continue
            _kind, key = ed
            obj = draft.get(key)
            if sid in ("metadata_lines", "boilerplate") and isinstance(obj, list):
                n = sum(max(0, count_matches(p, text, STAGE_FLAGS[sid])) for p in obj if isinstance(p, str))
                counts.append(f"{STAGE_LABELS[sid]}: {n}")
            elif sid in ("phi_patterns", "literal_replacements", "learned_rules") and isinstance(obj, list):
                n = sum(max(0, count_matches(p[0], text, STAGE_FLAGS[sid]))
                        for p in obj if isinstance(p, list) and len(p) == 2)
                counts.append(f"{STAGE_LABELS[sid]}: {n}")
            elif sid == "headers" and isinstance(obj, list):
                n = sum(max(0, count_matches(rf"^\s*({h})\s*:?\s*$", text, re.IGNORECASE | re.MULTILINE))
                        for h in obj if isinstance(h, str))
                counts.append(f"{STAGE_LABELS[sid]}: {n}")
            elif sid == "medical_abbreviations":
                _, n, _ = abbreviate(text, draft)
                counts.append(f"{STAGE_LABELS[sid]}: {n}")
        test_results.set_text("Match counts → " + " · ".join(counts) if counts else "Nothing to test.")

    def export_rules() -> None:
        name = store.save_export(json.dumps(draft, indent=4, ensure_ascii=False), "chart_rules")
        download_file(f"/exports/{name}", "chart_rules.json")
        ui.notify("Rules exported (check your downloads).", type="positive")

    async def import_rules(e) -> None:
        try:
            _, data = await read_upload(e)
            cfg = json.loads(data.decode("utf-8-sig"))
        except Exception as ex:
            ui.notify(f"Invalid JSON: {ex}", type="negative")
            return
        errs, _ = validate_config(cfg)
        if errs:
            ui.notify("Imported rules have problems: " + errs[0], type="negative")
            return
        draft.clear()
        draft.update(cfg)
        resolve_order()
        save_all()

    def new_preset_dialog() -> None:
        with ui.dialog() as dlg, ui.card():
            ui.label("Save the current rules as a preset:")
            name_in = ui.input("Preset name", placeholder="e.g. Neuro-ICU strict")
            err = ui.label("").classes("text-red-500 text-xs")

            def do_save() -> None:
                name = (name_in.value or "").strip()
                if not name:
                    err.set_text("Name required.")
                    return
                store.save_preset(name, draft)
                dlg.close()
                ui.notify(f"Preset '{name}' saved.", type="positive")
                ui.navigate.to("/pipeline")

            with ui.row():
                ui.button("Save preset", on_click=do_save).props("unelevated color=primary")
                ui.button("Cancel", on_click=dlg.close).props("flat")
        dlg.open()

    def delete_preset_clicked() -> None:
        name = preset_dd.value
        if not name:
            ui.notify("Pick a preset first.", type="warning")
            return
        store.delete_preset(name)
        ui.notify(f"Preset '{name}' deleted.", type="positive")
        ui.navigate.to("/pipeline")

    def on_preset_load(e) -> None:
        if not e.value:
            return
        try:
            cfg = store.load_preset(e.value)
            perrs, _ = validate_config(cfg)
            if perrs:
                ui.notify("Preset invalid: " + perrs[0], type="negative")
                return
            draft.clear()
            draft.update(cfg)
            resolve_order()
            save_all()
        except Exception as ex:
            ui.notify(str(ex), type="negative")

    def render_packs() -> None:
        packs_holder.clear()
        try:
            packs = rulepacks.list_packs()
        except Exception:
            packs = []
        with packs_holder:
            if not packs:
                ui.label("No packs found.").classes("text-xs opacity-60")
            for p in packs:
                with ui.card().classes("w-full gap-1"):
                    ui.label(f"{p['name']} — {p['rules']} rule entries").classes("font-medium")
                    ui.label(p["description"]).classes("text-xs opacity-70 -mt-2")
                    if p["source"]:
                        ui.label(f"Source: {p['source']}").classes("text-xs opacity-50 -mt-1")

                    def install(name=p["name"]) -> None:
                        _n, msg = rulepacks.install_pack(name)
                        ui.notify(msg, type="positive")

                    def apply(name=p["name"]) -> None:
                        def do_it() -> None:
                            ok, msg = rulepacks.apply_pack(name)
                            ui.notify(msg, type="positive" if ok else "negative")
                            if ok:
                                ui.navigate.to("/pipeline")
                        confirm_dialog(f"Replace the current rules with pack '{name}'? "
                                       "(a config backup is kept)", do_it)

                    with ui.row().classes("gap-2"):
                        ui.button("Install as preset", icon="save_as", on_click=install) \
                            .props("outline dense")
                        ui.button("Apply now", icon="bolt", on_click=apply) \
                            .props("outline dense color=orange")

    # ---- UI ------------------------------------------------------------------
    resolve_order()

    with shell("Pipeline & Rules", "pipeline"):
        ui.label("Every cleaning rule lives here: enable, disable, edit, reorder — plus presets and "
                 "import/export. Changes apply after you save.").classes("opacity-70 -mt-2 text-sm")

        pending_holder = ui.column().classes("w-full")
        with pending_holder:
            render_pending_card()

        suggestions_holder = ui.column().classes("w-full")
        with suggestions_holder:
            render_suggestions()

        with ui.row().classes("w-full items-center gap-2 flex-wrap"):
            save_btn = ui.button("Save changes", icon="save", on_click=save_all)
            save_btn.props("unelevated color=primary")
            ui.button("Revert", icon="undo", on_click=lambda: ui.navigate.to("/pipeline")).props("flat")
            ui.button("Test patterns on sample text", icon="science", on_click=run_tests).props("outline")
            ui.separator().props("vertical")
            preset_dd = ui.select(options={p: p for p in store.list_presets()}, value=None,
                                  label="Presets").classes("w-44")
            ui.button(icon="save_as", on_click=new_preset_dialog).props("flat").tooltip("Save current rules as a preset")
            ui.button(icon="delete", on_click=delete_preset_clicked).props("flat").tooltip("Delete selected preset")
            ui.button(icon="file_download", on_click=export_rules).props("flat").tooltip("Export rules as JSON")
            ui.upload(on_upload=import_rules, auto_upload=True) \
                .props("accept=.json,application/json flat").classes("max-w-[210px]")
            ui.button("Factory defaults", icon="restart_alt", on_click=restore_defaults).props("flat")

        with ui.expansion("Test text (used by the per-pattern match counters)", icon="science").classes("w-full"):
            ui.textarea("Sample chart to test patterns against", value=PIPE_TEST["text"],
                        on_change=lambda e: PIPE_TEST.update(text=e.value)) \
                .props("outlined input-style='min-height: 120px'").classes("w-full cc-mono")
            test_results = ui.label("").classes("text-xs opacity-80")

        with ui.expansion("Review checks (post-run audit)", icon="fact_check").classes("w-full"):
            audit_holder = ui.column().classes("w-full gap-2")
            with audit_holder:
                render_audit_card()

        with ui.expansion("Rule packs (curated, read-only rule sets)", icon="inventory_2").classes("w-full"):
            ui.label("Complete rule sets shipped with the app — install one as a preset, or apply it "
                     "directly (your current rules are backed up first).").classes("text-xs opacity-60")
            packs_holder = ui.column().classes("w-full gap-2")
            with packs_holder:
                render_packs()

        stages_container = ui.column().classes("w-full gap-1")

        preset_dd.on_value_change(on_preset_load)
        refresh_stages()


# ===========================================================================
# PAGE: Statistics
# ===========================================================================
