from types import SimpleNamespace as NS

from app_pages.batch_view import batch_counts, batch_rows


def _result(name="one.txt", status="ok", review=False, error=""):
    return NS(name=name, path=name, status=status, needs_review=review,
              chars_before=100, chars_after=60, reduction=40.0, phi_total=2,
              findings=1, facts_status="ok", facts="4 / 4", note_type="progress",
              preset="daily", elapsed_ms=12, summary="Stable", error=error)


def test_counts_and_default_rows_keep_review_and_error_distinct():
    results = [_result(), _result("two.txt", review=True),
               _result("bad.txt", status="error", error="decode failed")]
    assert batch_counts(results) == [("Processed", 3), ("Needs review", 1), ("Errors", 1)]
    rows = batch_rows(results)
    assert list(rows[0]) == ["result_id", "file", "reduction", "findings", "status", "actions"]
    assert rows[0]["result_id"] == str(id(results[0]))
    assert rows[1]["result_id"] != rows[2]["result_id"]
    assert rows[0]["reduction"] == "+40.0%"
    assert rows[2]["status"] == "error"
    assert rows[2]["findings"] == "—"


def test_row_projection_does_not_replace_exception_details():
    row = batch_rows([_result(status="error", error="decode failed")])[0]
    assert row["actions"] == "View details"
