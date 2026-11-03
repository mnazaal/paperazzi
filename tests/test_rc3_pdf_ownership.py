"""PDF ownership: external attachments, shared-file rollback, no-hardlink storage."""

from __future__ import annotations

from pathlib import Path

import pytest

from pzi import reindex_service
from pzi.bib_repository import read_bib_file

# -- filename reindex leaves external attachments alone ---------------------


def _mixed_library(tmp_path: Path) -> tuple[str, str, Path, Path]:
    papers = tmp_path / "papers"
    elsewhere = tmp_path / "elsewhere"
    papers.mkdir()
    elsewhere.mkdir()
    owned = papers / "A textbraceleft Owned.pdf"
    external = elsewhere / "A textbraceleft External.pdf"
    owned.write_bytes(b"%PDF-owned\n")
    external.write_bytes(b"%PDF-external\n")
    bib = tmp_path / "t.bib"
    bib.write_text(
        f"@article{{owned2024,\n  title = {{Owned Title}},\n  file = {{{owned}}},\n}}\n\n"
        f"@article{{ext2024,\n  title = {{External Title}},\n  file = {{{external}}},\n}}\n"
    )
    return str(bib), str(papers), owned, external


@pytest.mark.parametrize("include_all", [False, True])
@pytest.mark.parametrize("dry_run", [True, False])
def test_rename_files_never_moves_an_attachment_outside_papers_dir(
    tmp_path: Path, include_all: bool, dry_run: bool
) -> None:
    bib, papers, owned, external = _mixed_library(tmp_path)

    result = reindex_service.rename_files_to_policy(
        bib_path=bib, papers_dir=papers, pdf_filename_format="{{ title }}",
        dry_run=dry_run, include_all=include_all,
    )

    assert {c["citekey"] for c in result["changed"]} == {"owned2024"}, result
    assert external.read_bytes() == b"%PDF-external\n"
    assert not (Path(papers) / "External Title.pdf").exists()
    stored = {
        r["citekey"] if "citekey" in r else e["citekey"]: r.get("local_pdf_path")
        for e, r in zip(*[read_bib_file(bib)[k] for k in ("entries", "records")])
    }
    assert Path(stored["ext2024"]) == external
    if not dry_run:
        assert (Path(papers) / "Owned Title.pdf").exists()
        assert not owned.exists()


# -- no-hardlink storage is collision-safe ----------------------------------


def _no_hardlinks(monkeypatch: pytest.MonkeyPatch, *, rendezvous: bool) -> None:
    """Make ``os.link`` fail like exFAT, and optionally line two writers up.

    The barriers hold both writers at the point where each has decided the
    name is free (before ``os.link`` returns, and again before ``os.replace``),
    which is the schedule that let the second silently overwrite the first.
    """
    import errno
    import os
    import threading

    link_gate = threading.Barrier(2, timeout=2)
    replace_gate = threading.Barrier(2, timeout=0.5)
    real_replace = os.replace

    def link(src, dst, *args, **kwargs):
        if rendezvous:
            try:
                link_gate.wait()
            except threading.BrokenBarrierError:
                pass
        raise OSError(errno.EPERM, "Operation not permitted")

    def replace(src, dst, *args, **kwargs):
        if rendezvous:
            try:
                replace_gate.wait()
            except threading.BrokenBarrierError:
                pass
        return real_replace(src, dst, *args, **kwargs)

    monkeypatch.setattr(os, "link", link)
    monkeypatch.setattr(os, "replace", replace)


def test_no_hardlink_fallback_still_stores_a_pdf(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pzi.pdf_download import write_pdf_bytes

    _no_hardlinks(monkeypatch, rendezvous=False)
    first = write_pdf_bytes(data=b"%PDF-one", papers_dir=str(tmp_path), citekey="k2024")
    again = write_pdf_bytes(data=b"%PDF-one", papers_dir=str(tmp_path), citekey="k2024")
    other = write_pdf_bytes(data=b"%PDF-two", papers_dir=str(tmp_path), citekey="k2024")

    assert Path(first).read_bytes() == b"%PDF-one"
    assert again == first
    assert other != first and Path(other).read_bytes() == b"%PDF-two"
    assert sorted(p.name for p in tmp_path.iterdir() if p.name.startswith(".")) == []


def test_two_no_hardlink_writers_never_share_one_name_for_different_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import threading

    from pzi.pdf_download import write_pdf_bytes

    _no_hardlinks(monkeypatch, rendezvous=True)
    payloads = [b"%PDF-writer-a", b"%PDF-writer-b"]
    paths: list[str | None] = [None, None]

    def run(i: int) -> None:
        paths[i] = write_pdf_bytes(
            data=payloads[i], papers_dir=str(tmp_path), citekey="same2024"
        )

    threads = [threading.Thread(target=run, args=(i,)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)

    assert paths[0] and paths[1] and paths[0] != paths[1], paths
    for path, data in zip(paths, payloads):
        assert Path(path).read_bytes() == data


def test_filename_rename_cannot_replace_a_file_that_appears_after_the_guard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Another writer stores a PDF at the target between guard and rename."""
    bib, papers, owned, _external = _mixed_library(tmp_path)
    target = Path(papers) / "Owned Title.pdf"
    real_guard = reindex_service._blocks_rename
    calls: list[str] = []

    def guard_then_race(destination: str, source: str) -> bool:
        blocked = real_guard(destination, source)
        calls.append(destination)
        # Plan-time call first, apply-time call second: plant after the second.
        if destination == str(target) and len(calls) == 2:
            target.write_bytes(b"%PDF-someone-elses")
        return blocked

    monkeypatch.setattr(reindex_service, "_blocks_rename", guard_then_race)
    result = reindex_service.rename_files_to_policy(
        bib_path=bib, papers_dir=papers, pdf_filename_format="{{ title }}",
        dry_run=False,
    )

    assert target.read_bytes() == b"%PDF-someone-elses"
    assert owned.read_bytes() == b"%PDF-owned\n"
    assert any("already exists" in e for e in result["errors"]), result


# -- rollback never deletes a PDF another writer committed ------------------

_PDF = b"%PDF-1.4 shared bytes"
_FORMAT = "{{ title }}"


def _fetch(url: str) -> tuple[bytes, str | None]:
    return _PDF, "application/pdf"


def _bib(tmp_path: Path) -> dict:
    papers = tmp_path / "papers"
    papers.mkdir(exist_ok=True)
    return {"name": "t", "path": str(tmp_path / "lib.bib"), "papers_dir": str(papers)}


def _paper(tag: str) -> dict:
    """Distinct papers (own DOI and citekey) whose PDFs plan one filename."""
    return {
        "citekey": f"{tag}2024", "title": "Shared Title", "doi": f"10.1000/{tag}",
        "pdf_url": f"http://example.com/{tag}.pdf",
    }


def _add(bib: dict, tag: str) -> None:
    from pzi.add_service import add_record_with_bib

    result = add_record_with_bib(
        bib=bib, record=_paper(tag), dry_run=False, fetch_binary=_fetch,  # type: ignore[arg-type]
        pdf_filename_format=_FORMAT,
    )
    assert result["status"] == "ok", result


def _stored_pdfs(bib: dict) -> dict[str, str]:
    return {
        r["citekey"]: r["local_pdf_path"]
        for r in read_bib_file(bib["path"])["records"]
        if r.get("local_pdf_path")
    }


def test_single_add_failure_keeps_a_pdf_a_concurrent_writer_committed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B snapshots, A downloads and commits the same file, B reuses it and fails."""
    from pzi import add_service

    bib = _bib(tmp_path)
    ran_a = []

    def fetch_b(url: str) -> tuple[bytes, str | None]:
        if not ran_a:  # B's download is the moment A gets in, after B's snapshot
            ran_a.append(1)
            _add(bib, "writera")
        return _fetch(url)

    from pzi import bib_repository

    def write_unless_b(path, plan, **kwargs):
        if plan["record"].get("citekey") == "writerb2024":
            raise OSError("synthetic BibTeX write failure")
        return bib_repository.execute_write_plan(path, plan, **kwargs)

    monkeypatch.setattr(add_service, "execute_write_plan", write_unless_b)

    with pytest.raises(OSError, match="synthetic"):
        add_service.add_record_with_bib(
            bib=bib, record=_paper("writerb"), dry_run=False,  # type: ignore[arg-type]
            fetch_binary=fetch_b, pdf_filename_format=_FORMAT,
        )

    stored = _stored_pdfs(bib)
    assert set(stored) == {"writera2024"}
    assert Path(stored["writera2024"]).read_bytes() == _PDF


def test_single_add_failure_still_removes_its_own_unshared_download(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The guard must not turn rollback into silent accumulation."""
    from pzi import add_service

    bib = _bib(tmp_path)

    def failing_write(*args, **kwargs):
        raise OSError("synthetic BibTeX write failure")

    monkeypatch.setattr(add_service, "execute_write_plan", failing_write)
    with pytest.raises(OSError):
        add_service.add_record_with_bib(
            bib=bib, record=_paper("solo"), dry_run=False,  # type: ignore[arg-type]
            fetch_binary=_fetch, pdf_filename_format=_FORMAT,
        )

    assert list(Path(bib["papers_dir"]).glob("*.pdf")) == []


def test_single_add_failure_keeps_a_pdf_a_separate_process_committed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """B downloads first; process A reuses B's file, commits; then B's write fails."""
    import os
    import subprocess
    import sys

    from pzi import add_service

    bib = _bib(tmp_path)
    src = str(Path(add_service.__file__).resolve().parent.parent)
    script = (
        "import json,sys\n"
        "from pzi.add_service import add_record_with_bib\n"
        "bib, rec = json.loads(sys.argv[1]), json.loads(sys.argv[2])\n"
        "r = add_record_with_bib(bib=bib, record=rec, dry_run=False,\n"
        "    fetch_binary=lambda u: (b'%PDF-1.4 shared bytes', 'application/pdf'),\n"
        "    pdf_filename_format='{{ title }}')\n"
        "sys.exit(0 if r['status'] == 'ok' else 3)\n"
    )

    def write_b(path, plan, **kwargs):
        # B's PDF is already on disk; the other process now snapshots, reuses it
        # and commits its entry before B's own write fails.
        import json

        env = {**os.environ, "PYTHONPATH": src, "PZI_SKIP_AUTO_START": "1"}
        proc = subprocess.run(
            [sys.executable, "-c", script, json.dumps(bib), json.dumps(_paper("writera"))],
            env=env, capture_output=True, text=True, timeout=120,
        )
        assert proc.returncode == 0, proc.stderr
        raise OSError("synthetic BibTeX write failure")

    monkeypatch.setattr(add_service, "execute_write_plan", write_b)

    with pytest.raises(OSError, match="synthetic"):
        add_service.add_record_with_bib(
            bib=bib, record=_paper("writerb"), dry_run=False,  # type: ignore[arg-type]
            fetch_binary=_fetch, pdf_filename_format=_FORMAT,
        )

    stored = _stored_pdfs(bib)
    assert set(stored) == {"writera2024"}
    assert Path(stored["writera2024"]).read_bytes() == _PDF


def test_batch_commit_failure_keeps_a_pdf_a_concurrent_writer_committed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Batch B snapshots, writer A commits the same file, B reuses it, B's commit fails."""
    from pzi import add_service
    from pzi.bib_repository import BatchWriteSession

    bib = _bib(tmp_path)
    real_session = add_service.batch_write_session
    opened = []

    def session_after_a(*args, **kwargs):
        if not opened:
            opened.append(1)
            _add(bib, "writera")
        return real_session(*args, **kwargs)

    def failing_commit(self):
        raise RuntimeError("synthetic commit failure")

    monkeypatch.setattr(add_service, "batch_write_session", session_after_a)
    monkeypatch.setattr(BatchWriteSession, "check_consistency", failing_commit)

    with pytest.raises(RuntimeError, match="synthetic commit"):
        add_service.add_records_to_bib_batch(
            bib=bib, records=[_paper("writerb")], dry_run=False,  # type: ignore[arg-type]
            fetch_binary=_fetch, pdf_filename_format=_FORMAT,
        )

    stored = _stored_pdfs(bib)
    assert set(stored) == {"writera2024"}
    assert Path(stored["writera2024"]).read_bytes() == _PDF
