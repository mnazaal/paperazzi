"""CLI tests for `pzi inbox <file>` command."""

from __future__ import annotations

from argparse import Namespace
from io import StringIO
from pathlib import Path
from typing import Any

from pzi.commands.inbox import run_inbox_command

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _inbox_with_lines(tmp_path: Path, n: int = 1) -> Path:
    """Create an inbox file with `n` processable URL lines."""
    inbox = tmp_path / "inbox.txt"
    inbox.write_text("".join(f"https://arxiv.org/abs/{i}\n" for i in range(n)))
    return inbox


def _args(inbox: Path, *, dry_run: bool = False, tags: str | None = None,
          delay: float = 0.0) -> Namespace:
    return Namespace(file=str(inbox), dry_run=dry_run, tags=tags, delay=delay)


def _drain_ok(
    *,
    added: int = 2,
    exists: int = 0,
    failed: int = 0,
    inbox_file: str = "/tmp/inbox.txt",
    dry_run: bool = False,
) -> dict[str, Any]:
    items = []
    for i in range(added):
        items.append({"value": f"https://arxiv.org/abs/added{i}",
                      "status": "added", "citekey": f"smith202{i}", "errors": []})
    for i in range(exists):
        items.append({"value": f"https://arxiv.org/abs/exists{i}",
                      "status": "exists", "citekey": f"jones202{i}", "errors": []})
    for i in range(failed):
        items.append({"value": f"https://arxiv.org/abs/fail{i}",
                      "status": "failed", "citekey": None,
                      "errors": ["capture failed"]})
    return {
        "status": "ok",
        "inbox_file": inbox_file,
        "dry_run": dry_run,
        "total": added + exists + failed,
        "counts": {"added": added, "exists": exists, "failed": failed},
        "items": items,
        "errors": [],
    }


def _drain_error(message: str = "cannot read inbox file") -> dict[str, Any]:
    return {
        "status": "error",
        "inbox_file": "/tmp/inbox.txt",
        "dry_run": False,
        "total": 0,
        "counts": {"added": 0, "exists": 0, "failed": 0},
        "items": [],
        "errors": [message],
    }


# ---------------------------------------------------------------------------
# Fast-fail: no backend touched before checking the file
# ---------------------------------------------------------------------------


def test_inbox_missing_file_exits_1_without_calling_drain(tmp_path: Path) -> None:
    calls: list[dict] = []

    def fake_drain(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return _drain_ok()

    args = _args(tmp_path / "does_not_exist.txt")
    stderr = StringIO()
    code = run_inbox_command(
        args,
        home_dir=str(tmp_path),
        config_path=str(tmp_path / "config.toml"),
        stdout=StringIO(),
        stderr=stderr,
        drain_inbox_fn=fake_drain,
    )
    assert code == 1
    assert "not found" in stderr.getvalue()
    assert calls == []  # drain (and thus any backend) never invoked


def test_inbox_empty_file_exits_0_without_calling_drain(tmp_path: Path) -> None:
    calls: list[dict] = []

    def fake_drain(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return _drain_ok()

    inbox = tmp_path / "inbox.txt"
    inbox.write_text("# only a comment\n\n")
    stdout = StringIO()
    code = run_inbox_command(
        _args(inbox),
        home_dir=str(tmp_path),
        config_path=str(tmp_path / "config.toml"),
        stdout=stdout,
        stderr=StringIO(),
        drain_inbox_fn=fake_drain,
    )
    assert code == 0
    assert "empty" in stdout.getvalue()
    assert calls == []  # nothing to process — drain never invoked


# ---------------------------------------------------------------------------
# Drain
# ---------------------------------------------------------------------------


def test_inbox_drain_calls_service_with_correct_kwargs(tmp_path: Path) -> None:
    calls: list[dict] = []

    def fake_drain(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return _drain_ok()

    inbox = _inbox_with_lines(tmp_path)
    args = _args(inbox, delay=1.0)
    exit_code = run_inbox_command(
        args,
        home_dir=str(tmp_path),
        config_path=str(tmp_path / "config.toml"),
        stdout=StringIO(),
        stderr=StringIO(),
        drain_inbox_fn=fake_drain,
    )
    assert exit_code == 0
    assert len(calls) == 1
    assert calls[0]["config_path"] == str(tmp_path / "config.toml")
    assert calls[0]["home_dir"] == str(tmp_path)
    assert calls[0]["inbox_path"] == str(inbox)
    assert calls[0]["dry_run"] is False
    assert calls[0]["delay"] == 1.0


def test_inbox_drain_dry_run_flag(tmp_path: Path) -> None:
    calls: list[dict] = []

    def fake_drain(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return _drain_ok(dry_run=True)

    run_inbox_command(
        _args(_inbox_with_lines(tmp_path), dry_run=True),
        home_dir=str(tmp_path),
        config_path=str(tmp_path / "config.toml"),
        stdout=StringIO(),
        stderr=StringIO(),
        drain_inbox_fn=fake_drain,
    )
    assert calls[0]["dry_run"] is True


def test_inbox_drain_with_tags(tmp_path: Path) -> None:
    calls: list[dict] = []

    def fake_drain(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return _drain_ok()

    run_inbox_command(
        _args(_inbox_with_lines(tmp_path), tags="ml,systems"),
        home_dir=str(tmp_path),
        config_path=str(tmp_path / "config.toml"),
        stdout=StringIO(),
        stderr=StringIO(),
        drain_inbox_fn=fake_drain,
    )
    assert set(calls[0]["extra_tags"]) == {"ml", "systems"}


def test_inbox_drain_exit_0_when_no_failures(tmp_path: Path) -> None:
    def fake_drain(**kwargs: Any) -> dict[str, Any]:
        return _drain_ok(added=2, failed=0)

    code = run_inbox_command(
        _args(_inbox_with_lines(tmp_path)),
        home_dir=str(tmp_path),
        config_path=str(tmp_path / "config.toml"),
        stdout=StringIO(),
        stderr=StringIO(),
        drain_inbox_fn=fake_drain,
    )
    assert code == 0


def test_inbox_drain_exit_1_when_failures(tmp_path: Path) -> None:
    def fake_drain(**kwargs: Any) -> dict[str, Any]:
        return _drain_ok(added=1, failed=1)

    code = run_inbox_command(
        _args(_inbox_with_lines(tmp_path)),
        home_dir=str(tmp_path),
        config_path=str(tmp_path / "config.toml"),
        stdout=StringIO(),
        stderr=StringIO(),
        drain_inbox_fn=fake_drain,
    )
    assert code == 1


def test_inbox_drain_service_error_exit_1(tmp_path: Path) -> None:
    def fake_drain(**kwargs: Any) -> dict[str, Any]:
        return _drain_error("cannot read inbox file: boom")

    stderr = StringIO()
    code = run_inbox_command(
        _args(_inbox_with_lines(tmp_path)),
        home_dir=str(tmp_path),
        config_path=str(tmp_path / "config.toml"),
        stdout=StringIO(),
        stderr=stderr,
        drain_inbox_fn=fake_drain,
    )
    assert code == 1
    assert "cannot read inbox file" in stderr.getvalue()


def test_inbox_drain_renders_summary_to_stdout(tmp_path: Path) -> None:
    def fake_drain(**kwargs: Any) -> dict[str, Any]:
        return _drain_ok(added=2, exists=1, failed=0)

    stdout = StringIO()
    run_inbox_command(
        _args(_inbox_with_lines(tmp_path)),
        home_dir=str(tmp_path),
        config_path=str(tmp_path / "config.toml"),
        stdout=stdout,
        stderr=StringIO(),
        drain_inbox_fn=fake_drain,
    )
    out = stdout.getvalue()
    assert "done:" in out
    assert "2 added" in out
    assert "1 already present" in out


def test_inbox_drain_renders_progress_to_stderr(tmp_path: Path) -> None:
    def fake_drain(**kwargs: Any) -> dict[str, Any]:
        return _drain_ok(added=1, failed=1)

    stderr = StringIO()
    run_inbox_command(
        _args(_inbox_with_lines(tmp_path)),
        home_dir=str(tmp_path),
        config_path=str(tmp_path / "config.toml"),
        stdout=StringIO(),
        stderr=stderr,
        drain_inbox_fn=fake_drain,
    )
    err = stderr.getvalue()
    assert "[1/2]" in err
    assert "[2/2]" in err
