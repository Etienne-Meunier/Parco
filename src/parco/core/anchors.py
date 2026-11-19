"""Resolution of anchors: exact pieces of source text that must match exactly once."""

from __future__ import annotations

from parco.core.errors import AnchorError


def find_all(source: str, anchor: str) -> list[int]:
    """Offsets of every occurrence of `anchor` in `source`, overlapping occurrences included."""
    offsets: list[int] = []
    start = source.find(anchor)
    while start != -1:
        offsets.append(start)
        start = source.find(anchor, start + 1)
    return offsets


def locate(source: str, anchor: str, file: str) -> int:
    """Offset of the unique occurrence of `anchor` in `source`, or raise AnchorError."""
    if not anchor:
        raise AnchorError("The anchor is empty: quote an exact piece of the source text.")
    offsets = find_all(source, anchor)
    if not offsets:
        raise AnchorError(
            f"Anchor not found in {file}: {anchor!r}. "
            "The anchor must be an exact copy of the source text (same spaces, newlines, backslashes)."
        )
    if len(offsets) > 1:
        raise AnchorError(
            f"Anchor is ambiguous in {file}, {len(offsets)} matches: {anchor!r}. "
            "Include more surrounding text so that it matches exactly once."
        )
    return offsets[0]


def spans_overlap(a_start: int, a_end: int, b_start: int, b_end: int) -> bool:
    """True if the half-open intervals [a_start, a_end) and [b_start, b_end) intersect."""
    return a_start < b_end and b_start < a_end


def line_of(source: str, offset: int) -> int:
    """1-based line number of `offset` in `source`."""
    return source.count("\n", 0, offset) + 1


def match_newlines(text: str, source: str) -> str:
    """Rewrite the newlines of `text` as `\\r\\n` when `source` uses Windows line endings."""
    if "\r\n" not in source:
        return text
    return text.replace("\r\n", "\n").replace("\n", "\r\n")
