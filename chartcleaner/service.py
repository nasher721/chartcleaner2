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

__all__ = ["FORMATS", "load_active_config", "format_output", "clean", "abbreviate", "expand", "prompt", "ask"]

FORMATS = ("text", "markdown", "json", "xml")


def load_active_config(preset: str | None = None, config_path: str | Path | None = None) -> dict:
    """The saved config (or a named preset) — what the app would clean with."""
    if preset:
        return store.load_preset(preset)
    return load_config(config_path or store.CONFIG_PATH)


def format_output(text: str, fmt: str = "text", delta: bool = False) -> tuple[str, Any]:
    """Apply the optional copy-forward delta and structured format.

    Returns ``(text, delta_result)``; ``delta_result`` is None unless ``delta``.
    """
    if fmt not in FORMATS:
        raise ValueError(f"Unknown format {fmt!r}; expected one of {', '.join(FORMATS)}")
    delta_res = None
    out = text
    if delta:
        from .delta_engine import extract_note_deltas
        delta_res = extract_note_deltas(out)
        out = delta_res.compact_text
    if fmt != "text":
        from .section_parser import parse_clinical_sections
        parsed = parse_clinical_sections(out)
        out = {"markdown": parsed.to_markdown, "json": parsed.to_json,
               "xml": parsed.to_llm_xml}[fmt]()
    return out, delta_res


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
    }


def clean(
    text: str,
    *,
    preset: str | None = None,
    fmt: str = "text",
    delta: bool = False,
    wrap: bool | None = None,
    audit: bool = False,
    source: str = "api",
    record: bool = True,
    config: dict | None = None,
    custom_dir: str | Path | None = None,
) -> dict:
    """Full clean, exactly as the Clean page runs it."""
    cfg = config if config is not None else load_active_config(preset)
    result = Pipeline(cfg, custom_dir=custom_dir or store.CUSTOM_RULES_DIR).run(text, wrap=wrap)
    out, delta_res = format_output(result.text, fmt, delta)
    _record(result, source, record)
    payload = _payload(result, out)
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
    cfg = config if config is not None else load_active_config(preset)
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

    cfg = config if config is not None else load_active_config(preset)
    chart = clean(text, config=cfg, wrap=False, record=record, source=source)["text"] if clean_first else text
    return {"template": template, "text": render(template, chart, cfg)}


def ask(question: str, chart: str, *, preset: str | None = None,
        config: dict | None = None, client: Any = None) -> dict:
    """Grounded question about a chart via the on-device LLM (see chart_qa)."""
    from .chart_qa import ask_chart
    cfg = config if config is not None else load_active_config(preset)
    res = ask_chart(question, chart, cfg, client=client)
    return {
        "question": res.question,
        "answer": res.answer,
        "model": res.model,
        "grounding_score": res.grounding.grounding_score,
        "grounded": res.grounding.is_safe,
        "ungrounded_entities": list(res.grounding.ungrounded_entities),
        "duration_ms": res.duration_ms,
    }
