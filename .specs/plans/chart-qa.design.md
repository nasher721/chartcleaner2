# Design: Ask this chart — grounded local Q&A on the Clean page

Extend the shipped local-AI stack (design: local-ai-summarizer) from one-shot
summaries to **conversational Q&A about the current cleaned chart**. The chart
never leaves the machine (same loopback-guarded Ollama endpoint) and every
answer is run through the existing `verify_clinical_grounding`, so a model that
invents a dose or date gets flagged in the UI.

Decided (2026-09-21): service module `chart_qa.py` mirroring `summarizer.py`
(app.py calls only `ask_chart`); UI = second expansion on the Clean page results
column; conversation history is kept client-side only (last 3 turns embedded in
each prompt, nothing persisted). Rejected: streaming (complexity, no clear value
for chart Q&A), server-side chat sessions / persistence (privacy: conversations
would outlive the chart in data/), reusing the summary textarea (muddles two
modes).

## Module: `chartcleaner/chart_qa.py`

Reuses `summarizer.merge_llm_config`, `LlmUnavailableError`, `NoModelError` and
`local_llm.verify_clinical_grounding` — one `local_llm` config group drives both
features (same base_url/model/grounding_threshold).

- `QA_INSTRUCTION` — answer-from-the-chart-only system line; if the chart does
  not contain the answer the model must say exactly *"The chart does not say."*
  (a checkable, ungrounded-number-free sentence).
- `@dataclass QaTurn: question, answer` — one prior exchange.
- `@dataclass QaResult: question, answer, grounding, model, duration_ms`.
- `build_qa_prompt(question, chart, history=None) -> str` — instruction,
  question, optional "Earlier in this conversation (for context only):" block
  (last `MAX_HISTORY_TURNS = 3` turns), then `Chart:\n{chart}`.
- `ask_chart(question, chart, cfg, client=None, history=None) -> QaResult` —
  same skeleton as `summarize`: merge config → client (injectable) → availability
  check → resolve model → timed generate → grounding verify. Raises the same
  error types so the Clean page handler can share them.

## Clean page panel

`ui.expansion("Ask this chart", icon="forum")` right after the summary
expansion inside `render_results()`. Widgets (dict `qa_refs`, same pattern as
`summary_refs`):

- log column rendering `CLEAN_STATE["qa"]` (list of turn dicts: question,
  answer, grounding score/safety): per turn a bold `Q:` label, the answer as
  `ui.markdown`, a grounding badge (green 100 / amber <100 / red unsafe / grey
  no-checkable-facts) and a copy button.
- question `ui.input` + Ask button + dots spinner; **Enter asks** (a
  `keydown.enter` DOM handler on the input).
- no second model select: Q&A answers on the model already chosen in the
  summary panel (same `local_llm.model` pref; falls back to the first listed
  model); the panel shows it as a caption instead of duplicating the control.
- `run_ask()` mirrors `run_summarize`: guard `running` flag + no-result, work in
  `run.io_bound`, append turn to `CLEAN_STATE["qa"]`, re-render log; shares the
  `LlmUnavailableError` / `NoModelError` notifications.

`do_clean_core` resets `CLEAN_STATE["qa"] = []` (new cleaned text invalidates
old answers — same reason `summary` is reset). appstate.CLEAN_STATE gains the
key so the NiceGUI test-harness namespaces stay shared.

## Tests (`tests/test_chart_qa.py`)

FakeClient pattern from test_summarizer: config merge, prompt shape (instruction
verbatim, chart at the end, history block appears with 3-turn cap, custom
history trimmed), unavailable endpoint / no models / empty response raise the
documented errors, ungrounded numbers in an answer lower the score and flip
`is_safe`, loopback-guard ValueError propagates.
