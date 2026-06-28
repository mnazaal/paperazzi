"""Unit tests for pzi.inbox_service."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pzi.inbox_service import (
    drain_inbox,
    parse_inbox_line,
)

# ---------------------------------------------------------------------------
# Add function fakes
# ---------------------------------------------------------------------------


def _ok_add(action: str = "insert", citekey: str = "smith2024") -> Any:
    def fake(**kwargs: Any) -> dict[str, Any]:
        return {
            "status": "ok",
            "action": action,
            "citekey": citekey,
            "bib_name": "main",
            "warnings": [],
            "errors": [],
        }
    return fake


# ---------------------------------------------------------------------------
# parse_inbox_line
# ---------------------------------------------------------------------------


def test_parse_inbox_line_plain_url() -> None:
    result = parse_inbox_line("https://arxiv.org/abs/2301.07041")
    assert result is not None
    assert result.value == "https://arxiv.org/abs/2301.07041"
    assert result.tags == []
    assert result.target is None


def test_parse_inbox_line_plain_doi() -> None:
    result = parse_inbox_line("10.1145/1327452.1327492")
    assert result is not None
    assert result.value == "10.1145/1327452.1327492"
    assert result.tags == []
    assert result.target is None


def test_parse_inbox_line_with_inline_tags() -> None:
    result = parse_inbox_line("https://arxiv.org/abs/2301.07041 #ml #systems")
    assert result is not None
    assert result.value == "https://arxiv.org/abs/2301.07041"
    assert result.tags == ["ml", "systems"]
    assert result.target is None


def test_parse_inbox_line_with_target() -> None:
    result = parse_inbox_line("10.1145/xxx @thesis")
    assert result is not None
    assert result.value == "10.1145/xxx"
    assert result.tags == []
    assert result.target == "thesis"


def test_parse_inbox_line_combined() -> None:
    result = parse_inbox_line("https://arxiv.org/abs/2301.07041 #ml @phd-bib")
    assert result is not None
    assert result.value == "https://arxiv.org/abs/2301.07041"
    assert result.tags == ["ml"]
    assert result.target == "phd-bib"


def test_parse_inbox_line_first_target_wins() -> None:
    result = parse_inbox_line("https://arxiv.org/abs/x @first @second")
    assert result is not None
    assert result.target == "first"


def test_parse_inbox_line_comment_returns_none() -> None:
    assert parse_inbox_line("# a comment") is None


def test_parse_inbox_line_comment_with_leading_whitespace_returns_none() -> None:
    assert parse_inbox_line("   # indented comment") is None


def test_parse_inbox_line_blank_returns_none() -> None:
    assert parse_inbox_line("   ") is None
    assert parse_inbox_line("") is None


def test_parse_inbox_line_url_fragment_not_confused_as_tag() -> None:
    # The #section is inside the first whitespace token — not a separate #tag
    result = parse_inbox_line("https://host/path#section")
    assert result is not None
    assert result.value == "https://host/path#section"
    assert result.tags == []


def test_parse_inbox_line_url_fragment_then_tag() -> None:
    result = parse_inbox_line("https://host/path#section #ml")
    assert result is not None
    assert result.value == "https://host/path#section"
    assert result.tags == ["ml"]


def test_parse_inbox_line_bare_hash_ignored() -> None:
    # Bare '#' token (length == 1) must not be treated as a tag
    result = parse_inbox_line("https://arxiv.org/abs/x #")
    assert result is not None
    assert result.tags == []


def test_parse_inbox_line_bare_at_ignored() -> None:
    result = parse_inbox_line("https://arxiv.org/abs/x @")
    assert result is not None
    assert result.target is None


# ---------------------------------------------------------------------------
# _write_inbox_atomically
# ---------------------------------------------------------------------------


def test_write_inbox_atomically_replaces_content(tmp_path: Path) -> None:
    from pzi.inbox_service import _write_inbox_atomically
    inbox = tmp_path / "inbox.txt"
    inbox.write_text("old content\n")
    _write_inbox_atomically(inbox, ["line1", "line2"])
    assert inbox.read_text() == "line1\nline2\n"


def test_write_inbox_atomically_no_tmp_left_behind(tmp_path: Path) -> None:
    from pzi.inbox_service import _write_inbox_atomically
    inbox = tmp_path / "inbox.txt"
    _write_inbox_atomically(inbox, ["a", "b"])
    tmp_files = list(tmp_path.glob("*.tmp"))
    assert tmp_files == []


def test_write_inbox_atomically_empty_list_writes_empty_file(tmp_path: Path) -> None:
    from pzi.inbox_service import _write_inbox_atomically
    inbox = tmp_path / "inbox.txt"
    inbox.write_text("had content\n")
    _write_inbox_atomically(inbox, [])
    assert inbox.read_text() == ""


# ---------------------------------------------------------------------------
# drain_inbox — empty / missing inbox
# ---------------------------------------------------------------------------

_CFG = "/unused/config.toml"  # never read: add_fn is always faked here
_HOME = "/home/tester"


def test_drain_inbox_empty_file(tmp_path: Path) -> None:
    inbox = tmp_path / "inbox.txt"
    inbox.write_text("")
    result = drain_inbox(config_path=_CFG, home_dir=_HOME,
                         inbox_path=str(inbox), delay=0, add_fn=_ok_add())
    assert result["status"] == "ok"
    assert result["total"] == 0
    assert result["counts"] == {"added": 0, "exists": 0, "failed": 0}


def test_drain_inbox_file_not_found(tmp_path: Path) -> None:
    inbox = tmp_path / "does_not_exist.txt"
    result = drain_inbox(config_path=_CFG, home_dir=_HOME,
                         inbox_path=str(inbox), delay=0, add_fn=_ok_add())
    assert result["status"] == "ok"
    assert result["total"] == 0


# ---------------------------------------------------------------------------
# drain_inbox — processing
# ---------------------------------------------------------------------------


def test_drain_inbox_all_succeed(tmp_path: Path) -> None:
    inbox = tmp_path / "inbox.txt"
    inbox.write_text(
        "https://arxiv.org/abs/2301.07041\n"
        "https://arxiv.org/abs/2305.10601\n"
    )
    result = drain_inbox(config_path=_CFG, home_dir=_HOME,
                         inbox_path=str(inbox), delay=0, add_fn=_ok_add())
    assert result["status"] == "ok"
    assert result["total"] == 2
    assert result["counts"]["added"] == 2
    assert result["counts"]["failed"] == 0
    # Inbox should now be empty (no remaining entries)
    assert inbox.read_text().strip() == ""


def test_drain_inbox_some_fail(tmp_path: Path) -> None:
    inbox = tmp_path / "inbox.txt"
    inbox.write_text(
        "https://arxiv.org/abs/ok\n"
        "https://arxiv.org/abs/fail\n"
        "https://arxiv.org/abs/ok2\n"
    )

    call_order: list[str] = []
    def mixed_add(**kwargs: Any) -> dict[str, Any]:
        value = kwargs["value"]
        call_order.append(value)
        if "fail" in value:
            return {"status": "error", "action": None, "citekey": None,
                    "bib_name": None, "warnings": [], "errors": ["net error"]}
        return {"status": "ok", "action": "insert", "citekey": "ck",
                "bib_name": "main", "warnings": [], "errors": []}

    result = drain_inbox(config_path=_CFG, home_dir=_HOME,
                         inbox_path=str(inbox), delay=0, add_fn=mixed_add)
    assert result["counts"]["added"] == 2
    assert result["counts"]["failed"] == 1
    # Failed line must remain; succeeded lines must be gone
    remaining = inbox.read_text()
    assert "fail" in remaining
    assert "ok" not in remaining


def test_drain_inbox_exists_action_counts_as_success(tmp_path: Path) -> None:
    inbox = tmp_path / "inbox.txt"
    inbox.write_text("https://arxiv.org/abs/existing\n")
    result = drain_inbox(config_path=_CFG, home_dir=_HOME,
                         inbox_path=str(inbox), delay=0,
                         add_fn=_ok_add(action="update"))
    assert result["counts"]["exists"] == 1
    assert result["counts"]["failed"] == 0
    # Entry already in library — should be removed from inbox
    assert inbox.read_text().strip() == ""


def test_drain_inbox_dry_run_does_not_rewrite(tmp_path: Path) -> None:
    inbox = tmp_path / "inbox.txt"
    original = "https://arxiv.org/abs/x\n"
    inbox.write_text(original)

    called_with_dry_run: list[bool] = []
    def tracking_add(**kwargs: Any) -> dict[str, Any]:
        called_with_dry_run.append(kwargs.get("dry_run", False))
        return {"status": "ok", "action": "insert", "citekey": "ck",
                "bib_name": "main", "warnings": [], "errors": []}

    result = drain_inbox(config_path=_CFG, home_dir=_HOME, inbox_path=str(inbox),
                         dry_run=True, delay=0, add_fn=tracking_add)
    assert result["dry_run"] is True
    assert all(called_with_dry_run)  # add called with dry_run=True
    # Inbox file must be unchanged
    assert inbox.read_text() == original


def test_drain_inbox_merges_extra_tags_with_inline_tags(tmp_path: Path) -> None:
    inbox = tmp_path / "inbox.txt"
    inbox.write_text("https://arxiv.org/abs/x #ml\n")

    received: list[dict[str, Any]] = []
    def capturing_add(**kwargs: Any) -> dict[str, Any]:
        received.append(kwargs)
        return {"status": "ok", "action": "insert", "citekey": "ck",
                "bib_name": "main", "warnings": [], "errors": []}

    drain_inbox(config_path=_CFG, home_dir=_HOME, inbox_path=str(inbox),
                extra_tags=["systems"], delay=0, add_fn=capturing_add)
    tags = received[0]["record_overrides"]["tags"]
    assert "ml" in tags
    assert "systems" in tags


def test_drain_inbox_inline_target_forwarded(tmp_path: Path) -> None:
    inbox = tmp_path / "inbox.txt"
    inbox.write_text("https://arxiv.org/abs/x @thesis\n")

    received: list[dict[str, Any]] = []
    def capturing_add(**kwargs: Any) -> dict[str, Any]:
        received.append(kwargs)
        return {"status": "ok", "action": "insert", "citekey": "ck",
                "bib_name": "main", "warnings": [], "errors": []}

    drain_inbox(config_path=_CFG, home_dir=_HOME, inbox_path=str(inbox),
                delay=0, add_fn=capturing_add)
    assert received[0]["bib_selector"] == "thesis"


def test_drain_inbox_preserves_comments_and_blanks(tmp_path: Path) -> None:
    inbox = tmp_path / "inbox.txt"
    inbox.write_text(
        "# a comment\n"
        "\n"
        "https://arxiv.org/abs/x\n"
    )
    drain_inbox(config_path=_CFG, home_dir=_HOME, inbox_path=str(inbox),
                delay=0, add_fn=_ok_add())
    remaining = inbox.read_text()
    assert "# a comment" in remaining
    assert remaining.count("\n") >= 2  # blank line preserved


def test_drain_inbox_read_error_returns_error(tmp_path: Path) -> None:
    # A path that exists but is a directory raises OSError (not FileNotFoundError).
    inbox = tmp_path / "inbox_dir"
    inbox.mkdir()
    result = drain_inbox(config_path=_CFG, home_dir=_HOME,
                         inbox_path=str(inbox), delay=0, add_fn=_ok_add())
    assert result["status"] == "error"
    assert any("cannot read inbox file" in e for e in result["errors"])


def test_drain_inbox_preserves_order_of_remaining_lines(tmp_path: Path) -> None:
    inbox = tmp_path / "inbox.txt"
    inbox.write_text(
        "# comment\n"
        "https://arxiv.org/abs/ok\n"
        "https://arxiv.org/abs/fail\n"
        "# another comment\n"
    )

    def mixed(**kwargs: Any) -> dict[str, Any]:
        if "fail" in kwargs["value"]:
            return {"status": "error", "action": None, "citekey": None,
                    "bib_name": None, "warnings": [], "errors": ["err"]}
        return {"status": "ok", "action": "insert", "citekey": "ck",
                "bib_name": "main", "warnings": [], "errors": []}

    drain_inbox(config_path=_CFG, home_dir=_HOME, inbox_path=str(inbox),
                delay=0, add_fn=mixed)
    lines = inbox.read_text().splitlines()
    # Comment before fail entry must appear before the fail entry
    comment_idx = next(i for i, line in enumerate(lines) if line.startswith("# comment"))
    fail_idx = next(i for i, line in enumerate(lines) if "fail" in line)
    assert comment_idx < fail_idx
