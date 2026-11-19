"""Minimal BibTeX handling: entry keys and appending entries to a bibliography file."""

from __future__ import annotations

import re

_ENTRY_RE = re.compile(r"@\s*(\w+)\s*[{(]\s*([^,\s{}()]+)\s*,")
_NON_ENTRIES = {"comment", "preamble", "string"}


def entry_keys(text: str) -> list[str]:
    """Citation keys of the entries in a BibTeX text (`@comment`/`@string`/`@preamble` ignored)."""
    return [key for kind, key in _ENTRY_RE.findall(text) if kind.lower() not in _NON_ENTRIES]


def split_entries(text: str) -> list[str]:
    """Split a BibTeX text into its entries, each one starting at its `@type{`."""
    starts = [m.start() for m in _ENTRY_RE.finditer(text)]
    return [text[a:b].strip() for a, b in zip(starts, [*starts[1:], len(text)])]


def append_entries(bib_text: str, entries: list[str]) -> tuple[str, list[str], list[str]]:
    """Append the BibTeX entries contained in `entries` whose keys are not in `bib_text` yet.

    Returns the new text, the keys added, and the keys skipped because already present.
    """
    known = set(entry_keys(bib_text))
    added: list[str] = []
    skipped: list[str] = []
    chunks: list[str] = []
    for entry in (e for text in entries for e in split_entries(text)):
        keys = entry_keys(entry)
        if not keys:
            continue
        if keys[0] in known:
            skipped.append(keys[0])
            continue
        chunks.append(entry)
        added.append(keys[0])
        known.add(keys[0])
    if not chunks:
        return bib_text, added, skipped
    separator = "" if not bib_text or bib_text.endswith("\n\n") else ("\n" if bib_text.endswith("\n") else "\n\n")
    return bib_text + separator + "\n\n".join(chunks) + "\n", added, skipped
