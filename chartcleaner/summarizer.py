"""Local AI chart summarization service.

Turns a cleaned chart into a summary via an on-device LLM (Ollama or any
OpenAI-compatible local endpoint exposed by ``LocalLlmClient``), then verifies
that every clinical number/date in the output exists in the source chart
(``verify_clinical_grounding``). App code calls only :func:`summarize`; the
LLM client is injectable so tests never touch the network.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Protocol

from chartcleaner.local_llm import (
    GroundingResult,
    LocalLlmClient,
    verify_clinical_grounding,
)

__all__ = [
    "DEFAULT_LLM",
    "SUMMARY_PRESETS",
    "LlmUnavailableError",
    "NoModelError",
    "SummaryResult",
    "build_prompt",
    "merge_llm_config",
    "summarize",
]

DEFAULT_LLM: dict[str, Any] = {
    "base_url": "http://127.0.0.1:11434",
    "model": "",  # empty = first model the endpoint lists
    "prompt_preset": "clinical",
    "custom_prompt": "",
    "grounding_threshold": 90.0,
}

PRESET_INSTRUCTION = (
    "Use ONLY facts explicitly present in the chart. Do not invent or "
    "extrapolate any medication, dose, lab value, date, or diagnosis. "
    "If something is not in the chart, omit it."
)

SUMMARY_PRESETS: dict[str, str] = {
    "clinical": (
        f"{PRESET_INSTRUCTION}\n\nSummarize the chart below into concise "
        "sections with these exact headings:\n"
        "## Assessment\n## Medications\n## Labs & Vitals\n## Plan"
    ),
    "brief": (
        f"{PRESET_INSTRUCTION}\n\nSummarize the chart below in one short "
        "paragraph for a colleague who has not read it"
    ),
    "findings": (
        f"{PRESET_INSTRUCTION}\n\nList the key findings from the chart below "
        "as bullets, one finding per bullet, most important first"
    ),
}


class LlmUnavailableError(Exception):
    """No LLM endpoint answered at the configured loopback URL."""

    def __init__(self, base_url: str, detail: str = ""):
        self.base_url = base_url
        msg = f"No local LLM at {base_url}"
        if detail:
            msg += f" ({detail})"
        super().__init__(msg)


class NoModelError(Exception):
    """The endpoint is up but has no models pulled."""


@dataclass
class SummaryResult:
    text: str
    grounding: GroundingResult
    model: str
    preset: str
    duration_ms: int


class LlmClient(Protocol):  # structural type for injection in tests
    def is_available(self) -> bool: ...
    def list_models(self) -> list[str]: ...
    def generate(self, prompt: str, model: str = ..., system: str | None = ...) -> str: ...


def merge_llm_config(cfg: dict) -> dict[str, Any]:
    """Merge the ``local_llm`` config group over the defaults."""
    return {**DEFAULT_LLM, **(cfg.get("local_llm") or {})}


def build_prompt(preset_key: str, custom_prompt: str, chart: str) -> str:
    """Compose the full prompt; a non-empty custom prompt replaces the preset."""
    instruction = custom_prompt.strip() or SUMMARY_PRESETS.get(
        preset_key, SUMMARY_PRESETS["clinical"]
    )
    return f"{instruction}\n\nChart:\n{chart}"


def _resolve_model(client: LlmClient, model: str) -> str:
    if model:
        return model
    models = client.list_models()
    if not models:
        raise NoModelError(
            "Local LLM is running but has no models pulled — "
            "run e.g. `ollama pull llama3.1` and retry."
        )
    return models[0]


def summarize(
    chart: str,
    cfg: dict,
    client: LlmClient | None = None,
) -> SummaryResult:
    """Summarize ``chart`` on-device and attach clinical grounding verification."""
    opts = merge_llm_config(cfg)
    base_url: str = opts["base_url"]
    threshold = min(max(float(opts["grounding_threshold"]), 0.0), 100.0)

    try:
        client = client or LocalLlmClient(base_url, timeout=60.0)
        if not client.is_available():
            raise LlmUnavailableError(base_url)
    except LlmUnavailableError:
        raise
    except ValueError as exc:  # loopback guard rejected the configured URL
        raise LlmUnavailableError(base_url, str(exc)) from exc
    except Exception as exc:
        raise LlmUnavailableError(base_url, str(exc)) from exc

    model = _resolve_model(client, str(opts["model"]))
    prompt = build_prompt(
        str(opts["prompt_preset"]), str(opts["custom_prompt"]), chart
    )

    started = time.monotonic()
    try:
        text = client.generate(prompt, model=model)
    except ValueError:
        raise
    except Exception as exc:
        raise LlmUnavailableError(base_url, f"generation failed: {exc}") from exc
    duration_ms = int((time.monotonic() - started) * 1000)

    if not text.strip():
        raise ValueError("empty response from model")

    grounding = verify_clinical_grounding(chart, text, threshold=threshold)
    return SummaryResult(
        text=text,
        grounding=grounding,
        model=model,
        preset=str(opts["prompt_preset"]),
        duration_ms=duration_ms,
    )
