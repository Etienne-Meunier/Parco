"""End of a version: apply accepted edits, append bibtex entries, take the new snapshot, reset.

A close is computed entirely in memory first (`plan_close`), then journaled in
`.parco/close.json` before any file is touched. If writing fails midway, the next
`parco close` replays the journal (full contents, so replaying is idempotent) instead of
re-applying the edits, which can never apply an edit twice.
"""

from __future__ import annotations

import json
import shutil
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from parco.core.anchors import find_all
from parco.core.bib import append_entries, entry_keys
from parco.core.fileio import atomic_write_text, read_text, write_text
from parco.core.model import Edit, EditStatus, Phase, State, ThreadStatus
from parco.core.project import Project, bib_snapshot_name
from parco.core.review import require_review_phase, store_of

JOURNAL_NAME = "close.json"
NEW_SNAPSHOT_DIR = "snapshot.new"
OLD_SNAPSHOT_DIR = "snapshot.old"


@dataclass
class SkippedEdit:
    """An accepted edit that could not be applied, with the reason."""

    id: str
    reason: str


@dataclass
class CloseReport:
    """What `close` did."""

    version: int
    applied: list[str] = field(default_factory=list)
    skipped: list[SkippedEdit] = field(default_factory=list)
    rejected: list[str] = field(default_factory=list)
    resolved_threads: list[str] = field(default_factory=list)
    bib_file: str | None = None
    bib_added: list[str] = field(default_factory=list)
    bib_already_present: list[str] = field(default_factory=list)
    bib_not_written: list[str] = field(default_factory=list)
    snapshot_files: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CloseReport:
        """Build a report from its serialized form."""
        return cls(**{**data, "skipped": [SkippedEdit(**s) for s in data["skipped"]]})


@dataclass
class ClosePlan:
    """Everything a close writes, computed before touching any file.

    `writes` maps absolute paths (tex files, bibliography) to their new content;
    `snapshot` maps snapshot-relative paths to the content of the new snapshot.
    """

    report: CloseReport
    writes: dict[str, str]
    snapshot: dict[str, str]

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-compatible dict."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ClosePlan:
        """Build a plan from its serialized form."""
        return cls(report=CloseReport.from_dict(data["report"]), writes=data["writes"], snapshot=data["snapshot"])


def ensure_closable(project: Project) -> None:
    """Raise PhaseError if there is no version to close (nor an interrupted close to finish)."""
    state = store_of(project).load()
    if _pending_plan(project, state) is None:
        require_review_phase(state)


def close_version(project: Project) -> CloseReport:
    """Close the open version. Works even if tracked files were modified during the review."""
    with store_of(project).transaction() as state:
        plan = _pending_plan(project, state)
        if plan is None:
            require_review_phase(state)
            plan = plan_close(project, state)
            atomic_write_text(_journal_path(project), json.dumps(plan.to_dict(), ensure_ascii=False))
        _execute(project, plan)
        _reset(state, plan.report.version)
    _journal_path(project).unlink(missing_ok=True)
    return plan.report


def plan_close(project: Project, state: State) -> ClosePlan:
    """Compute the close of the open version without writing anything."""
    report = CloseReport(version=state.version)
    report.rejected = [e.id for e in state.edits if e.status is EditStatus.PENDING]
    report.resolved_threads = [t.id for t in state.threads if t.status is ThreadStatus.OPEN]
    new_texts, applied = _apply_accepted(project, state.edits, report)
    report.applied = [e.id for e in state.edits if e in applied]
    tracked = project.tracked_files(new_texts)
    bib = project.bib_path(new_texts)
    bib_text = _new_bib_text(project, bib, [e.bibtex for e in applied if e.bibtex], report)

    writes = {str(project.path(rel)): text for rel, text in new_texts.items()}
    snapshot = {rel: new_texts[rel] if rel in new_texts else project.read(rel) for rel in tracked}
    if bib is not None and bib_text is not None:
        writes[str(bib)] = bib_text
    if bib is not None and (bib_text is not None or bib.is_file()):
        snapshot[bib_snapshot_name(bib)] = bib_text if bib_text is not None else read_text(bib)
    report.snapshot_files = list(snapshot)
    return ClosePlan(report=report, writes=writes, snapshot=snapshot)


def apply_edits(source: str, edits: list[Edit]) -> tuple[str, list[Edit], list[SkippedEdit]]:
    """Apply edits to a source; returns the new text, the applied edits and the skipped ones."""
    located: list[tuple[int, Edit]] = []
    skipped: list[SkippedEdit] = []
    for edit in edits:
        offset = _relocate(source, edit)
        if offset is None:
            skipped.append(SkippedEdit(edit.id, "anchor no longer found exactly once"))
        else:
            located.append((offset, edit))
    applied: list[Edit] = []
    boundary = len(source) + 1
    # Descending offsets: applying an edit never moves the offsets of the edits before it.
    for offset, edit in sorted(located, key=lambda item: item[0], reverse=True):
        if offset + len(edit.anchor) > boundary:
            skipped.append(SkippedEdit(edit.id, "overlaps another applied edit"))
            continue
        source = source[:offset] + edit.replace + source[offset + len(edit.anchor):]
        boundary = offset
        applied.append(edit)
    return source, applied, skipped


def _apply_accepted(project: Project, edits: list[Edit], report: CloseReport) -> tuple[dict[str, str], list[Edit]]:
    by_file: dict[str, list[Edit]] = defaultdict(list)
    for edit in edits:
        if edit.status is EditStatus.ACCEPTED:
            by_file[edit.file].append(edit)
    new_texts: dict[str, str] = {}
    applied: list[Edit] = []
    for file, file_edits in by_file.items():
        if not project.path(file).is_file():
            report.skipped += [SkippedEdit(e.id, f"{file} no longer exists") for e in file_edits]
            continue
        text, file_applied, skipped = apply_edits(project.read(file), file_edits)
        report.skipped += skipped
        if file_applied:
            new_texts[file] = text
            applied += file_applied
    return new_texts, applied


def _relocate(source: str, edit: Edit) -> int | None:
    """Offset of the edit's anchor: the recorded one if still valid, else a unique re-find."""
    if source[edit.offset:edit.end] == edit.anchor:
        return edit.offset
    matches = find_all(source, edit.anchor)
    return matches[0] if len(matches) == 1 else None


def _new_bib_text(project: Project, bib: Path | None, entries: list[str], report: CloseReport) -> str | None:
    """New bibliography content with the entries appended, or None if it does not change."""
    if not entries:
        return None
    if bib is None:
        report.bib_not_written = [k for entry in entries for k in entry_keys(entry)]
        return None
    report.bib_file = project.display(bib)
    current = read_text(bib) if bib.is_file() else ""
    text, added, present = append_entries(current, entries)
    report.bib_added, report.bib_already_present = added, present
    return text if added else None


def _execute(project: Project, plan: ClosePlan) -> None:
    for path, text in plan.writes.items():
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(target, text)
    _install_snapshot(project, plan.snapshot)


def _install_snapshot(project: Project, files: dict[str, str]) -> None:
    """Build the new snapshot next to the current one, then swap the folders."""
    new = project.parco_dir / NEW_SNAPSHOT_DIR
    old = project.parco_dir / OLD_SNAPSHOT_DIR
    shutil.rmtree(new, ignore_errors=True)
    new.mkdir()
    for rel, text in files.items():
        target = new / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        write_text(target, text)
    shutil.rmtree(old, ignore_errors=True)
    if project.snapshot_dir.exists():
        project.snapshot_dir.rename(old)
    new.rename(project.snapshot_dir)
    shutil.rmtree(old, ignore_errors=True)


def _journal_path(project: Project) -> Path:
    return project.parco_dir / JOURNAL_NAME


def _pending_plan(project: Project, state: State) -> ClosePlan | None:
    """The journaled plan of an interrupted close of the current version; stale journals are dropped."""
    path = _journal_path(project)
    if not path.is_file():
        return None
    plan = ClosePlan.from_dict(json.loads(path.read_text(encoding="utf-8")))
    if state.phase is not Phase.IDLE and plan.report.version == state.version:
        return plan
    path.unlink()
    return None


def _reset(state: State, closed_version: int) -> None:
    state.version = closed_version + 1
    state.phase = Phase.IDLE
    state.mode = None
    state.opened_at = None
    state.file_hashes = {}
    state.edits = []
    state.threads = []
    state.next_id = 1
