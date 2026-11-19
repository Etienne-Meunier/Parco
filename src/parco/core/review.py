"""Review operations of a version: open, iterate, edits, threads, replies and user decisions.

Every operation runs as one locked load -> mutate -> save transaction; an error aborts it
without saving, which makes batches all-or-nothing.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import TypeVar

from parco.core.anchors import locate, match_newlines, spans_overlap
from parco.core.bib import entry_keys
from parco.core.changes import Span, changed_spans
from parco.core.latex import cite_resolution_keys
from parco.core.errors import InputError, OverlapError, ParcoError, PhaseError, RuleError
from parco.core.model import (
    Author,
    Edit,
    EditStatus,
    Message,
    Mode,
    Phase,
    State,
    Thread,
    ThreadStatus,
    now_iso,
)
from parco.core.project import Project
from parco.core.requests import CommentRequest, EditRequest
from parco.core.rules import (
    check_ai_answers_user,
    check_ai_edit,
    check_ai_new_thread,
    check_bibtex_keys,
    classify_edit,
    touches,
    touches_tag,
)
from parco.core.store import Store

T = TypeVar("T")


def store_of(project: Project) -> Store:
    """The state store of a project."""
    return Store(project.parco_dir)


def require_review_phase(state: State) -> None:
    """Raise PhaseError if no version is open."""
    if state.phase is Phase.IDLE:
        raise PhaseError("No version is open. Start one with `parco open --mode <mode>`.")


@contextmanager
def review_transaction(project: Project) -> Iterator[State]:
    """Transaction for an operation inside an open version with unmodified tracked files."""
    with store_of(project).transaction() as state:
        require_review_phase(state)
        project.ensure_unchanged(state.file_hashes)
        yield state


def open_version(project: Project, mode: Mode) -> State:
    """Start a new version in `mode` (read phase), recording the hashes of the tracked files."""
    with store_of(project).transaction() as state:
        if state.phase is not Phase.IDLE:
            raise PhaseError(
                f"Version {state.version} is already open ({state.phase} phase, {state.mode} mode). "
                "Run `parco close` before opening a new one."
            )
        state.phase = Phase.READ
        state.mode = mode
        state.opened_at = now_iso()
        state.file_hashes = project.current_hashes()
        state.edits = []
        state.threads = []
        state.next_id = 1
        return state


def start_iterate(project: Project) -> State:
    """Move the open version to the iterate phase (idempotent once in iterate)."""
    with review_transaction(project) as state:
        state.phase = Phase.ITERATE
        return state


def add_ai_edits(project: Project, requests: list[EditRequest]) -> list[str]:
    """Validate and record edits proposed by the AI; returns their ids (all or nothing)."""
    with review_transaction(project) as state:
        sources = _SourceCache(project)
        return _each(requests, lambda req: _add_ai_edit(state, req, sources).id)


def add_ai_threads(project: Project, requests: list[CommentRequest]) -> list[str]:
    """Validate and record threads opened by the AI; returns their ids (all or nothing)."""
    with review_transaction(project) as state:
        sources = _SourceCache(project)

        def add(request: CommentRequest) -> str:
            assert state.mode is not None
            check_ai_new_thread(state.phase, state.mode)
            return _add_thread(state, request, Author.AI, sources, restrict_to_changes=True).id

        return _each(requests, add)


def ai_reply(project: Project, thread_id: str, body: str) -> None:
    """Add an AI message to an open thread (in iterate, only if the user spoke last)."""
    _reply(project, thread_id, body, Author.AI)


def user_reply(project: Project, thread_id: str, body: str) -> None:
    """Add a user message to an open thread."""
    _reply(project, thread_id, body, Author.USER)


def user_add_thread(project: Project, request: CommentRequest) -> str:
    """Open a thread as the user (anchored or global, any mode or phase of a version)."""
    with review_transaction(project) as state:
        return _add_thread(state, request, Author.USER, _SourceCache(project), restrict_to_changes=False).id


def user_add_thread_at(project: Project, file: str, offset: int, length: int, body: str) -> str:
    """Open a user thread on an explicit source range (no uniqueness required, e.g. from a selection)."""
    with review_transaction(project) as state:
        sources = _SourceCache(project)
        rel = sources.resolve(file)
        text = sources.text(rel)
        if length <= 0 or offset < 0 or offset + length > len(text):
            raise InputError(f"Invalid range [{offset}, {offset + length}) in {rel}.")
        thread = Thread(
            id=state.new_id("t"),
            file=rel,
            offset=offset,
            anchor=text[offset:offset + length],
            messages=[Message(Author.USER, _non_empty(body))],
        )
        state.threads.append(thread)
        return thread.id


def user_comment_edit(project: Project, edit_id: str, body: str) -> str:
    """Open a user thread discussing an edit; returns the thread id."""
    with review_transaction(project) as state:
        edit = state.find_edit(edit_id)
        thread = Thread(
            id=state.new_id("t"),
            file=edit.file,
            offset=edit.offset,
            anchor=edit.anchor,
            edit=edit.id,
            messages=[Message(Author.USER, _non_empty(body))],
        )
        state.threads.append(thread)
        return thread.id


def resolve_thread(project: Project, thread_id: str) -> None:
    """Mark a thread as resolved (user)."""
    _set_thread_status(project, thread_id, ThreadStatus.RESOLVED)


def reopen_thread(project: Project, thread_id: str) -> None:
    """Reopen a resolved thread (user)."""
    _set_thread_status(project, thread_id, ThreadStatus.OPEN)


def accept_edits(project: Project, edit_ids: list[str]) -> None:
    """Accept edits (user). They are only written to the sources at close."""
    _set_edit_status(project, edit_ids, EditStatus.ACCEPTED)


def reject_edits(project: Project, edit_ids: list[str]) -> None:
    """Reject edits (user)."""
    _set_edit_status(project, edit_ids, EditStatus.REJECTED)


def reset_edits(project: Project, edit_ids: list[str]) -> None:
    """Put edits back to pending (user)."""
    _set_edit_status(project, edit_ids, EditStatus.PENDING)


class _SourceCache:
    """Sources of tracked files read at most once per operation."""

    def __init__(self, project: Project) -> None:
        self.project = project
        self.tracked = project.tracked_files()
        self.has_snapshot = project.has_snapshot()
        self.texts: dict[str, str] = {}
        self.changes: dict[str, list[Span]] = {}

    def resolve(self, file: str) -> str:
        """Normalized tracked path of `file`, or raise InputError."""
        rel = self.project.normalize(file)
        if rel not in self.tracked:
            raise InputError(f"{file!r} is not a tracked file. Tracked files: {', '.join(self.tracked)}.")
        return rel

    def text(self, rel: str) -> str:
        """Content of a tracked file."""
        if rel not in self.texts:
            self.texts[rel] = self.project.read(rel)
        return self.texts[rel]

    def changed(self, rel: str) -> list[Span]:
        """Spans of a tracked file changed since the snapshot."""
        if rel not in self.changes:
            self.changes[rel] = changed_spans(self.project.snapshot_text(rel), self.text(rel))
        return self.changes[rel]


def _each(items: list[T], operation: Callable[[T], str]) -> list[str]:
    """Apply `operation` to every item, prefixing error messages with the item index in batches."""
    results: list[str] = []
    for index, item in enumerate(items):
        try:
            results.append(operation(item))
        except ParcoError as exc:
            if len(items) > 1:
                exc.args = (f"Item {index}: {exc}",)
            raise
    return results


def _add_ai_edit(state: State, request: EditRequest, sources: _SourceCache) -> Edit:
    assert state.mode is not None
    file = sources.resolve(request.file)
    source = sources.text(file)
    anchor = match_newlines(request.anchor, source)
    replace = match_newlines(request.replace, source)
    if replace == anchor:
        raise RuleError("The edit changes nothing: `replace` equals `anchor`.")
    offset = locate(source, anchor, file)
    end = offset + len(anchor)
    kind = classify_edit(source, offset, anchor, replace)
    thread = _thread_answered_by_ai(state, request.thread) if request.thread else None
    check_ai_edit(state.phase, state.mode, kind, request.bibtex is not None, request.thread)
    if request.bibtex is not None:
        _check_bibtex(request.bibtex, anchor, replace)
    _ensure_in_changed_region(state, sources, file, offset, end, thread)
    superseded = _superseded_edit(state, request.supersedes, file, offset, end, thread) if request.supersedes else None
    _ensure_no_overlap(state, file, offset, end, superseded)
    edit = Edit(
        id=state.new_id("e"),
        file=file,
        offset=offset,
        anchor=anchor,
        replace=replace,
        reason=request.reason,
        kind=kind,
        bibtex=request.bibtex,
        thread=request.thread,
        supersedes=request.supersedes,
    )
    if superseded is not None:
        superseded.status = EditStatus.REJECTED
    state.edits.append(edit)
    return edit


def _add_thread(
    state: State, request: CommentRequest, author: Author, sources: _SourceCache, restrict_to_changes: bool
) -> Thread:
    file = offset = anchor = None
    if not request.is_global:
        assert request.file is not None and request.anchor is not None
        file = sources.resolve(request.file)
        anchor = match_newlines(request.anchor, sources.text(file))
        offset = locate(sources.text(file), anchor, file)
        if restrict_to_changes:
            _ensure_in_changed_region(state, sources, file, offset, offset + len(anchor), None)
    thread = Thread(
        id=state.new_id("t"),
        file=file,
        offset=offset,
        anchor=anchor,
        is_global=request.is_global,
        messages=[Message(author, request.body)],
    )
    state.threads.append(thread)
    return thread


def _thread_answered_by_ai(state: State, thread_id: str) -> Thread:
    thread = _open_thread(state, thread_id)
    check_ai_answers_user(state.phase, thread)
    return thread


def _check_bibtex(bibtex: str, anchor: str, replace: str) -> None:
    keys = entry_keys(bibtex)
    if not keys:
        raise InputError("`bibtex` must contain at least one BibTeX entry like `@article{key, ...}`.")
    check_bibtex_keys(keys, cite_resolution_keys(anchor, replace) or [])


def _ensure_in_changed_region(
    state: State, sources: _SourceCache, file: str, start: int, end: int, thread: Thread | None
) -> None:
    """From version 2 on, the AI only works on text changed since the snapshot.

    Exempt: anchors touching a tag, and iterate answers in a thread where the user wrote.
    """
    if not sources.has_snapshot or touches_tag(sources.text(file), start, end):
        return
    if state.phase is Phase.ITERATE and thread is not None and any(m.author is Author.USER for m in thread.messages):
        return
    if not touches(sources.changed(file), start, end):
        raise RuleError(
            f"Refused: this anchor in {file} lies in text unchanged since the last version, which is "
            "context only. Only the changed lines (see the diffs of `parco context`) are reviewed; "
            "use a global comment for remarks on the rest."
        )


def _reply(project: Project, thread_id: str, body: str, author: Author) -> None:
    with review_transaction(project) as state:
        thread = _open_thread(state, thread_id)
        if author is Author.AI:
            check_ai_answers_user(state.phase, thread)
        thread.messages.append(Message(author, _non_empty(body)))


def _open_thread(state: State, thread_id: str) -> Thread:
    thread = state.find_thread(thread_id)
    if thread.status is not ThreadStatus.OPEN:
        raise RuleError(f"Thread {thread_id} is resolved; only open threads can be answered.")
    return thread


def _superseded_edit(state: State, edit_id: str, file: str, start: int, end: int, thread: Thread | None) -> Edit:
    """The edit a new edit supersedes: on the same text, or linked to the same thread (only the latter in iterate)."""
    edit = state.find_edit(edit_id)
    if edit.status is EditStatus.ACCEPTED:
        raise RuleError(f"Edit {edit_id} is accepted and cannot be superseded.")
    linked = thread is not None and edit in state.edits_of_thread(thread)
    if state.phase is Phase.ITERATE and not linked:
        raise RuleError(f"In iterate, only an edit linked to the answered thread can be superseded, not {edit_id}.")
    same_text = edit.file == file and spans_overlap(start, end, edit.offset, edit.end)
    if not (linked or same_text):
        raise RuleError(
            f"Edit {edit_id} cannot be superseded by this edit: it must cover the same text "
            "(same file, overlapping anchor) or be linked to the same thread."
        )
    return edit


def _ensure_no_overlap(state: State, file: str, start: int, end: int, ignore: Edit | None) -> None:
    for other in state.edits:
        if other is ignore or other.file != file or other.status is EditStatus.REJECTED:
            continue
        if spans_overlap(start, end, other.offset, other.end):
            raise OverlapError(
                f"The edit overlaps edit {other.id} ({other.status}) on {other.anchor!r} in {file}. "
                "Overlapping edits are refused: supersede it or choose a disjoint anchor."
            )


def _set_edit_status(project: Project, edit_ids: list[str], status: EditStatus) -> None:
    with review_transaction(project) as state:
        for edit_id in edit_ids:
            edit = state.find_edit(edit_id)
            if edit.status is EditStatus.REJECTED and status is not EditStatus.REJECTED:
                # Non-rejected edits never overlap: reviving a rejected edit must keep that true.
                _ensure_no_overlap(state, edit.file, edit.offset, edit.end, ignore=edit)
            edit.status = status


def _set_thread_status(project: Project, thread_id: str, status: ThreadStatus) -> None:
    with review_transaction(project) as state:
        state.find_thread(thread_id).status = status


def _non_empty(body: str) -> str:
    if not body.strip():
        raise InputError("The message body is empty.")
    return body
