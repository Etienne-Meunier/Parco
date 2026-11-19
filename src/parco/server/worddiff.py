"""Word-level diff of an edit's old and new text, so a small change is not shown as a full rewrite."""

from __future__ import annotations

import re
from difflib import SequenceMatcher

# A command with flat arguments (`\cite{a,b}`, `\emph{word}`) is one token: a change inside it
# then replaces the whole command, which keeps every hunk brace-balanced and displayable.
_WORD_RE = re.compile(r"\s+|\\[A-Za-z@]+\*?(?:\[[^\[\]{}]*\]|\{[^{}]*\})*|\\.|\w+|[^\w\s]")


def word_diff(old: str, new: str) -> list[tuple[str, str, str]]:
    """Hunks `(op, old_part, new_part)` covering `old` and `new` in order.

    `op` is `equal` (both parts identical) or `change` (either part may be empty). Equal runs
    made of whitespace only are folded into the surrounding changes, so a rewritten phrase
    reads as one deletion and one insertion rather than a word-by-word alternation. When less
    than half of the text is shared, only the common start and end are kept and everything
    between them is one change: scattered common words would only fragment a rewrite.
    """
    a = _WORD_RE.findall(old)
    b = _WORD_RE.findall(new)
    hunks: list[tuple[str, str, str]] = []
    for tag, i1, i2, j1, j2 in SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        op = "equal" if tag == "equal" else "change"
        hunks.append((op, "".join(a[i1:i2]), "".join(b[j1:j2])))
    hunks = _merge(_fold_whitespace(hunks))
    shared = sum(len(old_part) for op, old_part, _ in hunks if op == "equal")
    if shared < MIN_SHARED * max(len(old), len(new)):
        hunks = _keep_ends(hunks)
    return hunks


MIN_SHARED = 0.5


def _keep_ends(hunks: list[tuple[str, str, str]]) -> list[tuple[str, str, str]]:
    head = hunks[:1] if hunks and hunks[0][0] == "equal" else []
    tail = hunks[-1:] if len(hunks) > len(head) and hunks[-1][0] == "equal" else []
    middle = hunks[len(head):len(hunks) - len(tail)]
    if not middle:
        return hunks
    return [*head, ("change", "".join(h[1] for h in middle), "".join(h[2] for h in middle)), *tail]


def _fold_whitespace(hunks: list[tuple[str, str, str]]) -> list[tuple[str, str, str]]:
    folded = []
    for index, hunk in enumerate(hunks):
        inner = 0 < index < len(hunks) - 1
        if hunk[0] == "equal" and inner and not hunk[1].strip():
            folded.append(("change", hunk[1], hunk[2]))
        else:
            folded.append(hunk)
    return folded


def _merge(hunks: list[tuple[str, str, str]]) -> list[tuple[str, str, str]]:
    merged: list[tuple[str, str, str]] = []
    for op, old, new in hunks:
        if merged and merged[-1][0] == op == "change":
            _, prev_old, prev_new = merged.pop()
            merged.append(("change", prev_old + old, prev_new + new))
        else:
            merged.append((op, old, new))
    return merged
