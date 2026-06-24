"""Binary HTTP route planning for file responses.

Keep socket writes in ``http_api``. Keep BibTeX lookup and path validation here.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pzi.bib_repository import read_bib_file
from pzi.config import load_and_resolve_bib
from pzi.export_service import export_bibtex, export_csv, export_json, export_ris

EXPORT_FORMATS = {
    "bibtex": (export_bibtex, "bib"),
    "csv": (export_csv, "csv"),
    "json": (export_json, "json"),
    "ris": (export_ris, "ris"),
}


@dataclass(frozen=True)
class PdfFileResponse:
    path: Path
    content_type: str
    filename: str


@dataclass(frozen=True)
class ExportBytesResponse:
    content: bytes
    content_type: str
    filename: str


def build_pdf_file_response(
    *,
    config_path: str,
    home_dir: str,
    citekey: str,
    bib_selector: str | None,
) -> tuple[int, PdfFileResponse | dict[str, Any]]:
    """Resolve a citekey to a safe local PDF response plan."""
    if not citekey:
        return 400, {"error": "citekey required"}

    resolved = load_and_resolve_bib(
        config_path=config_path,
        home_dir=home_dir,
        bib_selector=bib_selector,
    )
    if isinstance(resolved, list):
        return 400, {"status": "error", "errors": resolved}

    _config, bib = resolved
    read_result = read_bib_file(bib["path"])
    pdf_path = None
    for record in read_result["records"]:
        if record.get("citekey") == citekey:
            pdf_path = record.get("local_pdf_path")
            break

    pdf_file = safe_pdf_file(pdf_path, bib["papers_dir"])
    if pdf_file is None:
        return 404, {"error": f"PDF not found: {citekey}"}

    return 200, PdfFileResponse(
        path=pdf_file,
        content_type="application/pdf",
        filename=f"{citekey}.pdf",
    )


def build_export_bytes_response(
    *,
    config_path: str,
    home_dir: str,
    fmt: str,
    bib_selector: str | None,
) -> tuple[int, ExportBytesResponse | dict[str, Any]]:
    """Build raw export response bytes for download/inline serving."""
    if fmt not in EXPORT_FORMATS:
        return 400, {"error": f"unsupported format: {fmt}"}

    resolved = load_and_resolve_bib(
        config_path=config_path,
        home_dir=home_dir,
        bib_selector=bib_selector,
    )
    if isinstance(resolved, list):
        return 400, {"status": "error", "errors": resolved}

    _config, bib = resolved
    exporter, extension = EXPORT_FORMATS[fmt]
    result = exporter(bib_path=bib["path"])
    if result["status"] != "ok":
        return 500, {"error": "export failed", "errors": result.get("errors", [])}

    content = str(result["content"]).encode("utf-8")
    bib_name = str(bib.get("name") or "library")
    return 200, ExportBytesResponse(
        content=content,
        content_type=str(result["content_type"]),
        filename=f"{bib_name}.{extension}",
    )


def safe_pdf_file(pdf_path: object, papers_dir: object) -> Path | None:
    """Return confined existing PDF path, or None.

    Path must resolve under configured papers_dir, be a regular file, and start
    with PDF magic bytes.
    """
    if not isinstance(pdf_path, (str, Path)) or not isinstance(papers_dir, (str, Path)):
        return None
    try:
        candidate = Path(pdf_path).expanduser().resolve(strict=True)
        root = Path(papers_dir).expanduser().resolve(strict=True)
    except OSError:
        return None
    if candidate != root and root not in candidate.parents:
        return None
    if not candidate.is_file():
        return None
    try:
        with candidate.open("rb") as fh:
            return candidate if fh.read(5) == b"%PDF-" else None
    except OSError:
        return None
