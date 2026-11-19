# Parco design

This document describes the design of Parco: the workflow, the rules enforced by the program and the layout of the code. See the [README](../README.md) for the motivation and the user guide.

## Workflow

Each version of the document goes through three phases.

1. Read (mode: `reference` < `comment` < `proofread` < `generative`). The AI reads the current version. Depending on the mode it resolves `\cite{parco}` placeholders, comments (advisor-like remarks: an unclear passage, a missing component, without dictating a rewrite), proofreads existing text, or writes new text, only in `%parco` tags. Each stronger mode includes the weaker ones. From the second version on, only the parts changed since the last snapshot are under review, the rest is context.
2. Iterate. The user went through the edits and comments, and the AI answers the open threads and applies the requested changes. There can be several iterate rounds, but the AI does not open new subjects: it only goes towards solving the open problems.
3. Close. Pending edits are rejected, accepted ones are applied, all threads are closed and a snapshot is taken. The AI keeps no memory of earlier versions: the user is master of the information given to the AI at each round, and starting from a blank slate avoids accumulating complications.

## Architecture

Parco is called from the LaTeX project, where it adds a hidden `.parco` folder holding what it needs: the review state (edits and threads of the current version), a snapshot of the last version to compute the next diff, and a configuration file with the main tex file, the bibliography and the context paths (code repositories or other documents used as background).

Parco is a standalone program, independent of the AI. Claude Code skills are thin wrappers: they call `parco` commands and bring the judgment, while parco owns all the state and enforces the rules. Stack: Python for the program, a small web frontend for the platform.

### Commands

```bash
Commands (called from the terminal by the user):
  init          : create the .parco folder, ask for the main tex file, bibliography and context paths
  render        : start the local server and show the interactive platform for the current version
  status        : show phase, mode, open threads and pending edits
  accept/reject : decide on edits from the terminal
  close         : end of the version (reject pending edits, apply accepted ones, take a new snapshot)

Skills (called from Claude Code, they use the AI-side commands below):
  read    : open a new version in a given mode and do the initial reading (error if a version is in progress)
  iterate : go through the open threads of the current version and answer them
```

AI-side commands, used by the skills:

```bash
parco open --mode <reference|comment|proofread|generative>   start a new version (error if one is open)
parco iterate            move the open version to the iterate phase
parco context            phase, mode, diff since snapshot, file list, context paths
parco threads [--open]   threads with their full discussion (used by iterate)
parco edit add           propose a replacement on an anchor (optionally --thread <id>)
parco comment add        open a thread on an anchor, or --global for a global comment
parco reply <thread-id>  answer in an existing thread
parco guide              usage documentation for the LLM
```

`parco guide` is printed by the program itself so that it cannot drift from the actual CLI. The first step of every skill is to run it.

`parco context` is responsible for the "only the changed parts are read, the rest is context" behaviour: it computes the diff between the snapshot and the current files and presents it to the AI. This logic lives in parco, not in the skill.

### Rules enforced by the program

The program enforces the rules, not the prompt. Every action of the AI goes through a `parco` command that validates it against the current phase and mode, so a drifting model cannot break the workflow.

Modes are ordered from weakest to strongest: `reference` < `comment` < `proofread` < `generative`. Every stronger mode includes the permissions of the weaker ones.

- `reference` mode: edits are only accepted on a `\cite{parco}` (replacing it with actual keys, optionally carrying BibTeX entries), no threads;
- `comment` mode: adds threads on anchors and global comments, any edit other than on a `\cite{parco}` is refused;
- `proofread` mode: adds edits replacing existing text, but adding new text in a `%parco` tag is refused;
- `generative` mode: adds edits filling a `%parco` tag, any other empty or missing part stays untouched;
- `iterate` phase: opening a new thread is refused, only replies and edits attached to an existing thread (`--thread <id>`) are accepted.

### Edits

AI-side commands take JSON on stdin rather than command-line arguments, because LaTeX (backslashes, `$`, quotes, newlines) is painful to escape in a shell. A `--batch` flag accepts a JSON array so that a full proofreading pass can be submitted in a single call.

```bash
parco edit add <<'EOF_JSON'
{"file": "intro.tex", "anchor": "we shows that", "replace": "we show that", "reason": "grammar"}
EOF_JSON
```

For the `reference` mode, an edit can carry a BibTeX entry which is appended to the bibliography file when the edit is applied.

Since the tex files are frozen during a review, offsets never move within a version. An anchor is an exact quoted piece of source text that must match exactly once in its file; if it matches several times, parco refuses the edit and asks for more surrounding text. Parco stores the file, the offset and the text. Overlapping edits are refused at `edit add` time.

### Interactive platform

The central element of the platform is a compiled view of the tex file (HTML: the goal is not a perfect final document, but one with the global form and the figures) with the edits shown word by word (new text in green, removed text in red and struck through), and a column with the edit cards and discussion threads.

Global comments, which are not tied to a passage, live in a separate tab.

### User editing and apply at close

If the user edits one of the LaTeX files manually during a review, parco shows an error on render (checking that the content hash of one of the main or included files has changed, so a simple `touch` does not break the review) and refuses to continue the review phase, offering to close and start a new one.

Accepting an edit in the platform does not touch the tex files either. Accepted edits are kept in `.parco` and are only written to the sources at `parco close`, which keeps the files frozen during the whole review (and the timestamp check above valid). The HTML view renders the document with the accepted edits applied, so the user still reads the up-to-date state.

At close: pending edits are rejected, accepted edits are applied to the sources, all threads are closed, and the snapshot is replaced by the new state of the files. Only the previous version is kept, history is left to the git of the latex project.

### Code layout

```
parco/
  core/      store, anchors, validation, diff, snapshot, apply   (all the logic)
  cli/       thin layer over core (user and AI commands)
  server/    HTTP API over core + static frontend (accept / reject / comment)
```

The web platform and the CLI both write through `core`, so the same rules apply on both sides. The server watches the state file so the browser updates live while the AI is working.

```
.parco/
  config.toml    main tex file, context paths, bibliography
  state.json     phase, mode, threads, edits (written under a file lock)
  snapshot/      copy of the tex files at the last close
```
