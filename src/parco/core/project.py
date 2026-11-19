"""Locate a parco project and resolve the LaTeX files it tracks."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from parco.core.config import Config, read_config
from parco.core.errors import ConfigError, FilesModifiedError, NotInitializedError
from parco.core.fileio import read_text, sha256_file
from parco.core.latex import comment_spans, strip_comments

PARCO_DIR = ".parco"
SNAPSHOT_DIR = "snapshot"
BIB_SNAPSHOT_DIR = "_bib"

_INCLUDE_RE = re.compile(r"\\(?:input|include)\s*\{([^{}]+)\}")
_BIBLIOGRAPHY_RE = re.compile(r"\\bibliography\s*\{([^{}]+)\}")
_ADDBIBRESOURCE_RE = re.compile(r"\\addbibresource\s*(?:\[[^\]]*\])?\s*\{([^{}]+)\}")

Overrides = Mapping[str, str]
"""In-memory contents that replace tracked files on disk (relative path -> text)."""


def find_parco_dir(start: Path) -> Path:
    """Walk up from `start` to find the `.parco` folder, or raise NotInitializedError."""
    for folder in [start, *start.parents]:
        candidate = folder / PARCO_DIR
        if candidate.is_dir():
            return candidate
    raise NotInitializedError(
        f"No {PARCO_DIR} folder found in {start} or its parents. Run `parco init` first."
    )


@dataclass(frozen=True)
class Include:
    """An `\\input`/`\\include` command spanning [start, end) of a file, resolved to `target`."""

    start: int
    end: int
    target: str


@dataclass
class Project:
    """A LaTeX project with its `.parco` folder and configuration.

    Tracked paths are relative to the project root (POSIX style). LaTeX references
    (`\\input`, `\\include`, `\\bibliography`, `\\addbibresource`) are resolved relative
    to the directory of the main file, as LaTeX does.
    """

    root: Path
    config: Config

    @classmethod
    def discover(cls, start: Path) -> Project:
        """Load the project whose `.parco` folder contains `start` (or one of its parents)."""
        parco_dir = find_parco_dir(start.resolve())
        return cls(root=parco_dir.parent, config=read_config(parco_dir))

    @property
    def parco_dir(self) -> Path:
        """The `.parco` folder."""
        return self.root / PARCO_DIR

    @property
    def snapshot_dir(self) -> Path:
        """The folder holding the copy of the tracked files at the last close."""
        return self.parco_dir / SNAPSHOT_DIR

    @property
    def context_paths(self) -> list[Path]:
        """Context folders/files of the configuration, as absolute paths."""
        return [(self.root / c).resolve() for c in self.config.context]

    def path(self, rel: str) -> Path:
        """Absolute path of a project-relative path."""
        return self.root / rel

    def read(self, rel: str) -> str:
        """Read a project file exactly (no newline translation)."""
        return read_text(self.path(rel))

    def normalize(self, rel: str | Path) -> str | None:
        """Project-relative POSIX path, or None if it points outside the project."""
        absolute = (self.root / rel).resolve()
        try:
            return absolute.relative_to(self.root.resolve()).as_posix()
        except ValueError:
            return None

    def display(self, path: Path) -> str:
        """Path relative to the project root when inside it, absolute otherwise."""
        return self.normalize(path) or str(path)

    def main_file(self) -> str:
        """Normalized main tex file, or raise ConfigError if it is missing."""
        main = self.normalize(self.config.main)
        if main is None or not self.path(main).is_file():
            raise ConfigError(f"Main tex file {self.config.main!r} not found in {self.root}.")
        return main

    def tracked_files(self, overrides: Overrides | None = None) -> list[str]:
        """Main tex file and every file reached through `\\input`/`\\include`, in reading order."""
        ordered: list[str] = []
        self._collect(self.main_file(), ordered, overrides or {})
        return ordered

    def _collect(self, rel: str, ordered: list[str], overrides: Overrides) -> None:
        if rel in ordered:
            return
        ordered.append(rel)
        for include in self.includes(rel, overrides):
            self._collect(include.target, ordered, overrides)

    def includes(self, rel: str, overrides: Overrides | None = None) -> list[Include]:
        """`\\input`/`\\include` commands of a file (outside comments) whose target exists."""
        text = self._text(rel, overrides or {})
        comments = comment_spans(text)
        found: list[Include] = []
        for match in _INCLUDE_RE.finditer(text):
            if any(start <= match.start() < end for start, end in comments):
                continue
            target = self._resolve_include(match.group(1).strip())
            if target is not None:
                found.append(Include(start=match.start(), end=match.end(), target=target))
        return found

    def _resolve_include(self, target: str) -> str | None:
        """Like LaTeX: try `target.tex` first, then `target` as written, from the main file's folder."""
        base = self._main_dir()
        for candidate in (f"{target}.tex", target):
            rel = self.normalize(base / candidate)
            if rel is not None and self.path(rel).is_file():
                return rel
        return None

    def _main_dir(self) -> PurePosixPath:
        return PurePosixPath(self.main_file()).parent

    def _text(self, rel: str, overrides: Overrides) -> str:
        return overrides[rel] if rel in overrides else self.read(rel)

    def bib_path(self, overrides: Overrides | None = None) -> Path | None:
        """Absolute path of the bibliography: from the config (relative to the root),
        else the first one declared in tracked files (relative to the main file's folder).

        It may lie outside the project, and may not exist yet.
        """
        if self.config.bib:
            return (self.root / self.config.bib).resolve()
        overrides = overrides or {}
        for rel in self.tracked_files(overrides):
            declared = _declared_bib(strip_comments(self._text(rel, overrides)))
            if declared is not None:
                return (self.root / self._main_dir() / declared).resolve()
        return None

    def current_hashes(self) -> dict[str, str]:
        """SHA-256 of every tracked file, keyed by relative path."""
        return {rel: sha256_file(self.path(rel)) for rel in self.tracked_files()}

    def modified_files(self, recorded: dict[str, str]) -> list[str]:
        """Tracked files whose content differs from `recorded`: changed, added, removed or missing.

        Never raises on a missing main file: every recorded file is then reported.
        """
        try:
            current = self.current_hashes()
        except (ConfigError, FileNotFoundError):
            current = {}
        names = sorted(set(current) | set(recorded))
        return [n for n in names if current.get(n) != recorded.get(n)]

    def ensure_unchanged(self, recorded: dict[str, str]) -> None:
        """Raise FilesModifiedError if tracked files changed since `recorded` was taken."""
        modified = self.modified_files(recorded)
        if modified:
            raise FilesModifiedError(modified)

    def has_snapshot(self) -> bool:
        """True once a version has been closed (the snapshot holds at least one file)."""
        return self.snapshot_dir.is_dir() and any(p.is_file() for p in self.snapshot_dir.rglob("*"))

    def snapshot_text(self, rel: str) -> str | None:
        """Content of a tracked file in the snapshot, or None if the snapshot has no such file."""
        path = self.snapshot_dir / rel
        return read_text(path) if path.is_file() else None


def bib_snapshot_name(bib: Path) -> str:
    """Path of the bibliography inside the snapshot (fixed folder, it may live outside the project)."""
    return f"{BIB_SNAPSHOT_DIR}/{bib.name}"


def _declared_bib(source: str) -> str | None:
    """First bibliography file declared by `\\bibliography` or `\\addbibresource`, with `.bib` suffix."""
    matches = [m for m in (_BIBLIOGRAPHY_RE.search(source), _ADDBIBRESOURCE_RE.search(source)) if m]
    if not matches:
        return None
    first = min(matches, key=lambda m: m.start()).group(1).split(",")[0].strip()
    return first if first.endswith(".bib") else f"{first}.bib"
