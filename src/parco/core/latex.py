"""LaTeX syntax parco relies on: comments, `%parco` fill tags and `\\cite{parco}` tags."""

from __future__ import annotations

import re

# A `%` starts a comment unless escaped, i.e. preceded by an odd number of backslashes.
_UNESCAPED = r"(?<!\\)(?:\\\\)*"
_COMMENT_RE = re.compile(rf"({_UNESCAPED})%[^\n]*")
_FILL_TAG_RE = re.compile(rf"{_UNESCAPED}(%parco\b[^\n]*)")

CITE_TAG = r"\cite{parco}"
_CITE_KEY = r"[^\s,{}\\%]+"
_CITE_WITH_KEYS = rf"\\cite\{{(\s*{_CITE_KEY}(?:\s*,\s*{_CITE_KEY})*\s*)\}}"


def strip_comments(source: str) -> str:
    """Remove LaTeX line comments (an unescaped `%` up to the end of the line)."""
    return _COMMENT_RE.sub(r"\1", source)


def comment_spans(source: str) -> list[tuple[int, int]]:
    """Spans of every LaTeX line comment, from its `%` to the end of the line (newline excluded)."""
    return [(m.end(1), m.end()) for m in _COMMENT_RE.finditer(source)]


def fill_tag_spans(source: str) -> list[tuple[int, int]]:
    """Spans of every `%parco ...` fill tag (from `%parco` to the end of its line)."""
    return [m.span(1) for m in _FILL_TAG_RE.finditer(source)]


def cite_tag_spans(source: str) -> list[tuple[int, int]]:
    """Spans of every literal `\\cite{parco}` tag."""
    return [m.span() for m in re.finditer(re.escape(CITE_TAG), source)]


def cite_resolution_keys(anchor: str, replace: str) -> list[str] | None:
    """Keys introduced if `replace` is `anchor` with each `\\cite{parco}` turned into `\\cite{k1,k2}`.

    Returns None when `replace` is not such a resolution (or `anchor` has no `\\cite{parco}`).
    """
    if CITE_TAG not in anchor:
        return None
    pattern = _CITE_WITH_KEYS.join(re.escape(part) for part in anchor.split(CITE_TAG))
    match = re.fullmatch(pattern, replace)
    if match is None:
        return None
    return [key.strip() for group in match.groups() for key in group.split(",")]
