"""The Statistics page (/stats)."""

from __future__ import annotations

from app_pages import common
from app_pages.common import *  # noqa: F401,F403 — shared imports and helpers


def render_rule_health(runs: list[dict], summary: dict | None = None) -> None:
    """Statistics → Rule health: rules that never match, touch too much, or cost facts."""
    try:
        rows = rule_health_report(load_config(common.CONFIG_PATH), runs)
    except Exception:
        return
    summary = summary or store.summarize(runs)
    never = sum(r["status"] == "never matched" for r in rows)
    broad = sum(r["status"] == "very broad" for r in rows)
    slow = sum(r["status"] == "slow" for r in rows)
    risky = sum(bool(r.get("risks")) for r in rows)
    losses = summary.get("fact_losses_by_stage") or {}
    with ui.card().classes("w-full gap-2").mark("rule-health"):
        with ui.row().classes("w-full items-center gap-2"):
            ui.icon("health_and_safety").classes("text-2xl text-primary")
            ui.label("Rule health").classes("text-lg font-semibold")
        tiles = ui.row().classes("gap-3 flex-wrap")
        stat_chip(tiles, "rules that never fire", str(never), "grey" if not never else "orange")
        stat_chip(tiles, "very broad rules", str(broad), "green" if not broad else "orange")
        stat_chip(tiles, "slow rules", str(slow), "green" if not slow else "orange")
        stat_chip(tiles, "rules that could hang", str(risky), "green" if not risky else "red")
        stat_chip(tiles, "charts with clinical values flagged",
                  f"{summary.get('fact_flagged', 0)} / {summary.get('fact_runs', 0)}",
                  "green" if not summary.get("fact_flagged") else "red")
        if losses:
            ui.label("Stages behind removed clinical values (across your history):") \
                .classes("text-sm font-semibold")
            with ui.row().classes("gap-2 flex-wrap"):
                for sid, n in list(losses.items())[:8]:
                    ui.badge(f"{STAGE_LABELS.get(sid, sid)} · {n}", color="red").props("outline")
    flagged = [r for r in rows if r["status"] in ("never matched", "very broad", "slow")
               or r.get("risks")]
    title = (f"Rule details — {len(flagged)} rule(s) to review" if flagged
             else "Rule details — no problems found")
    with ui.expansion(title, icon="rule", value=bool(flagged)).classes("w-full"):
        ui.label("From your recent runs: rules that never match are probably dead weight; rules "
                 "that touch over 30% of a chart's lines may be removing real content; slow "
                 "rules cost time on every clean; ⚠ marks a pattern shape that can backtrack "
                 "for minutes on a long line.") \
            .classes("text-xs opacity-70")
        holder = ui.column().classes("w-full gap-1")

        def remove(row: dict) -> None:
            cfg = load_config(common.CONFIG_PATH)
            entries = cfg.get(row["key"]) or []
            current = [e[0] if isinstance(e, list) else e for e in entries]
            if row["pattern"] not in current:
                ui.notify("That rule changed since this report; refresh the page.", type="warning")
                return
            entries.pop(current.index(row["pattern"]))
            save_config_with_backup(cfg)
            ui.notify("Rule removed (a backup of the previous rules was kept).", type="positive")
            draw()

        def draw() -> None:
            holder.clear()
            current = rule_health_report(load_config(common.CONFIG_PATH), runs)
            with holder:
                for row in [r for r in current
                            if r["status"] != "not enough runs yet" or r.get("risks")][:60]:
                    with ui.row().classes("w-full items-center gap-2 no-wrap border-b pb-1"):
                        color = {"very broad": "orange", "never matched": "grey",
                                 "slow": "orange"}.get(row["status"], "green")
                        ui.badge(row["status"] + (f" · {row['max_ms']:.0f} ms" if row.get("max_ms") else ""),
                                 color=color)
                        if row.get("risks"):
                            ui.icon("warning", color="red").tooltip("; ".join(row["risks"]))
                        ui.label(STAGE_LABELS.get(row["stage"], row["stage"])).classes("text-xs w-40")
                        ui.label(row["pattern"][:90]).classes("text-xs cc-mono flex-grow break-all")
                        ui.label(f"{row['hits']} hits / {row['runs']} runs").classes("text-xs w-32")
                        if row["status"] == "never matched":
                            ui.button("Remove", icon="delete",
                                      on_click=lambda r=row: confirm_dialog(
                                          f"Remove this {STAGE_LABELS.get(r['stage'], r['stage'])} rule?",
                                          lambda: remove(r))).props("flat dense color=negative")
                if not current or all(r["status"] == "not enough runs yet" for r in current):
                    ui.label("Not enough runs yet — clean a few more charts.").classes("text-sm")

        draw()


def render_stage_noise(s: dict) -> None:
    """Which stages earn their keep: characters each removed, per day and overall."""
    share = s.get("stage_share") or []
    days = s.get("days") or []
    if not share:
        return
    with ui.card().classes("w-full gap-2").mark("stage-noise"):
        ui.label("Noise removed by each stage over time").classes("text-lg font-semibold")
        ui.label("Characters each stage took out, per day (top six stages; the rest are "
                 "grouped). A stage that never removes anything is a candidate to switch off.") \
            .classes("text-xs opacity-70")
        top = [row["label"] for row in share[:6]]
        by_day = s.get("stage_by_day") or {}
        if len(days) > 1:
            series = [{"name": label, "type": "bar", "stack": "removed",
                       "data": [by_day.get(d, {}).get(label, 0) for d in days]} for label in top]
            other = [sum(n for lbl, n in by_day.get(d, {}).items() if lbl not in top) for d in days]
            if any(other):
                series.append({"name": "Other stages", "type": "bar", "stack": "removed", "data": other})
            ui.echart({
                "backgroundColor": "transparent", "tooltip": {"trigger": "axis"},
                "legend": {"type": "scroll", "data": [x["name"] for x in series]},
                "grid": {"left": 64, "right": 24, "top": 44, "bottom": 32},
                "xAxis": {"type": "category", "data": days},
                "yAxis": {"type": "value", "name": "Chars"},
                "series": series,
            }).classes("w-full h-72")
        cols = [{"name": "label", "label": "Stage", "field": "label", "align": "left"},
                {"name": "removed", "label": "Characters removed", "field": "removed", "sortable": True},
                {"name": "share", "label": "% of all removed", "field": "share", "sortable": True},
                {"name": "runs", "label": "Runs where it removed text", "field": "runs", "sortable": True},
                {"name": "avg", "label": "Avg per such run", "field": "avg_per_run"}]
        ui.table(columns=cols, rows=[{**r, "removed": f"{r['removed']:,}", "share": f"{r['share']}%"}
                                     for r in share], row_key="label", pagination=10) \
            .classes("w-full").props("flat dense")


def stats_page():
    runs = store.load_runs()
    s = store.summarize(runs)

    with shell("Statistics — how much has been cleaned", "stats"):
        if not runs:
            with ui.card().classes("w-full items-center py-10"):
                ui.icon("insights").classes("text-5xl opacity-30")
                ui.label("No runs recorded yet.").classes("text-lg")
                ui.label("Clean a chart on the Clean page — every run is tracked here, "
                         "including per-stage detail.").classes("opacity-60 text-sm")
            return

        cards = ui.row().classes("gap-3 flex-wrap")
        stat_chip(cards, "total runs", f"{s['runs']:,}")
        stat_chip(cards, "characters removed", f"{s['chars_removed']:,}", "green")
        stat_chip(cards, "avg reduction", f"{s['avg_reduction']:+.1f}%")
        stat_chip(cards, "PHI items redacted", f"{s['phi']:,}", "red")
        stat_chip(cards, "words removed", f"{max(0, s['words_before'] - s['words_after']):,}", "indigo")
        stat_chip(cards, "avg time / run", f"{s['avg_duration_ms']:.0f} ms", "blue-grey")

        render_rule_health(runs, s)

        if s["days"]:
            days = s["days"]
            ui.echart({
                "backgroundColor": "transparent",
                "tooltip": {"trigger": "axis"},
                "legend": {"data": ["Runs", "Characters removed", "% removed"]},
                "grid": {"left": 64, "right": 96, "top": 44, "bottom": 32},
                "xAxis": {"type": "category", "data": days},
                "yAxis": [
                    {"type": "value", "name": "Runs", "minInterval": 1},
                    {"type": "value", "name": "Chars", "splitLine": {"show": False}},
                    {"type": "value", "name": "%", "position": "right", "offset": 56,
                     "min": 0, "max": 100, "splitLine": {"show": False}},
                ],
                "series": [
                    {"name": "Runs", "type": "bar", "data": [s["by_day"][d]["runs"] for d in days],
                     "itemStyle": {"color": "#5c6bc0"}},
                    {"name": "Characters removed", "type": "line", "yAxisIndex": 1, "smooth": True,
                     "data": [s["by_day"][d]["chars_removed"] for d in days],
                     "areaStyle": {"opacity": 0.15}, "itemStyle": {"color": "#66bb6a"}},
                    {"name": "% removed", "type": "line", "yAxisIndex": 2, "smooth": True,
                     "data": [s["by_day"][d].get("reduction", 0) for d in days],
                     "itemStyle": {"color": "#f59e0b"}, "lineStyle": {"type": "dashed"}},
                ],
            }).classes("w-full h-72")

        with ui.row().classes("w-full gap-4 flex-wrap"):
            if s["top_stages"]:
                labels = [k for k, _ in s["top_stages"]]
                vals = [v for _, v in s["top_stages"]]
                ui.echart({
                    "backgroundColor": "transparent",
                    "title": {"text": "Which stages clean the most", "textStyle": {"fontSize": 14}},
                    "tooltip": {},
                    "grid": {"left": 210, "right": 40, "top": 36, "bottom": 24},
                    "xAxis": {"type": "value"},
                    "yAxis": {"type": "category", "data": labels[::-1],
                              "axisLabel": {"width": 190, "overflow": "truncate"}},
                    "series": [{"type": "bar", "data": vals[::-1], "itemStyle": {"color": "#26a69a"}}],
                }).classes("flex-grow min-w-[430px] h-72")
            if s["phi_by_type"]:
                ui.echart({
                    "backgroundColor": "transparent",
                    "title": {"text": "PHI redactions by type", "textStyle": {"fontSize": 14}},
                    "tooltip": {"trigger": "item"},
                    "legend": {"orient": "vertical", "right": 0, "top": "middle", "textStyle": {"fontSize": 11}},
                    "series": [{"type": "pie", "radius": ["35%", "70%"], "center": ["40%", "55%"],
                                "data": [{"name": k, "value": v} for k, v in s["phi_by_type"].items()]}],
                }).classes("flex-grow min-w-[380px] h-72")

        render_stage_noise(s)

        cols = [
            {"name": "ts", "label": "When", "field": "ts", "align": "left", "sortable": True},
            {"name": "source", "label": "Source", "field": "source", "align": "left"},
            {"name": "before", "label": "Chars before", "field": "chars_before", "sortable": True},
            {"name": "after", "label": "Chars after", "field": "chars_after"},
            {"name": "reduction", "label": "Reduction", "field": "reduction", "sortable": True},
            {"name": "ms", "label": "Time (ms)", "field": "duration_ms"},
        ]
        rows = [{
            "ts": r.get("ts", ""), "source": r.get("source", ""), "chars_before": r.get("chars_before", 0),
            "chars_after": r.get("chars_after", 0), "reduction": f"{r.get('reduction', 0):+.1f}%",
            "duration_ms": r.get("duration_ms", 0),
        } for r in reversed(runs[-200:])]
        ui.table(columns=cols, rows=rows, row_key="ts", pagination=10).classes("w-full").props("flat")

        with ui.row().classes("gap-2"):
            ui.button("Refresh", icon="refresh", on_click=lambda: ui.navigate.to("/stats")).props("flat")
            ui.button("Open data folder", icon="folder",
                      on_click=lambda: open_folder(store.DATA_DIR)).props("flat")
            ui.button("Clear history", icon="delete_forever",
                      on_click=lambda: confirm_dialog("Delete the entire cleaning history?", store.clear_runs)) \
                .props("flat color=negative")

        # ---- evaluation: how well does the current rule set actually clean? ----
        eval_state: dict = {"running": False}

        with ui.expansion("Evaluation — recall report card", icon="verified").classes("w-full"):
            ui.label("Generates synthetic charts with known PHI, runs your current rules on them, "
                     "and reports how many PHI items were actually removed. A real exam, not a vibe.") \
                .classes("text-xs opacity-70")
            eval_holder = ui.column().classes("w-full gap-2")
            with eval_holder:
                last = load_last_evaluation()
                if last:
                    ui.label(f"Last run: {last.get('ts', '')} — recall {last.get('recall', 0)}% "
                             f"over {last.get('samples', 0)} sample chart(s).") \
                        .classes("text-sm opacity-80")

            n_sel = ui.number("Charts to generate", value=25, min=5, max=200, format="%.0f").classes("w-44")
            eval_btn = ui.button("Run evaluation", icon="play_arrow")

            async def run_eval() -> None:
                if eval_state["running"]:
                    return
                eval_state["running"] = True
                eval_btn.set_enabled(False)

                def work():
                    cfg = load_config(common.CONFIG_PATH)
                    samples = generate_benchmark(n=int(n_sel.value or 25))
                    rep = evaluate_samples(cfg, samples, custom_dir=common.CUSTOM_DIR)
                    save_evaluation(rep)
                    return rep

                try:
                    rep = await run.io_bound(work)
                    eval_holder.clear()
                    with eval_holder:
                        stat_chip_row = ui.row().classes("gap-3 flex-wrap")
                        stat_chip(stat_chip_row, "overall recall", f"{rep['recall']}%",
                                  "green" if rep["recall"] >= 90 else "orange")
                        stat_chip(stat_chip_row, "safety F2-score", f"{rep.get('f2', 0.0)}%", "purple")
                        cp_rate = rep.get("clinical_preservation", {}).get("preservation_rate", 100.0)
                        stat_chip(stat_chip_row, "clinical terms kept", f"{cp_rate}%", "teal")
                        eq_ratio = rep.get("fairness", {}).get("disparate_impact_ratio", 1.0)
                        stat_chip(stat_chip_row, "demographic equity", f"{eq_ratio}x", "blue")
                        stat_chip(stat_chip_row, "PHI items caught",
                                  f"{rep['caught']}/{rep['items']}", "indigo")
                        for t, b in list(rep["by_type"].items())[:9]:
                            stat_chip(stat_chip_row, t, f"{b['recall']}%",
                                      "green" if b["recall"] >= 90 else "red")

                        demog = rep.get("demographics", {})
                        if demog:
                            ui.label("Demographic Fairness & Cohort Breakdown:").classes("text-sm font-semibold mt-2")
                            cohort_row = ui.row().classes("gap-2 flex-wrap")
                            for cname, cstat in sorted(demog.items()):
                                stat_chip(cohort_row, cname.replace("_", " ").title(), f"{cstat.get('recall', 0)}%",
                                          "green" if cstat.get("recall", 0) >= 85 else "orange")

                        if rep["missed"]:
                            ui.label("Missed items (tighten these rules — see the packs on the "
                                     "Pipeline page):").classes("text-sm font-semibold mt-1")
                            for m in rep["missed"][:12]:
                                ui.label(f"• [{m['type']}] {m['value']}  ({m['sample']})") \
                                    .classes("text-xs cc-mono opacity-80")
                        ui.label("Synthetic charts are approximations — a high recall is encouraging, "
                                 "not a guarantee.").classes("text-xs opacity-60")
                    ui.notify("Evaluation complete.", type="positive")
                except Exception as e:
                    report_error("Evaluation failed", e)
                finally:
                    eval_state["running"] = False
                    eval_btn.set_enabled(True)

            eval_btn.on("click", lambda: asyncio.get_running_loop().create_task(run_eval()))


# ===========================================================================
# PAGE: Custom Scripts
# ===========================================================================
