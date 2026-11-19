"""Smoke test of the platform page in headless Chrome (skipped when Chrome or pandoc is missing)."""

from __future__ import annotations

import os
import signal
import subprocess
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

from parco.core import review
from parco.core.model import Mode
from parco.core.project import Project
from parco.core.requests import CommentRequest, EditRequest
from parco.server.app import make_server
from parco.server.tools import find_tool

CHROME = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
TIMEOUT_S = 60

pytestmark = pytest.mark.skipif(
    not CHROME.exists() or find_tool("pandoc") is None, reason="needs Google Chrome and pandoc"
)


@pytest.fixture
def page_url(project: Project) -> Iterator[str]:
    """URL of a server over the test project with one AI edit and one AI thread."""
    review.open_version(project, Mode.PROOFREAD)
    review.add_ai_edits(project, [EditRequest("main.tex", "We shows", "We show")])
    review.add_ai_threads(project, [CommentRequest(body="Unclear.", file="sections/intro.tex", anchor="The model are stable.\n")])
    server = make_server(project, 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}/"
    server.shutdown()
    server.server_close()


def _dump_dom(url: str, profile: Path) -> str:
    """DOM of `url` after scripts ran; the Chrome process group is killed if it outlives the timeout."""
    command = [
        str(CHROME), "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
        f"--user-data-dir={profile}", "--virtual-time-budget=8000", "--dump-dom", url,
    ]
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, start_new_session=True)
    try:
        output, _ = process.communicate(timeout=TIMEOUT_S)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.communicate()
        pytest.fail(f"headless Chrome did not finish in {TIMEOUT_S} s")
    return output.decode()


def test_page_renders_document_and_cards(page_url: str, tmp_path: Path) -> None:
    dom = _dump_dom(page_url, tmp_path / "chrome-profile")
    assert 'data-card="e1"' in dom and 'data-card="t2"' in dom
    assert 'data-kind="del-start" data-id="e1"' in dom
    assert "ZQPM" not in dom
