"""PDF storage must terminate, and a bad PDF must not be a traceback.

The hang here is the one bug in the review that consumed a core indefinitely:
every acquisition stage funnels through `write_pdf_bytes`, so `pzi add`,
`pzi pdf retry`, `pzi pdf attach` and an HTTP worker all wedged on a papers dir
containing a dangling symlink — a normal state for a library kept on another
volume.


`test_a_dangling_symlink_at_the_planned_name_does_not_hang` has no explicit
timeout marker (pytest-timeout is not a dependency); a regression makes it spin,
which shows up as a hung suite rather than a red test. That is still louder than
the silence it replaces.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from pzi.pdf_download import resolve_pdf_destination, write_pdf_bytes
from pzi.pdf_service import extract_pdf_metadata

PDF_BYTES = b"%PDF-1.4 fake body"


def test_a_dangling_symlink_at_the_planned_name_does_not_hang(tmp_path: Path) -> None:
    papers = tmp_path / "papers"
    papers.mkdir()
    (papers / "paper.pdf").symlink_to(tmp_path / "gone.pdf")

    stored = write_pdf_bytes(data=PDF_BYTES, papers_dir=str(papers), citekey="paper")

    assert Path(stored).read_bytes() == PDF_BYTES
    assert Path(stored).name != "paper.pdf"  # the occupied name was skipped
    # The dangling link is left exactly as it was.
    assert (papers / "paper.pdf").is_symlink()


def test_resolve_pdf_destination_treats_a_dangling_symlink_as_occupied(
    tmp_path: Path,
) -> None:
    target = tmp_path / "paper.pdf"
    target.symlink_to(tmp_path / "gone.pdf")

    assert resolve_pdf_destination(target, PDF_BYTES).name == "paper-1.pdf"


def test_identical_content_still_dedupes(tmp_path: Path) -> None:
    papers = tmp_path / "papers"
    papers.mkdir()

    first = write_pdf_bytes(data=PDF_BYTES, papers_dir=str(papers), citekey="paper")
    second = write_pdf_bytes(data=PDF_BYTES, papers_dir=str(papers), citekey="paper")

    assert first == second
    assert len(list(papers.glob("*.pdf"))) == 1


def test_different_content_gets_a_suffix(tmp_path: Path) -> None:
    papers = tmp_path / "papers"
    papers.mkdir()

    first = write_pdf_bytes(data=PDF_BYTES, papers_dir=str(papers), citekey="paper")
    second = write_pdf_bytes(
        data=PDF_BYTES + b" other", papers_dir=str(papers), citekey="paper"
    )

    assert first != second
    assert Path(second).name == "paper-1.pdf"


# ---------------------------------------------------------------------------
# Unreadable PDFs
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "content"),
    [
        ("empty.pdf", b""),
        ("truncated.pdf", b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog"),
        ("nonsense.pdf", b"%PDF-1.4\n" + os.urandom(64)),
    ],
)
def test_an_unreadable_pdf_yields_no_metadata_rather_than_a_traceback(
    tmp_path: Path, name: str, content: bytes
) -> None:
    path = tmp_path / name
    path.write_bytes(content)

    result = extract_pdf_metadata(str(path))

    assert result["doi"] is None
    assert result["title"] is None


def test_an_encrypted_pdf_yields_no_metadata(tmp_path: Path) -> None:
    pypdf = pytest.importorskip("pypdf")

    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=200, height=200)
    writer.encrypt("secret")
    path = tmp_path / "encrypted.pdf"
    with open(path, "wb") as handle:
        writer.write(handle)

    result = extract_pdf_metadata(str(path))

    assert result["doi"] is None
    assert result["title"] is None
