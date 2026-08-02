"""Binary HTTP route planning for file responses.

Keep socket writes in ``http_api``. Keep BibTeX lookup and path validation here.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pzi.bib_repository import read_bib_file
from pzi.config import BibResolutionFailure, load_bib_target, load_config_file
from pzi.export_service import export_bibtex, export_csv, export_json, export_ris
from pzi.http_status import reject_unconfigured_bib_selector

# Characters unsafe inside a quoted ``Content-Disposition`` filename: control
# bytes (incl. CR/LF, which could split the header) plus the quote/backslash
# that would terminate or escape the quoted-string. Citekeys and bib names flow
# from local files, but sanitising here keeps a stray quote/newline from
# corrupting the response headers.
_UNSAFE_FILENAME_RE = re.compile(r'[\x00-\x1f"\\]')


def safe_header_filename(name: str, *, fallback: str = "download") -> str:
    """Return *name* with header-unsafe characters replaced, never empty."""
    cleaned = _UNSAFE_FILENAME_RE.sub("_", name).strip()
    return cleaned or fallback


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

    rejection = reject_unconfigured_bib_selector(
        bib_selector,
        config=load_config_file(config_path, home_dir=home_dir)["config"],
        home_dir=home_dir,
    )
    if rejection is not None:
        return rejection

    resolved = load_bib_target(
        config_path=config_path,
        home_dir=home_dir,
        bib_selector=bib_selector,
    )
    if isinstance(resolved, BibResolutionFailure):
        return 400, {"status": "error", "errors": resolved.errors}

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
        filename=safe_header_filename(f"{citekey}.pdf"),
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

    rejection = reject_unconfigured_bib_selector(
        bib_selector,
        config=load_config_file(config_path, home_dir=home_dir)["config"],
        home_dir=home_dir,
    )
    if rejection is not None:
        return rejection

    resolved = load_bib_target(
        config_path=config_path,
        home_dir=home_dir,
        bib_selector=bib_selector,
    )
    if isinstance(resolved, BibResolutionFailure):
        return 400, {"status": "error", "errors": resolved.errors}

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
        filename=safe_header_filename(f"{bib_name}.{extension}"),
    )


def path_confined_to(candidate_path: object, roots: object) -> Path | None:
    """Resolve *candidate_path* and return it only if it sits under one of *roots*.

    ``resolve(strict=True)`` on both sides is what makes this safe: it collapses
    ``..`` and follows symlinks *before* the containment test, so neither can be
    used to point outside a root. Both operands must exist.

    *roots* is a single path or an iterable of them. An empty iterable confines
    to nothing and always returns ``None`` — that is the intended reading of an
    unset allowlist, not a bug.
    """
    if isinstance(roots, (str, Path)):
        root_paths: list[object] = [roots]
    elif isinstance(roots, Iterable):
        root_paths = list(roots)
    else:
        return None
    if not isinstance(candidate_path, (str, Path)):
        return None
    try:
        candidate = Path(candidate_path).expanduser().resolve(strict=True)
    except (OSError, ValueError):
        # `ValueError`, not `OSError`, is what an embedded NUL raises — and the
        # path can come straight out of a request, so letting it escape turned a
        # malformed input into a 500 rather than a refusal.
        return None
    for raw_root in root_paths:
        if not isinstance(raw_root, (str, Path)):
            continue
        try:
            root = Path(raw_root).expanduser().resolve(strict=True)
        except (OSError, ValueError):
            continue
        if candidate == root or root in candidate.parents:
            return candidate
    return None


def safe_pdf_file(pdf_path: object, papers_dir: object) -> Path | None:
    """Return confined existing PDF path, or None.

    Path must resolve under configured papers_dir, be a regular file, and start
    with PDF magic bytes.
    """
    if not isinstance(papers_dir, (str, Path)):
        return None
    candidate = path_confined_to(pdf_path, papers_dir)
    if candidate is None:
        return None
    if not candidate.is_file():
        return None
    try:
        with candidate.open("rb") as fh:
            return candidate if fh.read(5) == b"%PDF-" else None
    except OSError:
        return None
