"""Display source markers, pandoc token survival, post-processing, figures and selection mapping."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from helpers import write_files
from parco.core.errors import InputError
from parco.core.initialize import init_project
from parco.core.model import Edit, EditKind, EditStatus, Thread
from parco.server.figures import FigureCache, graphics_paths, resolve_figure
from parco.server.markers import TOKEN_RE, DisplaySource, build_display_source, token
from parco.server.render import RenderError, postprocess, render_document, run_pandoc
from parco.server.scan import is_structural
from parco.server.selection import anchor_for_selection
from parco.server.tools import find_tool
from parco.server.virtual import Segment, VirtualSource, build_virtual_source

needs_pandoc = pytest.mark.skipif(find_tool("pandoc") is None, reason="pandoc not installed")
needs_pdftoppm = pytest.mark.skipif(find_tool("pdftoppm") is None, reason="pdftoppm not installed")
DEMO_PDF = Path(__file__).resolve().parents[1] / "examples/demo/figures/bars.pdf"

SOURCE = r"""\documentclass{article}
\begin{document}
We shows that the scheme works \cite{parco}.

%parco explain the method
The value \(x^2\) is \emph{positive}.
\begin{equation}
y = x^2 \label{eq:y}
\end{equation}
Last paragraph here.
\end{document}
"""


def _virtual(text: str = SOURCE) -> VirtualSource:
    return VirtualSource(text=text, segments=[Segment(0, len(text), "main.tex", 0)], files={"main.tex": text})


def _edit(edit_id: str, anchor: str, replace: str, status: EditStatus = EditStatus.PENDING, text: str = SOURCE) -> Edit:
    return Edit(edit_id, "main.tex", text.index(anchor), anchor, replace, "", EditKind.REPLACE, status=status)


def _thread(thread_id: str, anchor: str, text: str = SOURCE) -> Thread:
    return Thread(thread_id, file="main.tex", offset=text.index(anchor), anchor=anchor)


def _shown(display: DisplaySource) -> dict[str, str]:
    return {item_id: placement.display for item_id, placement in display.placements.items()}


def _tokens(text: str) -> list[tuple[str, str]]:
    return TOKEN_RE.findall(text)


def test_pending_and_accepted_edits_are_wrapped() -> None:
    display = build_display_source(
        _virtual(), [_edit("e1", "We shows", "We show"), _edit("e2", "positive", "nonnegative", EditStatus.ACCEPTED)], []
    )
    assert "We " + token("ds", "e1") + "shows" + token("de", "e1") + token("is", "e1") + "show" + token("ie", "e1") + " that" in display.text
    assert token("as", "e2") + "nonnegative" + token("ae", "e2") in display.text
    assert "positive}" not in display.text.replace("nonnegative", "")
    assert _shown(display) == {"e1": "inline", "e2": "inline"}


def test_pending_edit_marks_only_changed_words() -> None:
    anchor = "We shows that the scheme works"
    display = build_display_source(_virtual(), [_edit("e1", anchor, "We show that the new scheme works")], [])
    assert "We " + token("ds", "e1") + "shows" + token("de", "e1") + token("is", "e1") + "show" + token("ie", "e1") in display.text
    assert "the " + token("is", "e1") + "new " + token("ie", "e1") + "scheme works" in display.text


def test_cite_change_replaces_only_the_command() -> None:
    display = build_display_source(_virtual(), [_edit("e1", "works \\cite{parco}.", "works \\cite{smith2020}.")], [])
    expected = token("ds", "e1") + "\\cite{parco}" + token("de", "e1") + token("is", "e1") + "\\cite{smith2020}" + token("ie", "e1")
    assert "works " + expected + "." in display.text


def test_pending_edit_with_unbalanced_hunk_is_wrapped_whole() -> None:
    text = "\\begin{document}\nSee \\emph{a \\textbf{b} c} here.\n\\end{document}\n"
    anchor = "\\emph{a \\textbf{b} c}"
    edit = _edit("e1", anchor, "\\emph{x \\textbf{b} c}", text=text)
    display = build_display_source(_virtual(text), [edit], [])
    assert token("ds", "e1") + anchor + token("de", "e1") + token("is", "e1") in display.text


def test_rejected_edit_is_a_point() -> None:
    display = build_display_source(_virtual(), [_edit("e1", "We shows", "We show", EditStatus.REJECTED)], [])
    assert token("pt", "e1") + "We shows" in display.text
    assert _shown(display) == {"e1": "point"}


def test_math_edit_falls_back_to_point_before_math() -> None:
    display = build_display_source(_virtual(), [_edit("e1", "y = x^2", "y = x^3"), _edit("e2", "x^2\\)", "x^3\\)")], [])
    assert token("pt", "e1") + "\\begin{equation}" in display.text
    assert token("pt", "e2") + "\\(x^2" in display.text
    assert _shown(display) == {"e1": "point", "e2": "point"}


def test_command_argument_and_unbalanced_edits_fall_back() -> None:
    edits = [_edit("e1", "parco}", "smith}"), _edit("e2", "\\emph{positive", "\\emph{negative")]
    display = build_display_source(_virtual(), edits, [])
    assert _shown(display) == {"e1": "point", "e2": "point"}
    assert token("pt", "e1") + "\\cite{parco}" in display.text


def test_fill_tag_placeholder_and_fill_edit() -> None:
    display = build_display_source(_virtual(), [], [])
    assert token("fp", "f1") + "%parco explain the method" in display.text
    assert display.fills["f1"].instruction == "explain the method" and display.fills["f1"].line == 5
    filled = build_display_source(_virtual(), [_edit("e1", "%parco explain the method", "The method is X.")], [])
    assert token("ds", "e1") + token("fp", "f1") + token("de", "e1") + token("is", "e1") + "The method is X." in filled.text
    assert filled.text.count(token("fp", "f1")) == 1
    assert "%parco" not in filled.text


def test_thread_inside_edit_snaps_to_edit_bounds() -> None:
    display = build_display_source(_virtual(), [_edit("e1", "We shows that", "We show that")], [_thread("t2", "shows that the scheme")])
    assert display.text.index(token("ts", "t2")) < display.text.index(token("ds", "e1"))
    assert display.text.index(token("te", "t2")) > display.text.index(token("ie", "e1"))
    assert _shown(display)["t2"] == "inline"


def test_thread_in_math_is_a_point() -> None:
    display = build_display_source(_virtual(), [], [_thread("t1", "y = x^2")])
    assert token("pt", "t1") + "\\begin{equation}" in display.text


def test_blocks_mark_paragraph_starts() -> None:
    display = build_display_source(_virtual(), [], [])
    starts = [SOURCE[b.start:b.start + 4] for b in display.blocks]
    assert "We s" in starts and "Last" in starts
    last = next(b for b in display.blocks if SOURCE.startswith("Last", b.start))
    assert SOURCE[last.start:last.paragraph_end] == "Last paragraph here."


def test_selection_exact_whitespace_and_fallback() -> None:
    virtual = _virtual()
    display = build_display_source(virtual, [], [])
    first = next(b.id for b in display.blocks if SOURCE.startswith("We", b.start))
    exact = anchor_for_selection(virtual, display, "the scheme", first, None)
    assert SOURCE[exact.offset:exact.offset + exact.length] == "the scheme"
    spaced = anchor_for_selection(virtual, display, "the\n  scheme   works", first, None)
    assert SOURCE[spaced.offset:spaced.offset + spaced.length] == "the scheme works"
    last = next(b for b in display.blocks if SOURCE.startswith("Last", b.start))
    fallback = anchor_for_selection(virtual, display, "rendered “quote” text", last.id, None)
    assert SOURCE[fallback.offset:fallback.offset + fallback.length] == "Last paragraph here."
    with pytest.raises(InputError):
        anchor_for_selection(virtual, display, "   ", first, None)


def test_selection_is_clipped_to_one_file(tmp_path: Path) -> None:
    write_files(tmp_path, {"main.tex": "\\begin{document}\nIntro text \\input{part} end.\n\\end{document}\n", "part.tex": "inner words"})
    virtual = build_virtual_source(init_project(tmp_path, "main.tex", []))
    display = build_display_source(virtual, [], [])
    found = anchor_for_selection(virtual, display, "not in the source", None, None)
    assert found.file == "main.tex"
    inner = anchor_for_selection(virtual, display, "inner words", None, None)
    assert (inner.file, inner.offset, inner.length) == ("part.tex", 0, len("inner words"))


@needs_pandoc
@pytest.mark.parametrize(
    "context",
    [
        "plain TOKEN text",
        "\\emph{in TOKEN emphasis}",
        "\\textbf{TOKEN}",
        "\\section{Title TOKEN}",
        "\\subsection*{TOKEN starred}",
        "text\\footnote{in a TOKEN footnote}",
        "\\begin{itemize}\n\\item first TOKEN\n\\item second\n\\end{itemize}",
        "\\begin{enumerate}\n\\item TOKEN\n\\end{enumerate}",
        "\\begin{figure}\\centering\\caption{A TOKEN caption}\\end{figure}",
        "\\begin{tabular}{ll} a TOKEN & b \\\\ \\end{tabular}",
        "\\textsc{small TOKEN}",
        "``quoted TOKEN'' --- dash",
        "\\begin{quote}TOKEN\\end{quote}",
        "\\href{http://x.org}{link TOKEN}",
        "glued\\_wordTOKEN2020",
    ],
)
def test_tokens_survive_pandoc(context: str) -> None:
    latex = "\\documentclass{article}\\begin{document}\n" + context.replace("TOKEN", token("ts", "t1") + "x" + token("te", "t1")) + "\n\\end{document}\n"
    html, _ = postprocess(run_pandoc(latex, Path.cwd()), build_display_source(_virtual(""), [], []), Path.cwd(), [])
    assert '<span class="pm" data-kind="thread-start" data-id="t1"></span>' in html
    assert '<span class="pm" data-kind="thread-end" data-id="t1"></span>' in html
    assert "zqpm" not in html.lower()


@needs_pandoc
def test_tokens_removed_from_attributes() -> None:
    latex = "\\documentclass{article}\\begin{document}\n\\section{Intro" + token("ts", "t1") + "duction}\n\\end{document}\n"
    html, _ = postprocess(run_pandoc(latex, Path.cwd()), build_display_source(_virtual(""), [], []), Path.cwd(), [])
    assert "zqpm" not in html.lower()
    assert re.search(r'<h1 id="introduction">Intro<span class="pm"', html)


@needs_pandoc
def test_render_document_end_to_end(tmp_path: Path) -> None:
    write_files(tmp_path, {"main.tex": SOURCE.replace("Last paragraph here.", "Last paragraph here.\n\\includegraphics{fig}\n\\includegraphics{nofig}")})
    shutil.copy(DEMO_PDF, tmp_path / "fig.pdf")
    project = init_project(tmp_path, "main.tex", [])
    text = project.read("main.tex")
    doc = render_document(project, [_edit("e1", "We shows", "We show", text=text)], [_thread("t2", "the scheme", text=text)])
    for kind, item in [("del-start", "e1"), ("ins-end", "e1"), ("thread-start", "t2"), ("block", "b0")]:
        assert f'data-kind="{kind}" data-id="{item}"' in doc.html
    assert '<span class="parco-fill" data-id="f1">' in doc.html and "explain the method" in doc.html
    assert '<span class="citation" data-cites="parco">[parco]</span>' in doc.html
    assert "\\(x^2\\)" in doc.html and "\\begin{equation}" in doc.html
    assert 'src="/fig/fig.pdf"' in doc.html and doc.figures["fig.pdf"] == (tmp_path / "fig.pdf").resolve()
    assert "Missing figure: nofig" in doc.html


def test_graphics_paths_and_resolution(tmp_path: Path) -> None:
    write_files(tmp_path, {"figs/a.png": "x", "b.jpg": "y", "figs/c": "z"})
    assert graphics_paths("\\graphicspath{{figs/}{other/}}") == ["figs/", "other/"]
    assert resolve_figure("a", tmp_path, ["figs/"]) == (tmp_path / "figs/a.png").resolve()
    assert resolve_figure("b", tmp_path, []) == (tmp_path / "b.jpg").resolve()
    assert resolve_figure("c", tmp_path, ["figs/"]) == (tmp_path / "figs/c").resolve()
    assert resolve_figure("missing", tmp_path, ["figs/"]) is None


@needs_pdftoppm
def test_pdf_figures_converted_and_cached(tmp_path: Path) -> None:
    cache = FigureCache(tmp_path / "cache")
    png, content_type = cache.servable(DEMO_PDF)
    assert content_type == "image/png" and png.read_bytes().startswith(b"\x89PNG")
    mtime = png.stat().st_mtime_ns
    assert cache.servable(DEMO_PDF)[0] == png and png.stat().st_mtime_ns == mtime


@pytest.mark.parametrize(
    "snippet",
    ["a & b", "line \\\\ next", "\\item two", "\\caption{X}", "\\label{x}", "\\section{S}", "\\begin{center}x\\end{center}"],
)
def test_structural_snippets(snippet: str) -> None:
    assert is_structural(snippet)


def test_structural_edits_fall_back_to_points() -> None:
    text = SOURCE.replace("Last paragraph here.", "\\begin{tabular}{ll}\na & b \\\\\n\\end{tabular}\n\\begin{itemize}\n\\item one\n\\item two\n\\end{itemize}")
    edits = [_edit("e1", "a & b", "a & c", text=text), _edit("e2", "\\item one\n\\item two", "\\item one\n\\item three", text=text)]
    assert _shown(build_display_source(_virtual(text), edits, [])) == {"e1": "point", "e2": "point"}
    assert not is_structural("50\\& more") and not is_structural("plain words")


def test_thread_starting_at_item_starts_after_it() -> None:
    text = SOURCE.replace("Last paragraph here.", "\\begin{itemize}\n\\item alpha beta\n\\item gamma\n\\end{itemize}")
    display = build_display_source(_virtual(text), [], [_thread("t1", "\\begin{itemize}\n\\item alpha", text=text)])
    assert "\\item " + token("ts", "t1") + "alpha" in re.sub(r"ZQPMbk\w+?QZ", "", display.text)


def test_unmappable_item_is_hidden_with_file_position(tmp_path: Path) -> None:
    write_files(tmp_path, {"main.tex": "\\begin{document}\nBefore \\input{part} after.\n\\end{document}\n", "part.tex": "inner"})
    virtual = build_virtual_source(init_project(tmp_path, "main.tex", []))
    source = virtual.files["main.tex"]
    thread = Thread("t1", file="main.tex", offset=source.index("Before"), anchor="Before \\input{part} after")
    placement = build_display_source(virtual, [], [thread]).placements["t1"]
    assert placement.display == "hidden" and placement.position == virtual.text.index("Before")


def test_blocks_follow_document_order_without_empty_start() -> None:
    display = build_display_source(_virtual(), [], [])
    assert SOURCE.startswith("We shows", display.blocks[0].start)
    assert [b.start for b in display.blocks] == sorted(b.start for b in display.blocks)
    assert [b.id for b in display.blocks] == [f"b{i}" for i in range(len(display.blocks))]


def test_selection_through_markup_citations_math_and_repeats() -> None:
    text = SOURCE.replace("Last paragraph here.", "The cat sat. The cat ran from the \\emph{big} dog, see \\cite{a,b} and $x$ here.")
    virtual = _virtual(text)
    display = build_display_source(virtual, [], [])
    block = next(b.id for b in display.blocks if text.startswith("The cat", b.start))

    def anchor(selected: str, occurrence: int | None = None) -> str:
        found = anchor_for_selection(virtual, display, selected, block, None, occurrence)
        return text[found.offset:found.offset + found.length]

    assert anchor("from the big dog") == "from the \\emph{big} dog"
    assert anchor("dog, see [a, b] and") == "dog, see \\cite{a,b} and"
    assert anchor("and  here") == "and $x$ here"
    assert anchor("and x here") == "and $x$ here"
    assert anchor("The cat", 1) == "The cat" and text.index("The cat ran") == virtual.text.index(anchor("The cat ran"))
    assert anchor("The cat").startswith("The cat sat. The cat ran")
    assert anchor("it\u2019s \u201cnot\u201d here") == anchor("The cat")


@needs_pandoc
@pytest.mark.parametrize(
    ("body", "anchor"),
    [
        ("\\begin{tabular}{ll}\na cell & b \\\\\nc & d\n\\end{tabular}", "a cell"),
        ("\\begin{itemize}\n\\item first thing\n\\item second\n\\end{itemize}", "first thing"),
        ("\\begin{figure}\n\\caption{The cap text}\n\\end{figure}", "cap text"),
        ("Text.\n\\begin{equation}\nx = 1 \\label{eq:a}\n\\end{equation}\nAfter.", "x = 1"),
    ],
)
def test_thread_markers_survive_structural_positions(body: str, anchor: str) -> None:
    text = "\\documentclass{article}\n\\begin{document}\n" + body + "\n\\end{document}\n"
    display = build_display_source(_virtual(text), [], [_thread("t1", anchor, text=text)])
    html, _ = postprocess(run_pandoc(display.text, Path.cwd()), display, Path.cwd(), [])
    kinds = ("point",) if _shown(display)["t1"] == "point" else ("thread-start", "thread-end")
    for kind in kinds:
        assert f'data-kind="{kind}" data-id="t1"' in html
    assert "zqpm" not in html.lower()


def test_pandoc_timeout_is_a_render_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def slow(*args: object, **kwargs: object) -> None:
        raise subprocess.TimeoutExpired(cmd="pandoc", timeout=30)

    monkeypatch.setattr("parco.server.render.find_tool", lambda name: "pandoc")
    monkeypatch.setattr("parco.server.render.subprocess.run", slow)
    with pytest.raises(RenderError, match="did not finish"):
        run_pandoc("x", Path.cwd())
