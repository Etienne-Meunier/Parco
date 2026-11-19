"""Parsing and validation of the JSON payloads that AI-side commands read on stdin."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from parco.core.errors import InputError


@dataclass(frozen=True)
class EditRequest:
    """An edit proposal: replace the unique occurrence of `anchor` in `file` by `replace`."""

    file: str
    anchor: str
    replace: str
    reason: str = ""
    bibtex: str | None = None
    thread: str | None = None
    supersedes: str | None = None

    @classmethod
    def from_dict(cls, data: Any, default_thread: str | None = None) -> EditRequest:
        """Validate a JSON object; `default_thread` applies when it has no `thread`."""
        obj = _require_object(data, {"file", "anchor", "replace", "reason", "bibtex", "thread", "supersedes"})
        return cls(
            file=_required_str(obj, "file"),
            anchor=_required_str(obj, "anchor"),
            replace=_required_str(obj, "replace", allow_empty=True),
            reason=_optional_str(obj, "reason") or "",
            bibtex=_optional_str(obj, "bibtex"),
            thread=_optional_str(obj, "thread") or default_thread,
            supersedes=_optional_str(obj, "supersedes"),
        )


@dataclass(frozen=True)
class CommentRequest:
    """A new thread: anchored on `anchor` in `file`, or global (right column) when `is_global`."""

    body: str
    file: str | None = None
    anchor: str | None = None
    is_global: bool = False

    @classmethod
    def from_dict(cls, data: Any) -> CommentRequest:
        """Validate a JSON object: either `{file, anchor, body}` or `{global: true, body}`."""
        obj = _require_object(data, {"file", "anchor", "body", "global"})
        body = _required_str(obj, "body")
        is_global = obj.get("global", False)
        if not isinstance(is_global, bool):
            raise InputError("`global` must be true or false.")
        if is_global:
            if "file" in obj or "anchor" in obj:
                raise InputError("A global comment takes no `file` nor `anchor`.")
            return cls(body=body, is_global=True)
        return cls(body=body, file=_required_str(obj, "file"), anchor=_required_str(obj, "anchor"))


def parse_reply_body(text: str) -> str:
    """Body of a reply read from stdin as `{"body": "..."}`."""
    return _required_str(_require_object(parse_json(text), {"body"}), "body")


def parse_items(text: str, batch: bool) -> list[Any]:
    """Decode stdin: a JSON object, or a non-empty JSON array of objects when `batch`."""
    data = parse_json(text)
    if not batch:
        if isinstance(data, list):
            raise InputError("Got a JSON array: use --batch to submit several items.")
        return [data]
    if not isinstance(data, list) or not data:
        raise InputError("--batch expects a non-empty JSON array.")
    return data


def parse_edit_requests(text: str, batch: bool, default_thread: str | None = None) -> list[EditRequest]:
    """Parse the stdin of `parco edit add`."""
    return [_at(i, batch, EditRequest.from_dict, item, default_thread) for i, item in enumerate(parse_items(text, batch))]


def parse_comment_requests(text: str, batch: bool) -> list[CommentRequest]:
    """Parse the stdin of `parco comment add`."""
    return [_at(i, batch, CommentRequest.from_dict, item) for i, item in enumerate(parse_items(text, batch))]


def parse_json(text: str) -> Any:
    """Decode JSON text, raising InputError with the position of the problem."""
    if not text.strip():
        raise InputError("Expected JSON on stdin, got nothing.")
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise InputError(f"Invalid JSON on stdin: {exc}") from exc


def _at(index: int, batch: bool, parse: Any, *args: Any) -> Any:
    try:
        return parse(*args)
    except InputError as exc:
        if not batch:
            raise
        raise InputError(f"Item {index}: {exc}") from exc


def _require_object(data: Any, allowed: set[str]) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise InputError("Expected a JSON object.")
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise InputError(f"Unknown field(s): {', '.join(unknown)}. Allowed: {', '.join(sorted(allowed))}.")
    return data


def _required_str(obj: dict[str, Any], key: str, allow_empty: bool = False) -> str:
    value = obj.get(key)
    if not isinstance(value, str) or (not value and not allow_empty):
        raise InputError(f"Field `{key}` is required and must be a {'string' if allow_empty else 'non-empty string'}.")
    return value


def _optional_str(obj: dict[str, Any], key: str) -> str | None:
    value = obj.get(key)
    if value is not None and not isinstance(value, str):
        raise InputError(f"Field `{key}` must be a string.")
    return value or None
