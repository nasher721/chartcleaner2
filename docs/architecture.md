# Architecture

## Processing model

Chart Cleaner runs locally. The NiceGUI app, CLI, REST API, MCP server, batch tools, clipboard helpers, watcher, and exporters share the same ordered pipeline and service-layer release rules. The network listener is loopback-only and the application has no telemetry pipeline.

Configuration comes from JSON rules, optional note-type presets, and run-local destination policies. A destination deep-copies the active configuration, applies its safety overrides, and never writes those overrides back to saved rules. An unset destination preserves legacy behavior.

## Pipeline and provenance

Built-in and trusted custom stages run in configured order. Stages propose edits through an `EditLedger` piece table rather than losing the relationship between source and output. The ledger retains source spans, a source digest, proposal decisions, and per-stage statistics so review screens can explain what changed.

Cleaning stages include metadata and boilerplate removal, PHI redaction, normalization, clinical compaction, duplicate handling, and abbreviation processing. Some stages intentionally remove or summarize detail; destination policies disable those stages where detail preservation is required. Custom Python rules execute with full user privileges and are not sandboxed.

## Validation and release

Validation modes are `off`, `shadow`, and `enforce`. Shadow mode must produce byte-identical output to validation-off behavior while collecting evidence. Enforce mode compares clinical statements, relationships, facts, meaning, scope, and abbreviation use. Coverage is explicit: `complete`, `partial`, `unavailable`, or `failed` must not be presented as equivalent.

The shared release decision can accept an edit, retain the original text, or withhold release. All output surfaces must honor a withheld decision and return no ordinary cleaned text. Local diagnostic raw-output options are not a public release path.

## Storage and integrations

Rules, presets, preferences, run history, tokens, and exports live in local application storage. Integration tokens protect the loopback API but do not make remote exposure safe. Exporters format an already gated result; they do not make an independent clinical-safety decision.

## Evidence boundary

Unit, integration, property-based, synthetic accuracy, and synthetic performance tests establish software behavior, not clinical correctness. Clinician labeling, representative multi-site chart review, native signing/notarization, and real-Mac/Windows installer and updater checks require separate authorized evidence. Release documentation must distinguish completed checks from remaining work.
