from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REQUIRED_MARKERS = {
    "run-clean", "copy-as", "copy-text", "copy-epic", "edit-result",
    "result-output", "review-tab", "review-diff", "release-status",
    "validation-panel", "validation-coverage", "ai-panel", "ai-answer-stream",
    "batch-filter", "census-summary", "settings-search", "doctor-summary",
    "input-overflow", "result-actions", "mobile-workspace-switch",
    "review-findings-tab", "review-changes-tab", "review-diagnostics-tab",
    "blocking-banner", "review-fallback", "census-filter", "census-export",
}


def test_required_ui_markers_remain_in_source():
    sources = "\n".join(path.read_text(encoding="utf-8") for path in (ROOT / "app_pages").glob("*.py"))
    missing = sorted(marker for marker in REQUIRED_MARKERS if marker not in sources)
    assert not missing, f"Missing UI compatibility markers: {missing}"


def test_review_ui_does_not_define_release_policy():
    source = (ROOT / "app_pages" / "review_model.py").read_text(encoding="utf-8")
    assert "release_decision(" not in source
    assert "acknowledgement" not in source
