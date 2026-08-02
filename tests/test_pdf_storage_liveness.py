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
    # `pypdf` is a hard dependency (see `pyproject.toml`), so an
    # `importorskip` here would hide a broken install as a skip.
    import pypdf

    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=200, height=200)
    writer.encrypt("secret")
    path = tmp_path / "encrypted.pdf"
    with open(path, "wb") as handle:
        writer.write(handle)

    result = extract_pdf_metadata(str(path))

    assert result["doi"] is None
    assert result["title"] is None


def _pdf_with_text(lines: list[str]) -> bytes:
    """A minimal but real PDF whose pages carry extractable text.

    Built by hand because `pypdf`'s writer cannot lay out text, and the point of
    the test below is the *extraction* path — a PDF with no text exercises none
    of it.
    """
    content = b"BT /F1 12 Tf 72 720 Td 14 TL\n"
    for line in lines:
        escaped = line.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
        content += f"({escaped}) Tj T*\n".encode()
    content += b"ET\n"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"endstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for index, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += f"{index} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode() + b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref}\n%%EOF"
    ).encode()
    return bytes(out)


def test_a_pdf_with_a_doi_and_title_actually_yields_them(tmp_path: Path) -> None:
    """Every other test of this function asserts `doi is None and title is None`.

    That is satisfied by a constant stub, so the whole extraction path — read
    the pages, pull the text, find the DOI, pick the title line — was pinned by
    nothing at all.
    """
    path = tmp_path / "paper.pdf"
    path.write_bytes(_pdf_with_text([
        "Deep Residual Learning for Image Recognition",
        "Kaiming He, Xiangyu Zhang",
        "doi:10.1109/CVPR.2016.90",
    ]))

    result = extract_pdf_metadata(str(path))

    assert result["doi"] == "10.1109/cvpr.2016.90"
    assert result["title"] == "Deep Residual Learning for Image Recognition"
