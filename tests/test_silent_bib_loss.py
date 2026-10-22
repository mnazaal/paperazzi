"""Malformed BibTeX that parsed "fine" must be reported on reads and refused on writes.

Two shapes reached the user's real library silently (audit 2026-10-22):

* **A truncated entry.** In ``title = {x}},`` bibtexparser ends the entry at the
  extra ``}`` and files every later field as an ``ImplicitComment`` — not a
  failed block, so no read warned, the write gate passed, and `library clean`
  saw the lost ``file =`` field's PDF as an orphan to quarantine.
* **An unrepresentable value.** ``{a} # {b}`` reads as ``a} # {b`` and the next
  write of the entry flattens it to ``{a # b}``; a quoted stray ``}``, a control
  character and a non-ASCII entry type are likewise rewritten by the
  serializer's sanitizers, which exist for untrusted *provider* metadata.

The sanitizers stay; a value read from disk that one of them would alter is a
malformed file, which is the established rule for mangled field keys, empty
citekeys and case-colliding field keys.
"""

from __future__ import annotations

import json
from io import StringIO
from pathlib import Path

import pytest

from pzi import exit_codes, parse_cache
from pzi.bib_repository import (
    execute_write_plan,
    plan_bib_write,
    read_bib_file_raw_with_failures,
    update_bib_entry,
)
from pzi.bib_serialize import parse_bibtex_for_import, parse_bibtex_with_failures
from pzi.clean_service import clean_library, validate_library
from pzi.cli import run_cli
from pzi.errors import PziError

GOOD = """@article{good2020,
  title = {Good},
  year = {2020},
}
"""


def _truncated(pdf: str = "/nowhere/bad2.pdf") -> str:
    return GOOD + f"""
@misc{{bad2,
  title = {{x}}}},
  year = {{2020}},
  file = {{{pdf}}},
}}
"""


#: Truncation shapes beyond the multi-line one above, each hiding `file`.
TRUNCATED_SHAPES: dict[str, str] = {
    "one_line": GOOD + "\n@misc{bad2, title = {x}}, year = {2020}, file = {PDF}}\n",
    "digit_in_tail_key": GOOD + "\n@misc{bad2,\n  title = {x}},\n  bdsk-url-1 = {u},\n  file = {PDF},\n}\n",
    "non_ascii_tail_key": GOOD + "\n@misc{bad2,\n  title = {x}},\n  ann\u00e9e = {2020},\n  file = {PDF},\n}\n",
    "two_stray_braces": GOOD + "\n@misc{bad2,\n  title = {x}}},\n  file = {PDF},\n}\n",
    "stray_brace_alone_on_a_line": GOOD + "\n@misc{bad2,\n  title = {x},\n  }\n  file = {PDF},\n}\n",
    "no_comma_after_stray_brace": GOOD + "\n@misc{bad2,\n  title = {x}}\n  file = {PDF}\n}\n",
    "text_before_the_comma": GOOD + "\n@misc{bad2, title = {a}} c, year = {2020}, file = {PDF}}\n",
}

#: Each is a library whose second entry the serializer would silently alter.
UNREPRESENTABLE: dict[str, str] = {
    "hash_concatenation": GOOD + "\n@article{cat1,\n  title = {a} # {b},\n}\n",
    "quoted_stray_brace": GOOD + '\n@article{q1,\n  title = "a } b",\n}\n',
    "control_character": GOOD + "\n@article{nul1,\n  title = {a\x01b},\n}\n",
    "non_ascii_entry_type": GOOD + "\n@artícle{u1,\n  title = {T},\n}\n",
}

#: Values the new checks must leave alone, each with its expected parse.
BENIGN: dict[str, str] = {
    "case_protection": GOOD + "\n@article{b1,\n  title = {The {DNA} of {LaTeX}},\n}\n",
    "escaped_brace": GOOD + "\n@article{b1,\n  note = {a \\} b},\n}\n",
    "literal_hash_in_word": GOOD + "\n@article{b1,\n  title = {C# and F# in practice},\n}\n",
    "string_macro": '@string{jmlr = "JMLR"}\n' + GOOD + "\n@article{b1,\n  journal = jmlr,\n}\n",
    # Text outside entries that contains `=` but is not the tail of one.
    "free_text_with_equals": GOOD + "\nAuthor = me, written 2020\n\n@article{b1,\n  title = {T},\n}\n",
    "entry_commented_out_by_dropping_at": (
        GOOD + "\narticle{old,\n  title = {Old},\n}\n\n@article{b1,\n  title = {T},\n}\n"
    ),
    "percent_comment_with_equals": GOOD + "\n% note = {draft}\n@article{b1,\n  title = {T},\n}\n",
    "trailing_comma_then_free_text": (
        "@article{good2020,\n  title = {Good},\n},\n\nNote: a = b\n\n@article{b1,\n  title = {T},\n}\n"
    ),
    "trailing_comma_then_entry_without_at": (
        "@article{good2020,\n  title = {Good},\n},\narticle{old, title = {T}}\n\n"
        "@article{b1,\n  title = {T},\n}\n"
    ),
    "extra_brace_after_last_field_hides_nothing": (
        GOOD + "\n@article{b1,\n  title = {T},\n  year = {2020}}\n}\n"
    ),
    # Concatenations the merge already writes back verbatim; only a brace-first
    # one (`{a} # {b}`) is flattened, so only that one is refused.
    "macro_then_braced_concatenation": (
        '@string{j = "J"}\n' + GOOD + "\n@article{b1,\n  journal = j # { 2020},\n}\n"
    ),
    "month_macro_concatenation": GOOD + '\n@article{b1,\n  month = jan # "~1",\n}\n',
}


def _write(tmp_path: Path, text: str) -> Path:
    bib = tmp_path / "main.bib"
    bib.write_text(text, encoding="utf-8")
    return bib


def _tag(entry, record):
    fields = dict(entry["fields"])
    fields["keywords"] = "probe"
    return {**entry, "fields": fields}


# --- truncated entry ---------------------------------------------------------


def test_a_truncated_entry_is_reported_on_read(tmp_path: Path) -> None:
    _entries, failures = parse_bibtex_with_failures(_truncated())
    assert any("bad2" in failure for failure in failures), failures


def test_a_truncated_entry_blocks_writes_to_any_entry(tmp_path: Path) -> None:
    bib = _write(tmp_path, _truncated())
    before = bib.read_bytes()

    with pytest.raises(PziError, match="malformed BibTeX"):
        update_bib_entry(str(bib), "good2020", _tag)
    with pytest.raises(PziError, match="malformed BibTeX"):
        execute_write_plan(
            str(bib),
            plan_bib_write({"citekey": "new1", "title": "New", "year": 2022}, []),  # type: ignore[arg-type]
        )

    assert bib.read_bytes() == before


def test_clean_fix_does_not_quarantine_a_truncated_entrys_pdf(tmp_path: Path) -> None:
    """The real-library scenario: the lost `file =` makes the PDF look orphaned."""
    papers = tmp_path / "papers"
    papers.mkdir()
    pdf = papers / "bad2.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    bib = _write(tmp_path, _truncated(str(pdf)))

    result = clean_library(
        bib_path=str(bib), papers_dir=str(papers), dry_run=False, move_orphans=True,
    )

    assert result["status"] == "error"
    assert any("bad2" in e for e in result["errors"])
    assert pdf.exists()
    assert not (papers / ".orphans").exists()


def test_import_skips_a_truncated_entry_rather_than_importing_it_half_read() -> None:
    entries, problems = parse_bibtex_for_import(_truncated())
    assert [e["citekey"] for e in entries] == ["good2020"]
    assert any("bad2" in p for p in problems)


def test_entries_exits_findings_on_a_truncated_library(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PZI_SKIP_AUTO_START", "1")
    bib = _write(tmp_path, _truncated())
    config = tmp_path / "config.toml"
    config.write_text(f'[[bibs]]\nname = "ml"\npath = "{bib}"\ndefault = true\n')
    stderr = StringIO()

    code = run_cli(
        ["entries", "--config", str(config)],
        home_dir=str(tmp_path), stdout=StringIO(), stderr=stderr,
    )

    assert code == exit_codes.FINDINGS
    assert "bad2" in stderr.getvalue()


def test_a_cache_written_before_truncation_detection_is_not_served(tmp_path: Path, monkeypatch) -> None:
    """The cache keys on file bytes, so an unchanged damaged library would keep
    serving the old, warning-free result unless the schema version moved."""
    monkeypatch.setattr(parse_cache, "MIN_CACHEABLE_BYTES", 0)
    bib = _write(tmp_path, _truncated())
    read_bib_file_raw_with_failures(str(bib))
    cache_file = parse_cache.cache_path(parse_cache.default_cache_dir(), str(bib))
    stale = json.loads(cache_file.read_text(encoding="utf-8"))
    stale["version"] = 1
    stale["failures"] = []
    cache_file.write_text(json.dumps(stale), encoding="utf-8")

    _result, failures = read_bib_file_raw_with_failures(str(bib))

    assert any("bad2" in failure for failure in failures)


@pytest.mark.parametrize("shape", sorted(TRUNCATED_SHAPES))
def test_every_truncation_shape_keeps_clean_away_from_the_pdf(shape: str, tmp_path: Path) -> None:
    """A one-line entry, a digit or accent in the tail's first key, two stray
    braces: the detector keyed on `key =` lines missed the first three, and
    `clean --fix` quarantined the PDF."""
    papers = tmp_path / "papers"
    papers.mkdir()
    pdf = papers / "bad2.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    bib = _write(tmp_path, TRUNCATED_SHAPES[shape].replace("PDF", str(pdf)))

    result = clean_library(
        bib_path=str(bib), papers_dir=str(papers), dry_run=False, move_orphans=True,
    )

    assert result["status"] == "error", result
    assert pdf.exists()


# --- unrepresentable values --------------------------------------------------


@pytest.mark.parametrize("shape", sorted(UNREPRESENTABLE))
def test_an_unrepresentable_value_is_reported_on_read(shape: str) -> None:
    entries, failures = parse_bibtex_with_failures(UNREPRESENTABLE[shape])
    bad_key = entries[-1]["citekey"]
    assert any(bad_key in failure for failure in failures), failures


@pytest.mark.parametrize("shape", sorted(UNREPRESENTABLE))
def test_an_unrepresentable_value_blocks_writes_and_is_not_rewritten(
    shape: str, tmp_path: Path,
) -> None:
    bib = _write(tmp_path, UNREPRESENTABLE[shape])
    before = bib.read_bytes()
    bad_key = parse_bibtex_with_failures(UNREPRESENTABLE[shape])[0][-1]["citekey"]

    for citekey in ("good2020", bad_key):
        with pytest.raises(PziError, match="malformed BibTeX"):
            update_bib_entry(str(bib), citekey, _tag)

    assert bib.read_bytes() == before


@pytest.mark.parametrize("shape", sorted(UNREPRESENTABLE))
def test_clean_reports_an_unrepresentable_value(shape: str, tmp_path: Path) -> None:
    bib = _write(tmp_path, UNREPRESENTABLE[shape])
    papers = tmp_path / "papers"
    papers.mkdir()

    result = validate_library(bib_path=str(bib), papers_dir=str(papers))

    assert any(issue["type"] == "unwritable_entry" for issue in result["issues"]), result


@pytest.mark.parametrize("shape", sorted(UNREPRESENTABLE))
def test_import_skips_an_unrepresentable_entry(shape: str) -> None:
    entries, problems = parse_bibtex_for_import(UNREPRESENTABLE[shape])
    assert [e["citekey"] for e in entries] == ["good2020"]
    assert problems


@pytest.mark.parametrize("shape", sorted(BENIGN))
def test_legitimate_values_still_read_clean_and_write(shape: str, tmp_path: Path) -> None:
    _entries, failures = parse_bibtex_with_failures(BENIGN[shape])
    assert failures == []

    bib = _write(tmp_path, BENIGN[shape])
    update_bib_entry(str(bib), "b1", _tag)
    assert "keywords = {probe}" in bib.read_text(encoding="utf-8")
