"""Word-level diff of edits."""

from __future__ import annotations

from parco.server.worddiff import word_diff


def test_small_change_keeps_common_words() -> None:
    assert word_diff("we shows that it works", "we show that it works") == [
        ("equal", "we ", "we "),
        ("change", "shows", "show"),
        ("equal", " that it works", " that it works"),
    ]


def test_whitespace_between_changes_is_folded() -> None:
    assert word_diff("a red big cat", "a blue small cat") == [
        ("equal", "a ", "a "),
        ("change", "red big", "blue small"),
        ("equal", " cat", " cat"),
    ]


def test_hunks_rebuild_both_texts() -> None:
    old, new = "Use \\cite{parco}. All of thoses", "Use \\cite{a,b}. All of those"
    hunks = word_diff(old, new)
    assert "".join(h[1] for h in hunks) == old
    assert "".join(h[2] for h in hunks) == new


def test_identical_and_empty_texts() -> None:
    assert word_diff("same", "same") == [("equal", "same", "same")]
    assert word_diff("", "new text") == [("change", "", "new text")]


def test_command_with_flat_argument_is_one_token() -> None:
    assert word_diff("as shown \\cite{parco}.", "as shown \\cite{a,b}.") == [
        ("equal", "as shown ", "as shown "),
        ("change", "\\cite{parco}", "\\cite{a,b}"),
        ("equal", ".", "."),
    ]


def test_rewrite_keeps_only_common_ends() -> None:
    old = "Then %parco explain why the scheme avoids the CFL restriction"
    new = "Then implicit schemes couple all unknowns, so the CFL restriction only bounds accuracy"
    hunks = word_diff(old, new)
    assert hunks[0] == ("equal", "Then ", "Then ")
    assert [h[0] for h in hunks] == ["equal", "change"]
