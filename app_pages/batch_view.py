"""Presentation projections for the Batch results view."""


def batch_counts(results) -> list[tuple[str, int]]:
    return [
        ("Processed", len(results)),
        ("Needs review", sum(bool(r.needs_review) for r in results)),
        ("Errors", sum(r.status == "error" for r in results)),
    ]


def batch_rows(results) -> list[dict]:
    rows = []
    for result in results:
        ok = result.status == "ok"
        rows.append({
            "file": result.name,
            "reduction": f"{result.reduction:+.1f}%" if ok else "—",
            "findings": str(result.findings) if ok else "—",
            "status": "review" if result.needs_review and ok else result.status,
            "actions": "View details",
        })
    return rows
