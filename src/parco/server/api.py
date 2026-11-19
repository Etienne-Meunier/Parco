"""User-side API of the platform: each call maps to core operations and returns JSON-able data."""

from __future__ import annotations

import hashlib
from dataclasses import asdict
from pathlib import Path
from typing import Any

from parco.core import review
from parco.core.close import close_version
from parco.core.context import build_status
from parco.core.errors import InputError, ParcoError
from parco.core.model import Phase, State
from parco.core.project import Project
from parco.core.requests import CommentRequest
from parco.server.figures import FigureCache
from parco.server.render import RenderedDocument, Renderer, RenderError
from parco.server.selection import anchor_for_selection
from parco.server.worddiff import word_diff

FIGURE_CACHE_DIR = "cache/figures"


class StaleDocumentError(ParcoError):
    """A request refers to a rendering of the document that is no longer current."""


class Api:
    """Operations behind the HTTP endpoints, for one project."""

    def __init__(self, project: Project) -> None:
        self.project = project
        self.renderer = Renderer()
        self.figure_cache = FigureCache(project.parco_dir / FIGURE_CACHE_DIR)

    def _load(self) -> State:
        return review.store_of(self.project).load()

    def _frozen(self, state: State) -> list[str]:
        return self.project.modified_files(state.file_hashes) if state.phase is not Phase.IDLE else []

    def state(self) -> dict[str, Any]:
        """Version, phase, mode, frozen files, counts, edits (with their word diff) and threads."""
        status = build_status(self.project)
        state = self._load()
        return {
            "project": self.project.root.name,
            "version": status.version,
            "phase": status.phase,
            "mode": status.mode,
            "opened_at": status.opened_at,
            "frozen": status.modified_files,
            "counts": status.edit_counts,
            "open_threads": status.open_threads,
            "edits": [{**e.to_dict(), "diff": word_diff(e.anchor, e.replace)} for e in state.edits],
            "threads": [t.to_dict() for t in state.threads],
        }

    def document(self) -> dict[str, Any]:
        """Rendered HTML and per-item display metadata; items are omitted when idle or frozen."""
        state = self._load()
        with_items = state.phase is not Phase.IDLE and not self._frozen(state)
        try:
            doc = self._render(state, with_items)
        except RenderError as exc:
            return {"html": "", "error": str(exc), "render_key": None, "items": {}, "fills": {}}
        return {
            "html": doc.html,
            "error": None,
            "render_key": doc.key,
            "items": {item_id: asdict(placement) for item_id, placement in doc.display.placements.items()},
            "fills": {fill_id: asdict(fill) for fill_id, fill in doc.display.fills.items()},
        }

    def _render(self, state: State, with_items: bool) -> RenderedDocument:
        edits = state.edits if with_items else []
        threads = state.threads if with_items else []
        return self.renderer.render(self.project, edits, threads)

    def revision(self) -> dict[str, str]:
        """Cheap token that changes whenever the state or a tracked file changes."""
        digest = hashlib.sha1()
        paths = [review.store_of(self.project).state_path]
        try:
            paths += [self.project.path(rel) for rel in self.project.tracked_files()]
        except (ParcoError, OSError):
            digest.update(b"tracked-files-unavailable")
        for path in paths:
            digest.update(_stat_signature(path).encode())
        return {"revision": digest.hexdigest()}

    def set_edit_status(self, edit_id: str, action: str) -> dict[str, Any]:
        """Accept, reject or reset an edit."""
        operations = {"accept": review.accept_edits, "reject": review.reject_edits, "reset": review.reset_edits}
        operations[action](self.project, [edit_id])
        return {"ok": True}

    def create_thread(self, payload: dict[str, Any]) -> dict[str, Any]:
        """New user thread: `{global: true, body}` or `{selection, before, after, occurrence, render_key, body}`.

        A selection is only mapped if it was made on the current rendering (`render_key`).
        """
        body = _required_str(payload, "body")
        if payload.get("global") is True:
            return {"id": review.user_add_thread(self.project, CommentRequest(body=body, is_global=True))}
        selected = _required_str(payload, "selection")
        doc = self._render(self._load(), with_items=True)
        if payload.get("render_key") != doc.key:
            raise StaleDocumentError("The document changed since the selection was made: select the text again.")
        occurrence = payload.get("occurrence")
        found = anchor_for_selection(
            doc.virtual, doc.display, selected, payload.get("before"), payload.get("after"),
            occurrence if isinstance(occurrence, int) else None,
        )
        thread_id = review.user_add_thread_at(self.project, found.file, found.offset, found.length, body)
        return {"id": thread_id}

    def discuss_edit(self, edit_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Open a user thread on an edit."""
        return {"id": review.user_comment_edit(self.project, edit_id, _required_str(payload, "body"))}

    def reply(self, thread_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """User reply in an open thread."""
        review.user_reply(self.project, thread_id, _required_str(payload, "body"))
        return {"ok": True}

    def set_thread_status(self, thread_id: str, action: str) -> dict[str, Any]:
        """Resolve or reopen a thread."""
        operation = review.resolve_thread if action == "resolve" else review.reopen_thread
        operation(self.project, thread_id)
        return {"ok": True}

    def close(self) -> dict[str, Any]:
        """Close the version and return the close report."""
        return asdict(close_version(self.project))

    def figure(self, source: str) -> tuple[Path, str] | None:
        """(file, content type) of `\\includegraphics{source}` in the last rendered document, or None."""
        doc = self.renderer.last
        path = doc.figures.get(source) if doc else None
        return self.figure_cache.servable(path) if path else None


def _required_str(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise InputError(f"Field `{key}` is required and must be a non-empty string.")
    return value


def _stat_signature(path: Path) -> str:
    try:
        info = path.stat()
    except OSError:
        return f"{path}:missing"
    return f"{path}:{info.st_mtime_ns}:{info.st_size}"
