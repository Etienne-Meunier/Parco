---
name: parco-read
description: Parco initial reading of a LaTeX document version. Use when the user asks for a parco read, "/parco-read <mode>", or to open a Parco review in mode reference, comment, proofread or generative. Opens the version and submits edits and comments through the parco CLI.
---

# Parco read

Parco is a review tool for LaTeX documents with track changes. You are the reader: you propose, the user decides in the platform (`parco render`). You never touch the `.tex` files or `.parco/` yourself.

Parco is invoked as `parco <command>` (installed with `pip install`). If `parco` is not on the PATH of your shell, use `python -m parco <command>` with the Python environment where parco is installed. Run every command from the LaTeX project root (the folder containing `.parco/`).

## Steps

1. Mode: the argument of the skill (`reference`, `comment`, `proofread`, `generative`). If the user gave none, ask; do not guess.
2. `parco guide` — read it fully, it is the authoritative reference for tags, JSON input, anchors and rules.
3. `parco status`. If a version is already open, stop and tell the user (they either continue with `/parco-iterate` or run `parco close`). Never close a version yourself.
4. `parco open --mode <mode>`.
5. `parco context` — read it all. Only the changed parts are under review (the whole document for the first version); unchanged parts are context. Context paths (code, notes) are background: open them on demand when a passage refers to them, do not review them.
6. Read the document as a careful advisor would, within the mode:
   - `comment`: point out unclear passages, gaps in the reasoning, unsupported claims, missing components. Comments say what is wrong or missing; they do not dictate a rewrite. Use global comments for remarks on structure or on the whole document.
   - `proofread`: also edits on existing text — grammar, wording, clearer formulation — keeping the author's voice and ideas. Do not rewrite for taste; each edit carries a short reason.
   - `generative`: also fill `%parco` tags following their instruction, matching the author's voice and style (if the user has a writing-style skill, use it). Never write text where there is no tag.
   - `reference` (and in every mode, for `\cite{parco}` tags): find real references supporting the sentence with the citation skills (`citation-management`, `paper-lookup`), and verify each one exists (DOI or database record). Never invent a reference; if nothing solid is found, leave the tag.
7. Submit with `parco edit add --batch` and `parco comment add --batch` (heredoc with quoted delimiter, LaTeX backslashes doubled in JSON). If a batch is refused, read the error (index and reason), fix that item, resubmit the whole batch. Never work around a refusal.
8. Finish with a short summary to the user: counts of edits and threads, the main points, and `parco render` to review them.
