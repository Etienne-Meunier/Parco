"""Shared fixtures: a small LaTeX project initialized with parco."""

from __future__ import annotations

from pathlib import Path

import pytest

from parco.core.initialize import init_project
from parco.core.project import Project

from helpers import INTRO_TEX, MAIN_TEX, REFS_BIB, write_files


@pytest.fixture
def latex_root(tmp_path: Path) -> Path:
    """A LaTeX project folder (not initialized)."""
    write_files(
        tmp_path,
        {"main.tex": MAIN_TEX, "sections/intro.tex": INTRO_TEX, "sections/old.tex": "old\n", "refs.bib": REFS_BIB},
    )
    return tmp_path


@pytest.fixture
def project(latex_root: Path) -> Project:
    """The LaTeX project initialized with parco."""
    return init_project(latex_root, "main.tex", ["../code"])
