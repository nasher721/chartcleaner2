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
from dataclasses import dataclass
from typing import Any, Protocol

from chartcleaner.local_llm import GroundingResult, LocalLlmClient, verify_clinical_grounding
from chartcleaner.summarizer import LlmUnavailableError, NoModelError, merge_llm_config

__all__ = [
    "MAX_HISTORY_TURNS",
    "QA_INSTRUCTION",
    "QaResult",
    "QaTurn",
    "ask_chart",
    "build_qa_prompt",
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


@dataclass
class QaResult:
    question: str
    answer: str
    grounding: GroundingResult
    model: str
    duration_ms: int


def build_qa_prompt(
    question: str,
    chart: str,
    history: list[QaTurn] | None = None,
) -> str:
    """Compose the prompt; prior turns (last ``MAX_HISTORY_TURNS`` only) add context."""
    parts = [QA_INSTRUCTION, "", f"Question: {question.strip()}"]
    turns = (history or [])[-MAX_HISTORY_TURNS:]
    if turns:
        parts.append("")
        parts.append("Earlier in this conversation (for context only):")
        for t in turns:
            parts.append(f"Q: {t.question.strip()}")
            parts.append(f"A: {t.answer.strip()}")
    parts.append("")
    parts.append(f"Chart:\n{chart}")
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
) -> QaResult:
    """Answer ``question`` about ``chart`` on-device and verify clinical grounding."""
    opts: dict[str, Any] = merge_llm_config(cfg)
    base_url: str = opts["base_url"]
    threshold = min(max(float(opts["grounding_threshold"]), 0.0), 100.0)

    try:
        client = client or LocalLlmClient(base_url, timeout=120.0)
        if not client.is_available():
            raise LlmUnavailableError(base_url)
    except LlmUnavailableError:
        raise
    except ValueError as exc:  # loopback guard rejected the configured URL
        raise LlmUnavailableError(base_url, str(exc)) from exc
    except Exception as exc:
        raise LlmUnavailableError(base_url, str(exc)) from exc

    model = _resolve_model(client, str(opts["model"]))
    prompt = build_qa_prompt(question, chart, history)

    started = time.monotonic()
    try:
        answer = client.generate(prompt, model=model)
    except ValueError:
        raise
    except Exception as exc:
        raise LlmUnavailableError(base_url, f"generation failed: {exc}") from exc
    duration_ms = int((time.monotonic() - started) * 1000)

    if not answer.strip():
        raise ValueError("empty response from model")

    grounding = verify_clinical_grounding(chart, answer, threshold=threshold)
    return QaResult(
        question=question.strip(),
        answer=answer,
        grounding=grounding,
        model=model,
        duration_ms=duration_ms,
    )
