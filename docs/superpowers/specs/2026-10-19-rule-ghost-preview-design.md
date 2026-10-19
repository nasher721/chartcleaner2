# Rule ghost preview

Status: awaiting spec review. The behavior below was agreed section by section. Not implemented.

The rule builder lists each match as a single truncated line. The chart on the Clean page is a plain textarea, so it cannot highlight those matches. This design adds a read-only excerpt inside the builder that shows each change in place, with a little of the note around it.

## Decisions

- The excerpt lives inside the rule builder. The chart text box on the page stays plain.
- Deleted text is struck through. Replacement text is green on that same line.
- The excerpt shows two lines of the original note above and below each cluster of changes. Gaps between clusters collapse to one row, “N lines unchanged.”
- The excerpt is look-only. Save and Cancel stay as they are. Today’s warnings stay: clinical values lost, never-remove clashes, a conflicting replacement, and a slow pattern.
- “Check my recent charts” still updates the headline and warnings only. Recent charts do not get an excerpt.

## What you see

While the builder is open, the excerpt sits under the headline and warnings and replaces the one-line samples in the dialog.

- Each cluster of changes is one window, with 2 lines of context on each side. A hit’s range is its changed line plus those context lines, clipped to the note. Ranges that overlap or touch become one window.
- A gap between windows is one row, “N lines unchanged.” There is no gap row before the first window or after the last. `gap_before` is the number of original lines between two windows, and the row is drawn only when that number is greater than 0.
- Text only on the original line is struck through. Text only on the result line is green. Shared text stays plain. A line the rule removes entirely is struck through and has nothing green beside it.
- The excerpt refreshes when the rule text, kind, placeholders, scope, or options change.
- No matches leaves the excerpt empty. The headline still reports that.
- At most 12 windows are drawn. Changed lines that belong to later windows are counted as M in a final row, “M more matches not shown.” The headline count still includes every hit.
- A displayed line’s visible text is cut at 240 characters and an ellipsis is added. Matching still uses the full line.
- Clicking the excerpt does nothing.

## How the excerpt is built

`rule_builder.preview` already applies the unsaved rule once. It keeps doing that, and it does not apply the rule a second time.

A new helper, `excerpt_windows(before, after)`, lives in `chartcleaner/rule_preview.py` beside `_changed_lines`. `preview` stores the result on `BuilderPreview.excerpt` and includes it in `to_dict`.

Alignment uses the lines that differ, the same surplus-line alignment `_changed_lines` already uses, then maps those lines back to their positions in the original note. The chart is aligned once per refresh. `samples` is filled from that same alignment, so existing callers of `samples` keep working. The dialog stops rendering `samples` when an excerpt is present.

`excerpt_windows` returns:

- `windows`: up to 12 windows. Each window has `gap_before` (unchanged original lines since the previous window; 0 for the first) and `lines`.
- Each line has `kind` of `context` or `change`, the original line index when it has one, and `segments`. A segment is `("plain", text)`, `("del", text)`, or `("ins", text)`.
- Context lines are one `plain` segment, taken from the original note.
- A changed line’s segments come from a per-line comparison of that original line and its paired result line. Only lines inside the drawn windows get that comparison.
- A result line with no original line is one `ins` segment, placed where the alignment puts it, inside the window of the nearest changed original line.
- `omitted`: the number of changed lines that belong to windows past the 12 drawn. This is M.

The dialog cuts each rendered line’s visible text at 240 characters and adds an ellipsis. The stored segment text stays whole.

The dialog change is `show()` in `app_pages/rule_builder_ui.py`. It renders the excerpt into an element marked `builder-excerpt`. Chart text is escaped in that HTML. The excerpt is not editable and has no click handler.

## When the preview cannot be drawn

- An invalid shape never reaches the excerpt. The headline keeps the error `check_shape` already reports, and Save does not save it.
- If applying the rule throws, the dialog keeps “Could not preview” and the excerpt stays blank.
- If `excerpt_windows` throws, `preview` catches it, sets `excerpt` to `None`, and still returns `samples` (from `_changed_lines` on this failure path only), warnings, and the headline. `show()` renders today’s one-line samples when `excerpt` is `None`.
- No matches sets `excerpt` to zero windows and `omitted` 0. That is a successful preview, so `show()` leaves the excerpt area empty and does not fall back to samples.
- Context clips at the start and end of the note.
- A run of deleted lines is one window: each deleted line struck through, with two lines of context at the edges of the run.
- Lines outside a section scope are not struck. They appear only as context when they sit within two lines of a hit. Scope is applied by the existing trial before the excerpt is built.
- A match kept by a “never remove” exception is not struck. The existing warning still names that exception.
- Blank lines that `_changed_lines` already ignores stay ignored.

## Testing

No cleaning stage or config default changes. `tests/golden` stays byte-identical.

Existing tests in `tests/test_rule_builder.py` keep their meaning: hit counts, clinical-value warnings, never-remove clashes, conflicting replacements, and section scope.

New cases in `tests/test_rule_builder.py`:

- A removed line is a `del` segment, with two `context` lines above and below.
- A replacement produces `del` and `ins` segments on that same line, with shared text as `plain`.
- Changed lines at indexes 0 and 3 merge into one window. Changed lines at indexes 0 and 8 produce a later window whose `gap_before` is 3.
- A hit on the first line has no context above it. Consecutive deleted lines are one window.
- A 13th window is not drawn, and `omitted` equals the changed lines in the undrawn windows.
- Stored segment text is whole. The rendered visible line is at most 240 characters plus an ellipsis.
- `preview` still fills `samples`, warnings, and the headline from the same alignment.
- When `excerpt_windows` raises, `preview` returns `excerpt is None` and the samples it computed.

One test in `tests/test_rule_ux_ui.py` opens the builder and uses the `builder-excerpt` marker. The struck phrase and a neighboring line are visible, the chart text box is unchanged, and the clinical-value warning still appears. Chart text containing `<` or `&` is escaped in that marker.

## Out of scope

Painting matches on the chart textarea, showing the whole note in the dialog, clicking a struck line to spare it, and drawing excerpts for recent charts.
