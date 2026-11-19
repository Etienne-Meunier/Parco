"""Read-only views of a version: the AI context (diff since snapshot), threads and status."""

from __future__ import annotations

import difflib
from dataclasses import asdict, dataclass, field
from typing import Any

from parco.core.anchors import line_of
from parco.core.model import Edit, EditStatus, Mode, Phase, State, Thread, ThreadStatus
from parco.core.project import Project
from parco.core.review import require_review_phase, store_of
from parco.core.latex import CITE_TAG, cite_tag_spans, fill_tag_spans
from parco.core.rules import allowed_actions

DIFF_CONTEXT_LINES = 10


@dataclass
class Tag:
    """A `%parco` fill tag or a `\\cite{parco}` tag found in a tracked file."""

    kind: str
    line: int
    text: str


@dataclass
class FileView:
    """A tracked file as presented to the AI.

    `status` is "new" (no snapshot copy: full `content`), "changed" (unified `diff`
    snapshot -> current) or "unchanged" (context only, neither diff nor content).
    """

    path: str
    status: str
    diff: str | None = None
    content: str | None = None
    tags: list[Tag] = field(default_factory=list)


@dataclass
class ThreadView:
    """A thread with the edits linked to it."""

    thread: Thread
    edits: list[Edit]

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-compatible dict."""
        return {**self.thread.to_dict(), "edits": [e.to_dict() for e in self.edits]}


@dataclass
class Context:
    """Everything the AI needs to work on the open version."""

    version: int
    phase: Phase
    mode: Mode
    allowed_actions: list[str]
    main: str
    bib: str | None
    tracked_files: list[str]
    context_paths: list[str]
    first_version: bool
    files: list[FileView]
    edits: list[Edit]
    open_threads: list[ThreadView]

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-compatible dict."""
        data = {k: v for k, v in self.__dict__.items() if k not in {"files", "edits", "open_threads"}}
        data["files"] = [asdict(f) for f in self.files]
        data["edits"] = [e.to_dict() for e in self.edits]
        data["open_threads"] = [t.to_dict() for t in self.open_threads]
        return data


@dataclass
class StatusReport:
    """Summary of the state of the project for `parco status`."""

    version: int
    phase: Phase
    mode: Mode | None
    opened_at: str | None
    edit_counts: dict[str, int]
    open_threads: int
    resolved_threads: int
    modified_files: list[str]

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-compatible dict."""
        return asdict(self)


def build_context(project: Project) -> Context:
    """Build the AI context of the open version (refuses if tracked files were modified)."""
    state = _load_review_state(project)
    assert state.mode is not None
    tracked = project.tracked_files()
    bib = project.bib_path()
    return Context(
        version=state.version,
        phase=state.phase,
        mode=state.mode,
        allowed_actions=allowed_actions(state.phase, state.mode),
        main=tracked[0],
        bib=project.display(bib) if bib else None,
        tracked_files=tracked,
        context_paths=[str(p) for p in project.context_paths],
        first_version=not project.has_snapshot(),
        files=[_file_view(project, rel) for rel in tracked],
        edits=list(state.edits),
        open_threads=_thread_views(state, only_open=True),
    )


def list_threads(project: Project, only_open: bool) -> list[ThreadView]:
    """Threads of the open version with their linked edits."""
    return _thread_views(_load_review_state(project), only_open)


def build_status(project: Project) -> StatusReport:
    """Status of the project; never refuses, reports modified files instead."""
    state = store_of(project).load()
    counts = {s.value: sum(e.status is s for e in state.edits) for s in EditStatus}
    modified = project.modified_files(state.file_hashes) if state.phase is not Phase.IDLE else []
    return StatusReport(
        version=state.version,
        phase=state.phase,
        mode=state.mode,
        opened_at=state.opened_at,
        edit_counts=counts,
        open_threads=sum(t.status is ThreadStatus.OPEN for t in state.threads),
        resolved_threads=sum(t.status is ThreadStatus.RESOLVED for t in state.threads),
        modified_files=modified,
    )


def find_tags(source: str) -> list[Tag]:
    """Fill and cite tags of a source, in order of appearance."""
    found = [(start, "fill", source[start:end]) for start, end in fill_tag_spans(source)]
    found += [(start, "cite", CITE_TAG) for start, _ in cite_tag_spans(source)]
    return [Tag(kind=kind, line=line_of(source, start), text=text) for start, kind, text in sorted(found)]


def _load_review_state(project: Project) -> State:
    state = store_of(project).load()
    require_review_phase(state)
    project.ensure_unchanged(state.file_hashes)
    return state


def _file_view(project: Project, rel: str) -> FileView:
    current = project.read(rel)
    previous = project.snapshot_text(rel)
    tags = find_tags(current)
    if previous is None:
        return FileView(path=rel, status="new", content=current, tags=tags)
    if previous == current:
        return FileView(path=rel, status="unchanged", tags=tags)
    diff = difflib.unified_diff(
        previous.splitlines(keepends=True),
        current.splitlines(keepends=True),
        fromfile=f"snapshot/{rel}",
        tofile=rel,
        n=DIFF_CONTEXT_LINES,
    )
    return FileView(path=rel, status="changed", diff="".join(_terminated(line) for line in diff), tags=tags)


def _terminated(line: str) -> str:
    return line if line.endswith("\n") else line + "\n"


def _thread_views(state: State, only_open: bool) -> list[ThreadView]:
    return [
        ThreadView(thread=t, edits=state.edits_of_thread(t))
        for t in state.threads
        if not only_open or t.status is ThreadStatus.OPEN
    ]
