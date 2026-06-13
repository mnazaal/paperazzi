import json
from argparse import Namespace
from io import StringIO
from pathlib import Path

from pzi.commands.search import run_search_command


def test_run_search_command_requires_at_least_one_filter(tmp_path: Path) -> None:
    stdout = StringIO()
    stderr = StringIO()
    args = Namespace(query=None, author=None, year=None, tag=None)

    exit_code = run_search_command(
        args,
        home_dir=str(tmp_path),
        config_path=str(tmp_path / "config.toml"),
        stdout=stdout,
        stderr=stderr,
        bib_selector=None,
    )

    assert exit_code == 1
    assert stdout.getvalue() == ""
    assert stderr.getvalue() == "error: at least one of --query, --author, --year, --tag is required\n"


def test_run_search_command_searches_each_target_and_renders_matches(tmp_path: Path) -> None:
    calls: list[dict] = []

    def fake_search_bib(**kwargs):
        calls.append(kwargs)
        return {
            "status": "ok",
            "matches": [
                {
                    "citekey": f"{kwargs['bib_selector']}2024",
                    "year": 2024,
                    "title": "Graph Paper",
                    "matched_fields": ["title"],
                }
            ],
            "errors": [],
        }

    stdout = StringIO()
    stderr = StringIO()
    args = Namespace(query="graph", author="Smith", year="2024", tag="ml")

    exit_code = run_search_command(
        args,
        home_dir=str(tmp_path),
        config_path=str(tmp_path / "config.toml"),
        stdout=stdout,
        stderr=stderr,
        bib_selector=["main", "ml"],
        search_bib_fn=fake_search_bib,
    )

    assert exit_code == 0
    assert calls == [
        {
            "config_path": str(tmp_path / "config.toml"),
            "home_dir": str(tmp_path),
            "bib_selector": "main",
            "query": "graph",
            "author": "Smith",
            "year": "2024",
            "tag": "ml",
        },
        {
            "config_path": str(tmp_path / "config.toml"),
            "home_dir": str(tmp_path),
            "bib_selector": "ml",
            "query": "graph",
            "author": "Smith",
            "year": "2024",
            "tag": "ml",
        },
    ]
    assert stdout.getvalue() == "main2024\t2024\tGraph Paper\t[title]\nml2024\t2024\tGraph Paper\t[title]\n"
    assert stderr.getvalue() == ""


def test_run_search_command_returns_failure_when_any_target_fails(tmp_path: Path) -> None:
    def fake_search_bib(**kwargs):
        if kwargs["bib_selector"] == "bad":
            return {"status": "error", "errors": ["missing bib"]}
        return {"status": "ok", "matches": [], "errors": []}

    stdout = StringIO()
    stderr = StringIO()
    args = Namespace(query="graph", author=None, year=None, tag=None)

    exit_code = run_search_command(
        args,
        home_dir=str(tmp_path),
        config_path=str(tmp_path / "config.toml"),
        stdout=stdout,
        stderr=stderr,
        bib_selector=["good", "bad"],
        search_bib_fn=fake_search_bib,
    )

    assert exit_code == 1
    assert stdout.getvalue() == "no matches\n"
    assert stderr.getvalue() == "search failed\n- missing bib\n"


def test_run_search_command_json_outputs_one_result_per_target(tmp_path: Path) -> None:
    def fake_search_bib(**kwargs):
        return {
            "status": "ok",
            "matches": [
                {
                    "citekey": f"{kwargs['bib_selector']}2024",
                    "year": 2024,
                    "title": "Graph Paper",
                    "matched_fields": ["title"],
                }
            ],
            "errors": [],
        }

    stdout = StringIO()
    stderr = StringIO()
    args = Namespace(query="graph", author=None, year=None, tag=None, json=True)

    exit_code = run_search_command(
        args,
        home_dir=str(tmp_path),
        config_path=str(tmp_path / "config.toml"),
        stdout=stdout,
        stderr=stderr,
        bib_selector=["main", "ml"],
        search_bib_fn=fake_search_bib,
    )

    assert exit_code == 0
    payload = json.loads(stdout.getvalue())
    assert [r["matches"][0]["citekey"] for r in payload] == ["main2024", "ml2024"]
    assert stderr.getvalue() == ""
