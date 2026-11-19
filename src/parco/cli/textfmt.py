"""Human-readable rendering of core results for the terminal."""

from __future__ import annotations

from parco.core.close import CloseReport
from parco.core.context import Context, FileView, StatusReport, ThreadView
from parco.core.model import Edit, Phase, State

_RULE = "=" * 72


def format_state_line(state: State) -> str:
    """One line describing the version, phase and mode."""
    mode = f", mode {state.mode}" if state.mode else ""
    return f"Version {state.version}: phase {state.phase}{mode}."


def format_status(status: StatusReport) -> str:
    """Text of `parco status`."""
    lines = [f"Version {status.version}, phase {status.phase}" + (f", mode {status.mode}" if status.mode else "")]
    if status.phase is Phase.IDLE:
        lines.append("No version open. Start one with `parco open --mode <mode>` (or the `read` skill).")
        return "\n".join(lines)
    counts = status.edit_counts
    lines.append(f"Opened at {status.opened_at}")
    lines.append(
        f"Edits: {counts['pending']} pending, {counts['accepted']} accepted, {counts['rejected']} rejected"
    )
    lines.append(f"Threads: {status.open_threads} open, {status.resolved_threads} resolved")
    if status.modified_files:
        lines.append(
            "WARNING: tracked files modified or missing since the version was opened: "
            + ", ".join(status.modified_files)
            + ". The review is frozen: run `parco close`, then open a new version."
        )
    return "\n".join(lines)


def format_edit(edit: Edit) -> str:
    """Compact description of an edit."""
    lines = [f"[{edit.id}] {edit.kind} edit, {edit.status}, {edit.file} @ {edit.offset}"]
    lines.append(f"  anchor:  {edit.anchor!r}")
    lines.append(f"  replace: {edit.replace!r}")
    if edit.reason:
        lines.append(f"  reason:  {edit.reason}")
    for label, value in (("thread", edit.thread), ("supersedes", edit.supersedes)):
        if value:
            lines.append(f"  {label}: {value}")
    if edit.bibtex:
        lines.append("  bibtex: " + edit.bibtex.strip().replace("\n", "\n          "))
    return "\n".join(lines)


def format_thread(view: ThreadView) -> str:
    """A thread with its messages and linked edits."""
    thread = view.thread
    if thread.is_global:
        where = "global"
    elif thread.edit:
        where = f"on edit {thread.edit}, {thread.file} @ {thread.offset}"
    else:
        where = f"{thread.file} @ {thread.offset}, anchor {thread.anchor!r}"
    lines = [f"[{thread.id}] {thread.status} thread, {where}"]
    lines += [f"  {m.author}: " + m.body.replace("\n", "\n    ") for m in thread.messages]
    lines += ["  " + format_edit(e).replace("\n", "\n  ") for e in view.edits]
    return "\n".join(lines)


def format_threads(views: list[ThreadView]) -> str:
    """Text of `parco threads`."""
    return "\n\n".join(format_thread(v) for v in views) if views else "No threads."


def format_context(context: Context) -> str:
    """Text of `parco context`."""
    sections = [_context_header(context)]
    sections += [_file_section(view) for view in context.files]
    if context.phase is Phase.ITERATE:
        sections.append(_RULE + "\nOPEN THREADS (answer them; do not open new subjects)\n" + _RULE)
        sections.append(format_threads(context.open_threads))
    elif context.edits or context.open_threads:
        sections.append(_RULE + "\nALREADY PROPOSED IN THIS VERSION\n" + _RULE)
        sections += [format_edit(e) for e in context.edits]
        sections += [format_thread(t) for t in context.open_threads]
    return "\n\n".join(sections) + "\n"


def format_close_report(report: CloseReport) -> str:
    """Text of `parco close`."""
    lines = [
        f"Version {report.version} closed.",
        f"Applied {len(report.applied)} accepted edit(s)" + _ids(report.applied),
        f"Rejected {len(report.rejected)} pending edit(s)" + _ids(report.rejected),
        f"Resolved {len(report.resolved_threads)} open thread(s)",
    ]
    for skipped in report.skipped:
        lines.append(f"Skipped accepted edit {skipped.id}: {skipped.reason}")
    if report.bib_added:
        lines.append(f"Added to {report.bib_file}: {', '.join(report.bib_added)}")
    if report.bib_already_present:
        lines.append(f"Already in {report.bib_file}: {', '.join(report.bib_already_present)}")
    if report.bib_not_written:
        lines.append(
            "No bibliography file known (set `bib` in .parco/config.toml); entries not written: "
            + ", ".join(report.bib_not_written)
        )
    lines.append(f"Snapshot updated ({len(report.snapshot_files)} file(s)). Next version: {report.version + 1}.")
    return "\n".join(lines)


def _ids(ids: list[str]) -> str:
    return f": {', '.join(ids)}" if ids else ""


def _context_header(context: Context) -> str:
    lines = [
        _RULE,
        f"PARCO CONTEXT: version {context.version}, phase {context.phase}, mode {context.mode}",
        _RULE,
        "Allowed actions:",
        *[f"  - {a}" for a in context.allowed_actions],
        f"Main file: {context.main}",
        f"Tracked files: {', '.join(context.tracked_files)}",
        f"Bibliography: {context.bib or 'none'}",
        "Context paths (read them for background, never review them): "
        + (", ".join(context.context_paths) or "none"),
    ]
    if context.first_version:
        lines.append("First version: no snapshot yet, every file is shown in full and is to be read.")
    else:
        lines.append(
            "Only the changes since the last version (lines starting with + in the diffs) are to be read; "
            "unchanged parts are context only."
        )
    return "\n".join(lines)


def _file_section(view: FileView) -> str:
    titles = {
        "new": "no snapshot, full content",
        "changed": "changed since last version, diff snapshot -> current",
        "unchanged": "unchanged since last version, context only",
    }
    lines = [_RULE, f"FILE {view.path} ({titles[view.status]})", _RULE]
    if view.tags:
        lines.append("Tags: " + "; ".join(f"line {t.line}: {t.text}" for t in view.tags))
    body = view.content if view.status == "new" else view.diff
    if body:
        lines.append(body.rstrip("\n"))
    return "\n".join(lines)
