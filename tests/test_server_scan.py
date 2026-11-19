"""Safe token positions in LaTeX source, balance checks and the virtual (inlined) source."""

from __future__ import annotations

from pathlib import Path

from helpers import write_files
from parco.core.initialize import init_project
from parco.server.scan import SourceMap, is_balanced
from parco.server.virtual import build_virtual_source

DOC = r"""\documentclass{article}
\title{T}
\begin{document}
Text $a+b$ and \emph{em} here \label{x}.
% a comment
\begin{equation}
y = \frac{1}{2} \label{eq}
\end{equation}
\LaTeX\ is \verb|a%b| fine.
\end{document}
"""


def _map() -> SourceMap:
    return SourceMap(DOC)


def test_preamble_and_end_are_unsafe() -> None:
    smap = _map()
    assert not smap.is_safe(DOC.index("\\title"))
    assert smap.is_safe(smap.body_start)
    assert not smap.is_safe(len(DOC))


def test_math_comment_command_arguments_are_unsafe() -> None:
    smap = _map()
    assert not smap.is_safe(DOC.index("a+b") + 1)
    assert not smap.is_safe(DOC.index("a comment"))
    assert not smap.is_safe(DOC.index("% a comment") + len("% a comment"))
    assert not smap.is_safe(DOC.index("{x}") + 1)
    assert not smap.is_safe(DOC.index("\\frac") + 2)
    assert not smap.is_safe(DOC.index("a%b"))
    assert smap.is_safe(DOC.index("em}"))
    assert smap.is_safe(DOC.index("Text"))


def test_position_right_after_a_control_word_is_unsafe() -> None:
    smap = _map()
    assert not smap.is_safe(DOC.index("\\LaTeX") + len("\\LaTeX"))
    assert smap.is_safe(DOC.index("\\LaTeX"))


def test_safe_point_moves_before_the_construct() -> None:
    smap = _map()
    assert smap.safe_point(DOC.index("{eq}") + 1) == DOC.index("\\begin{equation}")
    assert smap.safe_point(DOC.index("\\title")) == smap.body_start


def test_region_is_safe() -> None:
    smap = _map()
    start = DOC.index("Text")
    assert smap.region_is_safe(start, DOC.index(" and"))
    assert smap.region_is_safe(start, DOC.index(" here"))
    assert not smap.region_is_safe(start, DOC.index("+b"))
    comment_line = DOC.index("% a comment")
    assert smap.region_is_safe(DOC.index("here"), comment_line + len("% a comment"))


def test_is_balanced() -> None:
    assert is_balanced(r"a \emph{b} $c$ \(d\)")
    assert not is_balanced(r"a \emph{b")
    assert not is_balanced(r"b} c")
    assert not is_balanced(r"$x")
    assert not is_balanced(r"\begin{itemize} a")
    assert not is_balanced(r"the \LaTeX")
    assert is_balanced(r"50\% of \{x\}")


def test_virtual_source_inlines_includes_once(tmp_path: Path) -> None:
    write_files(
        tmp_path,
        {
            "main.tex": "A\n\\input{s/one}\nB\n\\input{s/one}\n% \\input{s/two}\nC\n",
            "s/one.tex": "one\n\\input{s/two}\n",
            "s/two.tex": "two\n",
        },
    )
    virtual = build_virtual_source(init_project(tmp_path, "main.tex", []))
    assert virtual.text == "A\none\ntwo\n\n\nB\n\n% \\input{s/two}\nC\n"
    start = virtual.text.index("two")
    assert virtual.to_file(start) == ("s/two.tex", 0)
    assert virtual.to_virtual_range("s/two.tex", 0, 3) == (start, start + 3)
    assert virtual.to_virtual_range("main.tex", 0, 5) is None
    assert virtual.to_file(virtual.text.index("C")) == ("main.tex", len("A\n\\input{s/one}\nB\n\\input{s/one}\n% \\input{s/two}\n"))
    assert virtual.line_of(virtual.text.index("C")) == ("main.tex", 6)
