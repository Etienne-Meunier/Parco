"""Rules of the workflow: edit classification and mode/phase permissions of the AI."""

from __future__ import annotations

from parco.core.anchors import spans_overlap
from parco.core.errors import RuleError
from parco.core.latex import cite_resolution_keys, cite_tag_spans, fill_tag_spans
from parco.core.model import Author, EditKind, Mode, Phase, Thread

REQUIRED_MODE: dict[EditKind, Mode] = {
    EditKind.CITE: Mode.REFERENCE,
    EditKind.REPLACE: Mode.PROOFREAD,
    EditKind.FILL: Mode.GENERATIVE,
}
THREAD_MODE = Mode.COMMENT

_KIND_DESCRIPTION: dict[EditKind, str] = {
    EditKind.CITE: r"resolves a \cite{parco} tag",
    EditKind.REPLACE: "replaces existing text",
    EditKind.FILL: "fills a %parco tag",
}


def classify_edit(source: str, offset: int, anchor: str, replace: str) -> EditKind:
    """Kind of an edit of `anchor` (at `offset` in `source`) into `replace`.

    An edit touching any part of a `%parco` tag is a fill; otherwise an edit that only
    resolves `\\cite{parco}` tags is a cite; anything else is a replace.
    """
    if touches(fill_tag_spans(source), offset, offset + len(anchor)):
        return EditKind.FILL
    if cite_resolution_keys(anchor, replace) is not None:
        return EditKind.CITE
    return EditKind.REPLACE


def touches(spans: list[tuple[int, int]], start: int, end: int) -> bool:
    """True if [start, end) overlaps one of `spans`."""
    return any(spans_overlap(start, end, a, b) for a, b in spans)


def touches_tag(source: str, start: int, end: int) -> bool:
    """True if [start, end) overlaps a `%parco` or `\\cite{parco}` tag of `source`."""
    return touches(fill_tag_spans(source) + cite_tag_spans(source), start, end)


def check_bibtex_keys(bibtex_keys: list[str], cited_keys: list[str]) -> None:
    """Raise RuleError unless every BibTeX entry is one of the keys the edit cites."""
    extra = sorted(set(bibtex_keys) - set(cited_keys))
    if extra:
        raise RuleError(
            f"BibTeX entries {', '.join(extra)} are not cited by the edit "
            f"(it cites {', '.join(cited_keys)}); only attach entries for the new keys."
        )


def check_ai_answers_user(phase: Phase, thread: Thread) -> None:
    """In iterate, the AI only acts on a thread whose last message is from the user."""
    if phase is Phase.ITERATE and thread.messages[-1].author is not Author.USER:
        raise RuleError(
            f"Thread {thread.id} is waiting for the user (its last message is yours). In iterate, "
            "only answer threads where the user spoke last; propose a thread's edits before replying."
        )


def check_ai_edit(phase: Phase, mode: Mode, kind: EditKind, has_bibtex: bool, thread: str | None) -> None:
    """Raise RuleError if the AI may not propose an edit of this kind now."""
    required = REQUIRED_MODE[kind]
    if not mode.includes(required):
        raise RuleError(
            f"Edit refused in {mode} mode: it {_KIND_DESCRIPTION[kind]}, which requires {required} mode."
        )
    if has_bibtex and kind is not EditKind.CITE:
        raise RuleError(r"A bibtex entry can only be attached to an edit resolving a \cite{parco} tag.")
    if phase is Phase.ITERATE and thread is None:
        raise RuleError(
            "Edit refused in iterate phase: it must answer an existing open thread "
            "(set `thread` to its id)."
        )


def check_ai_new_thread(phase: Phase, mode: Mode) -> None:
    """Raise RuleError if the AI may not open a new thread now."""
    if phase is Phase.ITERATE:
        raise RuleError(
            "Opening a new thread is refused in iterate phase: only reply to open threads "
            "and propose edits attached to them."
        )
    if not mode.includes(THREAD_MODE):
        raise RuleError(f"Comments are refused in {mode} mode, they require {THREAD_MODE} mode.")


def allowed_actions(phase: Phase, mode: Mode | None) -> list[str]:
    """Human-readable list of what the AI may do in this phase and mode."""
    if phase is Phase.IDLE or mode is None:
        return ["nothing: no version is open (the user starts one with `parco open`)"]
    actions = [r"edit add: resolve \cite{parco} tags with real keys (optional bibtex entry)"]
    if mode.includes(Mode.COMMENT) and phase is Phase.READ:
        actions.append("comment add: threads on anchors and global comments")
    if mode.includes(Mode.PROOFREAD):
        actions.append("edit add: replace existing text (except %parco tags)")
    if mode.includes(Mode.GENERATIVE):
        actions.append("edit add: fill %parco tags")
    if phase is Phase.ITERATE:
        actions.append("reply <thread-id>: answer an open thread")
        actions.append("every edit must carry `thread` = id of an open thread; no new threads")
    return actions
