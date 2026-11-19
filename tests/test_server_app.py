"""HTTP server: routes, status codes and static files, on an ephemeral port."""

from __future__ import annotations

import http.client
import json
import subprocess
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from typing import Any

import pytest

from parco.core import review
from parco.core.model import Mode
from parco.core.project import Project
from parco.core.requests import EditRequest
from parco.server.app import make_server
from parco.server.tools import find_tool

needs_pandoc = pytest.mark.skipif(find_tool("pandoc") is None, reason="pandoc not installed")


@pytest.fixture
def base_url(project: Project) -> Iterator[str]:
    """URL of a server running in a background thread for the test project."""
    server = make_server(project, 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def _request(url: str, method: str = "GET", body: Any = None, raw: bytes | None = None) -> tuple[int, bytes, str]:
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    request = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request) as response:
            return response.status, response.read(), response.headers["Content-Type"]
    except urllib.error.HTTPError as error:
        return error.code, error.read(), error.headers["Content-Type"]


def _json(url: str, method: str = "GET", body: Any = None) -> tuple[int, Any]:
    status, data, _ = _request(url, method, body)
    return status, json.loads(data)


def test_static_files(base_url: str) -> None:
    for path, kind in [("/", "text/html"), ("/static/app.js", "text/javascript"), ("/static/style.css", "text/css")]:
        status, data, content_type = _request(base_url + path)
        assert status == 200 and content_type.startswith(kind) and data
    assert _request(base_url + "/static/../api.py")[0] == 404
    assert _request(base_url + "/static/nope.js")[0] == 404


def test_state_and_errors_when_idle(base_url: str) -> None:
    status, state = _json(base_url + "/api/state")
    assert status == 200 and state["phase"] == "idle"
    status, error = _json(base_url + "/api/close", "POST")
    assert status == 409 and "No version is open" in error["error"]
    assert _json(base_url + "/api/unknown")[0] == 404
    assert _json(base_url + "/api/revision")[0] == 200


def test_operations(base_url: str, project: Project) -> None:
    review.open_version(project, Mode.PROOFREAD)
    review.add_ai_edits(project, [EditRequest("main.tex", "We shows", "We show")])
    assert _json(base_url + "/api/edits/e1/accept", "POST") == (200, {"ok": True})
    assert _json(base_url + "/api/edits/e9/accept", "POST")[0] == 404
    status, created = _json(base_url + "/api/threads", "POST", {"global": True, "body": "Nice."})
    assert status == 200 and created["id"] == "t2"
    assert _json(base_url + "/api/threads/t2/reply", "POST", {"body": "Thanks"})[0] == 200
    assert _json(base_url + "/api/threads/t2/resolve", "POST")[0] == 200
    assert _json(base_url + "/api/threads", "POST", {"global": True})[0] == 400
    assert _request(base_url + "/api/threads", "POST", raw=b"{oops")[0] == 400
    assert _json(base_url + "/api/edits/e1/discuss", "POST", {"body": "Hm"})[0] == 200
    status, report = _json(base_url + "/api/close", "POST")
    assert status == 200 and report["applied"] == ["e1"]


def test_unknown_figure(base_url: str) -> None:
    assert _request(base_url + "/fig/nothing.png")[0] == 404


@pytest.mark.parametrize("length", ["abc", "-5"])
def test_malformed_content_length_is_400(base_url: str, length: str) -> None:
    connection = http.client.HTTPConnection(base_url.removeprefix("http://"), timeout=5)
    connection.putrequest("POST", "/api/threads")
    connection.putheader("Content-Length", length)
    connection.endheaders()
    response = connection.getresponse()
    assert response.status == 400 and "Content-Length" in json.loads(response.read())["error"]
    connection.close()


@needs_pandoc
def test_selection_with_stale_render_key_is_409(base_url: str, project: Project) -> None:
    review.open_version(project, Mode.PROOFREAD)
    status, document = _json(base_url + "/api/document")
    assert status == 200 and document["render_key"]
    selection = {"selection": "We shows", "before": None, "after": None, "body": "Hm"}
    status, error = _json(base_url + "/api/threads", "POST", {**selection, "render_key": "stale"})
    assert status == 409 and "select the text again" in error["error"]
    assert _json(base_url + "/api/threads", "POST", {**selection, "render_key": document["render_key"]})[0] == 200


def test_pandoc_timeout_shows_in_document(base_url: str, project: Project, monkeypatch: pytest.MonkeyPatch) -> None:
    def slow(*args: object, **kwargs: object) -> None:
        raise subprocess.TimeoutExpired(cmd="pandoc", timeout=30)

    monkeypatch.setattr("parco.server.render.find_tool", lambda name: "pandoc")
    monkeypatch.setattr("parco.server.render.subprocess.run", slow)
    status, document = _json(base_url + "/api/document")
    assert status == 200 and document["html"] == "" and "did not finish" in document["error"]
