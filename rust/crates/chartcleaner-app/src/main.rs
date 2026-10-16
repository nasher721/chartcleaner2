//! `chartcleaner` CLI + local web UI & REST API server.

mod server;

use std::collections::BTreeMap;
use std::io::{IsTerminal, Read, Write};
use std::path::{Path, PathBuf};
use std::time::Instant;

use anyhow::{Context, Result};
use clap::Parser;
use rayon::prelude::*;
use walkdir::WalkDir;

use chartcleaner_core::{
    delta::extract_note_deltas,
    format_output,
    insights::extract_insights,
    untokenize, Config, Pipeline, BUILTIN_STAGE_IDS,
};

const EMBEDDED_SAMPLE_CHART: &str = include_str!("../../../../sample_chart.txt");

#[derive(Parser, Debug)]
#[command(
    name = "chartcleaner",
    version,
    about = "Ultra-fast, production-grade Medical Chart Cleaner & De-identifier (Rust)"
)]
struct Cli {
    /// Optional input file or directory to clean. If omitted, uses clipboard (or stdin if piped).
    input: Option<PathBuf>,

    /// Optional output file or directory.
    #[arg(short, long)]
    output: Option<PathBuf>,

    /// Path to config.json (defaults to ./config.json or embedded default_config.json).
    #[arg(short, long)]
    config: Option<PathBuf>,

    /// Read chart text from standard input.
    #[arg(long)]
    stdin: bool,

    /// Write cleaned chart text to standard output.
    #[arg(long)]
    stdout: bool,

    /// Clean text directly from and to the system clipboard.
    #[arg(long)]
    clipboard: bool,

    /// Cleaning mode: 'clean' or 'expand' (expand medical abbreviations).
    #[arg(long, default_value = "clean")]
    mode: String,

    /// Output format: text, markdown, xml, json, smartphrase.
    #[arg(short, long, default_value = "text")]
    format: String,

    /// Enable reversible [[Tn]] PHI tokenization.
    #[arg(long)]
    tokenize: bool,

    /// Reverse [[Tn]] tokens using the provided JSON token map file.
    #[arg(long)]
    untoken_map: Option<PathBuf>,

    /// Append chronological cross-note delta timeline.
    #[arg(long)]
    delta: bool,

    /// Print extracted clinical insights JSON to stderr (or stdout with --insights-only).
    #[arg(long)]
    insights: bool,

    /// Print only extracted clinical insights JSON to stdout.
    #[arg(long)]
    insights_only: bool,

    /// Disable NLP PHI redaction for this run.
    #[arg(long)]
    no_nlp: bool,

    /// Enable all clinical compactors (labs, meds, vitals, neuro, mar, flowsheets, hospital_day, imaging).
    #[arg(long)]
    compact_all: bool,

    /// Run self-diagnostics (--doctor).
    #[arg(long)]
    doctor: bool,

    /// Run performance benchmark on sample_chart.txt.
    #[arg(long)]
    bench: bool,

    /// Launch local loopback web UI and REST API server.
    #[arg(long)]
    serve: bool,

    /// Port for --serve (bound strictly to 127.0.0.1).
    #[arg(long, default_value_t = 8080)]
    port: u16,
}

fn resolve_config(cli_path: Option<&Path>) -> Result<(Config, PathBuf)> {
    if let Some(p) = cli_path {
        let cfg = chartcleaner_core::load_config(p)?;
        return Ok((cfg, p.to_path_buf()));
    }
    let cwd_cfg = PathBuf::from("config.json");
    if cwd_cfg.exists() {
        let cfg = chartcleaner_core::load_config(&cwd_cfg)?;
        return Ok((cfg, cwd_cfg));
    }
    let parent_cfg = PathBuf::from("../config.json");
    if parent_cfg.exists() {
        let cfg = chartcleaner_core::load_config(&parent_cfg)?;
        return Ok((cfg, parent_cfg));
    }
    Ok((chartcleaner_core::load_default_config(), cwd_cfg))
}

fn run_doctor(cfg: &Config, cfg_path: &Path) -> Result<()> {
    println!("=== ChartCleaner Rust Doctor ===");
    println!("Engine version : {}", env!("CARGO_PKG_VERSION"));
    println!("Config path    : {} (exists={})", cfg_path.display(), cfg_path.exists());
    println!("Builtin stages : {}", BUILTIN_STAGE_IDS.len());

    let t0 = Instant::now();
    let pipeline = Pipeline::new(cfg.clone(), None);
    let res = pipeline.run(EMBEDDED_SAMPLE_CHART, true, "clean");
    let elapsed = t0.elapsed();

    println!(
        "Self-test clean: OK ({} -> {} chars, {:.2}% reduction in {:.2} ms)",
        res.original_chars,
        res.cleaned_chars,
        res.reduction_pct(),
        elapsed.as_secs_f64() * 1000.0
    );
    if let Some(fc) = &res.fact_check {
        println!(
            "Fact-check     : status={}, kept={}/{}, unexpected_losses={}, meaning_shifts={}",
            fc.status,
            fc.kept,
            fc.total,
            fc.lost_unexpected,
            fc.meaning_flags
        );
    }
    Ok(())
}

fn run_benchmark(cfg: &Config) -> Result<()> {
    let mut bench_cfg = cfg.clone();
    chartcleaner_core::set_stage_enabled(&mut bench_cfg, "nlp_redaction", true);
    let pipeline = Pipeline::new(bench_cfg, None);

    // Warmup
    let _ = pipeline.run(EMBEDDED_SAMPLE_CHART, false, "clean");

    let iterations = 200;
    let mut durations_ms = Vec::with_capacity(iterations);
    let t_start = Instant::now();
    for _ in 0..iterations {
        let t0 = Instant::now();
        let _ = pipeline.run(EMBEDDED_SAMPLE_CHART, false, "clean");
        durations_ms.push(t0.elapsed().as_secs_f64() * 1000.0);
    }
    let total_sec = t_start.elapsed().as_secs_f64();
    durations_ms.sort_by(|a, b| a.partial_cmp(b).unwrap());

    let p50 = durations_ms[iterations / 2];
    let p95 = durations_ms[(iterations * 95) / 100];
    let p99 = durations_ms[(iterations * 99) / 100];
    let mean = durations_ms.iter().sum::<f64>() / iterations as f64;
    let charts_per_sec = iterations as f64 / total_sec;
    let mb_per_sec =
        (EMBEDDED_SAMPLE_CHART.len() as f64 * iterations as f64 / (1024.0 * 1024.0)) / total_sec;

    println!("=== ChartCleaner Rust Benchmark ({iterations} iterations) ===");
    println!(
        "Chart size     : {} bytes ({} lines)",
        EMBEDDED_SAMPLE_CHART.len(),
        EMBEDDED_SAMPLE_CHART.lines().count()
    );
    println!("Mean latency   : {mean:.3} ms");
    println!("p50 latency    : {p50:.3} ms");
    println!("p95 latency    : {p95:.3} ms");
    println!("p99 latency    : {p99:.3} ms");
    println!("Throughput     : {charts_per_sec:.1} charts/sec ({mb_per_sec:.2} MB/sec)");
    Ok(())
}

fn process_single_text(raw: &str, pipeline: &Pipeline, cli: &Cli) -> Result<(String, chartcleaner_core::CleanResult)> {
    let result = pipeline.run(raw, false, &cli.mode);
    let mut formatted = format_output(&result.text, &cli.format)?;
    if cli.delta {
        let delta_res = extract_note_deltas(raw);
        if delta_res.notes_found > 1 {
            formatted.push_str("\n\n");
            formatted.push_str(&delta_res.compact_text);
        }
    }
    Ok((formatted, result))
}

#[tokio::main]
async fn main() -> Result<()> {
    let cli = Cli::parse();
    let (mut cfg, cfg_path) = resolve_config(cli.config.as_deref())?;

    if cli.no_nlp {
        chartcleaner_core::set_stage_enabled(&mut cfg, "nlp_redaction", false);
    }
    if cli.tokenize {
        chartcleaner_core::set_stage_enabled(&mut cfg, "tokenize_phi", true);
    }
    if cli.compact_all {
        for sid in [
            "hospital_day",
            "imaging_impression",
            "labs_compact",
            "meds_reconcile",
            "vitals_summary",
            "neuro_summary",
            "mar_summary",
            "flowsheet_tables",
        ] {
            chartcleaner_core::set_stage_enabled(&mut cfg, sid, true);
        }
    }

    if cli.doctor {
        return run_doctor(&cfg, &cfg_path);
    }

    if cli.bench {
        return run_benchmark(&cfg);
    }

    if cli.serve {
        let data_dir = std::env::var("CHARTCLEANER_DATA_DIR")
            .map(PathBuf::from)
            .unwrap_or_else(|_| PathBuf::from("data"));
        return server::run_server(
            cfg,
            cfg_path,
            data_dir,
            EMBEDDED_SAMPLE_CHART.to_string(),
            cli.port,
        )
        .await;
    }

    if let Some(map_path) = &cli.untoken_map {
        let map_raw = std::fs::read_to_string(map_path)
            .with_context(|| format!("Failed to read token map {}", map_path.display()))?;
        let token_map: BTreeMap<String, String> = serde_json::from_str(&map_raw)?;
        let input_text = if let Some(in_path) = &cli.input {
            std::fs::read_to_string(in_path)?
        } else {
            let mut buf = String::new();
            std::io::stdin().read_to_string(&mut buf)?;
            buf
        };
        let (restored, _) = untokenize(&input_text, &token_map, "T");
        if let Some(out_path) = &cli.output {
            std::fs::write(out_path, restored)?;
        } else {
            print!("{restored}");
        }
        return Ok(());
    }

    let pipeline = Pipeline::new(cfg, None);

    // 1. Directory or file input
    if let Some(in_path) = &cli.input {
        if in_path.is_dir() {
            let out_dir = cli
                .output
                .clone()
                .unwrap_or_else(|| in_path.join("cleaned_output"));
            std::fs::create_dir_all(&out_dir)?;

            let files: Vec<PathBuf> = WalkDir::new(in_path)
                .max_depth(2)
                .into_iter()
                .filter_map(|e| e.ok())
                .filter(|e| {
                    e.file_type().is_file()
                        && e.path().extension().and_then(|s| s.to_str()) == Some("txt")
                        && !e
                            .file_name()
                            .to_string_lossy()
                            .ends_with(".cleaned.txt")
                })
                .map(|e| e.into_path())
                .collect();

            let t0 = Instant::now();
            let results: Vec<Result<(PathBuf, usize, usize)>> = files
                .par_iter()
                .map(|file_path| {
                    let raw = std::fs::read_to_string(file_path)?;
                    let (formatted, res) = process_single_text(&raw, &pipeline, &cli)?;
                    let stem = file_path
                        .file_stem()
                        .and_then(|s| s.to_str())
                        .unwrap_or("chart");
                    let dest = out_dir.join(format!("{stem}.cleaned.txt"));
                    std::fs::write(&dest, formatted)?;
                    Ok((dest, res.original_chars, res.cleaned_chars))
                })
                .collect();

            let mut ok_count = 0usize;
            for r in results {
                match r {
                    Ok((dest, orig, cleaned)) => {
                        ok_count += 1;
                        eprintln!("Cleaned -> {} ({orig} -> {cleaned} chars)", dest.display());
                    }
                    Err(err) => eprintln!("Error: {err}"),
                }
            }
            eprintln!(
                "Batch completed: {ok_count}/{} files in {:.2} ms",
                files.len(),
                t0.elapsed().as_secs_f64() * 1000.0
            );
            return Ok(());
        } else {
            let raw = std::fs::read_to_string(in_path)
                .with_context(|| format!("Failed to read input file {}", in_path.display()))?;
            let (formatted, res) = process_single_text(&raw, &pipeline, &cli)?;

            if cli.insights_only {
                let ins = extract_insights(&res.text);
                println!("{}", serde_json::to_string_pretty(&ins)?);
                return Ok(());
            }
            if cli.insights {
                let ins = extract_insights(&res.text);
                eprintln!("{}", serde_json::to_string_pretty(&ins)?);
            }

            if cli.stdout {
                std::io::stdout().write_all(formatted.as_bytes())?;
            } else {
                let dest = cli.output.clone().unwrap_or_else(|| {
                    let stem = in_path
                        .file_stem()
                        .and_then(|s| s.to_str())
                        .unwrap_or("chart");
                    in_path.with_file_name(format!("{stem}.cleaned.txt"))
                });
                std::fs::write(&dest, &formatted)?;
                eprintln!(
                    "Cleaned {} -> {} ({} -> {} chars, {:.1}% reduction in {:.2} ms)",
                    in_path.display(),
                    dest.display(),
                    res.original_chars,
                    res.cleaned_chars,
                    res.reduction_pct(),
                    res.duration_ms
                );
            }
            return Ok(());
        }
    }

    // 2. Stdin mode (explicit --stdin or piped stdin when not --clipboard)
    if cli.stdin || (!cli.clipboard && !std::io::stdin().is_terminal()) {
        let mut raw = String::new();
        std::io::stdin().read_to_string(&mut raw)?;
        let (formatted, res) = process_single_text(&raw, &pipeline, &cli)?;
        if cli.insights_only {
            let ins = extract_insights(&res.text);
            println!("{}", serde_json::to_string_pretty(&ins)?);
            return Ok(());
        }
        if let Some(out_path) = &cli.output {
            std::fs::write(out_path, &formatted)?;
        } else {
            std::io::stdout().write_all(formatted.as_bytes())?;
        }
        return Ok(());
    }

    // 3. Default: Clipboard mode
    let mut cb = arboard::Clipboard::new().context("Failed to access system clipboard")?;
    let raw = cb
        .get_text()
        .context("Clipboard is empty or does not contain plain text")?;
    if raw.trim().is_empty() {
        anyhow::bail!("Clipboard is empty.");
    }
    let (formatted, res) = process_single_text(&raw, &pipeline, &cli)?;
    if cli.stdout {
        std::io::stdout().write_all(formatted.as_bytes())?;
    } else {
        cb.set_text(formatted)
            .context("Failed to write cleaned chart back to clipboard")?;
        println!(
            "Clipboard chart cleaned in {:.2} ms ({} -> {} chars, {:.1}% reduction)",
            res.duration_ms,
            res.original_chars,
            res.cleaned_chars,
            res.reduction_pct()
        );
    }
    Ok(())
}
