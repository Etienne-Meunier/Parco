"""Access to the LLM usage guide shipped with the package."""

from importlib.resources import files


def guide_text() -> str:
    """Content of `guide.md`, printed by `parco guide`."""
    return files("parco").joinpath("guide.md").read_text(encoding="utf-8")
