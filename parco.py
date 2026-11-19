"""Run parco from the source tree, without installing it.

Usage: python path/to/parco.py <command> ...
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from parco.cli import main  # noqa: E402

if __name__ == "__main__":
    main(prog_name="parco")
