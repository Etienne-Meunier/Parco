"""Location of the external programs used by the renderer (pandoc, pdftoppm)."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

_EXTRA_DIRS = (Path(sys.executable).parent, Path("/opt/homebrew/bin"), Path("/usr/local/bin"))


def find_tool(name: str) -> str | None:
    """Path of an executable: `$PARCO_<NAME>`, then PATH, then the Python env and Homebrew folders."""
    override = os.environ.get(f"PARCO_{name.upper()}")
    if override:
        return override
    found = shutil.which(name)
    if found:
        return found
    for folder in _EXTRA_DIRS:
        candidate = folder / name
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None
