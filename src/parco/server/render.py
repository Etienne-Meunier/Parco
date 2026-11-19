"""LaTeX -> HTML pipeline: display source with tokens, pandoc, then marker and figure post-processing."""

from __future__ import annotations

import hashlib
import html
import json
import re
import subprocess
import threading
from dataclasses import dataclass, field
from importlib.resources import files
from pathlib import Path
from urllib.parse import quote

from parco.core.model import Edit, Thread
from parco.core.project import Project
from parco.server.figures import graphics_paths, resolve_figure
from parco.server.markers import TOKEN_KINDS, TOKEN_RE, DisplaySource, build_display_source
from parco.server.tools import find_tool
from parco.server.virtual import VirtualSource, build_virtual_source

_TOKEN_ANY_CASE_RE = re.compile(TOKEN_RE.pattern, re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]*>")
# pandoc writes a PDF figure it finds on disk as <embed>; browsers get a PNG <img> instead.
_IMG_RE = re.compile(r'<(?:img|embed)\b([^>]*?)\bsrc="([^"]*)"([^>]*)>')
_EMPTY_CITATION_RE = re.compile(r'(<span class="citation" data-cites="([^"]*)">)(</span>)')
PANDOC_TIMEOUT_S = 30


class RenderError(Exception):
    """The document could not be converted to HTML."""


@dataclass
class RenderedDocument:
    """HTML of the document and what the API needs to answer about it."""

    html: str
    display: DisplaySource
    virtual: VirtualSource
    key: str = ""
    """Identifies the sources, edits and threads this document was rendered from."""
    figures: dict[str, Path] = field(default_factory=dict)
    """Resolved figure files, keyed by the path written in `\\includegraphics`."""


def run_pandoc(latex: str, cwd: Path) -> str:
    """Convert LaTeX to an HTML fragment (title block + body) with MathJax math."""
    pandoc = find_tool("pandoc")
    if pandoc is None:
        raise RenderError("pandoc not found: install it or set PARCO_PANDOC to its path.")
    template = files("parco.server").joinpath("templates/fragment.html")
    command = [pandoc, "-f", "latex", "-t", "html5", "--mathjax", "--wrap=none", f"--template={template}"]
    try:
        result = subprocess.run(command, input=latex, capture_output=True, text=True, cwd=cwd, timeout=PANDOC_TIMEOUT_S)
    except subprocess.TimeoutExpired as exc:
        raise RenderError(f"pandoc did not finish within {PANDOC_TIMEOUT_S} s.") from exc
    if result.returncode != 0:
        raise RenderError(f"pandoc failed: {result.stderr.strip()}")
    return result.stdout


def postprocess(fragment: str, display: DisplaySource, figure_dir: Path, search_dirs: list[str]) -> tuple[str, dict[str, Path]]:
    """Turn tokens into marker elements, show citation keys, and route figures through `/fig/`."""
    fragment = _TAG_RE.sub(lambda m: _TOKEN_ANY_CASE_RE.sub("", m.group()), fragment)
    fragment = TOKEN_RE.sub(lambda m: _marker_html(m.group(1), m.group(2), display), fragment)
    fragment = _EMPTY_CITATION_RE.sub(lambda m: f"{m.group(1)}[{html.escape(', '.join(m.group(2).split()))}]{m.group(3)}", fragment)
    figures: dict[str, Path] = {}

    def figure(match: re.Match[str]) -> str:
        source = html.unescape(match.group(2))
        path = resolve_figure(source, figure_dir, search_dirs)
        if path is None:
            return f'<span class="fig-missing">Missing figure: {html.escape(source)}</span>'
        figures[source] = path
        return f'<img{match.group(1)}src="/fig/{quote(source, safe="")}"{match.group(3)}>'

    return _IMG_RE.sub(figure, fragment), figures


def _marker_html(kind: str, item_id: str, display: DisplaySource) -> str:
    if kind == "fp":
        fill = display.fills.get(item_id)
        instruction = html.escape(fill.instruction if fill else "")
        return f'<span class="parco-fill" data-id="{item_id}"><span class="parco-fill-label">parco</span> {instruction}</span>'
    return f'<span class="pm" data-kind="{TOKEN_KINDS[kind]}" data-id="{item_id}"></span>'


def render_document(project: Project, edits: list[Edit], threads: list[Thread]) -> RenderedDocument:
    """Render the tracked document with `edits` and `threads` marked."""
    virtual = build_virtual_source(project)
    display = build_display_source(virtual, edits, threads)
    main_dir = project.path(project.main_file()).parent
    fragment = run_pandoc(display.text, main_dir)
    html_text, figures = postprocess(fragment, display, main_dir, graphics_paths(virtual.text))
    return RenderedDocument(html=html_text, display=display, virtual=virtual, figures=figures)


class Renderer:
    """Renders on demand and keeps the last result while sources, edits and threads are unchanged."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._key: str | None = None
        self._document: RenderedDocument | None = None

    def render(self, project: Project, edits: list[Edit], threads: list[Thread]) -> RenderedDocument:
        """The rendered document, from cache when nothing relevant changed."""
        key = _cache_key(project, edits, threads)
        with self._lock:
            if key != self._key or self._document is None:
                self._document = render_document(project, edits, threads)
                self._document.key = key
                self._key = key
            return self._document

    @property
    def last(self) -> RenderedDocument | None:
        """The last rendered document, if any."""
        return self._document


def _cache_key(project: Project, edits: list[Edit], threads: list[Thread]) -> str:
    sources = {rel: project.read(rel) for rel in project.tracked_files()}
    items = [
        [e.id, e.file, e.offset, e.anchor, e.replace, e.status] for e in edits
    ] + [[t.id, t.file, t.offset, t.anchor, t.edit, t.is_global] for t in threads]
    payload = json.dumps([sources, items], sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()
