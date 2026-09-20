"""Tests for the local AI summarizer service (no network, fake client)."""

import pytest

from chartcleaner.summarizer import (
    DEFAULT_LLM,
    SUMMARY_PRESETS,
    LlmUnavailableError,
    NoModelError,
    build_prompt,
    merge_llm_config,
    summarize,
)

SOURCE = (
    "Patient started on Metoprolol 25 mg daily.\n"
    "Vitals: BP 138/76, HR 72 on 10/12/2026."
)


class FakeClient:
    def __init__(self, *, available=True, models=("llama3.1",), response="OK summary."):
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


def test_merge_llm_config_overrides_defaults():
    merged = merge_llm_config({"local_llm": {"model": "qwen2.5", "grounding_threshold": 80}})
    assert merged["model"] == "qwen2.5"
    assert merged["grounding_threshold"] == 80
    assert merged["prompt_preset"] == DEFAULT_LLM["prompt_preset"]


def test_merge_llm_config_no_group_returns_defaults():
    assert merge_llm_config({}) == DEFAULT_LLM


def test_build_prompt_uses_preset_and_includes_chart():
    prompt = build_prompt("clinical", "", SOURCE)
    assert prompt.endswith(SOURCE)
    assert "Assessment" in prompt
    assert "ONLY facts" in prompt


def test_build_prompt_custom_overrides_preset():
    prompt = build_prompt("clinical", "  Just list meds.  ", SOURCE)
    assert prompt.startswith("Just list meds.")
    assert "Assessment" not in prompt


def test_build_prompt_unknown_preset_falls_back_to_clinical():
    prompt = build_prompt("nope", "", SOURCE)
    assert prompt == build_prompt("clinical", "", SOURCE)


def test_summarize_happy_path():
    res = summarize(SOURCE, {}, client=FakeClient(response="On metoprolol 25 mg."))
    assert res.text == "On metoprolol 25 mg."
    assert res.model == "llama3.1"
    assert res.preset == "clinical"
    assert res.duration_ms >= 0
    assert res.grounding.grounding_score == 100.0


def test_summarize_unavailable_raises_with_base_url():
    with pytest.raises(LlmUnavailableError, match="127.0.0.1:11434"):
        summarize(SOURCE, {}, client=FakeClient(available=False))


def test_summarize_no_models_raises_nomodelerror():
    with pytest.raises(NoModelError):
        summarize(SOURCE, {}, client=FakeClient(models=[]))


def test_summarize_empty_response_raises_valueerror():
    with pytest.raises(ValueError, match="empty response"):
        summarize(SOURCE, {}, client=FakeClient(response="   "))


def test_summarize_grounding_flags_fabricated_dose():
    fake = FakeClient(response="Increase to 125 mg daily.")
    res = summarize(SOURCE, {}, client=fake)
    assert res.grounding.is_safe is False
    assert any("125" in u for u in res.grounding.ungrounded_entities)


def test_summarize_model_fallback_and_custom_prompt_flow_through():
    fake = FakeClient()
    summarize(
        SOURCE,
        {"local_llm": {"model": "qwen2.5", "prompt_preset": "brief", "custom_prompt": "One line."}},
        client=fake,
    )
    assert fake.prompts[0].startswith("One line.")


def test_summarize_threshold_clamped():
    fake = FakeClient(response="HR 72 on 10/12/2026.")
    res = summarize(SOURCE, {"local_llm": {"grounding_threshold": 500}}, client=fake)
    assert isinstance(res.grounding.is_safe, bool)


def test_summarize_bad_endpoint_url_maps_to_unavailable():
    # Non-loopback base_url makes the real LocalLlmClient constructor raise
    # (loopback guard) before any network I/O; summarize maps it to
    # LlmUnavailableError so the UI can show a friendly message.
    with pytest.raises(LlmUnavailableError, match="loopback"):
        summarize(SOURCE, {"local_llm": {"base_url": "http://10.0.0.9:11434"}})


def test_presets_all_bake_in_grounded_instruction():
    for preset in SUMMARY_PRESETS.values():
        assert "ONLY facts" in preset
