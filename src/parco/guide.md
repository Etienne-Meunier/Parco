# Parco guide for the AI

Parco runs the review of a LaTeX document with track changes. You never modify the `.tex` files nor anything in `.parco/` yourself: every action goes through a `parco` command, which validates it against the current phase and mode and refuses what is not allowed. Nothing reaches the sources until the user accepts it and closes the version.

## Workflow

1. Read (the user asks you to review the document with parco in a given mode, or runs the `parco-read` skill): you check `parco status`, open the version with `parco open --mode <mode>`, read `parco context` and submit edits and comments. If the user gave no mode, ask them. Never open a version when one is already open, and never close one yourself.
2. The user reviews in the platform (`parco render`): accepts, rejects, comments and replies.
3. Iterate (the user asks you to answer their comments, or runs the `parco-iterate` skill): you run `parco iterate`, then `parco context` and `parco threads --open`, and answer the threads where the user spoke last. Do not open new subjects.
4. The user closes the version (`parco close` or the platform). You keep no memory between versions: each version starts from a blank slate.

## Modes (weakest to strongest, each includes the weaker ones)

- `reference`: only edits resolving a `\cite{parco}` tag into real keys, optionally carrying a BibTeX entry. No threads.
- `comment`: also threads on anchors and global comments (advisor-like remarks: unclear passage, missing component). Any edit other than a `\cite{parco}` resolution is refused.
- `proofread`: also edits replacing existing text (grammar, wording, reformulation). Touching a `%parco` tag is refused.
- `generative`: also edits filling a `%parco` tag. Empty or missing parts without a tag stay untouched.

## Tags written by the user

- Fill tag: a LaTeX comment line starting with `%parco`, e.g. `%parco explain why the scheme is stable`. The tag runs to the end of its line. An edit whose anchor touches it is a fill edit: put the tag in the anchor and the new text in `replace` (dropping the tag line if it is fully answered).
- Cite tag: the literal `\cite{parco}`. A cite edit has `replace` equal to `anchor` with each `\cite{parco}` turned into `\cite{key1,key2}` (comma-separated keys without spaces, braces, `\` or `%`), nothing else changed. An attached `bibtex` may only contain entries for the keys the edit cites.

`parco context` lists the tags of each file with their line numbers.

## Reading the context

`parco context` prints the phase, mode, allowed actions, tracked files, bibliography, context paths, and for each tracked file either its full content (first version) or the unified diff since the last version. Review only the changed parts (lines with `+`); the rest is context. From the second version on, parco refuses edits and anchored comments on unchanged text, except on `%parco` and `\cite{parco}` tags; use a global comment for a remark on the rest. Context paths (code, notes) are background to read, never to review. Add `--json` for structured output.

## Anchors

An anchor is an exact copy of a piece of the source file (same spaces, newlines, backslashes) that must match exactly once in its file. If parco answers "not found", copy the text again exactly; if "ambiguous", include more surrounding text. Edits on overlapping text are refused: one edit per region (supersede an edit to change it). `file` is the path relative to the project root, as listed in the context.

## Commands

All AI commands read JSON on stdin. Always use a heredoc with a quoted delimiter so that the shell leaves backslashes and `$` alone. Errors go to stderr with a non-zero exit code; read the message, fix the input and retry.

Inside JSON strings, every LaTeX backslash is doubled (`\\cite`, `\\emph`), a newline is `\n` and a double quote is `\"`. Anchors are matched after JSON decoding, against the raw source text.

Propose an edit (`reason` is optional, `thread` and `supersedes` too):

```bash
parco edit add <<'EOF_JSON'
{"file": "intro.tex", "anchor": "we shows that", "replace": "we show that", "reason": "grammar"}
EOF_JSON
```

Resolve a citation tag, with a BibTeX entry appended to the bibliography if the user accepts:

```bash
parco edit add <<'EOF_JSON'
{"file": "intro.tex", "anchor": "as shown before \\cite{parco}", "replace": "as shown before \\cite{smith2020}",
 "bibtex": "@article{smith2020,\n  title={A title},\n  author={Smith, John},\n  year={2020}\n}"}
EOF_JSON
```

Submit a whole pass in one call with `--batch` and a JSON array. A batch is all or nothing: if one item is refused (its index is given), nothing is recorded; fix it and resubmit the whole array.

```bash
parco edit add --batch <<'EOF_JSON'
[{"file": "intro.tex", "anchor": "we shows that", "replace": "we show that", "reason": "grammar"},
 {"file": "sections/method.tex", "anchor": "%parco explain the scheme", "replace": "The scheme is ...", "reason": "fill tag"}]
EOF_JSON
```

Open a thread on an anchor, or a global comment (right column), also with `--batch`:

```bash
parco comment add <<'EOF_JSON'
{"file": "intro.tex", "anchor": "our method is optimal", "body": "This claim needs a justification or a reference."}
EOF_JSON

parco comment add <<'EOF_JSON'
{"global": true, "body": "The contribution is only stated in the conclusion; consider announcing it in the introduction."}
EOF_JSON
```

Answer an open thread:

```bash
parco reply t3 <<'EOF_JSON'
{"body": "Agreed, I propose a shorter formulation in edit e7."}
EOF_JSON
```

Read the threads with their full discussion and linked edits: `parco threads --open` (`--json` available). Show the state: `parco status`. Print this guide: `parco guide`.

## How to read

Read the document as a careful advisor would, within the mode:

- `comment`: point out unclear passages, gaps in the reasoning, unsupported claims, missing components. Comments say what is wrong or missing; they do not dictate a rewrite. Use global comments for remarks on structure or on the whole document.
- `proofread`: grammar, wording, clearer formulation, keeping the author's voice and ideas. Do not rewrite for taste; give each edit a short reason.
- `generative`: fill each `%parco` tag following its instruction, matching the author's voice and style.
- `\cite{parco}` tags (every mode): find real references supporting the sentence and check that each one exists (DOI or database record). Never invent a reference; if nothing solid is found, leave the tag.

Submit with `--batch` when you have several items. Finish with a short summary to the user: counts of edits and threads, the main points, and that they can review them in the platform (`parco render`).

## Iterate phase

- Opening a new thread (`comment add`) is refused.
- You only act on threads whose last message is from the user: you answer the user, you do not push your own subjects. A thread where you spoke last is waiting for the user.
- Every edit must carry `"thread": "<id>"` of such a thread (or use `parco edit add --thread <id>`). Mode rules still apply. Propose a thread's edits first, then `parco reply <thread-id>` once: after your reply the thread waits for the user again.
- To revise an edit the user commented on, propose a new edit with `"supersedes": "<edit-id>"`: the old edit is rejected and the new one may cover the same text. In iterate, only an edit linked to the answered thread can be superseded; in read, the superseded edit must cover the same text or be linked to the same thread.

## Never

- Never edit the `.tex` files, the bibliography or `.parco/` directly.
- Never work around a refusal: it is the rule of the current mode or phase.
