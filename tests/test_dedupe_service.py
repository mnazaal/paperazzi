"""Tests for pzi.dedupe_service — duplicate detection and merging."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from pzi.dedupe_service import find_duplicates, merge_duplicates


def _write_bib(path: str, content: str) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(content)


def test_find_duplicates_empty() -> None:
    with tempfile.TemporaryDirectory() as td:
        bib = os.path.join(td, "empty.bib")
        _write_bib(bib, "")
        result = find_duplicates(bib_path=bib)
        assert result["status"] == "ok"
        assert result["total_entries"] == 0
        assert result["exact_duplicates"] == []
        assert result["total_clusters"] == 0


def test_find_duplicates_no_duplicates() -> None:
    with tempfile.TemporaryDirectory() as td:
        bib = os.path.join(td, "clean.bib")
        _write_bib(
            bib,
            (
                '@article{smith2024, title={A}, author={S}, year={2024},'
                ' doi={10.1000/a}}\n'
                '@article{jones2023, title={B}, author={J}, year={2023},'
                ' doi={10.1000/b}}'
            ),
        )
        result = find_duplicates(bib_path=bib)
        assert result["status"] == "ok"
        assert result["total_entries"] == 2
        assert result["exact_duplicates"] == []


def test_find_duplicates_by_doi() -> None:
    with tempfile.TemporaryDirectory() as td:
        bib = os.path.join(td, "doi_dup.bib")
        _write_bib(
            bib,
            (
                '@article{smith2024, title={A}, author={S}, year={2024},'
                ' doi={10.1000/same}}\n'
                '@article{jones2023, title={B}, author={J}, year={2023},'
                ' doi={10.1000/same}}'
            ),
        )
        result = find_duplicates(bib_path=bib)
        assert result["total_clusters"] == 1
        dup = result["exact_duplicates"][0]
        assert set(dup["citekeys"]) == {"smith2024", "jones2023"}


def test_find_duplicates_by_arxiv() -> None:
    with tempfile.TemporaryDirectory() as td:
        bib = os.path.join(td, "arxiv_dup.bib")
        _write_bib(
            bib,
            (
                '@article{a, title={X}, author={X}, year={2024},'
                ' eprint={2101.12345}, archiveprefix={arXiv}}\n'
                '@article{b, title={Y}, author={Y}, year={2023},'
                ' eprint={2101.12345}, archiveprefix={arXiv}}'
            ),
        )
        result = find_duplicates(bib_path=bib)
        assert result["total_clusters"] == 1
        dup = result["exact_duplicates"][0]
        assert set(dup["citekeys"]) == {"a", "b"}


def test_merge_duplicates_dry_run() -> None:
    with tempfile.TemporaryDirectory() as td:
        bib = os.path.join(td, "merge_dry.bib")
        _write_bib(
            bib,
            (
                '@article{a, title={X}, author={A}, year={2024},'
                ' doi={10.1000/x}}\n'
                '@article{b, title={Y}, author={B}, year={2023},'
                ' doi={10.1000/x}}'
            ),
        )
        result = merge_duplicates(
            bib_path=bib, citekey_a="a", citekey_b="b", dry_run=True,
        )
        assert result["status"] == "ok"
        assert result["dry_run"] is True
        assert result["dropped_citekey"] == "a"
        # File unchanged
        content = Path(bib).read_text()
        assert "@article{a" in content


def test_merge_duplicates_self() -> None:
    with tempfile.TemporaryDirectory() as td:
        bib = os.path.join(td, "merge_self.bib")
        _write_bib(bib, '@article{a, title={X}, author={A}, year={2024}}')
        result = merge_duplicates(
            bib_path=bib, citekey_a="a", citekey_b="a", dry_run=True,
        )
        assert result["status"] == "error"
        assert "itself" in result["message"]


def test_merge_duplicates_not_found() -> None:
    with tempfile.TemporaryDirectory() as td:
        bib = os.path.join(td, "missing.bib")
        _write_bib(bib, '@article{a, title={X}, author={A}, year={2024}}')
        result = merge_duplicates(
            bib_path=bib, citekey_a="a", citekey_b="z", dry_run=True,
        )
        assert result["status"] == "error"
        assert "not found" in result["message"]


def test_merge_duplicates_real() -> None:
    with tempfile.TemporaryDirectory() as td:
        bib = os.path.join(td, "merge_real.bib")
        _write_bib(
            bib,
            (
                '@article{a, title={Title A}, author={Smith, J}, year={2024},'
                ' doi={10.1000/merged}}\n'
                '@article{b, title={Title B}, author={Jones, K}, year={2023},'
                ' doi={10.1000/merged}, keywords={ml, dl}}'
            ),
        )
        result = merge_duplicates(
            bib_path=bib, citekey_a="a", citekey_b="b", dry_run=False,
        )
        assert result["status"] == "ok"
        assert result["dropped_citekey"] == "a"
        # Entry b should now have merged tags (from b) and authors (a is longer? no, both length 1)
        content = Path(bib).read_text()
        assert "@article{a" not in content  # entry a removed
        assert "@article{b" in content
        assert "Title B" in content  # existing title preserved


def test_merge_duplicates_drops_a_keeps_b_and_preserves_comments() -> None:
    with tempfile.TemporaryDirectory() as td:
        bib = os.path.join(td, "merge_writer.bib")
        _write_bib(
            bib,
            (
                '% library comment\n'
                '@string{acm = {ACM}}\n'
                '@article{a, title={Title A}, author={Smith, J}, year={2024},'
                ' doi={10.1000/merged}}\n'
                '@article{b, title={Title B}, author={Jones, K}, year={2023},'
                ' doi={10.1000/merged}}'
            ),
        )

        result = merge_duplicates(
            bib_path=bib, citekey_a="a", citekey_b="b", dry_run=False,
        )

        assert result["status"] == "ok"
        text = Path(bib).read_text()
        # A is gone, B (the kept key) remains.
        assert "@article{a," not in text
        assert "@article{b," in text
        # Non-entry blocks survive the merge (comment-preserving write path).
        assert "% library comment" in text
        assert "@string{acm" in text
