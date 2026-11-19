"""Exact text file I/O: newlines are preserved so that offsets match the bytes on disk."""

from __future__ import annotations

import hashlib
import os
import stat
import tempfile
from pathlib import Path


def read_text(path: Path) -> str:
    """Read a UTF-8 file without newline translation."""
    with open(path, encoding="utf-8", newline="") as handle:
        return handle.read()


def write_text(path: Path, text: str) -> None:
    """Write a UTF-8 file without newline translation."""
    with open(path, "w", encoding="utf-8", newline="") as handle:
        handle.write(text)


def atomic_write_text(path: Path, text: str) -> None:
    """Write a file (no newline translation) through a temporary file and an atomic rename.

    Keeps the permissions of an existing file; a new file gets 0o644.
    """
    mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o644
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def sha256_file(path: Path) -> str:
    """Hex SHA-256 digest of a file's bytes."""
    return hashlib.sha256(path.read_bytes()).hexdigest()
