"""Close: apply accepted edits, bib append, snapshot update, reset."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from parco.core import review
from parco.core.close import close_version
from parco.core.context import build_context
from parco.core import close as close_module
from parco.core.errors import ConfigError, PhaseError
from parco.core.initialize import init_project
from parco.core.model import Mode, Phase
from parco.core.project import Project
from parco.core.requests import CommentRequest, EditRequest
from parco.core.store import Store
from helpers import MAIN_TEX, REFS_BIB, write_files


def _bibtex(key: str) -> str:
    return f"@article{{{key},\n  title={{T}}\n}}"


def test_close_requires_open_version(project: Project) -> None:
    with pytest.raises(PhaseError):
        close_version(project)


def test_close_applies_accepted_rejects_pending(project: Project) -> None:
    review.open_version(project, Mode.PROOFREAD)
    review.add_ai_edits(
        project,
        [
            EditRequest("main.tex", "We shows", "We show"),
            EditRequest("main.tex", "scheme works", "scheme is effective"),
            EditRequest("main.tex", "\\begin{document}", "\\begin{document}\n\\maketitle"),
            EditRequest("main.tex", "\\end{document}", "% end\n\\end{document}"),
        ],
    )
    review.add_ai_threads(project, [CommentRequest(body="Global remark", is_global=True)])
    review.accept_edits(project, ["e1", "e2", "e3"])
    report = close_version(project)

    assert report.applied == ["e1", "e2", "e3"]
    assert report.rejected == ["e4"]
    assert report.resolved_threads == ["t5"]
    assert project.read("main.tex") == MAIN_TEX.replace("We shows", "We show").replace(
        "scheme works", "scheme is effective"
    ).replace("\\begin{document}", "\\begin{document}\n\\maketitle")

    state = Store(project.parco_dir).load()
    assert (state.version, state.phase, state.mode) == (2, Phase.IDLE, None)
    assert state.edits == [] and state.threads == [] and state.next_id == 1


def test_close_snapshot_contains_tracked_files_and_bib(project: Project) -> None:
    review.open_version(project, Mode.PROOFREAD)
    review.add_ai_edits(project, [EditRequest("main.tex", "We shows", "We show")])
    review.accept_edits(project, ["e1"])
    report = close_version(project)
    assert report.snapshot_files == ["main.tex", "sections/intro.tex", "_bib/refs.bib"]
    assert project.snapshot_text("main.tex") == project.read("main.tex")
    assert project.snapshot_text("sections/old.tex") is None


def test_snapshot_recomputed_after_apply(project: Project) -> None:
    review.open_version(project, Mode.PROOFREAD)
    review.add_ai_edits(project, [EditRequest("main.tex", "% \\input{sections/old}\n", "\\input{sections/old}\n")])
    review.accept_edits(project, ["e1"])
    report = close_version(project)
    assert "sections/old.tex" in report.snapshot_files


def test_bib_append_skips_known_keys(project: Project) -> None:
    review.open_version(project, Mode.REFERENCE)
    review.add_ai_edits(
        project,
        [
            EditRequest(
                "main.tex",
                "works \\cite{parco}",
                "works \\cite{known2019,new2021}",
                bibtex=_bibtex("known2019") + "\n" + _bibtex("new2021"),
            )
        ],
    )
    review.accept_edits(project, ["e1"])
    report = close_version(project)
    assert report.bib_added == ["new2021"]
    bib = project.read("refs.bib")
    assert bib.count("@article{known2019") == 1 and "@article{new2021" in bib
    assert (project.snapshot_dir / "_bib/refs.bib").read_text() == bib


def test_rejected_cite_edit_does_not_touch_bib(project: Project) -> None:
    review.open_version(project, Mode.REFERENCE)
    review.add_ai_edits(project, [EditRequest("main.tex", "\\cite{parco}", "\\cite{x}", bibtex=_bibtex("x"))])
    before = project.read("refs.bib")
    close_version(project)
    assert project.read("refs.bib") == before
    assert "\\cite{parco}" in project.read("main.tex")


def test_bib_not_written_without_bib_file(tmp_path: Path) -> None:
    write_files(tmp_path, {"main.tex": "See \\cite{parco}.\n"})
    proj = init_project(tmp_path, "main.tex", [])
    review.open_version(proj, Mode.REFERENCE)
    review.add_ai_edits(proj, [EditRequest("main.tex", "\\cite{parco}", "\\cite{x}", bibtex=_bibtex("x"))])
    review.accept_edits(proj, ["e1"])
    report = close_version(proj)
    assert report.bib_not_written == ["x"]
    assert proj.read("main.tex") == "See \\cite{x}.\n"


def test_bib_created_when_configured_but_missing(tmp_path: Path) -> None:
    write_files(tmp_path, {"main.tex": "See \\cite{parco}.\n"})
    proj = init_project(tmp_path, "main.tex", [], bib="biblio/refs.bib")
    review.open_version(proj, Mode.REFERENCE)
    review.add_ai_edits(proj, [EditRequest("main.tex", "\\cite{parco}", "\\cite{x}", bibtex=_bibtex("x"))])
    review.accept_edits(proj, ["e1"])
    close_version(proj)
    assert proj.read("biblio/refs.bib").startswith("@article{x,")


def test_close_after_manual_modification_relocates_or_skips(project: Project) -> None:
    review.open_version(project, Mode.PROOFREAD)
    review.add_ai_edits(
        project,
        [EditRequest("main.tex", "We shows", "We show"), EditRequest("main.tex", "scheme works", "scheme is fine")],
    )
    review.accept_edits(project, ["e1", "e2"])
    text = project.read("main.tex")
    write_files(project.root, {"main.tex": "Preamble added.\n" + text.replace("scheme works", "scheme runs")})
    report = close_version(project)
    assert report.applied == ["e1"]
    assert [s.id for s in report.skipped] == ["e2"]
    assert project.read("main.tex").startswith("Preamble added.\n\\documentclass")
    assert "We show that the scheme runs" in project.read("main.tex")


def test_crlf_preserved(tmp_path: Path) -> None:
    (tmp_path / "main.tex").write_bytes(b"Line one.\r\nWe shows it.\r\n")
    proj = init_project(tmp_path, "main.tex", [])
    review.open_version(proj, Mode.PROOFREAD)
    review.add_ai_edits(proj, [EditRequest("main.tex", "shows it.\n", "show it.\n")])
    review.accept_edits(proj, ["e1"])
    close_version(proj)
    assert (tmp_path / "main.tex").read_bytes() == b"Line one.\r\nWe show it.\r\n"


def test_next_context_shows_diff_since_snapshot(project: Project) -> None:
    review.open_version(project, Mode.PROOFREAD)
    first = build_context(project)
    assert first.first_version and {f.status for f in first.files} == {"new"}
    review.add_ai_edits(project, [EditRequest("main.tex", "We shows", "We show")])
    review.accept_edits(project, ["e1"])
    close_version(project)

    path = project.root / "sections/intro.tex"
    path.write_text(path.read_text() + "A new paragraph.\n")
    review.open_version(project, Mode.PROOFREAD)
    context = build_context(project)
    assert not context.first_version
    by_path = {f.path: f for f in context.files}
    assert by_path["main.tex"].status == "unchanged" and by_path["main.tex"].diff is None
    intro = by_path["sections/intro.tex"]
    assert intro.status == "changed" and "+A new paragraph." in (intro.diff or "")
    assert [t.kind for t in intro.tags] == ["fill"]


def test_close_planning_failure_touches_nothing(project: Project) -> None:
    review.open_version(project, Mode.PROOFREAD)
    review.add_ai_edits(project, [EditRequest("main.tex", "We shows", "We show")])
    review.accept_edits(project, ["e1"])
    (project.root / "main.tex").rename(project.root / "gone.tex")
    with pytest.raises(ConfigError):
        close_version(project)
    assert Store(project.parco_dir).load().phase is Phase.READ
    assert not (project.parco_dir / "close.json").exists()


def test_interrupted_close_is_replayed_without_double_apply(project: Project, monkeypatch: pytest.MonkeyPatch) -> None:
    review.open_version(project, Mode.PROOFREAD)
    review.add_ai_edits(project, [EditRequest("main.tex", "We shows", "We shows indeed")])
    review.accept_edits(project, ["e1"])
    old_snapshot = {p.name for p in project.snapshot_dir.rglob("*")}

    def crash(*_: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(close_module, "_install_snapshot", crash)
    with pytest.raises(OSError):
        close_version(project)
    assert "We shows indeed that" in project.read("main.tex")
    assert {p.name for p in project.snapshot_dir.rglob("*")} == old_snapshot
    assert Store(project.parco_dir).load().phase is Phase.READ

    monkeypatch.undo()
    report = close_version(project)
    assert report.applied == ["e1"]
    assert project.read("main.tex").count("We shows indeed") == 1
    assert project.snapshot_text("main.tex") == project.read("main.tex")
    assert Store(project.parco_dir).load().phase is Phase.IDLE
    assert not (project.parco_dir / "close.json").exists()


def test_stale_journal_is_ignored(project: Project) -> None:
    stale = close_module.ClosePlan(report=close_module.CloseReport(version=0), writes={"x": "y"}, snapshot={})
    (project.parco_dir / "close.json").write_text(json.dumps(stale.to_dict()))
    with pytest.raises(PhaseError):
        close_version(project)
    assert not (project.parco_dir / "close.json").exists()


def test_bib_outside_project_appended_and_snapshotted(tmp_path: Path) -> None:
    root = tmp_path / "project"
    write_files(root, {"main.tex": "See \\cite{parco}.\n\\bibliography{../shared/refs}\n"})
    write_files(tmp_path, {"shared/refs.bib": REFS_BIB})
    proj = init_project(root, "main.tex", [])
    review.open_version(proj, Mode.REFERENCE)
    review.add_ai_edits(proj, [EditRequest("main.tex", "\\cite{parco}", "\\cite{x}", bibtex=_bibtex("x"))])
    review.accept_edits(proj, ["e1"])
    report = close_version(proj)
    bib = (tmp_path / "shared/refs.bib").read_text()
    assert report.bib_added == ["x"] and "@article{x," in bib
    assert (proj.snapshot_dir / "_bib/refs.bib").read_text() == bib
