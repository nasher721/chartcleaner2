"""Compare local AI models on your own charts, by the same checks every summary gets.

Runs each installed model over a few charts with the same summary preset and
scores it on what the app already measures for every summary:

* **values not in the chart** — clinical values, drugs and code-status words
  the summary states but the chart never does (``fact_check.verify_output``);
  the number that matters most, so it ranks first;
* **grounding** — share of numbers/doses/dates found in the chart
  (``verify_clinical_grounding``);
* **cited lines** — share of summary lines that trace back to a chart line
  (``citations.cite``);
* **speed** — median seconds per summary.

Charts come from your known-good set (the cleaned output you approved),
falling back to the bundled sample chart. Everything runs through the
loopback-guarded local client; nothing leaves the computer. The comparison is
in memory only — no chart text or model output is stored.
"""

from __future__ import annotations

import copy
import statistics
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

__all__ = ["Trial", "ModelScore", "Comparison", "default_charts", "compare"]

MAX_CHARTS = 3
SAMPLE_CHART = Path(__file__).resolve().parent.parent / "sample_chart.txt"


@dataclass
class Trial:
    model: str
    chart: str                 # chart label
    ok: bool
    error: str = ""
    seconds: float = 0.0
    grounding: float = 0.0
    values_checked: int = 0
    unsupported: list[str] = field(default_factory=list)
    cited: int = 0
    lines: int = 0

    def to_dict(self) -> dict:
        return dict(self.__dict__)


@dataclass
class ModelScore:
    model: str
    trials: list[Trial] = field(default_factory=list)

    @property
    def ok_trials(self) -> list[Trial]:
        return [t for t in self.trials if t.ok]

    @property
    def errors(self) -> int:
        return len(self.trials) - len(self.ok_trials)

    @property
    def unsupported(self) -> int:
        return sum(len(t.unsupported) for t in self.ok_trials)

    @property
    def unsupported_per_summary(self) -> float:
        ok = self.ok_trials
        return round(self.unsupported / len(ok), 2) if ok else float("inf")

    @property
    def grounding(self) -> float:
        ok = self.ok_trials
        return round(statistics.mean(t.grounding for t in ok), 1) if ok else 0.0

    @property
    def citation_coverage(self) -> float:
        lines = sum(t.lines for t in self.ok_trials)
        return round(100.0 * sum(t.cited for t in self.ok_trials) / lines, 1) if lines else 0.0

    @property
    def median_seconds(self) -> float:
        ok = self.ok_trials
        return round(statistics.median(t.seconds for t in ok), 1) if ok else 0.0

    def rank_key(self) -> tuple:
        """Fewest invented values, then grounding, citations and speed; failures last."""
        return (not self.ok_trials, self.unsupported_per_summary, -self.grounding,
                -self.citation_coverage, self.median_seconds)

    def examples(self, limit: int = 5) -> list[str]:
        return list(dict.fromkeys(u for t in self.ok_trials for u in t.unsupported))[:limit]

    def to_dict(self) -> dict:
        return {"model": self.model, "runs": len(self.trials), "errors": self.errors,
                "unsupported": self.unsupported,
                "unsupported_per_summary": (None if not self.ok_trials
                                            else self.unsupported_per_summary),
                "grounding": self.grounding, "citation_coverage": self.citation_coverage,
                "median_seconds": self.median_seconds, "examples": self.examples(),
                "trials": [t.to_dict() for t in self.trials]}


@dataclass
class Comparison:
    preset: str
    charts: list[str]
    scores: list[ModelScore]

    @property
    def best(self) -> ModelScore | None:
        return self.scores[0] if self.scores and self.scores[0].ok_trials else None

    def to_text(self) -> str:
        if not self.scores:
            return "No models to compare."
        out = [f"Local model comparison — preset {self.preset!r}, {len(self.charts)} chart(s)",
               f"{'model':<28} {'not in chart':>12} {'grounding':>10} {'cited':>7} {'sec':>6}  errors"]
        for s in self.scores:
            if not s.ok_trials:
                out.append(f"{s.model:<28} {'—':>12} {'—':>10} {'—':>7} {'—':>6}  {s.errors}")
                continue
            out.append(f"{s.model:<28} {s.unsupported_per_summary:>12} {s.grounding:>9}% "
                       f"{s.citation_coverage:>6}% {s.median_seconds:>6}  {s.errors}")
        if self.best:
            out.append(f"Best on these charts: {self.best.model}")
        out.append("'not in chart' = values per summary the chart never states (lower is better).")
        return "\n".join(out)

    def to_dict(self) -> dict:
        return {"preset": self.preset, "charts": list(self.charts),
                "best": self.best.model if self.best else None,
                "scores": [s.to_dict() for s in self.scores], "text": self.to_text()}


def default_charts(limit: int = MAX_CHARTS) -> list[tuple[str, str]]:
    """``(label, cleaned chart)`` from the known-good set, else the bundled sample chart."""
    from . import regression_set
    charts: list[tuple[str, str]] = []
    try:
        for kg in regression_set.list_charts():
            if kg.mode == "clean" and kg.expected.strip():
                charts.append((kg.label, kg.expected))
    except Exception:
        charts = []
    if not charts and SAMPLE_CHART.exists():
        charts.append(("Sample chart", SAMPLE_CHART.read_text(encoding="utf-8")))
    return charts[-limit:]


def _trial(model: str, label: str, chart: str, cfg: dict, client: Any) -> Trial:
    from .summarizer import summarize
    started = time.monotonic()
    try:
        res = summarize(chart, cfg, client=client)
    except Exception as ex:  # one model failing never stops the comparison
        return Trial(model, label, ok=False, error=str(ex)[:200],
                     seconds=round(time.monotonic() - started, 1))
    facts = res.facts
    return Trial(model, label, ok=True, seconds=round(res.duration_ms / 1000, 1),
                 grounding=res.grounding.grounding_score,
                 values_checked=facts.total if facts is not None else 0,
                 unsupported=[u.display for u in facts.unsupported] if facts is not None else [],
                 cited=sum(1 for c in res.citations if c.found), lines=len(res.citations))


def compare(cfg: dict, models: list[str] | None = None,
            charts: list[tuple[str, str]] | None = None, *, preset: str = "clinical",
            client: Any = None,
            on_progress: Callable[[int, int, str], None] | None = None) -> Comparison:
    """Summarize each chart with each model and rank the models.

    ``models`` defaults to every model the endpoint lists; ``charts`` to
    :func:`default_charts`. ``on_progress(done, total, model)`` is called before
    each summary. Raises ``LlmUnavailableError`` / ``NoModelError`` when there is
    nothing to compare.
    """
    from .local_llm import LocalLlmClient
    from .summarizer import LlmUnavailableError, NoModelError, merge_llm_config

    opts = merge_llm_config(cfg)
    base_url = str(opts["base_url"])
    try:
        client = client or LocalLlmClient(base_url, timeout=180.0)
        up = client.is_available()
    except Exception as ex:  # the loopback guard rejected the URL, or the probe failed
        raise LlmUnavailableError(base_url, str(ex)) from ex
    if not up:
        raise LlmUnavailableError(base_url)
    names = list(dict.fromkeys(m for m in (models or client.list_models()) if m))
    if not names:
        raise NoModelError("No models installed — pull one first (e.g. `ollama pull llama3.1`).")
    charts = charts if charts is not None else default_charts()
    if not charts:
        raise ValueError("No charts to compare on — add a known-good chart first.")

    total = len(names) * len(charts)
    done = 0
    scores = []
    for model in names:
        run_cfg = copy.deepcopy(cfg)
        run_cfg["local_llm"] = {**opts, "model": model, "prompt_preset": preset, "custom_prompt": ""}
        score = ModelScore(model)
        for label, chart in charts:
            if on_progress:
                try:
                    on_progress(done, total, model)
                except Exception:
                    pass  # a display callback must never stop the run
            score.trials.append(_trial(model, label, chart, run_cfg, client))
            done += 1
        scores.append(score)
    if on_progress:
        try:
            on_progress(total, total, "")
        except Exception:
            pass
    scores.sort(key=ModelScore.rank_key)
    return Comparison(preset, [label for label, _ in charts], scores)
