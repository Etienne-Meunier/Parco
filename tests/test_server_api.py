"""API operations of the platform, through core, on a temporary project."""

from __future__ import annotations

import pytest

from parco.core import review
from parco.core.errors import NotFoundError, PhaseError, RuleError
from parco.core.model import Mode
from parco.core.project import Project
from parco.core.requests import CommentRequest, EditRequest
from parco.server.api import Api, StaleDocumentError
from parco.server.tools import find_tool

needs_pandoc = pytest.mark.skipif(find_tool("pandoc") is None, reason="pandoc not installed")


@pytest.fixture
def api(project: Project) -> Api:
    """API over a project with an open proofread version, one AI edit (e1) and one AI thread (t2)."""
    review.open_version(project, Mode.PROOFREAD)
    review.add_ai_edits(project, [EditRequest("main.tex", "We shows", "We show", reason="grammar")])
    review.add_ai_threads(project, [CommentRequest(body="Unclear.", file="sections/intro.tex", anchor="The model are stable.\n")])
    return Api(project)


def test_state(api: Api) -> None:
    state = api.state()
    assert (state["phase"], state["mode"], state["frozen"]) == ("read", "proofread", [])
    assert state["counts"] == {"pending": 1, "accepted": 0, "rejected": 0} and state["open_threads"] == 1
    assert [e["id"] for e in state["edits"]] == ["e1"] and [t["id"] for t in state["threads"]] == ["t2"]


def test_edit_decisions(api: Api) -> None:
    api.set_edit_status("e1", "accept")
    assert api.state()["counts"]["accepted"] == 1
    api.set_edit_status("e1", "reject")
    api.set_edit_status("e1", "reset")
    assert api.state()["counts"]["pending"] == 1
    with pytest.raises(NotFoundError):
        api.set_edit_status("e9", "accept")


def test_threads_reply_resolve_discuss(api: Api) -> None:
    global_id = api.create_thread({"global": True, "body": "Overall fine."})["id"]
    api.reply("t2", {"body": "Which part?"})
    api.set_thread_status("t2", "resolve")
    with pytest.raises(RuleError):
        api.reply("t2", {"body": "again"})
    api.set_thread_status("t2", "reopen")
    discussion = api.discuss_edit("e1", {"body": "Why?"})["id"]
    threads = {t["id"]: t for t in api.state()["threads"]}
    assert threads[global_id]["global"] and threads[discussion]["edit"] == "e1"
    assert [m["author"] for m in threads["t2"]["messages"]] == ["ai", "user"]


@needs_pandoc
def test_document_and_selection_thread(api: Api, project: Project) -> None:
    doc = api.document()
    assert doc["error"] is None and {k: v["display"] for k, v in doc["items"].items()} == {"e1": "inline", "t2": "inline"}
    assert doc["fills"]["f1"]["instruction"] == "explain why the scheme is stable"
    assert 'data-kind="del-start" data-id="e1"' in doc["html"]
    selection = {"selection": "The model are stable in practice", "before": None, "after": None, "body": "Source?"}
    with pytest.raises(StaleDocumentError):
        api.create_thread({**selection, "render_key": "old"})
    thread_id = api.create_thread({**selection, "render_key": doc["render_key"]})["id"]
    thread = next(t for t in api.state()["threads"] if t["id"] == thread_id)
    assert (thread["file"], thread["anchor"]) == ("sections/intro.tex", "The model are stable in practice")


@needs_pandoc
def test_document_without_items_when_frozen(api: Api, project: Project) -> None:
    (project.root / "sections/intro.tex").write_text("Changed.\n")
    assert api.state()["frozen"] == ["sections/intro.tex"]
    assert api.document()["items"] == {}


def test_revision_changes_with_state(api: Api) -> None:
    before = api.revision()["revision"]
    assert api.revision()["revision"] == before
    api.set_edit_status("e1", "accept")
    assert api.revision()["revision"] != before


def test_close(api: Api) -> None:
    api.set_edit_status("e1", "accept")
    report = api.close()
    assert report["applied"] == ["e1"] and report["resolved_threads"] == ["t2"]
    assert api.state()["phase"] == "idle"
    with pytest.raises(PhaseError):
        api.close()
