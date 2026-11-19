"""Regions of a file that changed since the snapshot: the only parts read from version 2 on."""

from __future__ import annotations

import difflib

Span = tuple[int, int]


def changed_spans(previous: str | None, current: str) -> list[Span]:
    """Character spans of `current` on lines added or modified since `previous`.

    With no previous version the whole file counts as changed. A pure deletion marks
    the lines around the place where text was removed.
    """
    if previous is None:
        return [(0, len(current))]
    lines = current.splitlines(keepends=True)
    starts = _line_starts(lines)
    matcher = difflib.SequenceMatcher(None, previous.splitlines(keepends=True), lines, autojunk=False)
    spans: list[Span] = []
    for tag, _, _, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        if tag == "delete":
            j1, j2 = max(j1 - 1, 0), min(j1 + 1, len(lines))
        if j1 < j2:
            spans.append((starts[j1], starts[j2]))
    return spans


def _line_starts(lines: list[str]) -> list[int]:
    """Offset of the start of each line, plus the end of the text."""
    starts = [0]
    for line in lines:
        starts.append(starts[-1] + len(line))
    return starts
