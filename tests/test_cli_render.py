from pzi.cli_render import (
    error_lines,
    render_add_success,
    render_bib_promote_items,
    render_bib_update_items,
    render_pdf_success,
    render_search_matches,
    render_tag_mutation_success,
)


def test_error_lines_prefixes_each_error() -> None:
    assert error_lines("failed", ["first", "second"]) == [
        "failed",
        "- first",
        "- second",
    ]


def test_error_lines_does_not_say_the_same_thing_twice() -> None:
    """Many services set `message` and `errors[0]` to the same sentence.

    A renderer that prints the headline and then bullets every error shows that
    sentence twice, which reads as two separate failures. The bullets exist to
    add detail the headline does not have.

    This covers `error_lines` and only `error_lines`. The symptom it used to
    name — `pzi export --target <missing>` — goes through `cli._fail`, the
    other renderer sharing `distinct_details`, and nothing tested that one:
    `tests/test_cli_contract.py::test_export_says_a_missing_target_once` is
    where it lives now.
    """
    assert error_lines("no such library", ["no such library"]) == ["no such library"]
    assert error_lines("failed", ["failed", "and here is why"]) == [
        "failed",
        "- and here is why",
    ]


def test_render_add_success_includes_dry_run_prefix() -> None:
    assert (
        render_add_success(
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
        render_pdf_success(
            "attached",
            {"citekey": "smith2024graph", "local_pdf_path": "/tmp/paper.pdf"},
        )
        == "attached PDF smith2024graph -> /tmp/paper.pdf"
    )


def test_render_tag_mutation_success_uses_none_for_empty_tags() -> None:
    assert (
        render_tag_mutation_success(
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
    # No matches renders nothing: empty stdout keeps the output pipeable.
    assert render_search_matches({"matches": []}) == []
    assert render_search_matches(
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
    assert render_bib_update_items({"dry_run": True, "items": []}) == [
        "DRY RUN: no updates"
    ]
    assert render_bib_update_items(
        {
            "dry_run": False,
            "items": [
                {"citekey": "smith2024graph", "changed_fields": [], "note": "current"}
            ],
        }
    ) == ["smith2024graph: (no-op) [current]"]


def test_render_bib_promote_items_includes_pdf_and_published_key() -> None:
    assert render_bib_promote_items({"dry_run": False, "items": []}) == [
        "no preprints to promote"
    ]
    assert render_bib_promote_items(
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
    assert render_bib_promote_items(
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
    assert render_bib_promote_items(
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
    lines = render_bib_promote_items(
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


# ---------------------------------------------------------------------------
# Tab-separated output stays tab-separated
# ---------------------------------------------------------------------------


def test_a_tab_or_newline_in_a_field_does_not_forge_a_row() -> None:
    """`entries` and `search` emit tab-separated rows a script splits on.

    A title carrying a literal tab shifted every later column by one; one
    carrying a newline invented a whole extra row. Both are values a capture can
    write, so the rendering layer is where they have to be neutralized — the
    stored entry keeps them.
    """
    from pzi.cli_render import render_search_matches

    lines = render_search_matches({
        "matches": [
            {
                "citekey": "evil2024",
                "year": 2024,
                "title": "Real Title\tforged-column\nevil2025\t2025\tForged Row",
                "matched_fields": ["title"],
            }
        ]
    })

    assert len(lines) == 1
    assert lines[0].count("\t") == 3
    assert "\n" not in lines[0]
    # The text is still legible, not deleted.
    assert "Real Title" in lines[0] and "Forged Row" in lines[0]


def test_control_characters_are_stripped_from_rendered_output() -> None:
    """An ANSI escape in a captured title rewrites the user's terminal."""
    from pzi.cli_render import render_search_matches

    lines = render_search_matches({
        "matches": [
            {
                "citekey": "a2024",
                "year": 2024,
                "title": "Title\x1b[2J\x07 with escapes",
                "matched_fields": ["title"],
            }
        ]
    })

    assert "\x1b" not in lines[0]
    assert "\x07" not in lines[0]
