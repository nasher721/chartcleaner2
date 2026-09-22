"""Tests for the grounded chart Q&A service (no network, fake client)."""

import pytest

from chartcleaner.chart_qa import (
    MAX_HISTORY_TURNS,
    QA_INSTRUCTION,
    LlmUnavailableError,
    NoModelError,
    ask_chart,
    build_qa_prompt,
)
from chartcleaner.chart_qa import QaTurn

SOURCE = (
    "Patient started on Metoprolol 25 mg daily.\n"
    "Vitals: BP 138/76, HR 72 on 10/12/2026."
)


class FakeClient:
    def __init__(self, *, available=True, models=("llama3.1",), response="The chart does not say."):
        self.available = available
        self.models = list(models)
        self.response = response
        self.prompts: list[str] = []
        self.calls = 0

    def is_available(self) -> bool:
        return self.available

    def list_models(self) -> list[str]:
        return list(self.models)

    def generate(self, prompt: str, model: str = "", system: str | None = None) -> str:
        self.calls += 1
        self.prompts.append(prompt)
        return self.response


def test_build_qa_prompt_includes_instruction_question_and_chart():
    prompt = build_qa_prompt("What is the BP?", SOURCE)
    assert prompt.startswith(QA_INSTRUCTION)
    assert "Question: What is the BP?" in prompt
    assert prompt.endswith(f"Chart:\n{SOURCE}")


def test_build_qa_prompt_includes_prior_turns():
    history = [QaTurn("First question?", "First answer."), QaTurn("Second?", "Second answer.")]
    prompt = build_qa_prompt("Third?", SOURCE, history=history)
    assert "Earlier in this conversation" in prompt
    assert "Q: Second?" in prompt
    assert "A: Second answer." in prompt
    assert "Question: Third?" in prompt


def test_build_qa_prompt_caps_history():
    history = [QaTurn(f"q{i}?", f"a{i}.") for i in range(10)]
    prompt = build_qa_prompt("Now?", SOURCE, history=history)
    assert "q9?" in prompt                                 # newest turn kept
    assert "q6?" not in prompt                             # anything older trimmed


def test_ask_chart_returns_result_with_grounding():
    client = FakeClient(response="Metoprolol 25 mg daily.")
    res = ask_chart("What meds?", SOURCE, cfg={}, client=client)
    assert res.answer == "Metoprolol 25 mg daily."
    assert res.model == "llama3.1"
    assert res.duration_ms >= 0
    assert res.grounding.total_entities == 1  # "25 mg" (no date in the answer)
    assert res.grounding.is_safe
    assert client.calls == 1


def test_ask_chart_flags_ungrounded_answer():
    client = FakeClient(response="Started on Lisinopril 10 mg on 03/04/2025.")
    res = ask_chart("Meds?", SOURCE, cfg={}, client=client)
    assert not res.grounding.is_safe
    assert "10 mg" in res.grounding.ungrounded_entities


def test_ask_chart_unavailable_endpoint_raises():
    with pytest.raises(LlmUnavailableError):
        ask_chart("Q?", SOURCE, cfg={}, client=FakeClient(available=False))


def test_ask_chart_no_models_raises():
    with pytest.raises(NoModelError):
        ask_chart("Q?", SOURCE, cfg={}, client=FakeClient(models=()))


def test_ask_chart_model_from_config_wins():
    client = FakeClient(models=("llama3.1", "qwen2.5"))
    res = ask_chart("Q?", SOURCE, cfg={"local_llm": {"model": "qwen2.5"}}, client=client)
    assert res.model == "qwen2.5"


def test_ask_chart_empty_response_raises_value_error():
    with pytest.raises(ValueError, match="empty response"):
        ask_chart("Q?", SOURCE, cfg={}, client=FakeClient(response="   "))


def test_ask_chart_uses_config_threshold():
    client = FakeClient(response="Lisinopril 10 mg.")  # 0/1 grounded
    res = ask_chart("Meds?", SOURCE, cfg={"local_llm": {"grounding_threshold": 0}}, client=client)
    assert res.grounding.is_safe  # score 0% still passes a 0% threshold


def test_ask_chart_strips_question():
    client = FakeClient()
    res = ask_chart("  Q?  ", SOURCE, cfg={}, client=client)
    assert res.question == "Q?"
    assert client.prompts[0].endswith(f"Chart:\n{SOURCE}")


class _ExplodingClient(FakeClient):
    def is_available(self) -> bool:
        raise ValueError("not on the loopback interface")


def test_loopback_guard_value_error_surfaces_as_unavailable():
    # a client whose availability check raises the loopback-guard ValueError
    # must surface as LlmUnavailableError, not a raw traceback
    with pytest.raises(LlmUnavailableError):
        ask_chart("Q?", SOURCE, cfg={}, client=_ExplodingClient())
