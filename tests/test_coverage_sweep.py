"""Small integration smoke tests for the current public module layout.

This file used to be a large coverage sweep for an older module split.  Keep it
focused so test collection catches stale public imports without becoming a
second, hard-to-read test suite.
"""

import importlib

CURRENT_PUBLIC_MODULES = [
    "pzi.add_service",
    "pzi.bib_repository",
    "pzi.bib_service",
    "pzi.bibtex",
    "pzi.browser_pdf",
    "pzi.browser_pdf_hook",
    "pzi.browser_session",
    "pzi.cli",
    "pzi.config",
    "pzi.doctor_service",
    "pzi.fetch_helpers",
    "pzi.flaresolverr",
    "pzi.html_metadata",
    "pzi.http_api",
    "pzi.identifiers",
    "pzi.metadata_sources",
    "pzi.pdf",
    "pzi.pdf_discovery",
    "pzi.pdf_service",
    "pzi.promote_service",
    "pzi.search_service",
    "pzi.setup_service",
    "pzi.similarity",
    "pzi.tag_service",
    "pzi.translation_server",
    "pzi.update_service",
]


def test_current_public_modules_import() -> None:
    for module_name in CURRENT_PUBLIC_MODULES:
        assert importlib.import_module(module_name)


def test_deleted_compatibility_modules_are_not_part_of_public_layout() -> None:
    deleted_modules = [
        "pzi.citekeys",
        "pzi.config_loader",
        "pzi.config_writer",
        "pzi.crossref",
        "pzi.doaj",
        "pzi.europepmc",
        "pzi.identity",
        "pzi.merge",
        "pzi.openalex",
        "pzi.pdf_acquisition",
        "pzi.pdf_metadata",
        "pzi.semantic_scholar",
        "pzi.service_common",
        "pzi.tags",
        "pzi.write_plan",
    ]
    for module_name in deleted_modules:
        try:
            importlib.import_module(module_name)
        except ModuleNotFoundError:
            continue
        raise AssertionError(f"stale compatibility module still importable: {module_name}")
