"""Persistence of the review state in `.parco/state.json`, under an exclusive file lock."""

from __future__ import annotations

import fcntl
import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from parco.core.fileio import atomic_write_text
from parco.core.model import State

STATE_NAME = "state.json"
LOCK_NAME = "state.lock"


class Store:
    """Load and save the state; every mutation is load -> mutate -> save under one lock."""

    def __init__(self, parco_dir: Path) -> None:
        self.parco_dir = parco_dir

    @property
    def state_path(self) -> Path:
        """Path of `state.json`."""
        return self.parco_dir / STATE_NAME

    @contextmanager
    def _locked(self, mode: int) -> Iterator[None]:
        # The lock lives on a separate file: state.json is replaced atomically, so its inode changes.
        with open(self.parco_dir / LOCK_NAME, "a") as handle:
            fcntl.flock(handle, mode)
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    def _read(self) -> State:
        return State.from_dict(json.loads(self.state_path.read_text(encoding="utf-8")))

    def _write(self, state: State) -> None:
        text = json.dumps(state.to_dict(), indent=2, ensure_ascii=False) + "\n"
        atomic_write_text(self.state_path, text)

    def load(self) -> State:
        """Read the current state (shared lock)."""
        with self._locked(fcntl.LOCK_SH):
            return self._read()

    def save(self, state: State) -> None:
        """Overwrite the state (exclusive lock)."""
        with self._locked(fcntl.LOCK_EX):
            self._write(state)

    @contextmanager
    def transaction(self) -> Iterator[State]:
        """Yield the state for mutation and save it on normal exit; nothing is saved on error."""
        with self._locked(fcntl.LOCK_EX):
            state = self._read()
            yield state
            self._write(state)
