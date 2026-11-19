"""Creation of the `.parco` folder of a LaTeX project."""

from __future__ import annotations

from pathlib import Path

from parco.core.config import Config, write_config
from parco.core.errors import AlreadyInitializedError, ConfigError
from parco.core.model import State
from parco.core.project import PARCO_DIR, SNAPSHOT_DIR, Project
from parco.core.store import Store


def init_project(root: Path, main: str, context: list[str], bib: str | None = None) -> Project:
    """Create `.parco/` in `root` with its config, an idle state and an empty snapshot.

    An empty snapshot means the first reading covers the whole document.
    """
    parco_dir = root / PARCO_DIR
    if parco_dir.exists():
        raise AlreadyInitializedError(f"{parco_dir} already exists.")
    if not (root / main).is_file():
        raise ConfigError(f"Main tex file {main!r} not found in {root}.")
    if bib is not None and not bib.strip():
        bib = None
    config = Config(main=main, context=[c for c in context if c.strip()], bib=bib)
    (parco_dir / SNAPSHOT_DIR).mkdir(parents=True)
    write_config(parco_dir, config)
    Store(parco_dir).save(State())
    return Project(root=root, config=config)
