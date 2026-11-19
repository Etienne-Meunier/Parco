"""Figures: resolve `\\includegraphics` paths like LaTeX and serve them (PDF converted to PNG)."""

from __future__ import annotations

import hashlib
import re
import subprocess
from pathlib import Path

from parco.server.tools import find_tool

GRAPHICS_EXTENSIONS = ("", ".pdf", ".png", ".jpg", ".jpeg")
CONTENT_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".svg": "image/svg+xml"}
_GRAPHICSPATH_RE = re.compile(r"\\graphicspath\s*\{((?:\s*\{[^{}]*\})*)\s*\}")
_GROUP_RE = re.compile(r"\{([^{}]*)\}")


class FigureError(Exception):
    """A figure could not be converted for the browser."""


def graphics_paths(latex: str) -> list[str]:
    """Folders declared by `\\graphicspath{{a/}{b/}}` in the source."""
    match = _GRAPHICSPATH_RE.search(latex)
    return _GROUP_RE.findall(match.group(1)) if match else []


def resolve_figure(name: str, base_dir: Path, search_dirs: list[str]) -> Path | None:
    """File of `\\includegraphics{name}`: tried in the main folder then each graphicspath folder,
    as written then with the usual extensions."""
    for folder in ["", *search_dirs]:
        for extension in GRAPHICS_EXTENSIONS:
            candidate = base_dir / folder / f"{name}{extension}"
            if candidate.is_file():
                return candidate.resolve()
    return None


class FigureCache:
    """Browser-ready versions of figures; PDFs are rendered once per modification time."""

    def __init__(self, cache_dir: Path) -> None:
        self.cache_dir = cache_dir

    def servable(self, path: Path) -> tuple[Path, str]:
        """(file to send, content type) for a resolved figure."""
        suffix = path.suffix.lower()
        if suffix == ".pdf":
            return self._pdf_to_png(path), "image/png"
        return path, CONTENT_TYPES.get(suffix, "application/octet-stream")

    def _pdf_to_png(self, pdf: Path) -> Path:
        key = hashlib.sha1(f"{pdf}:{pdf.stat().st_mtime_ns}".encode()).hexdigest()[:16]
        target = self.cache_dir / f"{pdf.stem}-{key}.png"
        if target.is_file():
            return target
        tool = find_tool("pdftoppm")
        if tool is None:
            raise FigureError("pdftoppm not found: cannot display PDF figures (install poppler).")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        prefix = target.with_suffix("")
        result = subprocess.run(
            [tool, "-png", "-r", "150", "-singlefile", str(pdf), str(prefix)], capture_output=True, text=True
        )
        if result.returncode != 0 or not target.is_file():
            raise FigureError(f"pdftoppm failed on {pdf}: {result.stderr.strip()}")
        return target
