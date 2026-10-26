"""Source-linked edit provenance: which original characters does an edit touch?

The source text of one run is immutable. Stages work on the *current* accepted
text, whose offsets drift as earlier edits change lengths, so a stage proposes
an edit in current-text coordinates and the :class:`EditLedger` translates it
to original :class:`SourceSpan` ranges. The ledger is a piece table: every
piece is either a run of untouched source characters or text an accepted edit
inserted (anchored to the source range it replaced). A proposal over source
pieces maps ``exact``; one that covers inserted text maps ``coarse`` with an
enclosing anchor; one that inserts without replacing anything is an
``insertion`` and never claims the inserted text existed in the source.

Only accepted proposals advance the text. Regex stages propose every match on
the same input and resolve them as one batch (:meth:`EditLedger.resolve_many`)
so the table is rebuilt once per rule, not once per match. Opaque stages
(compactors, custom scripts) are reconciled after the fact into coarse
proposals: one per changed line region (:meth:`EditLedger.reconcile_hunks`),
so an opaque stage that touches the first and the last line of a long chart
does not make every later edit map to the whole chart. The ledger lives in memory for the run;
:meth:`EditLedger.summary` is the text-free part that may be persisted.
"""

from __future__ import annotations

import bisect
import difflib
from dataclasses import dataclass, field
from typing import Iterable, Iterator, NamedTuple, Sequence

__all__ = ["DECISIONS", "MAPPINGS", "EditLedger", "EditProposal", "SourceSpan", "merge_spans"]

DECISIONS = ("accept", "retain_original", "withhold_release")
MAPPINGS = ("exact", "coarse", "insertion")
# Line regions an opaque stage's output is split into, at most. Past it nearby
# regions are merged (still correct, just coarser); past MAX_DIFF_LINES the
# line diff is skipped and the whole differing window is one proposal.
MAX_HUNKS = 400
MAX_DIFF_LINES = 50_000


class SourceSpan(NamedTuple):
    """Half-open ``[start, end)`` range of Python character indices into the source."""

    start: int
    end: int

    @property
    def length(self) -> int:
        return self.end - self.start


def merge_spans(spans: Iterable[tuple[int, int]]) -> tuple[SourceSpan, ...]:
    """Sorted, non-overlapping spans; touching or overlapping ranges are joined."""
    out: list[SourceSpan] = []
    for start, end in sorted((int(s), int(e)) for s, e in spans):
        if out and start <= out[-1].end:
            if end > out[-1].end:
                out[-1] = SourceSpan(out[-1].start, end)
            continue
        out.append(SourceSpan(start, end))
    return tuple(out)


def union_length(spans: Iterable[tuple[int, int]]) -> int:
    return sum(s.length for s in merge_spans(spans))


@dataclass
class EditProposal:
    """One proposed edit with its provenance. ``decision`` is run-local state."""

    proposal_id: str
    stage_id: str
    rule_key: str | None
    source_spans: tuple[SourceSpan, ...]
    before: str
    after: str
    mapping: str
    # current-text coordinates at proposal time (for applying the edit)
    start: int = 0
    end: int = 0
    epoch: int = 0
    decision: str | None = None
    report: object = None  # a validation.ValidationReport once validated

    def __post_init__(self) -> None:
        if self.mapping not in MAPPINGS:
            raise ValueError(f"mapping must be one of {MAPPINGS}")

    @property
    def enclosing(self) -> SourceSpan:
        """One span from the first touched source character to the last."""
        return SourceSpan(self.source_spans[0].start, self.source_spans[-1].end)

    @property
    def is_removal(self) -> bool:
        return self.after == "" and self.before != ""

    def to_summary(self) -> dict:
        """Counts, ids and offsets — no chart text."""
        return {"id": self.proposal_id, "stage_id": self.stage_id, "rule_key": self.rule_key,
                "source_spans": [list(s) for s in self.source_spans], "mapping": self.mapping,
                "before_chars": len(self.before), "after_chars": len(self.after),
                "decision": self.decision}


@dataclass
class _Piece:
    kind: str          # "orig" | "ins"
    src_start: int     # orig: first source index; ins: anchor start
    src_end: int       # orig: one past the last source index; ins: anchor end
    text: str = ""     # ins only

    @property
    def length(self) -> int:
        return self.src_end - self.src_start if self.kind == "orig" else len(self.text)

    def cut(self, offset: int, length: int) -> "_Piece":
        """The sub-piece covering ``[offset, offset + length)`` of this piece."""
        if self.kind == "orig":
            return _Piece("orig", self.src_start + offset, self.src_start + offset + length)
        return _Piece("ins", self.src_start, self.src_end, self.text[offset:offset + length])


class EditLedger:
    """Immutable source + current accepted text + the proposals in between."""

    def __init__(self, source: str):
        self.source = source
        self.proposals: list[EditProposal] = []
        self._pieces: list[_Piece] = [_Piece("orig", 0, len(source))] if source else []
        self._starts: list[int] = [0] if source else []
        self.current = source
        self.epoch = 0

    # -- lookup ---------------------------------------------------------------

    def _locate(self, pos: int) -> int:
        """Index of the piece containing current offset ``pos`` (last piece at the end)."""
        if not self._pieces:
            return -1
        return max(0, bisect.bisect_right(self._starts, pos) - 1)

    def _overlapping(self, start: int, end: int) -> Iterator[tuple[int, int, _Piece]]:
        """``(cur_start, cur_end, piece)`` for every piece overlapping ``[start, end)``."""
        if not self._pieces or start >= end:
            return
        i = self._locate(start)
        while i < len(self._pieces):
            cs = self._starts[i]
            piece = self._pieces[i]
            ce = cs + piece.length
            if cs >= end:
                break
            if ce > start:
                yield cs, ce, piece
            i += 1

    def spans_for(self, start: int, end: int) -> tuple[tuple[SourceSpan, ...], str]:
        """Source spans for current ``[start, end)`` and the mapping quality."""
        self._check_range(start, end)
        if start == end:
            return (SourceSpan(*self._anchor_at(start)),), "insertion"
        spans: list[tuple[int, int]] = []
        coarse = False
        for cs, _ce, piece in self._overlapping(start, end):
            lo, hi = max(start, cs) - cs, min(end, cs + piece.length) - cs
            if piece.kind == "orig":
                spans.append((piece.src_start + lo, piece.src_start + hi))
            else:
                coarse = True
                spans.append((piece.src_start, piece.src_end))
        merged = merge_spans(spans)
        if coarse and len(merged) > 1:
            merged = (SourceSpan(merged[0].start, merged[-1].end),)
        return merged, ("coarse" if coarse else "exact")

    def _anchor_at(self, pos: int) -> tuple[int, int]:
        """The source position a pure insertion at current ``pos`` attaches to."""
        if not self._pieces:
            return 0, 0
        if pos >= len(self.current):
            last = self._pieces[-1]
            end = last.src_end
            return end, end
        i = self._locate(pos)
        piece = self._pieces[i]
        if piece.kind == "orig":
            at = piece.src_start + (pos - self._starts[i])
            return at, at
        return piece.src_start, piece.src_start

    def _check_range(self, start: int, end: int) -> None:
        if not (0 <= start <= end <= len(self.current)):
            raise ValueError(f"offsets [{start}, {end}) are outside the current text "
                             f"(length {len(self.current)})")

    # -- proposals ------------------------------------------------------------

    def propose(self, stage_id: str, rule_key: str | None, start: int, end: int,
                replacement: str, *, mapping: str | None = None) -> EditProposal:
        """Propose replacing current ``[start, end)`` with ``replacement``.

        Offsets reference the current accepted text; the returned proposal
        carries the original source spans. ``mapping`` forces ``coarse`` for
        opaque transformations whose alignment is only approximate.
        """
        spans, quality = self.spans_for(start, end)
        if mapping is not None:
            if mapping not in MAPPINGS:
                raise ValueError(f"mapping must be one of {MAPPINGS}")
            if quality != "insertion":
                quality = mapping
        proposal = EditProposal(
            proposal_id=f"p{len(self.proposals) + 1:06d}", stage_id=stage_id, rule_key=rule_key,
            source_spans=spans, before=self.current[start:end], after=replacement,
            mapping=quality, start=start, end=end, epoch=self.epoch)
        self.proposals.append(proposal)
        return proposal

    def resolve(self, proposal: EditProposal, decision: str) -> None:
        """Record ``decision``; only ``accept`` changes the current text."""
        self.resolve_many([(proposal, decision)])

    def resolve_many(self, decisions: Sequence[tuple[EditProposal, str]]) -> None:
        """Resolve proposals made against the *same* current text in one rebuild."""
        accepted: list[EditProposal] = []
        for proposal, decision in decisions:
            if decision not in DECISIONS:
                raise ValueError(f"decision must be one of {DECISIONS}")
            if proposal.epoch != self.epoch:
                raise ValueError(f"proposal {proposal.proposal_id} was made against an older text")
            proposal.decision = decision
            if decision == "accept":
                accepted.append(proposal)
        if accepted:
            self._apply(sorted(accepted, key=lambda p: (p.start, p.end)))

    def _apply(self, accepted: list[EditProposal]) -> None:
        pieces: list[_Piece] = []
        pos = 0
        for p in accepted:
            if p.start < pos:
                raise ValueError("accepted proposals overlap; resolve them in separate batches")
            pieces.extend(self._slice(pos, p.start))
            if p.after:
                anchor = p.enclosing
                pieces.append(_Piece("ins", anchor.start, anchor.end, p.after))
            pos = p.end
        pieces.extend(self._slice(pos, len(self.current)))
        self._pieces = [x for x in pieces if x.length > 0]
        self._starts = []
        total = 0
        for piece in self._pieces:
            self._starts.append(total)
            total += piece.length
        self.current = "".join(self.source[x.src_start:x.src_end] if x.kind == "orig" else x.text
                               for x in self._pieces)
        self.epoch += 1

    def _slice(self, start: int, end: int) -> list[_Piece]:
        out: list[_Piece] = []
        for cs, ce, piece in self._overlapping(start, end):
            lo, hi = max(start, cs), min(end, ce)
            out.append(piece if (lo == cs and hi == ce) else piece.cut(lo - cs, hi - lo))
        return out

    # -- opaque stages ----------------------------------------------------------

    def reconcile(self, stage_id: str, after: str, *, rule_key: str | None = None,
                  decision: str | None = "accept") -> EditProposal | None:
        """Fold a stage's whole-text result into one coarse proposal.

        The differing window (after the common prefix and suffix) becomes the
        proposal; alignment inside it is unknown, so the mapping is ``coarse``.
        ``decision=None`` leaves the proposal pending for the caller to judge
        and resolve. Returns ``None`` when nothing changed.
        """
        current = self.current
        if after == current:
            return None
        limit = min(len(current), len(after))
        a = 0
        while a < limit and current[a] == after[a]:
            a += 1
        b = 0
        while b < limit - a and current[-1 - b] == after[-1 - b]:
            b += 1
        start, end = a, len(current) - b
        proposal = self.propose(stage_id, rule_key, start, end, after[a:len(after) - b],
                                mapping="coarse")
        if decision is not None:
            self.resolve(proposal, decision)
        return proposal

    def reconcile_hunks(self, stage_id: str, after: str, *,
                        rule_key: str | None = None) -> list[EditProposal]:
        """Fold a stage's whole-text result into one coarse proposal per changed region.

        Like :meth:`reconcile`, but the differing window is line-diffed and each
        changed run of lines (trimmed to its differing characters) becomes its
        own pending proposal against the current text. Unchanged lines between
        regions keep their exact mapping, so later edits — and the validator's
        before/after statement comparison — stay local instead of spanning
        everything between the stage's first and last change. Resolve the
        returned proposals together with :meth:`resolve_many`.
        """
        current = self.current
        if after == current:
            return []
        limit = min(len(current), len(after))
        a = 0
        while a < limit and current[a] == after[a]:
            a += 1
        b = 0
        while b < limit - a and current[-1 - b] == after[-1 - b]:
            b += 1
        # Widen the window to whole lines so the diff compares complete lines.
        ws = current.rfind("\n", 0, a) + 1
        we = len(current) - b
        nl = current.find("\n", we)
        we = len(current) if nl == -1 else nl + 1
        aws, awe = ws, len(after) - (len(current) - we)
        hunks = _line_hunks(current[ws:we], after[aws:awe])
        if hunks is None:
            return [self.propose(stage_id, rule_key, a, len(current) - b,
                                 after[a:len(after) - b], mapping="coarse")]
        proposals = []
        for cs, ce, as_, ae in hunks:
            cs, ce, as_, ae = cs + ws, ce + ws, as_ + aws, ae + aws
            # trim the region to the characters that actually differ
            while cs < ce and as_ < ae and current[cs] == after[as_]:
                cs, as_ = cs + 1, as_ + 1
            while ce > cs and ae > as_ and current[ce - 1] == after[ae - 1]:
                ce, ae = ce - 1, ae - 1
            if cs == ce and as_ == ae:
                continue
            proposals.append(self.propose(stage_id, rule_key, cs, ce, after[as_:ae],
                                          mapping="coarse"))
        return proposals

    # -- reporting ------------------------------------------------------------

    def summary(self) -> dict:
        """Counts only — safe for history and logs."""
        by_stage: dict[str, int] = {}
        out = {"proposals": len(self.proposals), "accepted": 0, "retain_original": 0,
               "withhold_release": 0, "pending": 0, "exact": 0, "coarse": 0, "insertion": 0}
        for p in self.proposals:
            by_stage[p.stage_id] = by_stage.get(p.stage_id, 0) + 1
            out[p.decision or "pending"] = out.get(p.decision or "pending", 0) + 1
            out[p.mapping] += 1
        out["accepted"] = out.pop("accept", 0)
        out["by_stage"] = by_stage
        return out


def _line_hunks(old: str, new: str) -> list[tuple[int, int, int, int]] | None:
    """Changed line regions ``(old_start, old_end, new_start, new_end)`` as char offsets.

    ``None`` when the texts are too long to line-diff cheaply. Regions are in
    order and never overlap; at most :data:`MAX_HUNKS` (nearby regions merge).
    Any region list is a valid edit script — alignment quality only decides
    how local the provenance stays — so a linear greedy diff is used.
    """
    old_lines = old.splitlines(keepends=True)
    new_lines = new.splitlines(keepends=True)
    if len(old_lines) > MAX_DIFF_LINES or len(new_lines) > MAX_DIFF_LINES:
        return None
    old_at = [0]
    for line in old_lines:
        old_at.append(old_at[-1] + len(line))
    new_at = [0]
    for line in new_lines:
        new_at.append(new_at[-1] + len(line))
    regions = _greedy_regions(old_lines, new_lines)
    gap = 1
    while len(regions) > MAX_HUNKS:
        merged = [regions[0]]
        for r in regions[1:]:
            if r[0] - merged[-1][1] <= gap:
                merged[-1][1], merged[-1][3] = r[1], r[3]
            else:
                merged.append(r)
        regions, gap = merged, gap * 2
    return [(old_at[i1], old_at[i2], new_at[j1], new_at[j2]) for i1, i2, j1, j2 in regions]


RESYNC_WINDOW = 64        # lines searched ahead for the next matching pair
SMALL_DIFF_CELLS = 250_000  # len(a) * len(b) below which difflib is cheap


def _greedy_regions(a: list[str], b: list[str]) -> list[list[int]]:
    """``[i1, i2, j1, j2]`` for each changed run of lines, in O(lines x window).

    Walks both line lists; on a mismatch it looks for the nearest point where
    two consecutive lines agree again (a lone blank line is not a resync). When
    nothing resyncs inside the window, the rest is handed to difflib if it is
    small, else it becomes one region. ``difflib.SequenceMatcher`` alone is
    quadratic on charts that repeat lines (copied-forward notes).
    """
    regions: list[list[int]] = []
    i = j = 0
    n, m = len(a), len(b)

    def agree(x: int, y: int) -> bool:
        if x >= n or y >= m or a[x] != b[y]:
            return False
        return x + 1 >= n or y + 1 >= m or a[x + 1] == b[y + 1]

    while i < n and j < m:
        if a[i] == b[j]:
            i, j = i + 1, j + 1
            continue
        found = None
        for d in range(1, RESYNC_WINDOW + 1):
            for da in range(d + 1):
                if agree(i + da, j + d - da):
                    found = (i + da, j + d - da)
                    break
            if found:
                break
        if found is None:
            if (n - i) * (m - j) <= SMALL_DIFF_CELLS:
                matcher = difflib.SequenceMatcher(None, a[i:], b[j:], autojunk=False)
                regions.extend([i + i1, i + i2, j + j1, j + j2]
                               for tag, i1, i2, j1, j2 in matcher.get_opcodes() if tag != "equal")
            else:
                regions.append([i, n, j, m])
            return regions
        regions.append([i, found[0], j, found[1]])
        i, j = found
    if i < n or j < m:
        regions.append([i, n, j, m])
    return regions
