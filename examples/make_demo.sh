#!/usr/bin/env bash
# Build a fresh copy of the demo LaTeX project in examples/demo_run and simulate an AI
# reading through the parco CLI, so that `parco render` has something to show.
#
# Usage: examples/make_demo.sh            (then follow the printed instructions)
#        PYTHON=/path/to/python examples/make_demo.sh
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(dirname "$HERE")"
RUN="$HERE/demo_run"
PYTHON="${PYTHON:-python3}"

parco() { "$PYTHON" "$ROOT/parco.py" "$@"; }

rm -rf "$RUN"
cp -R "$HERE/demo" "$RUN"
cd "$RUN"

parco init --main main.tex --context "" --bib refs.bib
parco open --mode generative

parco edit add --batch <<'EOF_JSON'
[
  {"file": "main.tex", "anchor": "We study a implicit", "replace": "We study an implicit", "reason": "article before a vowel"},
  {"file": "main.tex", "anchor": "problems and shows that", "replace": "problems and show that", "reason": "subject-verb agreement"},
  {"file": "sections/introduction.tex", "anchor": "geophysical fluid dynamics \\cite{parco}", "replace": "geophysical fluid dynamics \\cite{vallis2017}", "reason": "reference for the claim"},
  {"file": "sections/introduction.tex", "anchor": "%parco explain why implicit schemes avoid the CFL restriction", "replace": "Implicit schemes couple all unknowns of a time step, so that information can travel across several cells per step: the CFL restriction then only bounds accuracy, not stability.", "reason": "fill the %parco tag"},
  {"file": "sections/introduction.tex", "anchor": "on a idealised basin", "replace": "on an idealised basin", "reason": "article before a vowel"},
  {"file": "sections/method.tex", "anchor": "\\qquad u(x, 0) = u_0(x),", "replace": "\\qquad u(x, 0) = u_0(x), \\quad x \\in \\mathbb{R},", "reason": "state the domain (edit inside math: shown as a point marker)"},
  {"file": "sections/method.tex", "anchor": "Figure~\\ref{fig:bars} confirms", "replace": "Figure~\\ref{fig:bars} confirm", "reason": "plural subject"}
]
EOF_JSON

parco comment add --batch <<'EOF_JSON'
[
  {"file": "sections/introduction.tex", "anchor": "which forces very small time steps when the velocity is large", "body": "Give an order of magnitude: how small is small for a typical ocean model?"},
  {"file": "sections/method.tex", "anchor": "so $|g| \\le 1$ whenever $\\theta \\ge 1/2$", "body": "This is the key result: consider stating it as a proposition with a short proof."},
  {"global": true, "body": "The method section does not say how the implicit system is solved (direct solver, iterative?). A reader will ask about the cost per step."}
]
EOF_JSON

parco accept e1

echo
echo "Demo ready in $RUN"
echo "Launch the platform with:"
echo "  cd \"$RUN\" && \"$PYTHON\" \"$ROOT/parco.py\" render"
