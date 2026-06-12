"""Watch a directory for new .pdf/.bib files and auto-import them."""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TypeAlias

WatchResult: TypeAlias = dict[str, Any]


def watch_directory(
    *,
    watch_dir: str,
    config_path: str,
    home_dir: str,
    bib_selector: str | None = None,
    poll_interval: int = 5,
    recursive: bool = False,
    max_runtime: int | None = None,
    dry_run: bool = False,
) -> WatchResult:
    """Poll a directory for new .pdf/.bib files and auto-import them.

    Returns after ``max_runtime`` seconds (None = run until interrupted).
    """
    wd = Path(watch_dir).expanduser().resolve()
    if not wd.is_dir():
        return {
            "status": "error",
            "message": f"watch directory not found: {wd}",
            "imported": [],
            "errors": [],
        }

    processed: set[str] = set()
    imported: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    # Snapshot existing files on start so we don't re-import them.
    for f in _scan_files(wd, recursive):
        processed.add(str(f))

    start_time = time.monotonic()

    try:
        while True:
            if max_runtime is not None and time.monotonic() - start_time > max_runtime:
                break

            new_files = [f for f in _scan_files(wd, recursive) if str(f) not in processed]
            for filepath in new_files:
                processed.add(str(filepath))

                # Allow files to finish writing (small delay).
                time.sleep(0.5)

                if not filepath.exists():
                    continue

                suffix = filepath.suffix.lower()
                try:
                    if suffix == ".bib":
                        result = _import_bib(
                            str(filepath), config_path, home_dir, bib_selector, dry_run,
                        )
                    elif suffix == ".pdf":
                        result = _import_pdf(
                            str(filepath), config_path, home_dir, bib_selector, dry_run,
                        )
                    else:
                        continue
                except Exception as exc:
                    errors.append({"file": str(filepath), "error": str(exc)})
                    continue

                if result.get("status") == "ok":
                    imported.append(result)
                else:
                    errors.append({
                        "file": str(filepath),
                        "error": result.get("message", "import failed"),
                    })

            time.sleep(poll_interval)
    except KeyboardInterrupt:
        pass

    return {
        "status": "ok",
        "watch_dir": str(wd),
        "imported": imported,
        "errors": errors,
        "total": len(imported),
        "error_count": len(errors),
    }


def _scan_files(directory: Path, recursive: bool) -> list[Path]:
    """Return list of .pdf/.bib files in directory."""
    pattern = "**/*" if recursive else "*"
    result: list[Path] = []
    for p in directory.glob(pattern):
        if p.is_file() and p.suffix.lower() in (".pdf", ".bib"):
            result.append(p)
    return result


def _import_bib(
    filepath: str,
    config_path: str,
    home_dir: str,
    bib_selector: str | None,
    dry_run: bool,
) -> dict[str, Any]:
    """Import a .bib file into the library."""
    from pzi.import_service import import_from_bibtex

    result = import_from_bibtex(
        config_path=config_path,
        home_dir=home_dir,
        source_path=filepath,
        bib_selector=bib_selector,
        dry_run=dry_run,
    )
    return {
        "status": result.get("status", "error"),
        "file": filepath,
        "citekey": _first_citekey(result),
        "type": "bib_import",
    }


def _import_pdf(
    filepath: str,
    config_path: str,
    home_dir: str,
    bib_selector: str | None,
    dry_run: bool,
) -> dict[str, Any]:
    """Import a PDF file into the library."""
    from pzi.add_service import add_input_to_bib

    result = add_input_to_bib(
        config_path=config_path,
        home_dir=home_dir,
        value=filepath,
        bib_selector=bib_selector,
        dry_run=dry_run,
    )
    return {
        "status": result.get("status", "ok"),
        "file": filepath,
        "citekey": result.get("citekey"),
        "type": "pdf_add",
    }


def _first_citekey(result: dict[str, Any]) -> str | None:
    """Extract first citekey from import result."""
    items = result.get("items", [])
    if items and isinstance(items, list):
        return items[0].get("citekey")
    return None
