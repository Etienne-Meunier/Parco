"""Where a plain-text marker token may be inserted into LaTeX source without changing its meaning.

The scanner lists the constructs a token must not enter: comments, math, verbatim,
control-sequence names, and the arguments of commands whose arguments are not text
(`\\label`, `\\cite`, `\\includegraphics`, `\\begin`...). The preamble is excluded too.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_BASE_MATH_ENVS = ("equation", "align", "gather", "multline", "eqnarray", "displaymath", "math", "flalign", "alignat")
MATH_ENVS = frozenset(_BASE_MATH_ENVS + tuple(f"{env}*" for env in _BASE_MATH_ENVS))
VERBATIM_ENVS = frozenset({"verbatim", "verbatim*", "Verbatim", "lstlisting", "minted", "comment"})
# Commands whose arguments are not running text, with the number of brace arguments to protect
# (None: every brace/bracket group that immediately follows).
RESTRICTED_COMMANDS: dict[str, int | None] = {
    **dict.fromkeys(
        ["label", "ref", "eqref", "pageref", "autoref", "cref", "Cref", "nameref", "cite", "citep", "citet",
         "citealp", "citeauthor", "citeyear", "nocite", "parencite", "textcite", "autocite", "footcite",
         "includegraphics", "input", "include", "end", "usepackage", "documentclass", "bibliography",
         "bibliographystyle", "addbibresource", "url", "href", "graphicspath", "hspace", "vspace",
         "setcounter", "addtocounter", "includepdf", "pagestyle", "thispagestyle", "color"],
        1,
    ),
    **dict.fromkeys(["begin", "newcommand", "renewcommand", "providecommand", "def", "newenvironment",
                     "setlength", "DeclareMathOperator", "definecolor"], None),
}

_NAME_RE = re.compile(r"[A-Za-z@]+\*?")
_BEGIN_DOCUMENT = r"\begin{document}"


@dataclass(frozen=True)
class Construct:
    """Source span [start, end) a token must not enter.

    `sticky` constructs (comments, control-word names) also forbid the position `end`
    itself: a token there would land in the comment or extend the command name.
    """

    start: int
    end: int
    kind: str
    sticky: bool = False

    def blocks(self, position: int) -> bool:
        """True if a token cannot be inserted at `position` because of this construct."""
        return self.start < position < self.end or (self.sticky and position == self.end)


class SourceMap:
    """Constructs of a LaTeX source, with safe-position queries."""

    def __init__(self, text: str) -> None:
        self.text = text
        index = text.find(_BEGIN_DOCUMENT)
        self.body_start = index + len(_BEGIN_DOCUMENT) if index >= 0 else 0
        self.body_end = _body_end(text, self.body_start)
        self.constructs = scan_constructs(text)

    def is_safe(self, position: int) -> bool:
        """True if a token can be inserted at `position`."""
        if not self.body_start <= position <= self.body_end:
            return False
        return not any(c.blocks(position) for c in self.constructs)

    def safe_point(self, position: int) -> int:
        """Nearest safe position at or before `position` (the start of the blocking construct)."""
        position = min(max(position, self.body_start), self.body_end)
        while not self.is_safe(position):
            blocking = [c.start for c in self.constructs if c.blocks(position)]
            position = max(min(blocking), self.body_start)
        return position

    def region_is_safe(self, start: int, end: int) -> bool:
        """True if [start, end) can be replaced by a copy wrapped in tokens.

        Both ends must be insertion-safe and every construct must lie wholly inside or
        outside the region (comments inside are fine: displayed copies drop them).
        """
        if not (start < end and self.body_start <= start and end <= self.body_end and self.is_safe(start)):
            return False
        return all((start <= c.start and c.end <= end) or c.end <= start or end <= c.start for c in self.constructs)


def scan_constructs(text: str) -> list[Construct]:
    """Every construct of `text` in which a token cannot live, in order of appearance."""
    found: list[Construct] = []
    i = 0
    n = len(text)
    while i < n:
        char = text[i]
        if char == "%":
            end = _line_end(text, i)
            found.append(Construct(i, end, "comment", sticky=True))
            i = end
        elif char == "$":
            end = math_dollar_end(text, i)
            found.append(Construct(i, end, "math"))
            i = end
        elif char == "\\":
            construct = _backslash_construct(text, i)
            found.append(construct)
            i = max(construct.end, i + 1)
        else:
            i += 1
    return found


def _backslash_construct(text: str, i: int) -> Construct:
    following = text[i + 1:i + 2]
    if following in ("(", "["):
        closing = "\\)" if following == "(" else "\\]"
        return Construct(i, _find_end(text, closing, i + 2), "math")
    name_match = _NAME_RE.match(text, i + 1)
    if name_match is None:
        return Construct(i, min(i + 2, len(text)), "escape")
    name = name_match.group()
    after_name = name_match.end()
    if name == "verb" or name == "verb*":
        delimiter = text[after_name:after_name + 1]
        end = text.find(delimiter, after_name + 1) if delimiter else -1
        return Construct(i, len(text) if end < 0 else end + 1, "verbatim")
    if name == "begin":
        env = _brace_content(text, after_name)
        if env in MATH_ENVS or env in VERBATIM_ENVS:
            closing = f"\\end{{{env}}}"
            return Construct(i, _find_end(text, closing, after_name), "math" if env in MATH_ENVS else "verbatim")
    bare = name.rstrip("*")
    if bare in RESTRICTED_COMMANDS:
        return Construct(i, skip_arguments(text, after_name, RESTRICTED_COMMANDS[bare]), "command")
    return Construct(i, after_name, "name", sticky=True)


def skip_arguments(text: str, position: int, brace_count: int | None) -> int:
    """Position after the optional `[...]` and the brace arguments following a command name."""
    seen = 0
    while position < len(text):
        wants_more = brace_count is None or seen < brace_count
        char = text[position]
        if char == "[" and wants_more:
            position = _group_end(text, position, "[", "]")
        elif char == "{" and wants_more:
            position = _group_end(text, position, "{", "}")
            seen += 1
        elif char in " \t" and brace_count is not None and wants_more:
            position += 1
        else:
            break
    return position


def _group_end(text: str, start: int, opening: str, closing: str) -> int:
    """Position just after the group opened at `start`, honouring nesting and escapes."""
    depth = 0
    i = start
    while i < len(text):
        char = text[i]
        if char == "\\":
            i += 2
            continue
        if char == opening:
            depth += 1
        elif char == closing:
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return len(text)


def _brace_content(text: str, position: int) -> str | None:
    if text[position:position + 1] != "{":
        return None
    end = text.find("}", position)
    return text[position + 1:end] if end > 0 else None


def _find_end(text: str, closing: str, start: int) -> int:
    index = text.find(closing, start)
    return len(text) if index < 0 else index + len(closing)


def _line_end(text: str, start: int) -> int:
    index = text.find("\n", start)
    return len(text) if index < 0 else index


def math_dollar_end(text: str, start: int) -> int:
    delimiter = "$$" if text.startswith("$$", start) else "$"
    i = start + len(delimiter)
    while i < len(text):
        if text[i] == "\\":
            i += 2
            continue
        if text.startswith(delimiter, i):
            return i + len(delimiter)
        i += 1
    return len(text)


def _body_end(text: str, body_start: int) -> int:
    index = text.find(r"\end{document}", body_start)
    return len(text) if index < 0 else index


_ESCAPED_OR_SPECIAL = re.compile(r"\\[{}$]|\\\\|[{}$]")
_CONTROL_WORD_AT_END = re.compile(r"\\[A-Za-z@]+$")


def is_balanced(latex: str) -> bool:
    """True if a comment-free LaTeX snippet can be displayed on its own: balanced braces,
    paired `$`, `\\(`/`\\)`, `\\[`/`\\]`, `\\begin`/`\\end`, and no trailing control word
    (a token appended to it would extend its name)."""
    depth = 0
    dollars = 0
    for match in _ESCAPED_OR_SPECIAL.finditer(latex):
        char = match.group()
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth < 0:
                return False
        elif char == "$":
            dollars += 1
    return (
        depth == 0
        and dollars % 2 == 0
        and latex.count("\\(") == latex.count("\\)")
        and latex.count("\\[") == latex.count("\\]")
        and latex.count("\\begin{") == latex.count("\\end{")
        and _CONTROL_WORD_AT_END.search(latex) is None
    )


_STRUCTURAL_RE = re.compile(
    r"(?<!\\)&|\\\\|\\(?:item|caption|label|begin|end|part|chapter|section|subsection|subsubsection"
    r"|paragraph|subparagraph)(?![A-Za-z@])"
)


def is_structural(latex: str) -> bool:
    """True if a snippet carries document structure (table cells, line breaks, list items,
    captions, labels, sectioning, environments): wrapping it in tokens would break the layout."""
    return _STRUCTURAL_RE.search(latex) is not None
