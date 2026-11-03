"""Review diff HTML with no NiceGUI or page-state dependency."""
from __future__ import annotations

import re
from html import escape as _escape

from chartcleaner.edit_provenance import line_opcodes


def esc(s: str) -> str:
    return _escape(s)


def _diff_table_start(caption: str, left: str, right: str) -> str:
    """Open a diff table with a screen-reader caption and column headers.

    The visual design has no header row, so the header cells are hidden from
    sight (``cc-sr-only``) but still expose the two columns to assistive tech.
    """
    return ("<table class='cc-diff' style='width:100%; border-collapse:collapse'>"
            f"<caption class='cc-sr-only'>{esc(caption)}</caption>"
            "<thead><tr>"
            f"<th class='cc-sr-only' scope='col'>{esc(left)}</th>"
            f"<th class='cc-sr-only' scope='col'>{esc(right)}</th>"
            "</tr></thead><tbody>")


def _wrap_fragments(line: str, fragments: list[tuple[str, str, str]]) -> str:
    """Escape ``line`` and wrap each (text, key, css) fragment's first occurrence."""
    spans: list[tuple[int, int, str, str]] = []
    for frag, key, css in sorted(fragments, key=lambda f: -len(f[0])):
        if not frag:
            continue
        if css == "cc-abbr":
            m = re.search(r"(?<![\w])" + re.escape(frag) + r"(?![\w])", line)
            pos = m.start() if m else -1
        else:
            pos = line.find(frag)
        while pos >= 0 and any(a < pos + len(frag) and pos < b for a, b, _k, _c in spans):
            pos = line.find(frag, pos + 1)
        if pos >= 0:
            spans.append((pos, pos + len(frag), key, css))
    out, cursor = [], 0
    for a, b, key, css in sorted(spans):
        out.append(esc(line[cursor:a]))
        out.append(f"<span class='{css} cc-click' data-cc='{key}' tabindex='0' "
                   f"role='button' title='Click for details' "
                   f"aria-label='Review this change'>{esc(line[a:b])}</span>")
        cursor = b
    out.append(esc(line[cursor:]))
    return "".join(out)


def collapse_context_ranges(line_count: int, changed_lines: set[int], context: int = 3) -> list[tuple[int, int]]:
    visible = set(changed_lines)
    for line in changed_lines:
        visible.update(range(max(1, line - context), min(line_count, line + context) + 1))
    ranges = []
    start = None
    for line in range(1, line_count + 2):
        if line not in visible and line <= line_count:
            start = line if start is None else start
            continue
        if start is not None:
            if line - start >= 2:
                ranges.append((start, line - 1))
            start = None
    return ranges


def review_diff_html(before: str, after: str, flag_lines: set[int] | None,
                     groups: list[dict], metadata: dict[str, int] | None = None) -> str:
    """Side-by-side diff where every tracked change is clickable.

    ``groups`` (built by the Clean page) are ``{"kind": "removal"|"replace"|"abbr",
    "before", "after", ...}``; removed/replaced text on the left and applied
    abbreviations on the right carry ``data-cc="g<index>"``.
    """
    if metadata is not None:
        metadata["change_count"] = 0
    if len(before) + len(after) > 600_000:
        return "<p>Diff too large to display (use a smaller input).</p>"
    flagged = flag_lines or set()
    left_frags: list[tuple[str, str, str]] = []
    right_frags: list[tuple[str, str, str]] = []
    for i, g in enumerate(groups):
        if g["kind"] == "abbr":
            right_frags.append((g["after"], f"g{i}", "cc-abbr"))
            continue
        for b in g.get("befores", [g.get("before", "")]):
            for piece in str(b).split("\n"):
                piece = piece.strip()
                if len(piece) >= 2:
                    left_frags.append((piece, f"g{i}", "cc-gone"))

    def left(line: str) -> str:
        frags = [f for f in left_frags if f[0] in line]
        return _wrap_fragments(line, frags) if frags else esc(line)

    def right(line: str) -> str:
        frags = [f for f in right_frags if f[0] in line]
        return _wrap_fragments(line, frags) if frags else esc(line)

    a_lines, b_lines = before.splitlines(), after.splitlines()
    opcodes = line_opcodes(a_lines, b_lines)
    rows: list[str] = []
    changed_lines: set[int] = set()
    flagged_diff_lines: set[int] = set()
    for tag, i1, i2, j1, j2 in opcodes:
        if tag == "equal":
            for k in range(i2 - i1):
                jl = j1 + k
                cls = " cc-flag" if (jl + 1) in flagged else ""
                rows.append(f"<tr data-diff-line='{len(rows) + 1}'>"
                            f"<td class='cc-diff-left'>{esc(a_lines[i1 + k])}</td>"
                            f"<td class='{cls.strip()}'>{esc(b_lines[jl])}</td></tr>")
                if (jl + 1) in flagged:
                    flagged_diff_lines.add(len(rows))
        elif tag == "delete":
            for k in range(i1, i2):
                rows.append(f"<tr class='cc-diff-change' data-diff-line='{len(rows) + 1}'>"
                            f"<td class='cc-diff-left cc-del'>{left(a_lines[k])}</td><td></td></tr>")
                changed_lines.add(len(rows))
        elif tag == "insert":
            for k in range(j1, j2):
                cls = "cc-ins cc-flag" if (k + 1) in flagged else "cc-ins"
                rows.append(f"<tr class='cc-diff-change' data-diff-line='{len(rows) + 1}'>"
                            f"<td class='cc-diff-left'></td><td class='{cls}'>{right(b_lines[k])}</td></tr>")
                changed_lines.add(len(rows))
                if (k + 1) in flagged:
                    flagged_diff_lines.add(len(rows))
        else:
            n = max(i2 - i1, j2 - j1)
            for k in range(n):
                lt = left(a_lines[i1 + k]) if i1 + k < i2 else ""
                rt = right(b_lines[j1 + k]) if j1 + k < j2 else ""
                jl = j1 + k
                cls = "cc-ins cc-flag" if (jl + 1) in flagged else "cc-ins"
                rows.append(f"<tr class='cc-diff-change' data-diff-line='{len(rows) + 1}'>"
                            f"<td class='cc-diff-left cc-del'>{lt}</td><td class='{cls}'>{rt}</td></tr>")
                changed_lines.add(len(rows))
                if jl < j2 and (jl + 1) in flagged:
                    flagged_diff_lines.add(len(rows))

    ranges = collapse_context_ranges(len(rows), changed_lines | flagged_diff_lines)
    parts = [_diff_table_start("Before and after the cleaning run", "Before", "After")]
    cursor = 0
    for start, end in ranges:
        parts.extend(rows[cursor:start - 1])
        context_id = f"diff-context-{start}-{end}"
        count = end - start + 1
        parts.append("</tbody><tbody class='cc-diff-context-summary'><tr>"
                     "<td colspan='2' class='cc-diff-context-cell'>"
                     "<button type='button' class='cc-diff-context' data-marker='diff-context' "
                     f"data-line-count='{count}' aria-expanded='false' aria-controls='{context_id}' "
                     f"aria-label='Show {count} unchanged lines'>Show {count} unchanged lines</button>"
                     "</td></tr></tbody>"
                     f"<tbody id='{context_id}' class='cc-diff-context-lines' hidden>")
        parts.extend(rows[start - 1:end])
        parts.append("</tbody><tbody>")
        cursor = end
    parts.extend(rows[cursor:])
    parts.append("</tbody></table>")
    if metadata is not None:
        metadata["change_count"] = len(changed_lines)
    return "".join(parts)
