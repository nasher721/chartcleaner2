//! Local loopback Axum web UI and `/api/v1/*` REST server with DNS-rebinding & Origin guards.

use std::net::SocketAddr;
use std::path::{Path, PathBuf};
use std::sync::{Arc, RwLock};

use axum::{
    extract::{DefaultBodyLimit, Request, State},
    http::{header, HeaderMap, StatusCode},
    middleware::{self, Next},
    response::{Html, IntoResponse, Json, Response},
    routing::{get, post},
    Router,
};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};

use chartcleaner_core::{
    abbreviations, format_output, insights::extract_insights, Config, Pipeline, BUILTIN_STAGE_IDS,
};

const MAX_BODY_BYTES: usize = 5 * 1024 * 1024;

#[derive(Clone)]
pub struct AppState {
    pub config: Arc<RwLock<Config>>,
    pub _config_path: PathBuf,
    pub api_token: String,
    pub sample_chart: String,
}

#[derive(Deserialize)]
pub struct CleanRequest {
    pub text: String,
    #[serde(default = "default_mode")]
    pub mode: String,
    #[serde(default = "default_format")]
    pub format: String,
    #[serde(default)]
    pub track_changes: bool,
    #[serde(default)]
    pub stage_overrides: Option<Value>,
}

fn default_mode() -> String {
    "clean".to_string()
}

fn default_format() -> String {
    "text".to_string()
}

#[derive(Deserialize)]
pub struct AbbreviateRequest {
    pub text: String,
    #[serde(default = "default_abbr_mode")]
    pub mode: String,
}

fn default_abbr_mode() -> String {
    "compress".to_string()
}

#[derive(Deserialize)]
pub struct InsightsRequest {
    pub text: String,
}

#[derive(Serialize)]
pub struct ErrorResponse {
    pub error: String,
}

fn is_loopback_host(host_header: &str) -> bool {
    let trimmed = host_header.trim();
    if trimmed.is_empty() {
        return false;
    }
    let host_only = if trimmed.starts_with('[') {
        trimmed.split(']').next().unwrap_or("").trim_start_matches('[')
    } else {
        trimmed.split(':').next().unwrap_or("")
    };
    matches!(
        host_only.to_ascii_lowercase().as_str(),
        "127.0.0.1" | "localhost" | "::1"
    )
}

fn is_allowed_origin(origin_header: &str) -> bool {
    let low = origin_header.trim().to_ascii_lowercase();
    for prefix in [
        "http://127.0.0.1",
        "http://localhost",
        "http://[::1]",
        "https://127.0.0.1",
        "https://localhost",
        "https://[::1]",
    ] {
        if let Some(rest) = low.strip_prefix(prefix) {
            if rest.is_empty() || rest.starts_with(':') || rest.starts_with('/') {
                return true;
            }
        }
    }
    false
}

/// Middleware enforcing loopback `Host` header (DNS rebinding guard) and loopback `Origin`.
async fn loopback_host_guard(req: Request, next: Next) -> Response {
    let headers = req.headers();
    if let Some(host) = headers.get(header::HOST).and_then(|v| v.to_str().ok()) {
        if !is_loopback_host(host) {
            return (
                StatusCode::FORBIDDEN,
                Json(ErrorResponse {
                    error: "Forbidden Host header (loopback only)".to_string(),
                }),
            )
                .into_response();
        }
    }
    if let Some(origin) = headers.get(header::ORIGIN).and_then(|v| v.to_str().ok()) {
        if !is_allowed_origin(origin) {
            return (
                StatusCode::FORBIDDEN,
                Json(ErrorResponse {
                    error: "Cross-origin requests are not permitted".to_string(),
                }),
            )
                .into_response();
        }
    }
    next.run(req).await
}

fn check_bearer(headers: &HeaderMap, expected_token: &str) -> Result<(), (StatusCode, Json<ErrorResponse>)> {
    let auth = headers
        .get(header::AUTHORIZATION)
        .and_then(|v| v.to_str().ok())
        .unwrap_or("");
    if let Some(tok) = auth.strip_prefix("Bearer ") {
        if tok.trim() == expected_token {
            return Ok(());
        }
    }
    Err((
        StatusCode::UNAUTHORIZED,
        Json(ErrorResponse {
            error: "Missing or invalid Bearer token".to_string(),
        }),
    ))
}

async fn handle_health() -> impl IntoResponse {
    Json(json!({
        "status": "ok",
        "engine": "chartcleaner-rs",
        "version": env!("CARGO_PKG_VERSION"),
        "stages": BUILTIN_STAGE_IDS.len(),
    }))
}

async fn handle_presets(
    State(state): State<AppState>,
    headers: HeaderMap,
) -> Result<Json<Value>, (StatusCode, Json<ErrorResponse>)> {
    check_bearer(&headers, &state.api_token)?;
    Ok(Json(json!({
        "stages": BUILTIN_STAGE_IDS,
        "formats": ["text", "markdown", "xml", "json", "smartphrase"],
        "modes": ["clean", "expand"],
    })))
}

async fn handle_clean(
    State(state): State<AppState>,
    headers: HeaderMap,
    Json(payload): Json<CleanRequest>,
) -> Result<Json<Value>, (StatusCode, Json<ErrorResponse>)> {
    check_bearer(&headers, &state.api_token)?;
    if payload.text.trim().is_empty() {
        return Err((
            StatusCode::BAD_REQUEST,
            Json(ErrorResponse {
                error: "Input text is empty".to_string(),
            }),
        ));
    }

    let mut cfg = state.config.read().unwrap().clone();
    if let Some(Value::Object(overrides)) = payload.stage_overrides {
        for (k, v) in overrides {
            if let Some(enabled) = v.as_bool() {
                chartcleaner_core::set_stage_enabled(&mut cfg, &k, enabled);
            }
        }
    }

    let mode = if payload.mode == "expand" {
        "expand"
    } else {
        "clean"
    };

    let pipeline = Pipeline::new(cfg, None);
    let result = pipeline.run(&payload.text, payload.track_changes, mode);
    let formatted = format_output(&result.text, &payload.format).map_err(|e| {
        (
            StatusCode::BAD_REQUEST,
            Json(ErrorResponse {
                error: e.to_string(),
            }),
        )
    })?;
    let insights_val = extract_insights(&result.text);

    Ok(Json(json!({
        "text": formatted,
        "raw_cleaned": result.text,
        "original_chars": result.original_chars,
        "cleaned_chars": result.cleaned_chars,
        "chars_removed": result.chars_removed(),
        "reduction_pct": result.reduction_pct(),
        "original_lines": result.original_lines,
        "cleaned_lines": result.cleaned_lines,
        "total_replacements": result.total_replacements(),
        "duration_ms": (result.duration_ms * 1000.0).round() / 1000.0,
        "stages": result.stages,
        "fact_check": result.fact_check,
        "insights": insights_val,
    })))
}

async fn handle_abbreviate(
    State(state): State<AppState>,
    headers: HeaderMap,
    Json(payload): Json<AbbreviateRequest>,
) -> Result<Json<Value>, (StatusCode, Json<ErrorResponse>)> {
    check_bearer(&headers, &state.api_token)?;
    let cfg = state.config.read().unwrap().clone();
    let cfg_val = Value::Object(cfg);

    let (out, count, hits) = if payload.mode == "expand" {
        abbreviations::expand(&payload.text, Some(&cfg_val), None)
    } else {
        abbreviations::abbreviate(&payload.text, Some(&cfg_val), None)
    };

    Ok(Json(json!({
        "text": out,
        "replacements": count,
        "hits": hits,
    })))
}

async fn handle_insights(
    State(state): State<AppState>,
    headers: HeaderMap,
    Json(payload): Json<InsightsRequest>,
) -> Result<Json<Value>, (StatusCode, Json<ErrorResponse>)> {
    check_bearer(&headers, &state.api_token)?;
    Ok(Json(extract_insights(&payload.text)))
}

async fn handle_index(State(state): State<AppState>) -> Html<String> {
    let sample_json = serde_json::to_string(&state.sample_chart).unwrap_or_else(|_| "\"\"".to_string());
    let html = WEB_UI_HTML
        .replace("__API_TOKEN__", &state.api_token)
        .replace("__SAMPLE_CHART_JSON__", &sample_json);
    Html(html)
}

pub fn ensure_api_token(data_dir: &Path) -> anyhow::Result<String> {
    std::fs::create_dir_all(data_dir)?;
    let token_path = data_dir.join("api_token");
    if let Ok(existing) = std::fs::read_to_string(&token_path) {
        let trimmed = existing.trim();
        if trimmed.len() >= 24 {
            return Ok(trimmed.to_string());
        }
    }
    use sha2::{Digest, Sha256};
    let mut hasher = Sha256::new();
    hasher.update(
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap_or_default()
            .as_nanos()
            .to_le_bytes(),
    );
    hasher.update(std::process::id().to_le_bytes());
    let digest = hasher.finalize();
    let mut tok = String::with_capacity(48);
    for b in &digest[..24] {
        use std::fmt::Write;
        let _ = write!(&mut tok, "{b:02x}");
    }
    std::fs::write(&token_path, &tok)?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let _ = std::fs::set_permissions(&token_path, std::fs::Permissions::from_mode(0o600));
    }
    Ok(tok)
}

pub fn build_router(state: AppState) -> Router {
    Router::new()
        .route("/", get(handle_index))
        .route("/api/v1/health", get(handle_health))
        .route("/api/v1/presets", get(handle_presets))
        .route("/api/v1/clean", post(handle_clean))
        .route("/api/v1/abbreviate", post(handle_abbreviate))
        .route("/api/v1/insights", post(handle_insights))
        .layer(DefaultBodyLimit::max(MAX_BODY_BYTES))
        .layer(middleware::from_fn(loopback_host_guard))
        .with_state(state)
}

pub async fn run_server(
    cfg: Config,
    config_path: PathBuf,
    data_dir: PathBuf,
    sample_chart: String,
    port: u16,
) -> anyhow::Result<()> {
    let api_token = ensure_api_token(&data_dir)?;
    let state = AppState {
        config: Arc::new(RwLock::new(cfg)),
        _config_path: config_path,
        api_token: api_token.clone(),
        sample_chart,
    };

    let app = build_router(state);
    let addr = SocketAddr::from(([127, 0, 0, 1], port));
    println!("ChartCleaner Rust server listening on http://{addr}");
    println!("Local API bearer token stored in {}", data_dir.join("api_token").display());
    let listener = tokio::net::TcpListener::bind(addr).await?;
    axum::serve(listener, app).await?;
    Ok(())
}

const WEB_UI_HTML: &str = r#"<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>ChartCleaner RS — Ultra-Fast Clinical Chart Cleaner</title>
<style>
  :root {
    --bg: #0b0f17;
    --panel: #131a26;
    --panel-alt: #192231;
    --border: #263246;
    --text: #e6edf7;
    --muted: #8b9bb4;
    --accent: #38bdf8;
    --accent-strong: #0284c7;
    --ok: #22c55e;
    --warn: #f59e0b;
    --danger: #ef4444;
    --mono: "JetBrains Mono", "SF Mono", Menlo, Consolas, monospace;
    --sans: -apple-system, BlinkMacSystemFont, "Inter", "Segoe UI", sans-serif;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0;
    background: var(--bg);
    color: var(--text);
    font-family: var(--sans);
    line-height: 1.45;
  }
  header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 12px 24px;
    border-bottom: 1px solid var(--border);
    background: var(--panel);
  }
  .brand {
    display: flex;
    align-items: center;
    gap: 12px;
    font-weight: 700;
    font-size: 16px;
    letter-spacing: -0.01em;
  }
  .badge {
    background: rgba(56, 189, 248, 0.14);
    color: var(--accent);
    border: 1px solid rgba(56, 189, 248, 0.35);
    font-family: var(--mono);
    font-size: 11px;
    padding: 2px 8px;
    border-radius: 999px;
  }
  .toolbar {
    display: flex;
    flex-wrap: wrap;
    gap: 10px;
    align-items: center;
  }
  button, select {
    background: var(--panel-alt);
    color: var(--text);
    border: 1px solid var(--border);
    padding: 7px 13px;
    border-radius: 6px;
    font-size: 13px;
    cursor: pointer;
    font-family: var(--sans);
  }
  button.primary {
    background: var(--accent-strong);
    border-color: var(--accent);
    color: #fff;
    font-weight: 600;
  }
  button:hover { filter: brightness(1.1); }
  main {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 16px;
    padding: 16px 24px;
    height: calc(100vh - 61px);
  }
  .col {
    display: flex;
    flex-direction: column;
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: 8px;
    overflow: hidden;
  }
  .col-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 10px 14px;
    background: var(--panel-alt);
    border-bottom: 1px solid var(--border);
    font-size: 13px;
    font-weight: 600;
  }
  textarea {
    flex: 1;
    width: 100%;
    background: transparent;
    color: var(--text);
    border: none;
    padding: 14px;
    font-family: var(--mono);
    font-size: 12.5px;
    resize: none;
    outline: none;
  }
  .toggles {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    padding: 10px 14px;
    border-top: 1px solid var(--border);
    background: var(--panel-alt);
    font-size: 12px;
  }
  .toggles label {
    display: inline-flex;
    align-items: center;
    gap: 5px;
    padding: 3px 8px;
    border-radius: 4px;
    background: var(--panel);
    border: 1px solid var(--border);
    cursor: pointer;
  }
  .tabs {
    display: flex;
    gap: 6px;
  }
  .tab {
    padding: 4px 10px;
    border-radius: 4px;
    font-size: 12px;
    background: transparent;
    border: 1px solid transparent;
  }
  .tab.active {
    background: rgba(56, 189, 248, 0.15);
    border-color: rgba(56, 189, 248, 0.4);
    color: var(--accent);
  }
  .pane {
    display: none;
    flex: 1;
    overflow: auto;
    padding: 14px;
  }
  .pane.active {
    display: flex;
    flex-direction: column;
  }
  .stats-bar {
    display: flex;
    gap: 16px;
    padding: 8px 14px;
    border-bottom: 1px solid var(--border);
    font-family: var(--mono);
    font-size: 12px;
    color: var(--muted);
  }
  .stats-bar strong { color: var(--text); }
  table {
    width: 100%;
    border-collapse: collapse;
    font-size: 12.5px;
    font-family: var(--mono);
  }
  th, td {
    text-align: left;
    padding: 7px 10px;
    border-bottom: 1px solid var(--border);
  }
  th { color: var(--muted); font-weight: 500; }
  .pill-ok { color: var(--ok); }
  .pill-warn { color: var(--warn); }
  .pill-alert { color: var(--danger); }
</style>
</head>
<body>
<header>
  <div class="brand">
    <span>ChartCleaner RS</span>
    <span class="badge">Pure Rust Engine</span>
    <span id="perf-badge" class="badge">Ready</span>
  </div>
  <div class="toolbar">
    <button onclick="loadSample()">Load Sample Chart</button>
    <select id="mode-select">
      <option value="clean">Mode: Clean &amp; Condense</option>
      <option value="expand">Mode: Expand Abbreviations</option>
    </select>
    <select id="format-select">
      <option value="text">Format: Clinical Text</option>
      <option value="markdown">Format: Markdown</option>
      <option value="xml">Format: LLM XML</option>
      <option value="json">Format: JSON</option>
      <option value="smartphrase">Format: Epic SmartPhrase</option>
    </select>
    <button class="primary" onclick="runClean()">Clean Chart (⌘/Ctrl+Enter)</button>
    <button onclick="copyOutput()">Copy Output</button>
  </div>
</header>
<main>
  <section class="col">
    <div class="col-header">
      <span>Raw EMR Export (100% Local Loopback)</span>
      <span id="raw-counter" style="color:var(--muted);font-family:var(--mono)">0 chars</span>
    </div>
    <textarea id="raw-input" placeholder="Paste Epic / Cerner / MEDITECH chart text here..."></textarea>
    <div class="toggles">
      <label><input type="checkbox" data-stage="nlp_redaction" checked /> NLP PHI Redact</label>
      <label><input type="checkbox" data-stage="clinical_identifiers" checked /> NPI/DEA/UDI</label>
      <label><input type="checkbox" data-stage="tokenize" /> Reversible [[Tn]]</label>
      <label><input type="checkbox" data-stage="abbreviations" checked /> Abbreviations</label>
      <label><input type="checkbox" data-stage="labs_compact" /> Compact Labs</label>
      <label><input type="checkbox" data-stage="meds_reconcile" /> Reconcile Meds</label>
      <label><input type="checkbox" data-stage="vitals_summary" /> Vitals Condenser</label>
      <label><input type="checkbox" data-stage="neuro_summary" /> Neuro Condenser</label>
      <label><input type="checkbox" data-stage="mar_summary" /> MAR Bolus Totals</label>
      <label><input type="checkbox" data-stage="flowsheet_tables" /> Flowsheet Tables</label>
      <label><input type="checkbox" data-stage="hospital_day" /> HD#/POD# Stamps</label>
    </div>
  </section>
  <section class="col">
    <div class="col-header">
      <div class="tabs">
        <button class="tab active" onclick="switchTab('output', this)">Cleaned Output</button>
        <button class="tab" onclick="switchTab('stages', this)">Stage Telemetry</button>
        <button class="tab" onclick="switchTab('facts', this)">Fact-Check</button>
        <button class="tab" onclick="switchTab('insights', this)">Clinical Insights</button>
      </div>
    </div>
    <div class="stats-bar" id="stats-bar">
      <span>Reduction: <strong id="stat-reduction">—</strong></span>
      <span>Lines: <strong id="stat-lines">—</strong></span>
      <span>Edits: <strong id="stat-edits">—</strong></span>
      <span>Fact-Check: <strong id="stat-fc">—</strong></span>
    </div>
    <div id="pane-output" class="pane active" style="padding:0">
      <textarea id="clean-output" readonly placeholder="Cleaned chart output will appear here..."></textarea>
    </div>
    <div id="pane-stages" class="pane">
      <table>
        <thead><tr><th>Stage</th><th>Edits</th><th>Chars Removed</th><th>Time (ms)</th></tr></thead>
        <tbody id="stages-tbody"></tbody>
      </table>
    </div>
    <div id="pane-facts" class="pane">
      <div id="facts-content">Run a clean to verify clinical facts (labs, vitals, allergies, dosing, laterality/negation).</div>
    </div>
    <div id="pane-insights" class="pane">
      <div id="insights-content">Run a clean to view extracted safety headers, conflicts, devices, micro, and ICU bundle items.</div>
    </div>
  </section>
</main>
<script>
const API_TOKEN = "__API_TOKEN__";
const SAMPLE_CHART = __SAMPLE_CHART_JSON__;

const rawEl = document.getElementById('raw-input');
rawEl.addEventListener('input', () => {
  document.getElementById('raw-counter').textContent = `${rawEl.value.length.toLocaleString()} chars`;
});
document.addEventListener('keydown', (e) => {
  if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
    e.preventDefault();
    runClean();
  }
});

function loadSample() {
  rawEl.value = SAMPLE_CHART;
  rawEl.dispatchEvent(new Event('input'));
  runClean();
}

function switchTab(id, btn) {
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.pane').forEach(p => p.classList.remove('active'));
  btn.classList.add('active');
  document.getElementById('pane-' + id).classList.add('active');
}

async function copyOutput() {
  const val = document.getElementById('clean-output').value;
  if (!val) return;
  await navigator.clipboard.writeText(val);
  document.getElementById('perf-badge').textContent = 'Copied!';
}

async function runClean() {
  const text = rawEl.value;
  if (!text.trim()) return;
  const overrides = {};
  document.querySelectorAll('[data-stage]').forEach(cb => {
    overrides[cb.dataset.stage] = cb.checked;
  });
  const res = await fetch('/api/v1/clean', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Authorization': 'Bearer ' + API_TOKEN
    },
    body: JSON.stringify({
      text,
      mode: document.getElementById('mode-select').value,
      format: document.getElementById('format-select').value,
      stage_overrides: overrides
    })
  });
  const data = await res.json();
  if (!res.ok) {
    alert(data.error || 'Error cleaning chart');
    return;
  }
  document.getElementById('clean-output').value = data.text;
  document.getElementById('perf-badge').textContent = `${data.duration_ms.toFixed(2)} ms`;
  document.getElementById('stat-reduction').textContent = `${data.reduction_pct}% (-${data.chars_removed.toLocaleString()} chars)`;
  document.getElementById('stat-lines').textContent = `${data.original_lines} → ${data.cleaned_lines}`;
  document.getElementById('stat-edits').textContent = data.total_replacements;

  const fc = data.fact_check || { status: 'ok', preserved_pct: 100, unexpected: [], meaning: [] };
  const fcEl = document.getElementById('stat-fc');
  fcEl.textContent = `${fc.status.toUpperCase()} (${fc.preserved_pct}%)`;
  fcEl.className = 'pill-' + fc.status;

  const tbody = document.getElementById('stages-tbody');
  tbody.innerHTML = '';
  for (const s of data.stages) {
    const removed = s.chars_before - s.chars_after;
    if (s.replacements === 0 && removed === 0) continue;
    const tr = document.createElement('tr');
    tr.innerHTML = `<td>${s.label}</td><td>${s.replacements}</td><td>${removed}</td><td>${s.duration_ms.toFixed(2)}</td>`;
    tbody.appendChild(tr);
  }

  document.getElementById('facts-content').innerHTML = `<pre style="font-family:var(--mono);font-size:12px">${JSON.stringify(fc, null, 2)}</pre>`;
  document.getElementById('insights-content').innerHTML = `<pre style="font-family:var(--mono);font-size:12px">${JSON.stringify(data.insights, null, 2)}</pre>`;
}
</script>
</body>
</html>
"#;
