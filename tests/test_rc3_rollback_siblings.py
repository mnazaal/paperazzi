"""Every PDF rollback spares a file a library entry points at.

The pre-download snapshot cannot tell a file this writer made from one another
writer reused and committed after the snapshot. These tests drive each public
entry point through the same schedule: the PDF this operation stored gets a
committed entry from another writer, then this operation's write fails.
"""

from __future__ import annotations

import base64
import dataclasses
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from pzi import add_service, capture_local_pdf, pdf_service, promote_service
from pzi.bib_repository import read_bib_file
from pzi.errors import PziError
from pzi.pdf import PdfSourceOutcome, pdf_is_stored, remove_new_pdf, stored_pdf_paths
from pzi.pdf_download import write_pdf_bytes

_PDF = b"%PDF-1.4 shared bytes"
_URL = "https://oa.example.org/paper.pdf"


class _Library:
    def __init__(self, tmp_path: Path) -> None:
        self.home = str(tmp_path)
        self.bib = tmp_path / "ml.bib"
        self.papers = tmp_path / "papers"
        self.papers.mkdir()
        self.config = tmp_path / "config.toml"
        self.config.write_text(
            f'[[bibs]]\nname = "ml"\npath = "{self.bib}"\n'
            f'papers_dir = "{self.papers}"\ndefault = true\n'
        )
        self.bib.write_text(
            "@article{target2024,\n  title = {Target Paper},\n  year = {2024},\n"
            f"  pzi-pdf-url = {{{_URL}}},\n}}\n"
        )
        self.bib_config: Any = {"name": "ml", "path": str(self.bib), "papers_dir": str(self.papers)}

    def stored_file(self) -> Path:
        """The one PDF this operation put in the papers directory."""
        found = list(self.papers.glob("*.pdf"))
        assert len(found) == 1, found
        return found[0]

    def commit_other_entry_on(self, pdf: Path) -> None:
        """Another writer reuses *pdf* for its own entry and commits."""
        with self.bib.open("a") as handle:
            handle.write(
                "\n@article{other2024,\n  title = {Other Paper},\n  year = {2024},\n"
                f"  file = {{{pdf}}},\n}}\n"
            )

    def keys(self) -> set[str]:
        return {record["citekey"] for record in read_bib_file(str(self.bib))["records"]}


def _fetch(_url: str) -> tuple[bytes, str | None]:
    return _PDF, "application/pdf"


def _common(lib: _Library) -> dict[str, Any]:
    return {"config_path": str(lib.config), "home_dir": lib.home, "bib_selector": None}


_OPERATIONS: dict[str, Callable[[_Library], Any]] = {
    "retry_pdf": lambda lib: pdf_service.retry_pdf(
        **_common(lib), citekey="target2024", fetch_binary=_fetch
    ),
    "retry_failed_pdfs": lambda lib: pdf_service.retry_failed_pdfs(**_common(lib)),
    "attach_pdf": lambda lib: pdf_service.attach_pdf(
        **_common(lib), citekey="target2024", source=_URL, fetch_binary=_fetch
    ),
    "attach_pdf_bytes": lambda lib: pdf_service.attach_pdf_bytes(
        **_common(lib), citekey="target2024", source_url=None,
        pdf_base64=base64.b64encode(_PDF).decode(),
    ),
    "attach_pdf_raw_bytes": lambda lib: pdf_service.attach_pdf_raw_bytes(
        **_common(lib), citekey="target2024", source_url=None, pdf_bytes=_PDF,
    ),
}


@pytest.mark.parametrize("operation", sorted(_OPERATIONS))
@pytest.mark.parametrize("failure", ["write_raises", "entry_disappeared"])
@pytest.mark.parametrize("shared", [True, False], ids=["shared", "unshared"])
def test_pdf_service_rollback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str, failure: str, shared: bool
) -> None:
    lib = _Library(tmp_path)
    seen: list[Path] = []

    def failing_update(*_args: object, **_kwargs: object) -> dict[str, bool]:
        pdf = lib.stored_file()
        seen.append(pdf)
        if shared:
            lib.commit_other_entry_on(pdf)
        if failure == "write_raises":
            raise PziError("synthetic refused write")
        return {"found": False}

    monkeypatch.setattr(pdf_service, "update_bib_entry", failing_update)
    if operation == "retry_failed_pdfs":  # this sweep has no fetcher parameter

        def store(**kwargs: Any) -> PdfSourceOutcome:
            path = write_pdf_bytes(
                data=_PDF, papers_dir=kwargs["papers_dir"], citekey=kwargs["citekey"]
            )
            return PdfSourceOutcome(
                local_pdf_path=path, warning=None, record=kwargs["record"], errors=[]
            )

        monkeypatch.setattr(pdf_service, "fetch_and_store_pdf_trying_sources", store)

    if failure == "write_raises":
        with pytest.raises(PziError, match="synthetic"):
            _OPERATIONS[operation](lib)
    else:
        _OPERATIONS[operation](lib)

    assert seen, "the schedule never reached the write"
    assert seen[0].exists() is shared
    if shared:
        assert seen[0].read_bytes() == _PDF
        assert "other2024" in lib.keys()


@pytest.mark.parametrize("shared", [True, False], ids=["shared", "unshared"])
def test_local_pdf_capture_rollback(tmp_path: Path, shared: bool) -> None:
    lib = _Library(tmp_path)
    source = tmp_path / "incoming.pdf"
    source.write_bytes(_PDF)
    seen: list[Path] = []

    def failing_add_record(**_kwargs: object) -> None:
        pdf = lib.stored_file()
        seen.append(pdf)
        if shared:
            lib.commit_other_entry_on(pdf)
        raise OSError("synthetic write failure")

    with pytest.raises(OSError, match="synthetic"):
        capture_local_pdf.add_local_pdf(
            bib=lib.bib_config,
            raw_value=str(source),
            record_overrides={"title": "Local Paper", "year": 2024, "authors": ["Smith, Jane"]},
            dry_run=False,
            server_url="http://127.0.0.1:9",
            fetch_search=lambda *_a, **_k: [],
            ensure_citekey=lambda record, *_a, **_k: {**record, "citekey": "local2024"},
            add_record=failing_add_record,  # type: ignore[arg-type]
            fetch_web=lambda *_a, **_k: [],  # type: ignore[arg-type]
        )

    assert seen and seen[0].exists() is shared
    assert source.read_bytes() == _PDF


def _batch_record(tag: str) -> dict[str, Any]:
    return {
        "citekey": f"{tag}2024", "title": f"Paper {tag}", "year": 2024,
        "authors": ["Smith, Jane"], "pdf_url": _URL,
    }


@pytest.mark.parametrize("shared", [True, False], ids=["shared", "unshared"])
def test_batch_commit_failure_rollback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, shared: bool
) -> None:
    """The session's lock is gone by the outer handler, so a writer can get in first."""
    lib = _Library(tmp_path)
    real_session = add_service.batch_write_session
    seen: list[Path] = []

    from contextlib import contextmanager

    @contextmanager
    def failing_commit(*args: Any, **kwargs: Any) -> Any:
        with real_session(*args, **{**kwargs, "write": False}) as session:
            yield session
        pdf = lib.stored_file()
        seen.append(pdf)
        if shared:
            lib.commit_other_entry_on(pdf)
        raise OSError("synthetic commit failure")

    monkeypatch.setattr(add_service, "batch_write_session", failing_commit)

    with pytest.raises(OSError, match="synthetic"):
        add_service.add_records_to_bib_batch(
            bib=lib.bib_config, records=[_batch_record("batch")], dry_run=False,
            fetch_binary=_fetch, pdf_filename_format="{{ title }}",
        )

    assert seen and seen[0].exists() is shared


def _promotable_library(lib: _Library) -> None:
    lib.bib.write_text(
        "@article{smith2024graph,\n  title = {Graph Parsers},\n  year = {2024},\n"
        "  author = {Smith, Jane},\n  eprint = {2401.12345},\n"
        "  archiveprefix = {arXiv},\n  journal = {arXiv preprint},\n}\n"
    )


def _published_search(_query: str, *, server_url: str) -> list[dict[str, Any]]:
    return [{
        "item_type": "journalArticle",
        "record": {
            "title": "Graph Parsers", "venue": "Journal of Parsing", "doi": "10.9/graph",
            "year": 2024, "authors": ["Smith, Jane"], "pdf_url": _URL,
        },
        "attachments": [],
    }]


def _run_promote(lib: _Library) -> Any:
    return promote_service.promote_bib(
        **_common(lib), keep_preprint=False, dry_run=False,
        fetch_search=_published_search, fetch_binary=_fetch,
    )


@pytest.mark.parametrize("shared", [True, False], ids=["shared", "unshared"])
@pytest.mark.parametrize("failure", ["apply_fails", "preprint_vanished", "session_refused"])
def test_promote_rollback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str, shared: bool
) -> None:
    lib = _Library(tmp_path)
    _promotable_library(lib)
    original = promote_service._handle_update_in_place
    seen: list[Path] = []

    def downloaded_then_failing(**kwargs: Any) -> Any:
        item, pending = original(**kwargs)
        assert pending is not None
        pdf = lib.stored_file()
        seen.append(pdf)
        if shared:
            lib.commit_other_entry_on(pdf)

        def failing_apply(_session: object) -> None:
            if failure == "preprint_vanished":
                raise promote_service._PreprintVanished("gone")
            raise RuntimeError("synthetic apply failure")

        return item, dataclasses.replace(pending, apply=failing_apply)

    monkeypatch.setattr(promote_service, "_handle_update_in_place", downloaded_then_failing)
    if failure == "session_refused":
        def refusing_session(*_args: object, **_kwargs: object) -> None:
            raise OSError("synthetic session refusal")

        monkeypatch.setattr(promote_service, "batch_write_session", refusing_session)

    _run_promote(lib)

    assert seen and seen[0].exists() is shared


# -- the helper's own refusals ----------------------------------------------


def test_rollback_keeps_the_file_when_the_library_dropped_a_block(tmp_path: Path) -> None:
    """A dropped block may be the entry that references this file."""
    lib = _Library(tmp_path)
    pdf = lib.papers / "new.pdf"
    pdf.write_bytes(_PDF)
    with lib.bib.open("a") as handle:
        handle.write("\n@article{target2024,\n  title = {Duplicate Key},\n}\n")

    pdf_service.rollback_new_pdfs([str(pdf)], set(), bib_path=str(lib.bib))

    assert pdf.exists()


def test_rollback_keeps_the_file_when_the_library_cannot_be_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lib = _Library(tmp_path)
    pdf = lib.papers / "new.pdf"
    pdf.write_bytes(_PDF)

    def unreadable(_path: str) -> None:
        raise OSError("synthetic unreadable library")

    monkeypatch.setattr(pdf_service, "read_bib_file_with_failures", unreadable)

    pdf_service.rollback_new_pdfs([str(pdf)], set(), bib_path=str(lib.bib))

    assert pdf.exists()


def test_stored_paths_match_across_a_symlinked_directory(tmp_path: Path) -> None:
    real = tmp_path / "onedrive"
    real.mkdir()
    link = tmp_path / "papers"
    link.symlink_to(real, target_is_directory=True)
    pdf = real / "x.pdf"
    pdf.write_bytes(_PDF)

    stored = stored_pdf_paths([{"local_pdf_path": str(link / "x.pdf")}, {"local_pdf_path": None}])

    assert pdf_is_stored(str(pdf), stored)
    assert pdf_is_stored(str(link / "x.pdf"), stored)
    assert not pdf_is_stored(str(real / "y.pdf"), stored)
    remove_new_pdf(str(pdf), set(), keep=[str(link / "x.pdf")])
    assert pdf.exists()
    remove_new_pdf(str(link / "x.pdf"), set(), keep=[str(pdf)])
    assert pdf.exists()
    remove_new_pdf(str(pdf), set(), keep=[])
    assert not pdf.exists()
