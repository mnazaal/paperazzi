from pzi.cli_render import (
    _error_lines,
    _render_add_success,
    _render_bib_promote_items,
    _render_bib_update_items,
    _render_pdf_success,
    _render_search_matches,
    _render_tag_mutation_success,
)


def test_error_lines_prefixes_each_error() -> None:
    assert _error_lines("failed", ["first", "second"]) == [
        "failed",
        "- first",
        "- second",
    ]


def test_render_add_success_includes_dry_run_prefix() -> None:
    assert (
        _render_add_success(
            {
                "action": "insert",
                "citekey": "smith2024graph",
                "bib_name": "ml",
                "dry_run": True,
            }
        )
        == "DRY RUN: insert smith2024graph in ml"
    )


def test_render_pdf_success_formats_action_path() -> None:
    assert (
        _render_pdf_success(
            "attached",
            {"citekey": "smith2024graph", "local_pdf_path": "/tmp/paper.pdf"},
        )
        == "attached PDF smith2024graph -> /tmp/paper.pdf"
    )


def test_render_tag_mutation_success_uses_none_for_empty_tags() -> None:
    assert (
        _render_tag_mutation_success(
            {
                "message": "removed tags",
                "citekey": "smith2024graph",
                "tags": [],
                "dry_run": False,
            }
        )
        == "removed tags for smith2024graph: (none)"
    )


def test_render_search_matches_formats_matches_and_empty_result() -> None:
    assert _render_search_matches({"matches": []}) == ["no matches"]
    assert _render_search_matches(
        {
            "matches": [
                {
                    "citekey": "smith2024graph",
                    "year": 2024,
                    "title": "Graph Parsers",
                    "matched_fields": ["title", "tags"],
                }
            ]
        }
    ) == ["smith2024graph\t2024\tGraph Parsers\t[matched: title,tags]"]


def test_render_bib_update_items_handles_noop_and_empty() -> None:
    assert _render_bib_update_items({"dry_run": True, "items": []}) == [
        "DRY RUN: no updates"
    ]
    assert _render_bib_update_items(
        {
            "dry_run": False,
            "items": [
                {"citekey": "smith2024graph", "changed_fields": [], "note": "current"}
            ],
        }
    ) == ["smith2024graph: (no-op) [current]"]


def test_render_bib_promote_items_includes_pdf_and_published_key() -> None:
    assert _render_bib_promote_items({"dry_run": False, "items": []}) == [
        "no preprints to promote"
    ]
    assert _render_bib_promote_items(
        {
            "dry_run": True,
            "items": [
                {
                    "preprint_citekey": "smith2024arxiv",
                    "published_citekey": "smith2024graph",
                    "changed_fields": ["doi"],
                    "pdf_attached": True,
                    "note": "published",
                }
            ],
        }
    ) == ["DRY RUN: smith2024arxiv -> smith2024graph: doi [PDF] [published]"]


def test_render_bib_promote_items_describes_create_and_update_actions() -> None:
    assert _render_bib_promote_items(
        {
            "dry_run": False,
            "items": [
                {
                    "preprint_citekey": "smith2023graph",
                    "published_citekey": "smith2024graph2",
                    "action": "create",
                    "changed_fields": ["doi", "venue"],
                    "pdf_attached": False,
                    "note": None,
                },
                {
                    "preprint_citekey": "doe2023search",
                    "published_citekey": "doe2023search",
                    "action": "update",
                    "changed_fields": ["doi"],
                    "pdf_attached": False,
                    "note": None,
                },
            ],
        }
    ) == [
        "smith2023graph: kept preprint, created smith2024graph2: doi, venue",
        "doe2023search: replaced preprint metadata in-place: doi",
    ]


def test_render_bib_promote_items_includes_summary_footer() -> None:
    assert _render_bib_promote_items(
        {
            "dry_run": True,
            "items": [],
            "summary": {
                "checked": 2,
                "created": 1,
                "updated": 0,
                "skipped_no_candidate": 1,
                "skipped_low_confidence": 0,
                "skipped_existing": 0,
                "provider_errors": 0,
            },
        }
    ) == [
        "DRY RUN: no preprints to promote",
        "DRY RUN: summary: checked 2; created 1; updated 0; no candidate 1; low confidence 0; existing 0; provider errors 0",
    ]


def test_render_bib_promote_items_surfaces_s2_warning() -> None:
    lines = _render_bib_promote_items(
        {
            "dry_run": False,
            "items": [],
            "summary": {
                "checked": 2,
                "created": 0,
                "updated": 0,
                "skipped_no_candidate": 2,
                "skipped_low_confidence": 0,
                "skipped_existing": 0,
                "provider_errors": 2,
                "s2_warning": (
                    "2 Semantic Scholar rate-limit failures. "
                    "Configure semantic_scholar_api_key_cmd in config.toml for higher limits."
                ),
            },
        }
    )
    assert any("warning:" in line and "semantic_scholar_api_key_cmd" in line for line in lines)
