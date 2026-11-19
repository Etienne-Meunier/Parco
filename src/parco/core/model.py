"""Data model of a review version, serialized to `.parco/state.json`."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from parco.core.errors import NotFoundError

def now_iso() -> str:
    """Return the current UTC time as an ISO 8601 string (second precision)."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Phase(StrEnum):
    """Phase of the review workflow."""

    IDLE = "idle"
    READ = "read"
    ITERATE = "iterate"


class Mode(StrEnum):
    """Reading mode, ordered from weakest to strongest."""

    REFERENCE = "reference"
    COMMENT = "comment"
    PROOFREAD = "proofread"
    GENERATIVE = "generative"

    @property
    def rank(self) -> int:
        """Position of the mode in the weakest-to-strongest order."""
        return list(Mode).index(self)

    def includes(self, other: Mode) -> bool:
        """Return True if this mode grants the permissions of `other`."""
        return self.rank >= other.rank


class EditKind(StrEnum):
    """What an edit does to the source: fill a `%parco` tag, resolve a `\\cite{parco}`, or replace text."""

    REPLACE = "replace"
    FILL = "fill"
    CITE = "cite"


class EditStatus(StrEnum):
    """Decision of the user on an edit."""

    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class ThreadStatus(StrEnum):
    """Whether a discussion thread is still open."""

    OPEN = "open"
    RESOLVED = "resolved"


class Author(StrEnum):
    """Who wrote a message or performs an operation."""

    AI = "ai"
    USER = "user"


@dataclass
class Message:
    """One message of a thread."""

    author: Author
    body: str
    at: str = field(default_factory=now_iso)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-compatible dict."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Message:
        """Build a message from its serialized form."""
        return cls(author=Author(data["author"]), body=data["body"], at=data["at"])


@dataclass
class Edit:
    """A proposed replacement of `anchor` (found at `offset` in `file`) by `replace`.

    `offset` is a Python string index (Unicode code points) into the file read without
    newline translation, not a byte offset nor a UTF-16 index as in JavaScript.
    """

    id: str
    file: str
    offset: int
    anchor: str
    replace: str
    reason: str
    kind: EditKind
    bibtex: str | None = None
    thread: str | None = None
    supersedes: str | None = None
    status: EditStatus = EditStatus.PENDING
    created_at: str = field(default_factory=now_iso)

    @property
    def end(self) -> int:
        """Offset just after the anchor in the source file."""
        return self.offset + len(self.anchor)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-compatible dict."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Edit:
        """Build an edit from its serialized form."""
        return cls(
            **{
                **data,
                "kind": EditKind(data["kind"]),
                "status": EditStatus(data["status"]),
            }
        )


@dataclass
class Thread:
    """A discussion, anchored on source text, on an edit, or global (right column)."""

    id: str
    file: str | None = None
    offset: int | None = None
    anchor: str | None = None
    is_global: bool = False
    edit: str | None = None
    status: ThreadStatus = ThreadStatus.OPEN
    messages: list[Message] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-compatible dict (`is_global` is stored as `global`)."""
        return {
            "id": self.id,
            "file": self.file,
            "offset": self.offset,
            "anchor": self.anchor,
            "global": self.is_global,
            "edit": self.edit,
            "status": self.status,
            "messages": [m.to_dict() for m in self.messages],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Thread:
        """Build a thread from its serialized form."""
        return cls(
            id=data["id"],
            file=data["file"],
            offset=data["offset"],
            anchor=data["anchor"],
            is_global=data["global"],
            edit=data["edit"],
            status=ThreadStatus(data["status"]),
            messages=[Message.from_dict(m) for m in data["messages"]],
        )


@dataclass
class State:
    """Whole review state of the project: current version, phase, mode, edits and threads."""

    version: int = 1
    phase: Phase = Phase.IDLE
    mode: Mode | None = None
    opened_at: str | None = None
    file_hashes: dict[str, str] = field(default_factory=dict)
    edits: list[Edit] = field(default_factory=list)
    threads: list[Thread] = field(default_factory=list)
    next_id: int = 1

    def new_id(self, prefix: str) -> str:
        """Allocate the next id of the version (edits and threads share one counter)."""
        new = f"{prefix}{self.next_id}"
        self.next_id += 1
        return new

    def find_edit(self, edit_id: str) -> Edit:
        """Return the edit with this id, or raise NotFoundError."""
        for edit in self.edits:
            if edit.id == edit_id:
                return edit
        raise NotFoundError(f"No edit with id {edit_id!r} in this version.")

    def find_thread(self, thread_id: str) -> Thread:
        """Return the thread with this id, or raise NotFoundError."""
        for thread in self.threads:
            if thread.id == thread_id:
                return thread
        raise NotFoundError(f"No thread with id {thread_id!r} in this version.")

    def edits_of_thread(self, thread: Thread) -> list[Edit]:
        """Edits linked to a thread: proposed in it, or the edit it discusses."""
        return [e for e in self.edits if e.thread == thread.id or e.id == thread.edit]

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-compatible dict."""
        return {
            "version": self.version,
            "phase": self.phase,
            "mode": self.mode,
            "opened_at": self.opened_at,
            "file_hashes": dict(self.file_hashes),
            "edits": [e.to_dict() for e in self.edits],
            "threads": [t.to_dict() for t in self.threads],
            "next_id": self.next_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> State:
        """Build the state from its serialized form."""
        return cls(
            version=data["version"],
            phase=Phase(data["phase"]),
            mode=Mode(data["mode"]) if data["mode"] else None,
            opened_at=data["opened_at"],
            file_hashes=dict(data["file_hashes"]),
            edits=[Edit.from_dict(e) for e in data["edits"]],
            threads=[Thread.from_dict(t) for t in data["threads"]],
            next_id=data["next_id"],
        )
