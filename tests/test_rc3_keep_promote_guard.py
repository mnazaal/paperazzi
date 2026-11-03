"""Keep-mode promote must not write onto a different paper that took the key."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from pzi import promote_service
from tests.test_rc3_rollback_siblings import (
    _common,
    _fetch,
    _Library,
    _promotable_library,
    _published_search,
)

PAPER_B = (
    "@article{smith2024graph,\n  title = {Unrelated Paper B},\n  year = {2024},\n"
    "  author = {Jones, Bob},\n  doi = {10.1000/bbb},\n  journal = {Nature},\n"
    "  note = {my notes on B},\n}\n"
)


@pytest.mark.parametrize("mark", [False, True])
def test_keep_mode_refuses_when_the_key_was_taken_during_the_search(
    tmp_path: Path, mark: bool
) -> None:
    lib = _Library(tmp_path)
    _promotable_library(lib)

    def search(query: str, *, server_url: str) -> list[dict[str, Any]]:
        lib.bib.write_text(PAPER_B)
        return _published_search(query, server_url=server_url)

    result = promote_service.promote_bib(
        **_common(lib), keep_preprint=True, mark_resolved=mark, dry_run=False,
        fetch_search=search, fetch_binary=_fetch,
    )
    assert lib.bib.read_text() == PAPER_B
    assert result["summary"]["created"] == 0
    assert result["summary"].get("marked_resolved", 0) == 0


def test_mark_resolved_refuses_a_paper_that_took_the_key_after_the_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lib = _Library(tmp_path)
    _promotable_library(lib)
    original = promote_service._tag_resolved

    def swap_then_tag(**kwargs: Any) -> Any:
        lib.bib.write_text(PAPER_B)
        return original(**kwargs)

    monkeypatch.setattr(promote_service, "_tag_resolved", swap_then_tag)
    result = promote_service.promote_bib(
        **_common(lib), keep_preprint=True, mark_resolved=True, dry_run=False,
        fetch_search=_published_search, fetch_binary=_fetch,
    )
    assert lib.bib.read_text() == PAPER_B
    assert result["summary"].get("marked_resolved", 0) == 0
    assert any("different paper" in e for e in result["errors"])
