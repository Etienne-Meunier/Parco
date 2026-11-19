"""State persistence and stdin payload parsing."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from parco.core.errors import InputError
from parco.core.model import Author, Edit, EditKind, Message, Mode, Phase, State, Thread
from parco.core.requests import parse_comment_requests, parse_edit_requests, parse_reply_body
from parco.core.store import Store


def test_state_round_trip(tmp_path: Path) -> None:
    state = State(version=3, phase=Phase.ITERATE, mode=Mode.PROOFREAD, file_hashes={"a.tex": "h"})
    state.edits.append(Edit(id=state.new_id("e"), file="a.tex", offset=3, anchor="x", replace="y", reason="", kind=EditKind.REPLACE))
    state.threads.append(Thread(id=state.new_id("t"), is_global=True, messages=[Message(Author.AI, "hi")]))
    store = Store(tmp_path)
    store.save(state)
    raw = json.loads((tmp_path / "state.json").read_text())
    assert raw["threads"][0]["global"] is True and raw["mode"] == "proofread"
    assert store.load() == state


def test_transaction_not_saved_on_error(tmp_path: Path) -> None:
    store = Store(tmp_path)
    store.save(State())
    with pytest.raises(RuntimeError):
        with store.transaction() as state:
            state.version = 99
            raise RuntimeError
    assert store.load().version == 1
    with store.transaction() as state:
        state.version = 2
    assert store.load().version == 2


def test_parse_edit_requests() -> None:
    (req,) = parse_edit_requests('{"file": "a.tex", "anchor": "\\\\cite{parco}", "replace": ""}', batch=False, default_thread="t1")
    assert req.anchor == "\\cite{parco}" and req.replace == "" and req.thread == "t1"
    reqs = parse_edit_requests('[{"file": "a", "anchor": "b", "replace": "c", "thread": "t2"}]', batch=True, default_thread="t1")
    assert reqs[0].thread == "t2"


@pytest.mark.parametrize(
    ("text", "batch", "message"),
    [
        ("", False, "nothing"),
        ("{bad", False, "Invalid JSON"),
        ("[]", True, "non-empty"),
        ('[{"file": "a", "anchor": "b", "replace": "c"}]', False, "--batch"),
        ('{"file": "a", "anchor": "b"}', False, "replace"),
        ('{"file": "a", "anchor": "", "replace": "c"}', False, "anchor"),
        ('{"file": "a", "anchor": "b", "replace": "c", "extra": 1}', False, "Unknown"),
        ('[{"file": "a", "anchor": "b", "replace": "c"}, {"file": 1}]', True, "Item 1"),
    ],
)
def test_parse_edit_errors(text: str, batch: bool, message: str) -> None:
    with pytest.raises(InputError, match=message):
        parse_edit_requests(text, batch)


def test_parse_comment_requests() -> None:
    anchored, glob = parse_comment_requests(
        '[{"file": "a", "anchor": "b", "body": "c"}, {"global": true, "body": "d"}]', batch=True
    )
    assert anchored.file == "a" and not anchored.is_global
    assert glob.is_global and glob.file is None
    with pytest.raises(InputError, match="global"):
        parse_comment_requests('{"global": true, "file": "a", "body": "d"}', batch=False)
    with pytest.raises(InputError, match="anchor"):
        parse_comment_requests('{"file": "a", "body": "d"}', batch=False)


def test_parse_reply_body() -> None:
    assert parse_reply_body('{"body": "ok"}') == "ok"
    with pytest.raises(InputError):
        parse_reply_body('{"text": "ok"}')
