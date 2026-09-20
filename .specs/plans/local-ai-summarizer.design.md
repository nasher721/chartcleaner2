# Design: Local AI summarizer on Clean page

Wire the existing, tested `chartcleaner/local_llm.py` (Ollama client + clinical
grounding verification) into the app via a new service module and a summary
panel on the Clean page. All processing stays on-device.

Decided in brainstorm (2026-09-20): approach = service module `summarizer.py`
(app.py calls only this); UI = panel on Clean page, hidden until a clean run
exists; prompts = presets + custom textarea; errors degrade gracefully when
Ollama is absent.

Rejected alternatives: calling `LocalLlmClient` straight from app.py (bloats a
2100-line file, untestable UI logic); token streaming via `run.io_task`
(complexity, no clear value); chunked map-reduce summarization (YAGNI until
charts exceed model context); embedded llama.cpp (packaging burden).

## Module: `chartcleaner/summarizer.py`

```python
DEFAULT_LLM = {
    "base_url": "http://127.0.0.1:11434",
    "model": "",            # empty = first model Ollama lists
    "prompt_preset": "clinical",
    "custom_prompt": "",
    "grounding_threshold": 90.0,
}
```

- `SUMMARY_PRESETS`: `clinical` (structured Assessment / Medications /
  Labs & Vitals / Plan, "use only facts from source" baked in), `brief`
  (one-paragraph prose), `findings` (bulleted key findings).
- `build_prompt(preset_key, custom_prompt, chart) -> str` — a non-empty
  `custom_prompt` replaces the preset entirely.
- `@dataclass SummaryResult: text, grounding, model, preset, duration_ms`.
- `summarize(chart, cfg, client=None) -> SummaryResult`:
  1. merge `{**DEFAULT_LLM, **(cfg.get("local_llm") or {})}` (same pattern as
     the engine option groups).
  2. `client = client or LocalLlmClient(base_url, timeout=60)`;
     `is_available()` false → `LlmUnavailableError(base_url)`.
  3. model empty → `list_models()[0]`; empty list → `NoModelError`.
  4. `generate(prompt, model=model)`, timed.
  5. attach `verify_clinical_grounding(chart, text, threshold)`.
- `client` injectable so tests run with a fake and need no Ollama.
- Grounding logic stays in `local_llm.py` (already unit-tested).

## Clean page panel

Lives in the results column (rebuilt by `render_results()` per run), after the
stage table, before the audit card; appears only once a clean run exists.
`ui.expansion("Local AI summary", icon="psychology")`, closed by default.

- Model `ui.select` (from Ollama), preset `ui.select`
  (`clinical`/`brief`/`findings`/`custom`), Summarize button.
- Custom prompt `ui.textarea`, visible only for the `custom` preset.
- Output area + Copy summary button.
- Grounding row: badge chip (green ≥ threshold / amber / red) plus red chips
  listing each ungrounded number/date; clicking a chip copies it.

Behavior:

- Runs on `run.io_bound` with its own busy flag — cleaning and summarizing do
  not disable each other's buttons.
- Source text is the **cleaned** output (`CLEAN_STATE["result_text"]`).
- Result kept in `CLEAN_STATE["summary"]` so re-renders preserve it.
- Model/preset choices persist to the `local_llm` group in config.json via the
  existing `save_config_with_backup` flow. No Settings page entry (YAGNI).

## Error handling

- `LlmUnavailableError` and `NoModelError` are expected states: notify with
  actionable text (install Ollama / `ollama pull <model>`), never the
  traceback dialog.
- `urllib` timeouts wrap as `LlmUnavailableError` ("Ollama timed out after
  60s…"). Empty model output → `ValueError`. Everything else hits the
  existing `report_error` fallback.
- Grounding threshold clamped to 0–100; `verify_clinical_grounding` is total
  and never raises.

## Config & validation

- `DEFAULT_LLM` exported from summarizer.py; merged at call time. No pipeline
  stage changes → golden file and `test_options.py` unaffected.
- `config_validator.py` gains an optional-group entry so typos warn, not crash.

## Testing

New `tests/test_summarizer.py` with `FakeClient` (no network): preset prompt
contains source + "only facts"; custom prompt overrides; model fallback;
`NoModelError`; `LlmUnavailableError` carries base_url; `duration_ms > 0`;
grounding attached (fabricated dose scores < 100, ungrounded item listed);
config merge. `tests/test_pages.py`: panel renders after clean, button
disabled before it. No new dependencies; all 141 existing tests stay green.

## Docs

README: one bullet under the Clean page feature list. AGENTS.md: workspace
fact that Ollama is an optional runtime (`127.0.0.1:11434`) and absent on the
user's Mac by default.

## Out of scope (YAGNI)

Token streaming, store/stats integration, CLI `--summarize`, chunked
long-chart summarization, dedicated Settings section.
