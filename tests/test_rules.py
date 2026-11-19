"""Edit classification and mode/phase permissions."""

from __future__ import annotations

import pytest

from parco.core.errors import RuleError
from parco.core.model import EditKind, Mode, Phase
from parco.core.latex import cite_resolution_keys, fill_tag_spans, strip_comments
from parco.core.model import Author, Message, Thread
from parco.core.rules import (
    allowed_actions,
    check_ai_answers_user,
    check_ai_edit,
    check_ai_new_thread,
    check_bibtex_keys,
    classify_edit,
    touches_tag,
)

SOURCE = "Text \\cite{parco} here.\n%parco explain this\nEnd. 50\\%parco not a tag\n"


def _classify(anchor: str, replace: str) -> EditKind:
    return classify_edit(SOURCE, SOURCE.index(anchor), anchor, replace)


def test_fill_tag_spans_ignore_escaped_percent() -> None:
    spans = fill_tag_spans(SOURCE)
    assert [SOURCE[a:b] for a, b in spans] == ["%parco explain this"]


@pytest.mark.parametrize(
    ("source", "tags"),
    [
        ("a\\\\%parco after a line break", ["%parco after a line break"]),
        ("a\\\\\\%parco escaped", []),
        ("%parco", ["%parco"]),
        ("%parcol not a tag", []),
    ],
)
def test_fill_tags_count_backslashes(source: str, tags: list[str]) -> None:
    assert [source[a:b] for a, b in fill_tag_spans(source)] == tags


def test_strip_comments_counts_backslashes() -> None:
    assert strip_comments("a \\% b\nc \\\\% d\ne \\\\\\% f") == "a \\% b\nc \\\\\ne \\\\\\% f"


def test_classify_fill_even_on_partial_tag() -> None:
    assert _classify("%parco explain this", "New text.") is EditKind.FILL
    assert _classify("here.\n%parco", "here. Added") is EditKind.FILL


def test_classify_cite() -> None:
    assert _classify("Text \\cite{parco} here", "Text \\cite{a, b} here") is EditKind.CITE


def test_classify_replace() -> None:
    assert _classify("End.", "The end.") is EditKind.REPLACE
    assert _classify("Text \\cite{parco} here", "Some \\cite{a} here") is EditKind.REPLACE


def test_cite_resolution_keys() -> None:
    assert cite_resolution_keys("\\cite{parco} and \\cite{parco}", "\\cite{a} and \\cite{b, c}") == ["a", "b", "c"]
    assert cite_resolution_keys("no tag", "no tag") is None
    for bad in ("\\cite{}", "\\citep{a}", "\\cite{a b}", "\\cite{a,,b}", "\\cite{\\x}", "\\cite{a%}", "\\cite{a,}"):
        assert cite_resolution_keys("\\cite{parco}", bad) is None, bad


def test_check_bibtex_keys() -> None:
    check_bibtex_keys(["a"], ["a", "b"])
    with pytest.raises(RuleError, match="z"):
        check_bibtex_keys(["a", "z"], ["a"])


def test_touches_tag() -> None:
    assert touches_tag(SOURCE, SOURCE.index("cite"), SOURCE.index("cite") + 2)
    assert not touches_tag(SOURCE, 0, 4)


def test_ai_answers_user_only_in_iterate() -> None:
    thread = Thread(id="t1", is_global=True, messages=[Message(Author.AI, "x")])
    check_ai_answers_user(Phase.READ, thread)
    with pytest.raises(RuleError, match="waiting for the user"):
        check_ai_answers_user(Phase.ITERATE, thread)
    thread.messages.append(Message(Author.USER, "y"))
    check_ai_answers_user(Phase.ITERATE, thread)


@pytest.mark.parametrize(
    ("mode", "allowed"),
    [
        (Mode.REFERENCE, {EditKind.CITE}),
        (Mode.COMMENT, {EditKind.CITE}),
        (Mode.PROOFREAD, {EditKind.CITE, EditKind.REPLACE}),
        (Mode.GENERATIVE, {EditKind.CITE, EditKind.REPLACE, EditKind.FILL}),
    ],
)
def test_edit_permissions_by_mode(mode: Mode, allowed: set[EditKind]) -> None:
    for kind in EditKind:
        if kind in allowed:
            check_ai_edit(Phase.READ, mode, kind, False, None)
        else:
            with pytest.raises(RuleError, match="requires"):
                check_ai_edit(Phase.READ, mode, kind, False, None)


def test_bibtex_only_on_cite() -> None:
    check_ai_edit(Phase.READ, Mode.REFERENCE, EditKind.CITE, True, None)
    with pytest.raises(RuleError, match="bibtex"):
        check_ai_edit(Phase.READ, Mode.PROOFREAD, EditKind.REPLACE, True, None)


def test_iterate_edits_need_thread() -> None:
    with pytest.raises(RuleError, match="thread"):
        check_ai_edit(Phase.ITERATE, Mode.PROOFREAD, EditKind.REPLACE, False, None)
    check_ai_edit(Phase.ITERATE, Mode.PROOFREAD, EditKind.REPLACE, False, "t1")


def test_thread_permissions() -> None:
    with pytest.raises(RuleError, match="comment mode"):
        check_ai_new_thread(Phase.READ, Mode.REFERENCE)
    for mode in (Mode.COMMENT, Mode.PROOFREAD, Mode.GENERATIVE):
        check_ai_new_thread(Phase.READ, mode)
        with pytest.raises(RuleError, match="iterate"):
            check_ai_new_thread(Phase.ITERATE, mode)


def test_allowed_actions_reflect_mode_and_phase() -> None:
    assert len(allowed_actions(Phase.READ, Mode.REFERENCE)) == 1
    assert any("fill" in a for a in allowed_actions(Phase.READ, Mode.GENERATIVE))
    iterate = allowed_actions(Phase.ITERATE, Mode.COMMENT)
    assert not any(a.startswith("comment add") for a in iterate)
    assert any(a.startswith("reply") for a in iterate)
