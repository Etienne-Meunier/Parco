"""End-to-end tests of the CLI with click's CliRunner on a temporary LaTeX project."""

from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest
from click.testing import CliRunner, Result

from parco.cli import cli
from parco.core import review
from parco.core.project import Project
from helpers import INTRO_TEX, MAIN_TEX, REFS_BIB


@pytest.fixture
def run(latex_root: Path, monkeypatch: pytest.MonkeyPatch):
    """Run a parco command in the LaTeX project, asserting its exit code."""
    monkeypatch.chdir(latex_root)
    # click < 8.2 mixes stderr into output unless told otherwise; 8.2+ always separates them.
    separate = {"mix_stderr": False} if "mix_stderr" in inspect.signature(CliRunner).parameters else {}
    runner = CliRunner(**separate)

    def invoke(*args: str, input: str | None = None, code: int = 0) -> Result:
        result = runner.invoke(cli, list(args), input=input)
        assert result.exit_code == code, (result.output, result.stderr)
        return result

    return invoke


def _json(result: Result):
    return json.loads(result.stdout)


def test_full_review_cycle(run, latex_root: Path) -> None:
    run("init", "--main", "main.tex", input="../code, notes\n\n")
    assert 'context = ["../code", "notes"]' in (latex_root / ".parco/config.toml").read_text()

    assert "phase read, mode generative" in run("open", "--mode", "generative").output
    assert "already open" in run("open", "--mode", "comment", code=1).stderr

    context = run("context").output
    assert "FILE sections/intro.tex (no snapshot, full content)" in context
    assert "line 3: %parco explain why the scheme is stable" in context
    ctx = _json(run("context", "--json"))
    assert ctx["tracked_files"] == ["main.tex", "sections/intro.tex"] and ctx["bib"] == "refs.bib"

    batch = [
        {"file": "main.tex", "anchor": "We shows", "replace": "We show", "reason": "grammar"},
        {
            "file": "main.tex",
            "anchor": "works \\cite{parco}",
            "replace": "works \\cite{smith2020}",
            "bibtex": "@article{smith2020,\n  title={Stability},\n  year={2020}\n}",
        },
        {
            "file": "sections/intro.tex",
            "anchor": "%parco explain why the scheme is stable",
            "replace": "The scheme is stable because it is implicit.",
        },
        {"file": "sections/intro.tex", "anchor": "The model are stable in", "replace": "The models are stable in"},
    ]
    assert _json(run("edit", "add", "--batch", "--json", input=json.dumps(batch))) == {"ids": ["e1", "e2", "e3", "e4"]}
    ambiguous = run("edit", "add", input=json.dumps({"file": "main.tex", "anchor": "e", "replace": "x"}), code=1)
    assert "ambiguous" in ambiguous.stderr

    anchored = {"file": "sections/intro.tex", "anchor": "The model are stable.", "body": "Which model?"}
    assert "Created t5" in run("comment", "add", input=json.dumps(anchored)).output
    assert "Created t6" in run("comment", "add", input='{"global": true, "body": "Add a figure."}').output

    run("accept", "e1", "e2", "e3")
    run("reject", "e4")
    run("reject", "e99", code=1)

    run("iterate")
    run("comment", "add", input='{"global": true, "body": "New subject"}', code=1)
    run("edit", "add", input=json.dumps({"file": "main.tex", "anchor": "scheme works", "replace": "x"}), code=1)
    assert "waiting for the user" in run("reply", "t6", input='{"body": "More"}', code=1).stderr
    review.user_reply(Project.discover(latex_root), "t5", "The advection one, please clarify.")
    clarification = {"file": "sections/intro.tex", "anchor": "The model are stable.", "replace": "The advection model is stable."}
    assert "Created e7" in run("edit", "add", "--thread", "t5", input=json.dumps(clarification)).output
    run("reply", "t5", input='{"body": "Clarified in e7."}')
    run("accept", "e7")

    threads = _json(run("threads", "--open", "--json"))
    t5 = next(t for t in threads if t["id"] == "t5")
    assert [m["author"] for m in t5["messages"]] == ["ai", "user", "ai"] and t5["edits"][0]["id"] == "e7"
    assert "[t5] open thread" in run("threads").output

    status = run("status").output
    assert "4 accepted, 1 rejected" in status and "2 open" in status

    run("close", input="n\n", code=1)
    report = run("close", "--yes").output
    assert "Applied 4 accepted edit(s): e1, e2, e3, e7" in report and "Added to refs.bib: smith2020" in report

    main = (latex_root / "main.tex").read_text()
    assert main == MAIN_TEX.replace("We shows", "We show").replace("\\cite{parco}", "\\cite{smith2020}")
    intro = (latex_root / "sections/intro.tex").read_text()
    assert intro == (
        INTRO_TEX.replace("%parco explain why the scheme is stable", "The scheme is stable because it is implicit.")
        .replace("The model are stable.", "The advection model is stable.")
    )
    bib = (latex_root / "refs.bib").read_text()
    assert bib.startswith(REFS_BIB) and "@article{smith2020," in bib

    snapshot = latex_root / ".parco/snapshot"
    assert (snapshot / "main.tex").read_text() == main
    assert (snapshot / "sections/intro.tex").read_text() == intro
    assert (snapshot / "_bib/refs.bib").read_text() == bib

    state = json.loads((latex_root / ".parco/state.json").read_text())
    assert (state["version"], state["phase"], state["mode"]) == (2, "idle", None)
    assert state["edits"] == [] and state["threads"] == []
    run("context", code=1)

    run("open", "--mode", "comment")
    assert "unchanged since last version" in run("context").output


def test_frozen_files_reported(run, latex_root: Path) -> None:
    run("init", "--main", "main.tex", "--context", "../code", "--bib", "refs.bib")
    run("open", "--mode", "proofread")
    (latex_root / "sections/intro.tex").write_text("hand edit\n")
    assert "sections/intro.tex" in run("context", code=1).stderr
    assert "WARNING" in run("status").output
    run("close", "--yes")


def test_misc_commands(run) -> None:
    run("status", code=1)
    run("init", "--main", "main.tex", "--context", "../code", "--bib", "")
    run("init", "--main", "main.tex", "--context", "x", "--bib", "", code=1)
    assert "phase idle" in run("status").output
    assert _json(run("status", "--json"))["phase"] == "idle"
    assert "parco edit add" in run("guide").output
    run("open", "--mode", "nonsense", code=2)
    run("open", "--mode", "reference")
    run("comment", "add", input='{"global": true, "body": "x"}', code=1)
    run("edit", "add", input="not json", code=1)


def test_close_checks_phase_before_confirmation(run) -> None:
    run("init", "--main", "main.tex", "--context", "../code", "--bib", "")
    result = run("close", input="y\n", code=1)
    assert "No version is open" in result.stderr and "Close the version?" not in result.output


def test_status_reports_missing_main(run, latex_root: Path) -> None:
    run("init", "--main", "main.tex", "--context", "../code", "--bib", "")
    run("open", "--mode", "comment")
    (latex_root / "main.tex").rename(latex_root / "paper.tex")
    status = run("status").output
    assert "WARNING" in status and "main.tex" in status
    run("context", code=1)


def test_render_starts_the_server(run, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []
    monkeypatch.setattr("parco.cli.commands.serve", lambda project, port, open_browser: calls.append((port, open_browser)))
    run("init", "--main", "main.tex", "--context", "../code", "--bib", "")
    run("render", "--port", "0", "--no-browser")
    run("render")
    assert calls == [(0, False), (8765, True)]
