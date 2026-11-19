"""Test data and helpers shared by the test modules."""

from __future__ import annotations

from pathlib import Path

MAIN_TEX = r"""\documentclass{article}
\begin{document}
We shows that the scheme works \cite{parco}.
% \input{sections/old}
\input{sections/intro}
\bibliography{refs}
\end{document}
"""

INTRO_TEX = r"""\section{Introduction}
The model are stable.
%parco explain why the scheme is stable
The model are stable in practice.
"""

REFS_BIB = """@article{known2019,
  title={Known},
  year={2019}
}
"""


def write_files(root: Path, files: dict[str, str]) -> None:
    """Write `files` (relative path -> content) under `root`."""
    for rel, content in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
