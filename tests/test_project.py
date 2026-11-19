"""Tracked files resolution, bibliography detection, config and init."""

from __future__ import annotations

from pathlib import Path

import pytest

from parco.core.config import Config, dump_config, read_config
from parco.core.errors import AlreadyInitializedError, ConfigError, FilesModifiedError, NotInitializedError
from parco.core.initialize import init_project
from parco.core.project import Project, find_parco_dir
from helpers import write_files


def test_tracked_files_follow_input_and_skip_comments(project: Project) -> None:
    assert project.tracked_files() == ["main.tex", "sections/intro.tex"]


def test_include_recursion_extensions_and_cycles(tmp_path: Path) -> None:
    write_files(
        tmp_path,
        {
            "main.tex": "\\include{a}\n\\input{b.tex}\n\\input{missing}\n\\input{../outside}\n",
            "a.tex": "\\input{sub/c}\n",
            "b.tex": "\\input{main} 50\\% done % \\input{d}\n",
            "sub/c.tex": "c\n",
            "d.tex": "d\n",
        },
    )
    (tmp_path.parent / "outside.tex").write_text("x")
    proj = init_project(tmp_path, "main.tex", [])
    assert proj.tracked_files() == ["main.tex", "a.tex", "sub/c.tex", "b.tex"]


def test_bib_detected_from_bibliography(project: Project) -> None:
    assert project.bib_path() == (project.root / "refs.bib").resolve()


def test_bib_from_addbibresource_and_config(tmp_path: Path) -> None:
    write_files(tmp_path, {"main.tex": "\\addbibresource[label=x]{lib/biblio.bib}\n\\bibliography{other}\n"})
    proj = init_project(tmp_path, "main.tex", [])
    assert proj.bib_path() == (tmp_path / "lib/biblio.bib").resolve()
    proj.config.bib = "custom.bib"
    assert proj.bib_path() == (tmp_path / "custom.bib").resolve()


def test_no_bib(tmp_path: Path) -> None:
    write_files(tmp_path, {"main.tex": "hello\n"})
    assert init_project(tmp_path, "main.tex", []).bib_path() is None


def test_config_round_trip(tmp_path: Path) -> None:
    config = Config(main='dir "x"/main.tex', context=["../code", "notes\\a"], bib="refs.bib")
    (tmp_path / "config.toml").write_text(dump_config(config))
    assert read_config(tmp_path) == config


def test_init_creates_folder_and_refuses_twice(project: Project) -> None:
    assert (project.parco_dir / "config.toml").is_file()
    assert (project.parco_dir / "state.json").is_file()
    assert project.snapshot_dir.is_dir() and not any(project.snapshot_dir.iterdir())
    with pytest.raises(AlreadyInitializedError):
        init_project(project.root, "main.tex", [])


def test_init_requires_main(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        init_project(tmp_path, "main.tex", [])
    assert not (tmp_path / ".parco").exists()


def test_discover_walks_up(project: Project) -> None:
    found = Project.discover(project.root / "sections")
    assert found.root == project.root.resolve()
    assert found.config.context == ["../code"]


def test_discover_without_parco(tmp_path: Path) -> None:
    with pytest.raises(NotInitializedError):
        find_parco_dir(tmp_path)


def test_modified_files_detects_change_addition_removal(project: Project) -> None:
    hashes = project.current_hashes()
    project.ensure_unchanged(hashes)
    (project.root / "sections/intro.tex").write_text("changed\n")
    assert project.modified_files(hashes) == ["sections/intro.tex"]
    (project.root / "main.tex").write_text("\\input{sections/old}\n")
    with pytest.raises(FilesModifiedError) as info:
        project.ensure_unchanged(hashes)
    assert info.value.files == ["main.tex", "sections/intro.tex", "sections/old.tex"]
    assert "parco close" in str(info.value)


def test_touch_does_not_count_as_modification(project: Project) -> None:
    hashes = project.current_hashes()
    (project.root / "main.tex").touch()
    assert project.modified_files(hashes) == []


def test_references_resolve_from_main_directory(tmp_path: Path) -> None:
    write_files(
        tmp_path,
        {
            "paper/main.tex": "\\input{sec/a}\n\\bibliography{refs}\n",
            "paper/sec/a.tex": "\\input{sec/b}\n",
            "paper/sec/b.tex": "b\n",
            "sec/a.tex": "decoy at the root\n",
        },
    )
    proj = init_project(tmp_path, "paper/main.tex", [])
    assert proj.tracked_files() == ["paper/main.tex", "paper/sec/a.tex", "paper/sec/b.tex"]
    assert proj.bib_path() == (tmp_path / "paper/refs.bib").resolve()


@pytest.mark.parametrize(
    ("files", "expected"),
    [
        ({"chap.1.tex": "x", "chap.1": "y"}, "chap.1.tex"),
        ({"chap.1": "y"}, "chap.1"),
        ({"intro.tex": "x"}, "intro.tex"),
    ],
)
def test_include_tries_tex_suffix_first(tmp_path: Path, files: dict[str, str], expected: str) -> None:
    target = "intro.tex" if "intro.tex" in files else "chap.1"
    write_files(tmp_path, {"main.tex": f"\\input{{{target}}}\n", **files})
    assert init_project(tmp_path, "main.tex", []).tracked_files() == ["main.tex", expected]


def test_bib_outside_project(tmp_path: Path) -> None:
    root = tmp_path / "project"
    write_files(root, {"main.tex": "\\bibliography{../shared/refs}\n"})
    proj = init_project(root, "main.tex", [])
    bib = proj.bib_path()
    assert bib == (tmp_path / "shared/refs.bib").resolve()
    assert proj.display(bib) == str(bib)


def test_comment_with_even_backslashes_hides_include(tmp_path: Path) -> None:
    write_files(tmp_path, {"main.tex": "a\\\\% \\input{x}\nb\\% \\input{y}\n", "x.tex": "", "y.tex": ""})
    assert init_project(tmp_path, "main.tex", []).tracked_files() == ["main.tex", "y.tex"]


def test_modified_files_with_missing_main(project: Project) -> None:
    hashes = project.current_hashes()
    (project.root / "main.tex").rename(project.root / "renamed.tex")
    assert project.modified_files(hashes) == ["main.tex", "sections/intro.tex"]
