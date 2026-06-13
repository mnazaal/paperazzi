import json
from argparse import Namespace
from io import StringIO
from pathlib import Path

from pzi.commands.tags import run_tag_command


def test_run_tag_command_list_prints_one_tag_per_line(tmp_path: Path) -> None:
    calls: list[dict] = []

    def fake_list_tags(**kwargs):
        calls.append(kwargs)
        return {"status": "ok", "tags": ["ml", "vision"], "errors": []}

    stdout = StringIO()
    stderr = StringIO()
    args = Namespace(tag_command="list", citekey="smith2024graph")

    exit_code = run_tag_command(
        args,
        home_dir=str(tmp_path),
        config_path=str(tmp_path / "config.toml"),
        stdout=stdout,
        stderr=stderr,
        bib_selector="main",
        list_tags_fn=fake_list_tags,
    )

    assert exit_code == 0
    assert calls == [
        {
            "config_path": str(tmp_path / "config.toml"),
            "home_dir": str(tmp_path),
            "bib_selector": "main",
            "citekey": "smith2024graph",
        }
    ]
    assert stdout.getvalue() == "ml\nvision\n"
    assert stderr.getvalue() == ""


def test_run_tag_command_list_json_dumps_result(tmp_path: Path) -> None:
    def fake_list_tags(**kwargs):
        return {"status": "ok", "tags": ["ml", "vision"], "errors": []}

    stdout = StringIO()
    stderr = StringIO()
    args = Namespace(tag_command="list", citekey=None, json=True)

    exit_code = run_tag_command(
        args,
        home_dir=str(tmp_path),
        config_path=str(tmp_path / "config.toml"),
        stdout=stdout,
        stderr=stderr,
        bib_selector="main",
        list_tags_fn=fake_list_tags,
    )

    assert exit_code == 0
    assert json.loads(stdout.getvalue())["tags"] == ["ml", "vision"]
    assert stderr.getvalue() == ""


def test_run_tag_command_add_flattens_csv_tags_and_renders_success(tmp_path: Path) -> None:
    calls: list[dict] = []

    def fake_add_tags(**kwargs):
        calls.append(kwargs)
        return {
            "status": "ok",
            "bib_name": "main",
            "citekey": kwargs["citekey"],
            "tags": kwargs["tags"],
            "dry_run": kwargs["dry_run"],
            "message": "would added tags",
            "errors": [],
        }

    stdout = StringIO()
    stderr = StringIO()
    args = Namespace(
        tag_command="add",
        citekey="smith2024graph",
        tags=["ml, vision", "rl"],
        dry_run=True,
    )

    exit_code = run_tag_command(
        args,
        home_dir=str(tmp_path),
        config_path=str(tmp_path / "config.toml"),
        stdout=stdout,
        stderr=stderr,
        bib_selector=None,
        add_tags_fn=fake_add_tags,
    )

    assert exit_code == 0
    assert calls == [
        {
            "config_path": str(tmp_path / "config.toml"),
            "home_dir": str(tmp_path),
            "bib_selector": None,
            "citekey": "smith2024graph",
            "tags": ["ml", "vision", "rl"],
            "dry_run": True,
        }
    ]
    assert stdout.getvalue() == "DRY RUN: would added tags for smith2024graph: ml, vision, rl\n"
    assert stderr.getvalue() == ""


def test_run_tag_command_remove_renders_service_errors(tmp_path: Path) -> None:
    def fake_remove_tags(**kwargs):
        return {"status": "error", "message": "could not tag", "errors": ["missing citekey"]}

    stdout = StringIO()
    stderr = StringIO()
    args = Namespace(
        tag_command="remove",
        citekey="missing2024",
        tags=["ml"],
        dry_run=False,
    )

    exit_code = run_tag_command(
        args,
        home_dir=str(tmp_path),
        config_path=str(tmp_path / "config.toml"),
        stdout=stdout,
        stderr=stderr,
        bib_selector="main",
        remove_tags_fn=fake_remove_tags,
    )

    assert exit_code == 1
    assert stdout.getvalue() == ""
    assert stderr.getvalue() == "could not tag\n- missing citekey\n"
