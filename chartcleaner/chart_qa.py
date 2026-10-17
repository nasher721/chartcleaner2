"""Grounded local Q&A over a cleaned chart (on-device LLM via Ollama).

Same privacy contract as :mod:`chartcleaner.summarizer`: app code calls only
:func:`ask_chart`; the LLM client is injectable so tests never touch the
network; the endpoint stays loopback-guarded by :class:`LocalLlmClient`. Every
answer is verified with ``verify_clinical_grounding`` against the chart the
question was asked about, so invented numbers/dates surface in the UI instead
of slipping into clinical use.

Conversation history is stateless server-side: the caller passes the prior
turns it wants contextualized, and only the last ``MAX_HISTORY_TURNS`` are
embedded in each prompt.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Protocol

from chartcleaner.context_router import DEFAULT_CONTEXT_TOKENS, pack_context, should_bookend
from chartcleaner.local_llm import GroundingResult, LocalLlmClient, verify_clinical_grounding
from chartcleaner.summarizer import (LlmUnavailableError, NoModelError, _check_facts, _cite,
                                     merge_llm_config, run_model)
from chartcleaner.token_estimator import estimate_tokens

__all__ = [
    "MAX_HISTORY_TURNS",
    "QA_INSTRUCTION",
    "QaResult",
    "QaTurn",
    "ask_chart",
    "build_qa_prompt",
    "filter_grounded_history",
]

MAX_HISTORY_TURNS = 3

QA_INSTRUCTION = (
    "Answer the question using ONLY facts explicitly present in the chart "
    "below. Never invent or extrapolate any medication, dose, lab value, "
    "date, or diagnosis. If the chart does not contain the answer, say "
    "exactly: The chart does not say."
)


class QaClient(Protocol):  # structural type for injection in tests
    def is_available(self) -> bool: ...
    def list_models(self) -> list[str]: ...
    def generate(self, prompt: str, model: str = ..., system: str | None = ...) -> str: ...


@dataclass
class QaTurn:
    question: str
    answer: str
    ungrounded: bool = False


@dataclass
class QaResult:
    question: str
    answer: str
    grounding: GroundingResult
    model: str
    duration_ms: int
    # clinical facts in the answer checked one by one (fact_check.OutputCheck)
    facts: Any = None
    # where each answer line comes from in the chart (citations.Citation)
    citations: list = field(default_factory=list)
    # context-window packing metadata (context_router.PackedContext)
    context: Any = None


def filter_grounded_history(
    history: list[QaTurn] | None,
    max_tokens: int = 1200,
) -> list[QaTurn]:
    """Filter out ungrounded turns ("context rot") and cap history token budget."""
    if not history:
        return []
    valid = [
        t for t in history
        if not getattr(t, "ungrounded", False)
        and t.answer.strip() != "The chart does not say."
    ]
    recent = valid[-MAX_HISTORY_TURNS:]
    if max_tokens <= 0:
        return recent
    kept: list[QaTurn] = []
    used = 0
    for turn in reversed(recent):
        cost = estimate_tokens(turn.question) + estimate_tokens(turn.answer) + 6
        if used + cost <= max_tokens or not kept:
            kept.append(turn)
            used += cost
        else:
            break
    kept.reverse()
    return kept


def build_qa_prompt(
    question: str,
    chart: str,
    history: list[QaTurn] | None = None,
    *,
    bookend: bool | None = None,
) -> str:
    """Compose the prompt; prior turns (last ``MAX_HISTORY_TURNS`` only) add context.

    When ``bookend`` is True (or ``None`` and ``chart`` exceeds
    ``BOOKEND_MIN_TOKENS``), the question and grounding constraint are repeated
    after the chart to counter Lost-in-the-Middle attention decay.
    """
    q_clean = question.strip()
    parts = [QA_INSTRUCTION, "", f"Question: {q_clean}"]
    turns = filter_grounded_history(history)
    if turns:
        parts.append("")
        parts.append("Earlier in this conversation (for context only):")
        for t in turns:
            parts.append(f"Q: {t.question.strip()}")
            parts.append(f"A: {t.answer.strip()}")
    parts.append("")
    parts.append(f"Chart:\n{chart}")
    if should_bookend(chart, bookend):
        parts.append("")
        parts.append("---")
        parts.append(
            f"Question: {q_clean}\n"
            "(Answer using ONLY facts explicitly present in the chart above. "
            "If the chart does not contain the answer, say: The chart does not say.)"
        )
    return "\n".join(parts)


def _resolve_model(client: QaClient, model: str) -> str:
    if model:
        return model
    models = client.list_models()
    if not models:
        raise NoModelError(
            "Local LLM is running but has no models pulled — "
            "run e.g. `ollama pull llama3.1` and retry."
        )
    return models[0]


def ask_chart(
    question: str,
    chart: str,
    cfg: dict,
    client: QaClient | None = None,
    history: list[QaTurn] | None = None,
    on_token: Any = None,
) -> QaResult:
    """Answer ``question`` about ``chart`` on-device and verify clinical grounding.

    ``on_token(text_so_far)`` is called as the answer streams in.
    """
    opts: dict[str, Any] = merge_llm_config(cfg)
    base_url: str = opts["base_url"]
    threshold = min(max(float(opts["grounding_threshold"]), 0.0), 100.0)
    try:
        context_tokens = int(opts.get("context_tokens") or DEFAULT_CONTEXT_TOKENS)
    except (TypeError, ValueError):
        context_tokens = DEFAULT_CONTEXT_TOKENS

    try:
        client = client or LocalLlmClient(base_url, timeout=120.0, num_ctx=context_tokens)
        if not client.is_available():
            raise LlmUnavailableError(base_url)
    except LlmUnavailableError:
        raise
    except ValueError as exc:  # loopback guard rejected the configured URL
        raise LlmUnavailableError(base_url, str(exc)) from exc
    except Exception as exc:
        raise LlmUnavailableError(base_url, str(exc)) from exc

    model = _resolve_model(client, str(opts["model"]))
    packed = pack_context(chart, context_tokens=context_tokens, query=question)
    prompt = build_qa_prompt(question, packed.text, history)

    started = time.monotonic()
    try:
        answer = run_model(client, prompt, model, on_token)
    except ValueError:
        raise
    except Exception as exc:
        raise LlmUnavailableError(base_url, f"generation failed: {exc}") from exc
    duration_ms = int((time.monotonic() - started) * 1000)

    if not answer.strip():
        raise ValueError("empty response from model")

    # Verify grounding, facts, and citations against the original chart so
    # character offsets match CLEAN_STATE["result_text"].
    grounding = verify_clinical_grounding(chart, answer, threshold=threshold)
    return QaResult(
        question=question.strip(),
        answer=answer,
        grounding=grounding,
        model=model,
        duration_ms=duration_ms,
        facts=_check_facts(chart, answer),
        citations=_cite(answer, chart),
        context=packed,
    )
