"""Anchor resolution."""

from __future__ import annotations

import pytest

from parco.core.anchors import find_all, line_of, locate, spans_overlap
from parco.core.errors import AnchorError


def test_unique_anchor() -> None:
    assert locate("abc def ghi", "def", "f.tex") == 4


def test_missing_anchor() -> None:
    with pytest.raises(AnchorError, match="not found"):
        locate("abc", "xyz", "f.tex")


def test_ambiguous_anchor_counts_matches() -> None:
    with pytest.raises(AnchorError, match="ambiguous.*3 matches"):
        locate("the cat, the dog, the end", "the", "f.tex")


def test_overlapping_occurrences_are_ambiguous() -> None:
    assert find_all("aaa", "aa") == [0, 1]
    with pytest.raises(AnchorError, match="2 matches"):
        locate("aaa", "aa", "f.tex")


def test_empty_anchor() -> None:
    with pytest.raises(AnchorError, match="empty"):
        locate("abc", "", "f.tex")


def test_anchor_with_latex_and_newlines() -> None:
    source = "a\n\\emph{$x$}\nb"
    assert locate(source, "\\emph{$x$}\nb", "f.tex") == 2
    assert line_of(source, 2) == 2


def test_spans_overlap() -> None:
    assert spans_overlap(0, 5, 4, 8)
    assert not spans_overlap(0, 5, 5, 8)
    assert spans_overlap(2, 3, 0, 10)
