"""Display source: the virtual LaTeX with sentinel tokens marking edits, threads, tags and paragraphs.

Tokens are plain ASCII words (`ZQPM<kind><id>QZ`) that pandoc copies to the HTML as text.
An item whose tokens cannot live where it sits (math, command arguments, unbalanced
snippets...) gets a single point token at the nearest safe place before it instead.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from parco.core.latex import fill_tag_spans, strip_comments
from parco.core.model import Edit, EditStatus, Thread
from parco.server.scan import SourceMap, is_balanced, is_structural
from parco.server.virtual import VirtualSource
from parco.server.worddiff import word_diff

TOKEN_RE = re.compile(r"ZQPM(ds|de|is|ie|as|ae|ts|te|pt|fp|bk)([a-z]+\d+)QZ")
TOKEN_KINDS = {
    "ds": "del-start",
    "de": "del-end",
    "is": "ins-start",
    "ie": "ins-end",
    "as": "acc-start",
    "ae": "acc-end",
    "ts": "thread-start",
    "te": "thread-end",
    "pt": "point",
    "fp": "fill",
    "bk": "block",
}
# Order of insertions sharing one position: close a range before opening the next one.
_RANK = {"te": 0, "bk": 1, "pt": 2, "fp": 3, "ts": 4}
_LINE_START = re.compile(r"\n[ \t]*")
_ITEM = re.compile(r"\\item\b(?:\[[^\]\n]*\])?\s*")
_PARAGRAPH_END = re.compile(r"\n[ \t]*(?:\n|\\(?:begin|end|item|section|subsection|subsubsection|paragraph)\b)")
_LEADING_STRUCTURE = re.compile(r"(?:\\begin\{[^{}]*\}(?:\[[^\]]*\])?|\\item\b(?:\[[^\]\n]*\])?|\s)+")
_TEXT_COMMANDS = ("\\emph{", "\\textbf{", "\\textit{", "\\textsc{", "\\underline{")


def token(kind: str, item_id: str) -> str:
    """Sentinel token of an item."""
    return f"ZQPM{kind}{item_id}QZ"


@dataclass(frozen=True)
class FillTag:
    """A `%parco` tag shown as a visible placeholder."""

    id: str
    instruction: str
    file: str
    line: int


@dataclass(frozen=True)
class Block:
    """A paragraph of the virtual source: selection mapping searches between block starts."""

    id: str
    start: int
    paragraph_end: int


@dataclass(frozen=True)
class Placement:
    """How an item appears: `inline` (wrapped), `point` (one marker), or `hidden` (not mappable
    to the rendered source, e.g. spanning an `\\input`). `position` orders cards (virtual offset)."""

    display: str
    position: int


@dataclass
class DisplaySource:
    """Display LaTeX with tokens, plus what the HTML post-processing and selection need."""

    text: str
    blocks: list[Block] = field(default_factory=list)
    placements: dict[str, Placement] = field(default_factory=dict)
    fills: dict[str, FillTag] = field(default_factory=dict)

    def block(self, block_id: str | None) -> Block | None:
        """Block with this id, or None."""
        return next((b for b in self.blocks if b.id == block_id), None)


@dataclass(frozen=True)
class _Replacement:
    start: int
    end: int
    text: str


@dataclass(frozen=True)
class _Insertion:
    position: int
    kind: str
    item_id: str


class _Builder:
    """Collects replacements and insertions over the virtual source, then composes the text."""

    def __init__(self, virtual: VirtualSource) -> None:
        self.virtual = virtual
        self.map = SourceMap(virtual.text)
        self.replacements: list[_Replacement] = []
        self.insertions: list[_Insertion] = []
        self.result = DisplaySource(text="")
        self.fill_ids = self._collect_fills()

    def _collect_fills(self) -> dict[int, str]:
        ids: dict[int, str] = {}
        for index, (start, end) in enumerate(fill_tag_spans(self.virtual.text), start=1):
            fill_id = f"f{index}"
            file, line = self.virtual.line_of(start)
            instruction = self.virtual.text[start + len("%parco"):end].strip()
            self.result.fills[fill_id] = FillTag(fill_id, instruction, file, line)
            ids[start] = fill_id
        return ids

    def add_edit(self, edit: Edit) -> None:
        span = self._span(edit.file, edit.offset, edit.anchor)
        if span is None:
            self._hidden(edit.id, edit.file, edit.offset)
            return
        start, end = span
        if edit.status is not EditStatus.REJECTED:
            old = self._display_copy(start, end)
            new = strip_comments(edit.replace)
            if self._can_wrap(start, end, old, new):
                self._replace(edit, start, end, old, new)
                return
        self._point(edit.id, start)

    def _can_wrap(self, start: int, end: int, old: str, new: str) -> bool:
        if not self.map.region_is_safe(start, end):
            return False
        return all(is_balanced(text) and not is_structural(text) for text in (old, new))

    def _replace(self, edit: Edit, start: int, end: int, old: str, new: str) -> None:
        if edit.status is EditStatus.ACCEPTED:
            text = token("as", edit.id) + new + token("ae", edit.id)
        else:
            text = _pending_text(edit.id, old, new)
        self.replacements.append(_Replacement(start, end, text))
        self.result.placements[edit.id] = Placement("inline", start)

    def add_thread(self, thread: Thread) -> None:
        if thread.is_global or thread.edit is not None or thread.file is None or thread.anchor is None:
            return
        assert thread.offset is not None
        span = self._span(thread.file, thread.offset, thread.anchor)
        if span is None:
            self._hidden(thread.id, thread.file, thread.offset)
            return
        start, start_snapped = self._snap(self._skip_leading_structure(*span), outward_to_start=True)
        end, end_snapped = self._snap(span[1], outward_to_start=False)
        if (start_snapped or self.map.is_safe(start)) and (end_snapped or self.map.is_safe(end)):
            self.insertions.append(_Insertion(start, "ts", thread.id))
            self.insertions.append(_Insertion(end, "te", thread.id))
            self.result.placements[thread.id] = Placement("inline", start)
        else:
            self._point(thread.id, span[0])

    def _skip_leading_structure(self, start: int, end: int) -> int:
        """Move a range start past leading `\\begin{..}`/`\\item`: a token before them would land
        outside the list or environment the range is about."""
        match = _LEADING_STRUCTURE.match(self.virtual.text, start, end)
        return match.end() if match and match.end() < end else start

    def add_fill_tags(self) -> None:
        for start, fill_id in self.fill_ids.items():
            if not any(r.start <= start < r.end for r in self.replacements):
                self.insertions.append(_Insertion(self.map.safe_point(start), "fp", fill_id))

    def add_blocks(self) -> None:
        """Mark each line or `\\item` that starts with text: selections are searched between marks."""
        text = self.virtual.text
        starts = [m.end() for m in _LINE_START.finditer(text, self.map.body_start)]
        starts += [m.end() for m in _ITEM.finditer(text, self.map.body_start)]
        accepted = sorted({p for p in starts if self._is_block_start(p)})
        for index, position in enumerate(accepted):
            block_id = f"b{index}"
            self.result.blocks.append(Block(block_id, position, self._paragraph_end(position)))
            self.insertions.append(_Insertion(position, "bk", block_id))

    def _is_block_start(self, position: int) -> bool:
        text = self.virtual.text
        if position >= self.map.body_end or not self.map.is_safe(position) or self._inside_replacement(position):
            return False
        char = text[position]
        return char.isalnum() or char in "(`'\"" or text.startswith(_TEXT_COMMANDS, position)

    def _paragraph_end(self, start: int) -> int:
        """End of the paragraph starting at `start`: a blank line or a structural command line."""
        match = _PARAGRAPH_END.search(self.virtual.text, start, self.map.body_end)
        end = match.start() if match else self.map.body_end
        return start + len(self.virtual.text[start:end].rstrip())

    def compose(self) -> DisplaySource:
        text = self.virtual.text
        replacements = sorted(self.replacements, key=lambda r: r.start)
        insertions = sorted((self._outside_replacements(i) for i in self.insertions), key=lambda i: (i.position, _RANK[i.kind]))
        parts: list[str] = []
        cursor = 0
        pending = iter(insertions)
        upcoming = next(pending, None)
        for replacement in [*replacements, _Replacement(len(text), len(text), "")]:
            while upcoming is not None and upcoming.position <= replacement.start:
                parts.append(text[cursor:upcoming.position] + token(upcoming.kind, upcoming.item_id))
                cursor = upcoming.position
                upcoming = next(pending, None)
            parts.append(text[cursor:replacement.start] + replacement.text)
            cursor = replacement.end
        parts.append(text[cursor:])
        self.result.text = "".join(parts)
        return self.result

    def _span(self, file: str, offset: int, anchor: str) -> tuple[int, int] | None:
        span = self.virtual.to_virtual_range(file, offset, offset + len(anchor))
        if span is None or self.virtual.text[span[0]:span[1]] != anchor:
            return None
        return span

    def _display_copy(self, start: int, end: int) -> str:
        """Source text of [start, end) without its comments; `%parco` tags become fill tokens."""
        parts: list[str] = []
        cursor = start
        for c in self.map.constructs:
            if c.kind == "comment" and start <= c.start and c.end <= end:
                fill_id = self.fill_ids.get(c.start)
                parts.append(self.virtual.text[cursor:c.start] + (token("fp", fill_id) if fill_id else ""))
                cursor = c.end
        parts.append(self.virtual.text[cursor:end])
        return "".join(parts)

    def _point(self, item_id: str, position: int) -> None:
        point = self.map.safe_point(position)
        self.insertions.append(_Insertion(point, "pt", item_id))
        self.result.placements[item_id] = Placement("point", point)

    def _hidden(self, item_id: str, file: str, offset: int) -> None:
        approximate = self.virtual.to_virtual(file, offset)
        self.result.placements[item_id] = Placement("hidden", len(self.virtual.text) if approximate is None else approximate)

    def _snap(self, position: int, outward_to_start: bool) -> tuple[int, bool]:
        for r in self.replacements:
            if r.start < position < r.end:
                return (r.start if outward_to_start else r.end), True
        return position, False

    def _inside_replacement(self, position: int) -> bool:
        return any(r.start < position < r.end for r in self.replacements)

    def _outside_replacements(self, insertion: _Insertion) -> _Insertion:
        for r in self.replacements:
            if r.start < insertion.position < r.end:
                return _Insertion(r.start, insertion.kind, insertion.item_id)
        return insertion


def _pending_text(edit_id: str, old: str, new: str) -> str:
    """Old and new text of a pending edit, wrapped hunk by hunk when every piece of the word diff
    can be displayed on its own, else as one deletion followed by one insertion."""
    hunks = word_diff(old, new)
    pieces = [piece for hunk in hunks for piece in hunk[1:] if piece]
    if len(hunks) > 1 and all(is_balanced(p) and not is_structural(p) for p in pieces):
        parts = []
        for op, old_part, new_part in hunks:
            if op == "equal":
                parts.append(old_part)
                continue
            if old_part:
                parts.append(token("ds", edit_id) + old_part + token("de", edit_id))
            if new_part:
                parts.append(token("is", edit_id) + new_part + token("ie", edit_id))
        return "".join(parts)
    return token("ds", edit_id) + old + token("de", edit_id) + token("is", edit_id) + new + token("ie", edit_id)


def build_display_source(virtual: VirtualSource, edits: list[Edit], threads: list[Thread]) -> DisplaySource:
    """Mark `edits` and `threads` (and every `%parco` tag and paragraph) in the virtual source.

    Pending edits show old and new text, accepted edits their new text, rejected edits only
    a point (their card stays reachable); edit discussions live in the edit's card.
    """
    builder = _Builder(virtual)
    for edit in sorted(edits, key=lambda e: e.status is EditStatus.REJECTED):
        builder.add_edit(edit)
    for thread in threads:
        builder.add_thread(thread)
    builder.add_fill_tags()
    builder.add_blocks()
    return builder.compose()
