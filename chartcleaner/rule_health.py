"""Which regex rules earn their keep (Statistics page → Rule health).

Every run records ``rule_hits`` (rule id → matches) per regex stage in run
history. ``report`` lines those counts up against the rules in the current
config and flags:

* **never matched** — no hits in the last ``recent`` instrumented runs (at
  least ``min_runs`` of them): probably dead weight or a typo.
* **very broad** — on average it touches more than ``broad_share`` of a
  chart's lines: worth checking it isn't removing real content.
* **slow** — it took at least ``stages.SLOW_RULE_MS`` on two or more charts
  (``details["slow_rules"]``), or once past ``very_slow_ms``.

Every row also carries ``risks``: backtracking shapes found by
:mod:`chartcleaner.regex_risk`, so a rule can be fixed before it ever hangs.

Runs from before rule tracking existed carry no ``rule_hits`` and are ignored.
"""

from __future__ import annotations

from .regex_risk import risks as regex_risks
from .stages import rule_id

__all__ = ["RULE_SOURCES", "report"]

# stage id -> (config key, entries are [pattern, replacement] pairs?)
RULE_SOURCES = {
    "metadata_lines": ("emr_line_metadata", False),
    "boilerplate": ("boilerplate", False),
    "phi_patterns": ("epic_phi_patterns", True),
    "literal_replacements": ("literal_replacements", True),
    "learned_rules": ("learned_rules", True),
}


def _instrumented(run: dict) -> bool:
    return any("rule_hits" in (st.get("details") or {}) for st in run.get("stages") or [])


def report(cfg: dict, runs: list[dict], *, recent: int = 30, min_runs: int = 5,
           broad_share: float = 0.3, very_slow_ms: float = 200.0) -> list[dict]:
    runs = [r for r in runs if _instrumented(r)][-recent:]
    rows = []
    for sid, (key, pairs) in RULE_SOURCES.items():
        ran = []
        for run in runs:
            stage = next((st for st in run.get("stages") or [] if st.get("id") == sid), None)
            if stage and not stage.get("skipped") and not stage.get("error"):
                details = stage.get("details") or {}
                ran.append((run, details.get("rule_hits") or {}, details.get("slow_rules") or {}))
        for index, entry in enumerate(cfg.get(key) or []):
            pattern = entry[0] if pairs else entry
            if not isinstance(pattern, str):
                continue
            rid = rule_id(pattern)
            hits = [(run, h.get(rid, 0)) for run, h, _slow in ran]
            timings = [float(slow[rid]) for _run, _h, slow in ran if rid in slow]
            total = sum(n for _r, n in hits)
            last = max((r.get("ts", "") for r, n in hits if n), default="")
            shares = [n / max(1, int(r.get("lines_before") or 0)) for r, n in hits]
            avg_share = sum(shares) / len(shares) if shares else 0.0
            if timings and (len(timings) >= 2 or max(timings) >= very_slow_ms) and len(hits) < min_runs:
                status = "slow"
            elif len(hits) < min_runs:
                status = "not enough runs yet"
            elif total == 0:
                status = "never matched"
            elif avg_share > broad_share:
                status = "very broad"
            elif len(timings) >= 2 or any(t >= very_slow_ms for t in timings):
                status = "slow"
            else:
                status = "ok"
            rows.append({"stage": sid, "key": key, "index": index, "pattern": pattern,
                         "runs": len(hits), "hits": total, "last_hit": last,
                         "avg_share": round(avg_share, 3), "status": status,
                         "max_ms": max(timings, default=0.0),
                         "risks": regex_risks(pattern)})
    order = {"very broad": 0, "slow": 1, "never matched": 2, "ok": 3, "not enough runs yet": 4}
    return sorted(rows, key=lambda r: (order[r["status"]], r["stage"], r["index"]))
