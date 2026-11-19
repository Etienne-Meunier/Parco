"""Click commands of parco: parse arguments, call core, print results."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import click

from parco import __version__
from parco.cli.textfmt import (
    format_close_report,
    format_context,
    format_state_line,
    format_status,
    format_threads,
)
from parco.core import review
from parco.core.close import close_version, ensure_closable
from parco.core.context import build_context, build_status, list_threads
from parco.core.errors import ParcoError
from parco.core.guide import guide_text
from parco.core.initialize import init_project
from parco.core.model import Mode
from parco.core.project import Project
from parco.core.requests import parse_comment_requests, parse_edit_requests, parse_reply_body
from parco.server.app import serve

JSON_OPTION = click.option("--json", "as_json", is_flag=True, help="Machine-readable JSON output.")
BATCH_OPTION = click.option("--batch", is_flag=True, help="Read a JSON array and add all items, or none.")


class ParcoGroup(click.Group):
    """Click group that turns ParcoError into a clean error message and exit code 1."""

    def invoke(self, ctx: click.Context) -> Any:
        """Invoke the command, converting core errors into click errors."""
        try:
            return super().invoke(ctx)
        except ParcoError as exc:
            raise click.ClickException(str(exc)) from exc


def _project() -> Project:
    return Project.discover(Path.cwd())


def _stdin() -> str:
    return sys.stdin.read()


def _echo_json(data: Any) -> None:
    click.echo(json.dumps(data, indent=2, ensure_ascii=False))


def _echo_ids(ids: list[str], as_json: bool) -> None:
    if as_json:
        _echo_json({"ids": ids})
    else:
        click.echo("Created " + ", ".join(ids))


@click.group(cls=ParcoGroup)
@click.version_option(version=__version__, prog_name="parco")
def cli() -> None:
    """Parco: AI-assisted reviewing of LaTeX documents with track changes."""


@cli.command()
@click.option("--main", "main_file", help="Main tex file, relative to the current folder.")
@click.option("--context", "context_paths", multiple=True, help="Context folder or file (repeatable).")
@click.option("--bib", help="Bibliography file (default: detected from \\bibliography).")
def init(main_file: str | None, context_paths: tuple[str, ...], bib: str | None) -> None:
    """Create the .parco folder in the current directory."""
    if main_file is None:
        main_file = click.prompt("Main tex file", default="main.tex")
    contexts = list(context_paths)
    if not contexts:
        answer = click.prompt("Context paths (comma separated, empty for none)", default="", show_default=False)
        contexts = [c.strip() for c in answer.split(",") if c.strip()]
    if bib is None:
        bib = click.prompt("Bibliography file (empty to detect it)", default="", show_default=False) or None
    project = init_project(Path.cwd(), main_file, contexts, bib)
    click.echo(f"Initialized {project.parco_dir} (main: {project.config.main}).")


@cli.command()
@JSON_OPTION
def status(as_json: bool) -> None:
    """Show version, phase, mode, edits and threads."""
    report = build_status(_project())
    if as_json:
        _echo_json(report.to_dict())
    else:
        click.echo(format_status(report))


@cli.command()
@click.option("--yes", is_flag=True, help="Do not ask for confirmation.")
def close(yes: bool) -> None:
    """End the version: reject pending edits, apply accepted ones, take a new snapshot."""
    project = _project()
    ensure_closable(project)
    if not yes:
        click.confirm("Close the version? Pending edits will be rejected", abort=True)
    click.echo(format_close_report(close_version(project)))


@cli.command()
@click.option("--port", default=8765, show_default=True, help="Port of the local server (0: any free port).")
@click.option("--no-browser", is_flag=True, help="Do not open the browser.")
def render(port: int, no_browser: bool) -> None:
    """Start the local review platform for the current version."""
    serve(_project(), port, open_browser=not no_browser)


@cli.command("open")
@click.option("--mode", required=True, type=click.Choice([m.value for m in Mode]), help="Reading mode.")
def open_cmd(mode: str) -> None:
    """Start a new version in the given mode (error if one is open)."""
    click.echo(format_state_line(review.open_version(_project(), Mode(mode))))


@cli.command()
def iterate() -> None:
    """Move the open version to the iterate phase."""
    click.echo(format_state_line(review.start_iterate(_project())))


@cli.command()
@JSON_OPTION
def context(as_json: bool) -> None:
    """Phase, mode, diff since snapshot, file list and context paths."""
    ctx = build_context(_project())
    if as_json:
        _echo_json(ctx.to_dict())
    else:
        click.echo(format_context(ctx), nl=False)


@cli.command()
@click.option("--open", "only_open", is_flag=True, help="Only open threads.")
@JSON_OPTION
def threads(only_open: bool, as_json: bool) -> None:
    """Threads with their full discussion."""
    views = list_threads(_project(), only_open)
    if as_json:
        _echo_json([v.to_dict() for v in views])
    else:
        click.echo(format_threads(views))


@cli.group()
def edit() -> None:
    """Edits proposed by the AI."""


@edit.command("add")
@BATCH_OPTION
@click.option("--thread", help="Attach the edit(s) to this thread when `thread` is not in the JSON.")
@JSON_OPTION
def edit_add(batch: bool, thread: str | None, as_json: bool) -> None:
    """Propose replacement(s) on anchors, JSON on stdin."""
    requests = parse_edit_requests(_stdin(), batch, default_thread=thread)
    _echo_ids(review.add_ai_edits(_project(), requests), as_json)


@cli.group()
def comment() -> None:
    """Threads opened by the AI."""


@comment.command("add")
@BATCH_OPTION
@JSON_OPTION
def comment_add(batch: bool, as_json: bool) -> None:
    """Open thread(s) on anchors or global comments, JSON on stdin."""
    requests = parse_comment_requests(_stdin(), batch)
    _echo_ids(review.add_ai_threads(_project(), requests), as_json)


@cli.command()
@click.argument("thread_id")
def reply(thread_id: str) -> None:
    """Answer in an existing open thread, JSON {"body": ...} on stdin."""
    review.ai_reply(_project(), thread_id, parse_reply_body(_stdin()))
    click.echo(f"Replied in {thread_id}.")


@cli.command()
def guide() -> None:
    """Usage documentation for the LLM."""
    click.echo(guide_text(), nl=False)


@cli.command()
@click.argument("edit_ids", nargs=-1, required=True)
def accept(edit_ids: tuple[str, ...]) -> None:
    """Accept edits (user side; applied to the sources at close)."""
    review.accept_edits(_project(), list(edit_ids))
    click.echo("Accepted " + ", ".join(edit_ids))


@cli.command()
@click.argument("edit_ids", nargs=-1, required=True)
def reject(edit_ids: tuple[str, ...]) -> None:
    """Reject edits (user side)."""
    review.reject_edits(_project(), list(edit_ids))
    click.echo("Rejected " + ", ".join(edit_ids))


def main(prog_name: str | None = None) -> None:
    """Console script entry point."""
    cli(prog_name=prog_name)
