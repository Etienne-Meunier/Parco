"""Review operations: lifecycle, mode permissions, iterate restrictions, overlap, supersede, frozen files."""

from __future__ import annotations

from pathlib import Path

import pytest

from parco.core import review
from parco.core.close import close_version
from parco.core.errors import (
    AnchorError,
    FilesModifiedError,
    InputError,
    NotFoundError,
    OverlapError,
    PhaseError,
    RuleError,
)
from parco.core.initialize import init_project
from parco.core.model import EditKind, EditStatus, Mode, Phase, ThreadStatus
from parco.core.project import Project
from parco.core.requests import CommentRequest, EditRequest
from parco.core.store import Store

GRAMMAR = EditRequest(file="main.tex", anchor="We shows", replace="We show", reason="grammar")
CITE = EditRequest(
    file="main.tex",
    anchor="works \\cite{parco}",
    replace="works \\cite{smith2020}",
    bibtex="@article{smith2020,\n  title={T}\n}",
)
FILL = EditRequest(file="sections/intro.tex", anchor="%parco explain why the scheme is stable", replace="Because.")
ANCHORED = CommentRequest(file="sections/intro.tex", anchor="The model are stable.\n", body="Unclear.")
GLOBAL = CommentRequest(body="Consider a figure.", is_global=True)


def _state(project: Project):
    return Store(project.parco_dir).load()


def test_open_records_hashes_and_refuses_second_open(project: Project) -> None:
    state = review.open_version(project, Mode.COMMENT)
    assert state.phase is Phase.READ and state.mode is Mode.COMMENT
    assert set(state.file_hashes) == {"main.tex", "sections/intro.tex"}
    with pytest.raises(PhaseError, match="already open"):
        review.open_version(project, Mode.PROOFREAD)


def test_operations_refused_when_idle(project: Project) -> None:
    with pytest.raises(PhaseError):
        review.start_iterate(project)
    with pytest.raises(PhaseError):
        review.add_ai_edits(project, [GRAMMAR])
    with pytest.raises(PhaseError):
        review.accept_edits(project, ["e1"])


def test_iterate_is_idempotent(project: Project) -> None:
    review.open_version(project, Mode.COMMENT)
    review.start_iterate(project)
    assert review.start_iterate(project).phase is Phase.ITERATE


def test_reference_mode(project: Project) -> None:
    review.open_version(project, Mode.REFERENCE)
    assert review.add_ai_edits(project, [CITE]) == ["e1"]
    edit = _state(project).edits[0]
    assert edit.kind is EditKind.CITE and edit.bibtex is not None
    with pytest.raises(RuleError):
        review.add_ai_edits(project, [GRAMMAR])
    with pytest.raises(RuleError):
        review.add_ai_threads(project, [GLOBAL])


def test_comment_mode(project: Project) -> None:
    review.open_version(project, Mode.COMMENT)
    assert review.add_ai_threads(project, [ANCHORED, GLOBAL]) == ["t1", "t2"]
    threads = _state(project).threads
    assert threads[0].offset == project.read("sections/intro.tex").index("The model are stable.\n")
    assert threads[1].is_global and threads[1].file is None
    with pytest.raises(RuleError, match="proofread"):
        review.add_ai_edits(project, [GRAMMAR])


def test_proofread_mode_refuses_fill(project: Project) -> None:
    review.open_version(project, Mode.PROOFREAD)
    assert review.add_ai_edits(project, [GRAMMAR]) == ["e1"]
    with pytest.raises(RuleError, match="generative"):
        review.add_ai_edits(project, [FILL])


def test_generative_mode_allows_everything(project: Project) -> None:
    review.open_version(project, Mode.GENERATIVE)
    assert review.add_ai_edits(project, [GRAMMAR, CITE, FILL]) == ["e1", "e2", "e3"]
    assert [e.kind for e in _state(project).edits] == [EditKind.REPLACE, EditKind.CITE, EditKind.FILL]


def test_bibtex_refused_outside_cite_and_must_be_an_entry(project: Project) -> None:
    review.open_version(project, Mode.GENERATIVE)
    with pytest.raises(RuleError, match="bibtex"):
        review.add_ai_edits(project, [EditRequest("main.tex", "We shows", "We show", bibtex="@a{k,}")])
    with pytest.raises(InputError, match="BibTeX"):
        review.add_ai_edits(project, [EditRequest(CITE.file, CITE.anchor, CITE.replace, bibtex="not bibtex")])


def test_edit_validation_errors(project: Project) -> None:
    review.open_version(project, Mode.GENERATIVE)
    with pytest.raises(AnchorError, match="ambiguous"):
        review.add_ai_edits(project, [EditRequest("sections/intro.tex", "The model are stable", "The models are stable")])
    with pytest.raises(AnchorError, match="not found"):
        review.add_ai_edits(project, [EditRequest("main.tex", "absent", "x")])
    with pytest.raises(InputError, match="not a tracked file"):
        review.add_ai_edits(project, [EditRequest("sections/old.tex", "old", "new")])
    with pytest.raises(RuleError, match="changes nothing"):
        review.add_ai_edits(project, [EditRequest("main.tex", "We shows", "We shows")])


def test_file_path_is_normalized(project: Project) -> None:
    review.open_version(project, Mode.PROOFREAD)
    review.add_ai_edits(project, [EditRequest("./sections/../main.tex", "We shows", "We show")])
    assert _state(project).edits[0].file == "main.tex"


def test_batch_is_all_or_nothing(project: Project) -> None:
    review.open_version(project, Mode.PROOFREAD)
    with pytest.raises(AnchorError, match="Item 1"):
        review.add_ai_edits(project, [GRAMMAR, EditRequest("main.tex", "absent", "x")])
    assert _state(project).edits == []
    assert _state(project).next_id == 1


def test_overlap_refused(project: Project) -> None:
    review.open_version(project, Mode.PROOFREAD)
    review.add_ai_edits(project, [GRAMMAR])
    with pytest.raises(OverlapError, match="e1"):
        review.add_ai_edits(project, [EditRequest("main.tex", "shows that", "show that")])
    review.add_ai_edits(project, [EditRequest("main.tex", "that the scheme", "that this scheme")])


def test_overlap_within_batch(project: Project) -> None:
    review.open_version(project, Mode.PROOFREAD)
    with pytest.raises(OverlapError):
        review.add_ai_edits(project, [GRAMMAR, EditRequest("main.tex", "shows that", "show that")])


def test_rejected_edit_does_not_block_and_cannot_be_revived_over_overlap(project: Project) -> None:
    review.open_version(project, Mode.PROOFREAD)
    review.add_ai_edits(project, [GRAMMAR])
    review.reject_edits(project, ["e1"])
    review.add_ai_edits(project, [EditRequest("main.tex", "shows that", "show that")])
    with pytest.raises(OverlapError):
        review.accept_edits(project, ["e1"])
    review.accept_edits(project, ["e2"])
    review.reset_edits(project, ["e2"])
    assert [e.status for e in _state(project).edits] == [EditStatus.REJECTED, EditStatus.PENDING]


def test_supersede(project: Project) -> None:
    review.open_version(project, Mode.PROOFREAD)
    review.add_ai_edits(project, [GRAMMAR])
    review.add_ai_edits(project, [EditRequest("main.tex", "We shows that", "We prove that", supersedes="e1")])
    first, second = _state(project).edits
    assert first.status is EditStatus.REJECTED
    assert second.supersedes == "e1" and second.status is EditStatus.PENDING


def test_supersede_refuses_accepted_and_unknown(project: Project) -> None:
    review.open_version(project, Mode.PROOFREAD)
    review.add_ai_edits(project, [GRAMMAR])
    review.accept_edits(project, ["e1"])
    with pytest.raises(RuleError, match="accepted"):
        review.add_ai_edits(project, [EditRequest("main.tex", "We shows that", "We prove that", supersedes="e1")])
    with pytest.raises(NotFoundError):
        review.add_ai_edits(project, [EditRequest("main.tex", "scheme", "method", supersedes="e9")])


def test_iterate_restrictions(project: Project) -> None:
    review.open_version(project, Mode.PROOFREAD)
    review.add_ai_threads(project, [ANCHORED])
    review.add_ai_edits(project, [GRAMMAR])
    user_thread = review.user_comment_edit(project, "e2", "Rather 'we prove'.")
    review.start_iterate(project)

    with pytest.raises(RuleError, match="iterate"):
        review.add_ai_threads(project, [GLOBAL])
    with pytest.raises(RuleError, match="thread"):
        review.add_ai_edits(project, [EditRequest("main.tex", "scheme", "method")])
    with pytest.raises(NotFoundError):
        review.add_ai_edits(project, [EditRequest("main.tex", "scheme", "method", thread="t9")])

    new_ids = review.add_ai_edits(
        project, [EditRequest("main.tex", "We shows that", "We prove that", thread=user_thread, supersedes="e2")]
    )
    review.ai_reply(project, user_thread, "Done in the new edit.")
    state = _state(project)
    thread = state.find_thread(user_thread)
    assert [m.author for m in thread.messages] == ["user", "ai"]
    assert [e.id for e in state.edits_of_thread(thread)] == ["e2", *new_ids]


def test_iterate_keeps_mode_rules(project: Project) -> None:
    review.open_version(project, Mode.COMMENT)
    review.add_ai_threads(project, [ANCHORED])
    review.user_reply(project, "t1", "Please fix it.")
    review.start_iterate(project)
    with pytest.raises(RuleError, match="proofread"):
        review.add_ai_edits(project, [EditRequest("main.tex", "We shows", "We show", thread="t1")])


def test_replies_only_on_open_threads(project: Project) -> None:
    review.open_version(project, Mode.COMMENT)
    review.add_ai_threads(project, [GLOBAL])
    review.user_reply(project, "t1", "Which figure?")
    review.resolve_thread(project, "t1")
    with pytest.raises(RuleError, match="resolved"):
        review.ai_reply(project, "t1", "A diagram.")
    review.reopen_thread(project, "t1")
    review.ai_reply(project, "t1", "A diagram.")
    assert _state(project).threads[0].status is ThreadStatus.OPEN
    with pytest.raises(InputError):
        review.ai_reply(project, "t1", "  ")


def test_user_threads_ignore_mode(project: Project) -> None:
    review.open_version(project, Mode.REFERENCE)
    assert review.user_add_thread(project, GLOBAL) == "t1"
    review.start_iterate(project)
    assert review.user_add_thread(project, ANCHORED) == "t2"


def test_frozen_files(project: Project) -> None:
    review.open_version(project, Mode.PROOFREAD)
    (project.root / "sections/intro.tex").write_text("edited by hand\n")
    for operation in (
        lambda: review.add_ai_edits(project, [GRAMMAR]),
        lambda: review.add_ai_threads(project, [GLOBAL]),
        lambda: review.start_iterate(project),
        lambda: review.accept_edits(project, ["e1"]),
    ):
        with pytest.raises(FilesModifiedError, match="sections/intro.tex"):
            operation()


def test_bibtex_keys_must_be_cited(project: Project) -> None:
    review.open_version(project, Mode.REFERENCE)
    request = EditRequest(CITE.file, CITE.anchor, CITE.replace, bibtex="@article{other2021,\n title={T}\n}")
    with pytest.raises(RuleError, match="other2021"):
        review.add_ai_edits(project, [request])


def test_cite_keys_must_be_a_key_list(project: Project) -> None:
    review.open_version(project, Mode.REFERENCE)
    with pytest.raises(RuleError, match="proofread"):
        review.add_ai_edits(project, [EditRequest(CITE.file, CITE.anchor, "works \\cite{a b}")])


def test_crlf_file_accepts_lf_anchor(tmp_path: Path) -> None:
    (tmp_path / "main.tex").write_bytes(b"Line one.\r\nWe shows it.\r\n")
    proj = init_project(tmp_path, "main.tex", [])
    review.open_version(proj, Mode.PROOFREAD)
    review.add_ai_edits(proj, [EditRequest("main.tex", "one.\nWe shows", "one.\nWe show")])
    review.add_ai_threads(proj, [CommentRequest(file="main.tex", anchor="it.\n", body="?")])
    state = _state(proj)
    assert state.edits[0].anchor == "one.\r\nWe shows" and state.edits[0].replace == "one.\r\nWe show"
    assert state.threads[0].anchor == "it.\r\n"


def test_supersede_needs_same_text_or_thread(project: Project) -> None:
    review.open_version(project, Mode.PROOFREAD)
    review.add_ai_edits(project, [GRAMMAR])
    with pytest.raises(RuleError, match="same text"):
        review.add_ai_edits(project, [EditRequest("main.tex", "scheme works", "scheme runs", supersedes="e1")])
    with pytest.raises(RuleError, match="same text"):
        review.add_ai_edits(project, [EditRequest("sections/intro.tex", "Introduction", "Intro", supersedes="e1")])
    thread = review.user_comment_edit(project, "e1", "Rephrase the whole sentence.")
    review.add_ai_edits(project, [EditRequest("main.tex", "scheme works", "scheme runs", supersedes="e1", thread=thread)])
    assert _state(project).find_edit("e1").status is EditStatus.REJECTED


def test_iterate_supersede_only_within_answered_thread(project: Project) -> None:
    review.open_version(project, Mode.PROOFREAD)
    review.add_ai_edits(project, [GRAMMAR])
    review.add_ai_threads(project, [ANCHORED])
    review.user_reply(project, "t2", "Please fix.")
    review.start_iterate(project)
    with pytest.raises(RuleError, match="linked to the answered thread"):
        review.add_ai_edits(project, [EditRequest("main.tex", "We shows that", "We prove that", supersedes="e1", thread="t2")])


def test_iterate_ai_waits_for_user(project: Project) -> None:
    review.open_version(project, Mode.PROOFREAD)
    review.add_ai_threads(project, [ANCHORED])
    review.start_iterate(project)
    fix = EditRequest("sections/intro.tex", "The model are stable.", "The models are stable.", thread="t1")
    with pytest.raises(RuleError, match="waiting for the user"):
        review.add_ai_edits(project, [fix])
    with pytest.raises(RuleError, match="waiting for the user"):
        review.ai_reply(project, "t1", "Anyone?")
    review.user_reply(project, "t1", "Yes, fix it.")
    review.add_ai_edits(project, [fix])
    review.ai_reply(project, "t1", "Done.")
    with pytest.raises(RuleError, match="waiting for the user"):
        review.ai_reply(project, "t1", "And another thing.")


def _second_version(project: Project, mode: Mode) -> None:
    """Close a first version, then add a paragraph to intro.tex and open version 2."""
    review.open_version(project, Mode.COMMENT)
    close_version(project)
    path = project.root / "sections/intro.tex"
    path.write_text(path.read_text() + "A new paragrap was added.\n")
    review.open_version(project, mode)


def test_version_two_only_reviews_changed_parts(project: Project) -> None:
    _second_version(project, Mode.GENERATIVE)
    with pytest.raises(RuleError, match="unchanged"):
        review.add_ai_edits(project, [GRAMMAR])
    with pytest.raises(RuleError, match="unchanged"):
        review.add_ai_threads(project, [ANCHORED])
    assert review.add_ai_edits(project, [EditRequest("sections/intro.tex", "paragrap", "paragraph")]) == ["e1"]
    assert review.add_ai_threads(project, [GLOBAL]) == ["t2"]


def test_version_two_tags_are_always_reviewable(project: Project) -> None:
    _second_version(project, Mode.GENERATIVE)
    assert review.add_ai_edits(project, [CITE, FILL]) == ["e1", "e2"]


def test_version_two_iterate_answers_user_threads_anywhere(project: Project) -> None:
    _second_version(project, Mode.PROOFREAD)
    review.user_add_thread(project, CommentRequest(file="main.tex", anchor="We shows", body="Fix this."))
    review.start_iterate(project)
    assert review.add_ai_edits(project, [EditRequest("main.tex", "We shows", "We show", thread="t1")]) == ["e2"]


def test_version_one_reviews_everything(project: Project) -> None:
    review.open_version(project, Mode.PROOFREAD)
    assert review.add_ai_edits(project, [GRAMMAR]) == ["e1"]


def test_user_thread_at_explicit_range(project: Project) -> None:
    review.open_version(project, Mode.COMMENT)
    text = project.read("sections/intro.tex")
    offset = text.index("The model are stable")
    thread_id = review.user_add_thread_at(project, "sections/intro.tex", offset, len("The model are stable"), "Which one?")
    thread = _state(project).find_thread(thread_id)
    assert (thread.offset, thread.anchor) == (offset, "The model are stable")
    with pytest.raises(InputError, match="Invalid range"):
        review.user_add_thread_at(project, "sections/intro.tex", len(text), 5, "x")
    with pytest.raises(InputError, match="not a tracked file"):
        review.user_add_thread_at(project, "sections/old.tex", 0, 1, "x")
