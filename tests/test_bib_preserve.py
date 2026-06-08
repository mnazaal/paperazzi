from pzi.bib_preserve import (
    append_entry_preserving_source,
    parse_bibtex_document,
    patch_entry_fields_preserving_source,
)


def test_parse_bibtex_document_preserves_comments_strings_and_entry_spans() -> None:
    text = """% keep this comment
@string{jmlr = {Journal of Machine Learning Research}}

@article{smith2024graph,
  title = {Graph Parsers},
  journal = jmlr,
}
"""

    doc = parse_bibtex_document(text)

    assert doc.source == text
    assert set(doc.entries_by_key) == {"smith2024graph"}
    entry = doc.entries_by_key["smith2024graph"]
    assert entry.entry_type == "article"
    assert entry.citekey == "smith2024graph"
    assert text[entry.span.start : entry.span.end].startswith("@article{smith2024graph")
    assert entry.fields["title"].value == "Graph Parsers"
    assert entry.fields["journal"].value == "jmlr"


def test_append_entry_preserving_source_leaves_existing_text_byte_identical() -> None:
    original = """% keep this comment
@string{jmlr = {Journal of Machine Learning Research}}

@article{smith2024graph,
  title = {Graph Parsers},
}
"""
    rendered = "@article{doe2025trees,\n  title = {Tree Parsers}\n}\n"

    updated = append_entry_preserving_source(original, rendered)

    assert updated.startswith(original)
    assert updated.endswith("\n" + rendered)


def test_patch_entry_fields_preserving_source_touches_only_changed_fields() -> None:
    original = """% keep this comment
@article{smith2024graph,
  title = {Old Title},
  doi = {10.1/foo},
}

@preamble{keep me}
"""

    updated = patch_entry_fields_preserving_source(
        original,
        "smith2024graph",
        {"title": "New Title", "file": "papers/smith2024graph.pdf"},
    )

    assert "% keep this comment" in updated
    assert "@preamble{keep me}" in updated
    assert "  doi = {10.1/foo}," in updated
    assert "  title = {New Title}," in updated
    assert "  file = {papers/smith2024graph.pdf}," in updated
    assert updated.count("@article{") == 1
