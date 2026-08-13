"""Fields the record model does not carry must survive import, merge and export.

`NormalizedRecord` models about fifteen fields. A real `.bib` carries `volume`,
`pages`, `publisher`, `editor`, `series`, `isbn`, `crossref` and whatever else
the user or their publisher put there. Every command here used to project the
entry through the record model and write the projection back, reporting a clean
success while the rest went away.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pzi.dedupe_service import merge_duplicates
from pzi.export_service import export_bibtex
from pzi.import_service import import_from_bibtex

RICH_SOURCE = """@proceedings{proc2019,
  title = {Proceedings of the Thing},
  year = {2019},
  publisher = {ACM},
}

@inproceedings{jones2019beta,
  author = {Jones, Alice},
  title = {Beta},
  booktitle = {Proc. of the Thing},
  year = {2019},
  volume = {7},
  pages = {10--20},
  publisher = {ACM},
  editor = {Editor, Ed},
  series = {LNCS},
  isbn = {978-1},
  crossref = {proc2019},
  doi = {10.1000/beta},
}
"""


MINIMAL_CONFIG_TOML = """
api_listen_host = "127.0.0.1"
api_listen_port = 8765

[[bibs]]
name = "main"
path = "{bib_path}"
papers_dir = "{papers_dir}"
default = true
"""


def _config(tmp_path: Path, bib_path: Path) -> Path:
    papers = tmp_path / "papers"
    papers.mkdir(exist_ok=True)
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        MINIMAL_CONFIG_TOML.format(bib_path=bib_path, papers_dir=papers),
        encoding="utf-8",
    )
    return config_path


def test_a_year_the_record_cannot_model_survives_an_update() -> None:
    """`2020a` and `in press` are ordinary library content, not junk.

    `NormalizedRecord.year` is an `int`, so a year that is not all digits parsed
    to `None`, the projection omitted it, and `merge_projected_entry` then
    *removed* the field — deleting it outright on any `add` onto an existing
    paper, `update`, or import onto a duplicate.
    """
    from pzi.bibtex import BibtexEntry, apply_record_to_entry, bibtex_entry_to_record

    for year in ("2020a", "in press", "{\\noopsort{1997}}1997"):
        entry: BibtexEntry = {  # type: ignore[assignment]
            "entry_type": "article",
            "citekey": "a1",
            "fields": {"title": "T", "year": year},
        }
        updated = apply_record_to_entry(entry, bibtex_entry_to_record(entry))
        assert updated["fields"]["year"] == year


def test_a_record_that_clears_an_ordinary_year_still_clears_it() -> None:
    """Preserving the unmodelled case must not turn `year` into a write-once field."""
    from pzi.bibtex import BibtexEntry, apply_record_to_entry, bibtex_entry_to_record

    entry: BibtexEntry = {  # type: ignore[assignment]
        "entry_type": "article",
        "citekey": "a1",
        "fields": {"title": "T", "year": "2020"},
    }
    record = bibtex_entry_to_record(entry)
    record["year"] = None

    updated = apply_record_to_entry(entry, record)

    assert "year" not in updated["fields"]


def test_import_preserves_every_unmodelled_field(tmp_path: Path) -> None:
    source = tmp_path / "source.bib"
    source.write_text(RICH_SOURCE, encoding="utf-8")
    target = tmp_path / "main.bib"
    target.write_text("", encoding="utf-8")

    result = import_from_bibtex(
        config_path=str(_config(tmp_path, target)),
        home_dir=str(tmp_path),
        source_path=str(source),
        bib_selector=None,
        dry_run=False,
    )

    assert result["imported"] == 2, result
    written = target.read_text(encoding="utf-8")
    for field in (
        "volume = {7}",
        "pages = {10--20}",
        "publisher = {ACM}",
        "editor = {Editor, Ed}",
        "series = {LNCS}",
        "isbn = {978-1}",
        # The inheritance link to the @proceedings entry imported alongside it.
        "crossref = {proc2019}",
    ):
        assert field in written, field
    assert "@inproceedings{jones2019beta" in written
    assert "@proceedings{proc2019" in written


def test_merge_keeps_the_survivors_macro_references_and_field_spelling(
    tmp_path: Path,
) -> None:
    """Every other write path routes the rebuilt block through
    `merge_preserving_unchanged_source`, which exists to stop exactly this:
    a rebuilt block carries no `@string` references and no original casing, so
    `journal = jmlr` became the literal token `{jmlr}` and `Title` became
    `title` — on a command whose job was to merge a *different* entry away.
    """
    bib = tmp_path / "main.bib"
    bib.write_text(
        '@string{jmlr = {Journal of Machine Learning Research}}\n\n'
        "@article{survivor,\n"
        "  Title = {Beta},\n"
        "  author = {Jones, Alice},\n"
        "  year = {2019},\n"
        "  journal = jmlr,\n"
        "  doi = {10.1/beta},\n"
        "}\n\n"
        "@article{dropped,\n"
        "  title = {Beta},\n"
        "  author = {Jones, Alice},\n"
        "  year = {2019},\n"
        "  doi = {10.1/beta},\n"
        "  pages = {10--20},\n"
        "}\n",
        encoding="utf-8",
    )

    result = merge_duplicates(
        bib_path=str(bib), citekey_a="dropped", citekey_b="survivor", dry_run=False
    )

    written = bib.read_text(encoding="utf-8")
    assert result["status"] == "ok"
    assert "journal = jmlr" in written
    assert "journal = {jmlr}" not in written
    assert "Title = {Beta}" in written
    # The merge still did its job.
    assert "pages = {10--20}" in written
    assert "@article{dropped" not in written


def test_merge_carries_the_dropped_entrys_unmodelled_fields(tmp_path: Path) -> None:
    bib = tmp_path / "main.bib"
    bib.write_text(
        "@inproceedings{rich,\n"
        "  author = {Jones, Alice},\n"
        "  title = {Beta},\n"
        "  year = {2019},\n"
        "  doi = {10.1/beta},\n"
        "  volume = {7},\n"
        "  pages = {10--20},\n"
        "  publisher = {ACM},\n"
        "  isbn = {978-1},\n"
        "  mynote = {kept},\n"
        "}\n\n"
        "@article{poor,\n"
        "  author = {Jones, Alice},\n"
        "  title = {Beta},\n"
        "  year = {2019},\n"
        "  doi = {10.1/beta},\n"
        "}\n",
        encoding="utf-8",
    )

    preview = merge_duplicates(
        bib_path=str(bib), citekey_a="rich", citekey_b="poor", dry_run=True
    )
    assert set(preview["carried_fields"]) >= {
        "volume", "pages", "publisher", "isbn", "mynote",
    }
    assert preview["dropped_fields"] == []

    result = merge_duplicates(
        bib_path=str(bib), citekey_a="rich", citekey_b="poor", dry_run=False
    )

    written = bib.read_text(encoding="utf-8")
    assert result["status"] == "ok"
    for field in ("volume = {7}", "pages = {10--20}", "publisher = {ACM}",
                  "isbn = {978-1}", "mynote = {kept}"):
        assert field in written, field
    assert "@inproceedings{rich" not in written
    # And the pre-merge file is recoverable, exactly as `delete` leaves it.
    backup = Path(result["backup_path"])
    assert backup.exists()
    assert "@inproceedings{rich" in backup.read_text(encoding="utf-8")


def test_merge_dry_run_names_the_fields_it_cannot_carry(tmp_path: Path) -> None:
    bib = tmp_path / "main.bib"
    bib.write_text(
        "@article{a,\n  title = {T},\n  doi = {10.1/x},\n  publisher = {ACM},\n}\n\n"
        "@article{b,\n  title = {T},\n  doi = {10.1/x},\n  publisher = {IEEE},\n}\n",
        encoding="utf-8",
    )

    preview = merge_duplicates(
        bib_path=str(bib), citekey_a="a", citekey_b="b", dry_run=True
    )

    assert preview["dropped_fields"] == ["publisher"]


BIB_WITH_NON_ENTRY_BLOCKS = """@preamble{ "\\newcommand{\\noopsort}[1]{}" }

@string{acm = "ACM"}

% a standing comment

@article{a1,
  title = {A Title},
  year = {2020},
  publisher = acm # { Press},
}
"""


def test_bibtex_export_preserves_preamble_strings_and_concatenation(
    tmp_path: Path,
) -> None:
    bib = tmp_path / "main.bib"
    bib.write_text(BIB_WITH_NON_ENTRY_BLOCKS, encoding="utf-8")

    result = export_bibtex(bib_path=str(bib))
    content = result["content"]

    assert result["status"] == "ok"
    assert "@preamble" in content
    assert "@string" in content
    assert "acm # { Press}" in content  # concatenation, not a literal string
    assert "{acm # { Press}}" not in content
    # And it re-imports as the same library.
    assert "@article{a1," in content


def test_export_to_an_existing_file_is_all_or_nothing(
    tmp_path: Path, monkeypatch
) -> None:
    from pzi.commands import common as common_module

    destination = tmp_path / "out.bib"
    destination.write_text("PREVIOUS GOOD BACKUP\n", encoding="utf-8")

    def _boom(*_args, **_kwargs):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(common_module.os, "replace", _boom)
    # `try/except OSError: pass` also passes when the call raises nothing at
    # all, or raises a *different* OSError than the one injected.
    with pytest.raises(OSError) as excinfo:
        common_module.write_atomic(destination, "new content")
    assert excinfo.value.errno == 28

    assert destination.read_text(encoding="utf-8") == "PREVIOUS GOOD BACKUP\n"
    assert list(tmp_path.glob(".out.bib-*.tmp")) == []


def test_import_dry_run_is_marked_as_a_dry_run(tmp_path: Path) -> None:
    source = tmp_path / "source.bib"
    source.write_text(RICH_SOURCE, encoding="utf-8")
    target = tmp_path / "main.bib"
    target.write_text("", encoding="utf-8")

    result = import_from_bibtex(
        config_path=str(_config(tmp_path, target)),
        home_dir=str(tmp_path),
        source_path=str(source),
        bib_selector=None,
        dry_run=True,
    )

    assert result["dry_run"] is True
    assert json.loads(json.dumps(result))["dry_run"] is True
    assert target.read_text(encoding="utf-8") == ""
