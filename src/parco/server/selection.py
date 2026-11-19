"""Map a selection made in the rendered document back to a source range.

The selected text is matched against a plain-text projection of the source (command
names, braces and comments dropped, LaTeX quotes and dashes turned into what pandoc
shows), ignoring whitespace, so that a selection across `\\emph{...}` or a citation
still finds its exact source span.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from parco.core.errors import InputError
from parco.server.markers import Block, DisplaySource
from parco.server.scan import MATH_ENVS, RESTRICTED_COMMANDS, math_dollar_end, skip_arguments
from parco.server.virtual import VirtualSource

_NAME_RE = re.compile(r"[A-Za-z@]+\*?")
_CITE_COMMANDS = frozenset({"cite", "citep", "citet", "citealp", "parencite", "textcite", "autocite"})
_LATEX_GLYPHS = (("---", "-"), ("--", "-"), ("``", '"'), ("''", '"'), ("`", "'"), ("~", " "))
_RENDERED_GLYPHS = str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"', "–": "-", "—": "-", " ": " "})


@dataclass(frozen=True)
class SourceRange:
    """A range of a tracked file."""

    file: str
    offset: int
    length: int


@dataclass(frozen=True)
class _PlainChar:
    """One visible character of the projection and the source span it comes from."""

    char: str
    start: int
    end: int


def anchor_for_selection(
    virtual: VirtualSource,
    display: DisplaySource,
    selected: str,
    before: str | None,
    after: str | None,
    occurrence: int | None = None,
) -> SourceRange:
    """Source range of `selected`, searched between the block markers around the selection.

    `before`/`after` are the ids of the block markers preceding and following the selection
    (missing: start / end of the document); `occurrence` is the index of the selection among
    the identical texts of that range, as counted in the page. If no single match is found,
    the whole paragraph of the preceding block is used.
    """
    target = "".join(selected.translate(_RENDERED_GLYPHS).split())
    if not target:
        raise InputError("The selection is empty.")
    first = display.block(before)
    following = display.block(after)
    start = first.start if first else 0
    end = following.start if following and following.start > start else len(virtual.text)
    found = _match(plain_projection(virtual.text, start, end), target, occurrence)
    if found is None:
        found = _match(plain_projection(virtual.text, start, end, drop_math=False), target, occurrence)
    if found is None:
        found = _paragraph(display, first)
    return _to_source_range(virtual, *found)


def plain_projection(text: str, start: int, end: int, drop_math: bool = True) -> list[_PlainChar]:
    """Visible characters of text[start:end] (whitespace excluded) with their source spans.

    MathJax output carries little selectable text, so math is dropped by default; with
    `drop_math=False` its content is kept, without delimiters or command names.
    """
    projector = _Projector(text, end, drop_math)
    chars: list[_PlainChar] = []
    i = start
    while i < end:
        consumed, emitted = projector.at(i)
        chars += [_PlainChar(c, i, i + consumed) for c in emitted if not c.isspace()]
        i += max(consumed, 1)
    return chars


class _Projector:
    """Plain-text projection of LaTeX constructs, one construct at a time."""

    def __init__(self, text: str, end: int, drop_math: bool) -> None:
        self.text = text
        self.end = end
        self.drop_math = drop_math

    def at(self, i: int) -> tuple[int, str]:
        """(characters consumed, visible text) of the construct starting at `i`."""
        text = self.text
        char = text[i]
        if char == "%":
            newline = text.find("\n", i, self.end)
            return (self.end if newline < 0 else newline) - i, ""
        if char == "$":
            return (self._stop(math_dollar_end(text, i)) - i if self.drop_math else 1), ""
        if char == "\\":
            return self._backslash(i)
        if char in "{}":
            return 1, ""
        for latex, shown in _LATEX_GLYPHS:
            if text.startswith(latex, i):
                return len(latex), shown
        return 1, char

    def _stop(self, position: int) -> int:
        return min(position, self.end)

    def _skip_to(self, closing: str, start: int) -> int:
        index = self.text.find(closing, start, self.end)
        return self.end if index < 0 else index + len(closing)

    def _backslash(self, i: int) -> tuple[int, str]:
        text = self.text
        name_match = _NAME_RE.match(text, i + 1, self.end)
        if name_match is None:
            following = text[i + 1:i + 2]
            if following in ("(", "["):
                closing = "\\)" if following == "(" else "\\]"
                return (self._skip_to(closing, i + 2) - i if self.drop_math else 2), ""
            return 2, (following if following in "%&$#_" else " ")
        name = name_match.group().rstrip("*")
        after_name = name_match.end()
        if name in _CITE_COMMANDS:
            stop = self._stop(skip_arguments(text, after_name, 1))
            keys = re.findall(r"\{([^{}]*)\}\s*$", text[after_name:stop])
            return stop - i, "[" + ",".join(k.strip() for k in (keys[0].split(",") if keys else [])) + "]"
        if name == "begin" and self.drop_math:
            env_end = self._stop(skip_arguments(text, after_name, 1))
            env = text[after_name + 1:env_end - 1]
            if env in MATH_ENVS:
                return self._skip_to(f"\\end{{{env}}}", env_end) - i, ""
        if name in {"begin", "end"} or name in RESTRICTED_COMMANDS:
            brace_count = 1 if name in {"begin", "end"} else RESTRICTED_COMMANDS[name]
            return self._stop(skip_arguments(text, after_name, brace_count)) - i, ""
        return after_name - i, ""


def _match(chars: list[_PlainChar], target: str, occurrence: int | None) -> tuple[int, int] | None:
    plain = "".join(c.char for c in chars)
    hits = [m.start() for m in re.finditer(f"(?={re.escape(target)})", plain)]
    if len(hits) == 1:
        index = hits[0]
    elif occurrence is not None and 0 <= occurrence < len(hits):
        index = hits[occurrence]
    else:
        return None
    return chars[index].start, chars[index + len(target) - 1].end


def _paragraph(display: DisplaySource, block: Block | None) -> tuple[int, int]:
    chosen = block or (display.blocks[0] if display.blocks else None)
    if chosen is None:
        raise InputError("The document has no text to anchor a comment on.")
    return chosen.start, chosen.paragraph_end


def _to_source_range(virtual: VirtualSource, vstart: int, vend: int) -> SourceRange:
    """Clip to the file segment holding `vstart` (a range cannot span an included file)."""
    segment = virtual.segment_at(vstart)
    vend = min(vend, segment.vend)
    if vend <= vstart:
        raise InputError("Could not map the selection to the source.")
    file, offset = virtual.to_file(vstart)
    return SourceRange(file=file, offset=offset, length=vend - vstart)
