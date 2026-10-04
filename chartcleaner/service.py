"""One entry point for every integration (local API, MCP, hotkeys, watchers).

Each function loads the same config the app uses, runs the same ``Pipeline``,
and records the run in history (``source="api:<caller>"`` by default) so the
Statistics page counts it. Results are plain dicts so callers can serialize
them directly. Nothing here talks to the network except :func:`ask`, which
goes through the loopback-guarded local LLM client.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import store
from .engine import Pipeline, RunResult, load_config

__all__ = ["FORMATS", "INSIGHTS", "load_active_config", "format_output", "trends_text", "clean", "abbreviate",
           "expand", "prompt", "ask", "restore", "timeline", "result_cache", "insights", "insights_text",
           "daily_note", "split_patients", "clean_patients", "note"]

FORMATS = ("text", "markdown", "json", "xml")


def load_active_config(preset: str | None = None, config_path: str | Path | None = None) -> dict:
    """The saved config (or a named preset) — what the app would clean with."""
    if preset:
        return store.load_preset(preset)
    return load_config(config_path or store.CONFIG_PATH)


def _config(config: dict | None, preset: str | None) -> dict:
    """An explicit ``config`` wins; otherwise the saved config or ``preset``."""
    return config if config is not None else load_active_config(preset)


def format_output(text: str, fmt: str = "text", delta: bool = False,
                  trends: bool = False, insights: bool = False) -> tuple[str, Any]:
    """Apply the optional copy-forward delta, trends block and structured format.

    Returns ``(text, delta_result)``; ``delta_result`` is None unless ``delta``.
    ``trends`` puts the lab-trend / medication-change block (see
    :mod:`chartcleaner.trends`) above the chart when it holds several notes.
    ``insights`` puts the problem / device / antibiotic / overnight blocks
    (see :func:`insights_text`) above it.
    """
    _check_format(fmt)
    block = trends_text(text) if trends else ""
    return _compose(text, fmt, delta, block, insights)


def _check_format(fmt: str) -> None:
    if fmt not in FORMATS:
        raise ValueError(f"Unknown format {fmt!r}; expected one of {', '.join(FORMATS)}")


def _compose(text: str, fmt: str, delta: bool, block: str, insights: bool) -> tuple[str, Any]:
    """format_output's body once the trends block is known."""
    if insights:
        extra = insights_text(text, ("overnight", "devices", "micro", "problems"))
        block = f"{block}\n\n{extra}".strip() if extra else block
    delta_res = None
    out = text
    if delta:
        from .delta_engine import extract_note_deltas
        delta_res = extract_note_deltas(out)
        out = delta_res.compact_text
    if block:
        out = f"{block}\n\n{out}"
    if fmt != "text":
        from .section_parser import parse_clinical_sections
        parsed = parse_clinical_sections(out)
        out = {"markdown": parsed.to_markdown, "json": parsed.to_json,
               "xml": parsed.to_llm_xml}[fmt]()
    return out, delta_res


def _trends_report(text: str, config: dict | None = None):
    """The trend report for ``text``, or None if building it failed."""
    from .trends import build
    try:
        return build(_unwrap(text), config)
    except Exception:
        return None  # trends are a bonus; never fail a clean over them


def trends_text(text: str, config: dict | None = None) -> str:
    """The trends block for ``text`` ("" for a single note)."""
    report = _trends_report(text, config)
    return report.to_text() if report is not None else ""


# name -> module with build(text) returning a report with to_text()/to_dict()
INSIGHTS = ("problems", "devices", "micro", "overnight", "trends")


def _insight_report(name: str, text: str):
    import importlib
    if name not in INSIGHTS:
        raise ValueError(f"Unknown insight {name!r}; expected one of {', '.join(INSIGHTS)}")
    module = importlib.import_module(f"chartcleaner.{name}")
    return module.build(text)


def _each_insight(text: str, which, render: str):
    """Yield ``(name, report.<render>())`` per insight; a failing extractor yields None
    (one extractor failing never hides the others). Unknown names raise ValueError."""
    names = list(which or INSIGHTS)
    unknown = [n for n in names if n not in INSIGHTS]
    if unknown:
        raise ValueError(f"Unknown insight {unknown[0]!r}; expected one of {', '.join(INSIGHTS)}")
    body = _unwrap(text)
    for name in names:
        try:
            yield name, getattr(_insight_report(name, body), render)()
        except Exception:
            yield name, None


def insights(text: str, which: tuple[str, ...] | list[str] | None = None) -> dict:
    """Problem-oriented view, devices, antibiotics/cultures, overnight events and
    trends read from ``text`` (each built from the chart's own lines)."""
    return dict(_each_insight(text, which, "to_dict"))


def insights_text(text: str, which: tuple[str, ...] | list[str] | None = None) -> str:
    """The insight blocks as plain text, in order, empty ones left out."""
    return "\n\n".join(block for _name, block in _each_insight(text, which, "to_text") if block)


def _cleaned(text: str, cfg: dict, record: bool, source: str) -> str:
    return clean(text, config=cfg, wrap=False, record=record, source=source)["text"]


def daily_note(today: str, previous: str | None = None, *, tag: str | None = None,
               preset: str | None = None, config: dict | None = None, clean_first: bool = True,
               record: bool = False, source: str = "api") -> dict:
    """Today's chart against a previous one (see :mod:`chartcleaner.daily_note`).

    ``previous`` defaults to the newest stored recent chart with bed ``tag``.
    Both charts are cleaned with the same config unless ``clean_first`` is False.
    """
    from . import recent_charts
    from .daily_note import build

    if previous is None:
        found = recent_charts.latest_for(tag, exclude_text=today) if tag else None
        if found is None:
            raise ValueError("No previous chart: pass one, or tag today's bed and clean "
                             "yesterday's chart with the same tag first.")
        previous = found["text"]
    cfg = _config(config, preset)
    if clean_first:
        today = _cleaned(today, cfg, record, source)
        previous = _cleaned(previous, cfg, False, source)
    return build(today, previous, cfg).to_dict()


def split_patients(text: str) -> list[dict]:
    """A multi-patient paste split per patient (see :mod:`chartcleaner.patients`)."""
    from .patients import split
    return [c.to_dict() for c in split(text)]


def clean_patients(text: str, *, preset: str | None = None, config: dict | None = None,
                   summarize: bool = False, client: Any = None, custom_dir: str | Path | None = None
                   ) -> list[dict]:
    """Split a patient list and clean each patient; ``summarize`` adds an on-device
    AI one-liner per patient (left empty when no local model answers)."""
    from .batch import run_texts
    from .patients import split

    cfg = _config(config, preset)
    one_liner = None
    if summarize:
        from .summarizer import summarize as summarize_chart
        llm_cfg = {**cfg, "local_llm": {**(cfg.get("local_llm") or {}),
                                        "prompt_preset": "one_liner", "custom_prompt": ""}}

        def one_liner(chart: str) -> str:
            return summarize_chart(chart, llm_cfg, client=client).text
    chunks = split(text)
    results = run_texts([(c.label, c.text) for c in chunks], {**cfg, "wrap_output": False},
                        custom_dir=custom_dir or store.CUSTOM_RULES_DIR, summarize=one_liner)
    return [{"label": r.name, "status": r.status, "error": r.error, "text": r.cleaned,
             "summary": r.summary, "reduction": r.reduction, "facts": r.facts,
             "facts_status": r.facts_status} for r in results]


def note(text: str, template: str, *, preset: str | None = None, clean_first: bool = True,
         record: bool = True, source: str = "api", config: dict | None = None) -> dict:
    """Clean ``text`` and fill note template ``template`` (see note_templates)."""
    from .note_templates import render

    cfg = _config(config, preset)
    chart = _cleaned(text, cfg, record, source) if clean_first else text
    return {"template": template, "text": render(template, chart, cfg)}


def _unwrap(text: str) -> str:
    from .delta_engine import _WRAPPER
    m = _WRAPPER.fullmatch(text.strip())
    return m.group("body") if m else text


def _slim_stages(result: RunResult) -> list[dict]:
    return [{"id": s.id, "label": s.label, "matches": s.matches,
             "chars_before": s.chars_before, "chars_after": s.chars_after,
             "skipped": s.skipped, "error": s.error}
            for s in result.stages]


def _record(result: RunResult, source: str, record: bool) -> None:
    if not record:
        return
    try:
        store.append_run(result.to_history_dict(source))
    except Exception:
        pass  # history must never break a clean
    try:
        for st in result.stages:
            tmap = st.details.get("token_map")
            if tmap:
                store.save_token_map(tmap, source)
    except Exception:
        pass  # same contract as the Clean page
    store.maybe_purge_old_data()


def _payload(result: RunResult, text: str) -> dict:
    return {
        "text": text,
        "summary": result.summary(),
        "chars_before": result.chars_before,
        "chars_after": result.chars_after,
        "reduction": result.reduction,
        "phi": result.phi_counts(),
        "warnings": list(result.warnings),
        "stages": _slim_stages(result),
        **({"fact_check": result.fact_check.to_dict()} if result.fact_check else {}),
    }


def clean(
    text: str,
    *,
    preset: str | None = None,
    fmt: str = "text",
    delta: bool = False,
    trends: bool = False,
    wrap: bool | None = None,
    audit: bool = False,
    source: str = "api",
    record: bool = True,
    config: dict | None = None,
    custom_dir: str | Path | None = None,
) -> dict:
    """Full clean, exactly as the Clean page runs it."""
    cfg = _config(config, preset)
    _check_format(fmt)  # before the (slow) run, not after it
    result = Pipeline(cfg, custom_dir=custom_dir or store.CUSTOM_RULES_DIR).run(text, wrap=wrap)
    # One trend report feeds both the block above the chart and payload["trends"].
    report = _trends_report(result.text, cfg) if trends else None
    out, delta_res = _compose(result.text, fmt, delta, report.to_text() if report else "", False)
    _record(result, source, record)
    payload = _payload(result, out)
    if trends:
        payload["trends"] = report.to_dict() if report else None
    if delta_res is not None:
        payload["delta"] = {"notes_found": delta_res.notes_found,
                            "compression_ratio": delta_res.compression_ratio}
    if audit:
        from .audit import run_audit
        findings = run_audit(result.text, cfg)
        payload["audit"] = {"skipped": findings.skipped, "counts": dict(findings.counts)}
    return payload


def _single_pass(mode: str, text: str, preset: str | None, source: str, record: bool,
                 config: dict | None) -> dict:
    cfg = _config(config, preset)
    result = Pipeline(cfg, mode=mode).run(text)
    _record(result, f"{source}:{mode}", record)
    payload = _payload(result, result.text)
    details = result.stages[0].details
    if mode == "abbreviations":
        payload["replacements"] = dict(details.get("replacements") or {})
    else:
        payload["expansions"] = dict(details.get("expansions") or {})
        payload["ambiguous"] = dict(details.get("ambiguous") or {})
    return payload


def abbreviate(text: str, *, preset: str | None = None, source: str = "api",
               record: bool = True, config: dict | None = None) -> dict:
    """Abbreviations-only pass (same as the Clean page's "Abbreviations only")."""
    return _single_pass("abbreviations", text, preset, source, record, config)


def expand(text: str, *, preset: str | None = None, source: str = "api",
           record: bool = True, config: dict | None = None) -> dict:
    """Expand abbreviations to full terms; ambiguous ones are listed, not changed."""
    return _single_pass("expand", text, preset, source, record, config)


def prompt(text: str, template: str, *, preset: str | None = None, clean_first: bool = True,
           record: bool = True, source: str = "api", config: dict | None = None) -> dict:
    """Clean ``text`` (unless ``clean_first`` is False) and wrap it in a prompt template."""
    from .prompt_templates import render

    cfg = _config(config, preset)
    chart = _cleaned(text, cfg, record, source) if clean_first else text
    return {"template": template, "text": render(template, chart, cfg)}


def ask(question: str, chart: str, *, preset: str | None = None,
        config: dict | None = None, client: Any = None) -> dict:
    """Grounded question about a chart via the on-device LLM (see chart_qa)."""
    from .chart_qa import ask_chart
    cfg = _config(config, preset)
    res = ask_chart(question, chart, cfg, client=client)
    return {
        "question": res.question,
        "answer": res.answer,
        "model": res.model,
        "grounding_score": res.grounding.grounding_score,
        "grounded": res.grounding.is_safe,
        "ungrounded_entities": list(res.grounding.ungrounded_entities),
        "facts": res.facts.to_dict() if res.facts is not None else None,
        "citations": [c.to_dict() for c in res.citations],
        "duration_ms": res.duration_ms,
    }


def restore(text: str, mapping: dict[str, str] | None = None) -> dict:
    """Put the real values back into text that carries ``[[Tn]]`` tokens.

    The PHI round-trip: clean with *Reversible tokenization* on, send the
    tokenized chart to an external AI, paste its reply here. ``mapping``
    defaults to the newest saved token map (data/tokens/, encrypted).
    """
    from .tokens import newest_token_map, untokenize

    source = "given"
    if mapping is None:
        found = newest_token_map()
        if found is None:
            return {"text": text, "restored": 0, "map": None,
                    "error": "No saved token map — clean a chart with Reversible tokenization on first."}
        path, mapping = found
        source = Path(path).name
    restored, n = untokenize(text, mapping)
    return {"text": restored, "restored": n, "map": source}


def timeline(text: str) -> list[dict]:
    """The notes in a cleaned chart with their offsets (see chartcleaner.timeline)."""
    from .timeline import build
    try:
        return [n.to_dict() for n in build(_unwrap(text))]
    except Exception:
        return []


_RESULT_CACHE: dict = {"cache": None}


def result_cache():
    """The process-wide :class:`~chartcleaner.stage_cache.StageCache` the app shares."""
    from .stage_cache import StageCache
    if _RESULT_CACHE["cache"] is None:
        _RESULT_CACHE["cache"] = StageCache()
    return _RESULT_CACHE["cache"]
