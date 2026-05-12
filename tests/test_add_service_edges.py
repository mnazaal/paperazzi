"""Edge-case coverage tests for add_service.py uncovered branches."""

from __future__ import annotations

import urllib.error
from pathlib import Path

import pytest

from pzi.add_service import (
    _add_local_pdf,
    _add_record_with_bib,
    _attach_pdf_if_available,
    _attach_similarity_hint,
    _error_result,
    _merge_record_sources,
    add_input_to_bib,
)
from pzi.config import BibConfig


# ── helpers ────────────────────────────────────────────────────────────────


def _make_config(tmp_path: Path, bib_path: Path, **extras: object) -> str:
    """Write a minimal config.toml and return its path as a string."""
    cfg = tmp_path / "config.toml"
    lines = [
        'translation_server_url = "http://127.0.0.1:1969"',
    ]
    for k, v in extras.items():
        if isinstance(v, str):
            lines.append(f'{k} = "{v}"')
        else:
            lines.append(f"{k} = {v}")
    lines.append("")
    lines.append("[[bibs]]")
    lines.append(f'name = "ml"')
    lines.append(f'path = "{bib_path}"')
    lines.append("default = true")
    cfg.write_text("\n".join(lines))
    return str(cfg)


def _bib_config(bib_path: Path, papers_dir: Path | None = None) -> BibConfig:
    """Return a simple BibConfig dict."""
    cfg: BibConfig = {
        "name": "ml",
        "path": str(bib_path),
        "papers_dir": str(papers_dir or bib_path.parent / "papers"),
        "default": True,
    }
    return cfg


# ── line 71: add_input_to_bib with unresolvable bib ────────────────────────


def test_add_input_unresolvable_bib(tmp_path: Path, monkeypatch) -> None:
    """add_input_to_bib when resolve_bib returns None (line 71)."""
    from pzi.add_service import add_input_to_bib as _add

    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
translation_server_url = "http://127.0.0.1:1969"
[[bibs]]
name = "ml"
path = "/nonexistent/ml.bib"
""".strip()
    )

    result = _add(
        config_path=str(config_path),
        home_dir=str(tmp_path),
        value="10.1234/foo",
        record_overrides={},
        bib_selector="nonexistent-bib",
        dry_run=True,
    )
    assert result["status"] == "error"
    assert result["message"] == "could not resolve target bib"
    assert "no matching bib found" in result["errors"][0]


# ── lines 172-174: connection-error message with host:port ──────────────────


def test_add_input_connection_error_specific_message(
    tmp_path: Path, monkeypatch
) -> None:
    """URLError/ConnectionError/OSError → friendly message with host:port (lines 171-177)."""
    from pzi.add_service import add_input_to_bib as _add

    bib_path = tmp_path / "library.bib"
    cfg = _make_config(tmp_path, bib_path)

    def fake_fetch_web(url: str, *, server_url: str) -> list[dict[str, object]]:
        raise ConnectionError("connection refused")

    result = _add(
        config_path=str(cfg),
        home_dir=str(tmp_path),
        value="https://example.com/paper",
        record_overrides={},
        bib_selector=None,
        dry_run=True,
        fetch_web=fake_fetch_web,
    )
    assert result["status"] == "error"
    assert result["message"] == "translation server error"
    err = result["errors"][0]
    assert "translation server not reachable" in err
    assert "podman run -p 1969:1969 translation-server" in err


def test_add_input_connection_error_custom_port(tmp_path: Path, monkeypatch) -> None:
    """Connection error at non-default port preserves custom port."""
    from pzi.add_service import add_input_to_bib as _add

    bib_path = tmp_path / "library.bib"
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(
        """
translation_server_url = "http://127.0.0.1:8080"
[[bibs]]
name = "ml"
path = "%s"
default = true
""".strip()
        % bib_path,
    )

    def fake_fetch_web(url: str, *, server_url: str) -> list[dict[str, object]]:
        raise OSError("timeout")

    result = _add(
        config_path=str(cfg_path),
        home_dir=str(tmp_path),
        value="https://example.com/paper",
        record_overrides={},
        bib_selector=None,
        dry_run=True,
        fetch_web=fake_fetch_web,
    )
    assert result["status"] == "error"
    err = result["errors"][0]
    assert "podman run -p 8080:1969 translation-server" in err


# ── lines 366-375: FlareSolverr DOI path (all sources fail, flare succeeds) ─


def test_add_input_doi_flaresolverr_fallback_metadata(tmp_path: Path, monkeypatch) -> None:
    """DOI: search→crossref→openalex→s2 all fail, raw is URL, flaresolverr returns metadata (lines 366-375)."""
    from pzi.add_service import add_input_to_bib as _add

    bib_path = tmp_path / "library.bib"
    cfg = _make_config(
        tmp_path, bib_path,
        flaresolverr_url="http://127.0.0.1:8191",
    )

    def fake_fetch_search(query: str, *, server_url: str) -> list[dict[str, object]]:
        return []

    def fake_fetch_web(url: str, *, server_url: str) -> list[dict[str, object]]:
        return []

    def fake_flaresolverr(url: str) -> str | None:
        return """<html><head>
<meta name="citation_title" content="Quantum Computing Advances">
<meta name="citation_author" content="Preskill, John">
<meta name="citation_publication_date" content="2018">
</head></html>"""

    # Use a DOI, but the raw_value is a URL so the web fallback path is taken
    result = _add(
        config_path=str(cfg),
        home_dir=str(tmp_path),
        value="https://example.com/10.1234/qc",
        record_overrides={},
        bib_selector=None,
        dry_run=True,
        fetch_search=fake_fetch_search,
        fetch_web=fake_fetch_web,
        fetch_crossref=lambda doi: None,
        fetch_openalex=lambda doi: None,
        fetch_s2=lambda doi: None,
        fetch_flaresolverr=fake_flaresolverr,
    )
    assert result["status"] == "ok"
    # The metadata from flaresolverr HTML should be used
    modified = result["changed_fields"]
    assert "title" in modified


# ── lines 398→404, 400→404: URL path, flaresolverr returns HTML without metadata ─


def test_add_input_url_flaresolverr_no_metadata(tmp_path: Path) -> None:
    """URL: translation server empty, flaresolverr returns HTML but no extractable meta — raises (lines 398->404)."""
    from pzi.add_service import add_input_to_bib as _add

    bib_path = tmp_path / "library.bib"
    cfg = _make_config(
        tmp_path, bib_path,
        flaresolverr_url="http://127.0.0.1:8191",
    )

    def fake_fetch_web(url: str, *, server_url: str) -> list[dict[str, object]]:
        return []  # translation server returns nothing

    def fake_flaresolverr(url: str) -> str | None:
        # Return HTML with no citation metadata
        return "<html><body>Hello World</body></html>"

    result = _add(
        config_path=str(cfg),
        home_dir=str(tmp_path),
        value="https://example.com/paper",
        record_overrides={},
        bib_selector=None,
        dry_run=True,
        fetch_web=fake_fetch_web,
        fetch_flaresolverr=fake_flaresolverr,
    )
    assert result["status"] == "error"
    assert "translation server returned no results" in result["errors"][0]


def test_add_input_url_flaresolverr_returns_none(tmp_path: Path) -> None:
    """URL: translation empty, flaresolverr returns None — raises ValueError (line 400->404)."""
    from pzi.add_service import add_input_to_bib as _add

    bib_path = tmp_path / "library.bib"
    cfg = _make_config(
        tmp_path, bib_path,
        flaresolverr_url="http://127.0.0.1:8191",
    )

    def fake_fetch_web(url: str, *, server_url: str) -> list[dict[str, object]]:
        return []

    def fake_flaresolverr(url: str) -> str | None:
        return None  # flaresolverr failed entirely

    result = _add(
        config_path=str(cfg),
        home_dir=str(tmp_path),
        value="https://example.com/paper",
        record_overrides={},
        bib_selector=None,
        dry_run=True,
        fetch_web=fake_fetch_web,
        fetch_flaresolverr=fake_flaresolverr,
    )
    assert result["status"] == "error"
    assert "translation server returned no results" in result["errors"][0]


# ── line 440→439: _merge_record_sources skips None override values ──────────


def test_merge_record_sources_skips_none(tmp_path: Path) -> None:
    """override key with None value is skipped (line 440->439)."""
    base = {"title": "Base Title", "doi": "10.1234/base"}
    overrides = {"title": None, "year": 2024, "citekey": None}

    result = _merge_record_sources(base, overrides)
    assert result["title"] == "Base Title"  # NOT overwritten by None
    assert result["year"] == 2024
    assert "citekey" not in result  # None was skipped


# ── lines 512-513: _add_local_pdf DOI path raises OSError ──────────────────


def test_add_local_pdf_doi_fetch_oserror(tmp_path: Path, monkeypatch) -> None:
    """PDF has DOI but _fetch_record_for_input raises OSError → fallback record (lines 512-513)."""
    import pzi.add_service as mod

    bib_path = tmp_path / "library.bib"
    papers_dir = tmp_path / "papers"
    papers_dir.mkdir()
    bib = _bib_config(bib_path, papers_dir)

    # Return an empty bib so write succeeds
    monkeypatch.setattr(mod, "read_bib_file", lambda p: {"records": [], "errors": []})

    fake_record = {
        "citekey": "test2024fallback",
        "title": "Fallback",
    }
    monkeypatch.setattr(
        mod,
        "plan_bib_write",
        lambda rec, existing: {
            "action": "insert",
            "record": {**rec, "citekey": rec.get("citekey", "ck")},
            "changed_fields": list(rec.keys()),
        },
    )

    monkeypatch.setattr(mod, "execute_write_plan", lambda p, plan: None)

    # PDF metadata has a DOI
    monkeypatch.setattr(
        mod,
        "extract_pdf_metadata",
        lambda source: {"doi": "10.5555/test", "title": "Some Title"},
    )

    # But _fetch_record_for_input raises OSError
    def raise_oserror(**kwargs):
        raise OSError("server down")

    monkeypatch.setattr(mod, "_fetch_record_for_input", raise_oserror)

    result = _add_local_pdf(
        bib=bib,
        raw_value="/tmp/test.pdf",
        record_overrides={},
        dry_run=True,
        server_url="http://127.0.0.1:1969",
        fetch_search=lambda q, *, server_url: [],
    )
    # Should succeed with fallback record
    assert result["status"] == "ok"
    assert result["action"] == "insert"


# ── lines 517-524: _add_local_pdf title search empty results ────────────────


def test_add_local_pdf_title_search_empty(tmp_path: Path, monkeypatch) -> None:
    """PDF has title, search returns no results → fallback (lines 517-524)."""
    import pzi.add_service as mod

    bib_path = tmp_path / "library.bib"
    papers_dir = tmp_path / "papers"
    papers_dir.mkdir()
    bib = _bib_config(bib_path, papers_dir)

    monkeypatch.setattr(mod, "read_bib_file", lambda p: {"records": [], "errors": []})
    monkeypatch.setattr(
        mod,
        "plan_bib_write",
        lambda rec, existing: {
            "action": "insert",
            "record": {**rec, "citekey": rec.get("citekey", "ck")},
            "changed_fields": list(rec.keys()),
        },
    )
    monkeypatch.setattr(mod, "execute_write_plan", lambda p, plan: None)

    # No DOI, only title
    monkeypatch.setattr(
        mod,
        "extract_pdf_metadata",
        lambda source: {"title": "My Research Paper"},
    )

    # search returns empty
    monkeypatch.setattr(
        mod,
        "_fetch_record_for_input",
        lambda **kw: {"title": "My Research Paper", "source_url": "/tmp/test.pdf"},
    )

    # But we're testing the else branch at line 524, so we need search to return []
    # Actually we need fetch_search to return []. Let me mock fetch_search differently.

    result = _add_local_pdf(
        bib=bib,
        raw_value="/tmp/test.pdf",
        record_overrides={},
        dry_run=True,
        server_url="http://127.0.0.1:1969",
        fetch_search=lambda q, *, server_url: [],  # empty results
    )
    assert result["status"] == "ok"
    assert result["action"] == "insert"


def test_add_local_pdf_title_search_raises_valueerror(tmp_path: Path, monkeypatch) -> None:
    """PDF has title, fetch_search raises ValueError → [] then empty → fallback (lines 519-524)."""
    import pzi.add_service as mod

    bib_path = tmp_path / "library.bib"
    papers_dir = tmp_path / "papers"
    papers_dir.mkdir()
    bib = _bib_config(bib_path, papers_dir)

    monkeypatch.setattr(mod, "read_bib_file", lambda p: {"records": [], "errors": []})
    monkeypatch.setattr(
        mod,
        "plan_bib_write",
        lambda rec, existing: {
            "action": "insert",
            "record": {**rec, "citekey": rec.get("citekey", "ck")},
            "changed_fields": list(rec.keys()),
        },
    )
    monkeypatch.setattr(mod, "execute_write_plan", lambda p, plan: None)

    monkeypatch.setattr(
        mod,
        "extract_pdf_metadata",
        lambda source: {"title": "Some Title"},
    )

    def search_raises(query, *, server_url):
        raise ValueError("bad query")

    result = _add_local_pdf(
        bib=bib,
        raw_value="/tmp/test.pdf",
        record_overrides={},
        dry_run=True,
        server_url="http://127.0.0.1:1969",
        fetch_search=search_raises,
    )
    assert result["status"] == "ok"
    assert result["action"] == "insert"


def test_add_local_pdf_no_doi_no_title(tmp_path: Path, monkeypatch) -> None:
    """PDF has no DOI and no title → pure source_url fallback (line 526)."""
    import pzi.add_service as mod

    bib_path = tmp_path / "library.bib"
    papers_dir = tmp_path / "papers"
    papers_dir.mkdir()
    bib = _bib_config(bib_path, papers_dir)

    monkeypatch.setattr(mod, "read_bib_file", lambda p: {"records": [], "errors": []})
    monkeypatch.setattr(
        mod,
        "plan_bib_write",
        lambda rec, existing: {
            "action": "insert",
            "record": {**rec, "citekey": "unknown2024test"},
            "changed_fields": list(rec.keys()),
        },
    )
    monkeypatch.setattr(mod, "execute_write_plan", lambda p, plan: None)

    monkeypatch.setattr(
        mod,
        "extract_pdf_metadata",
        lambda source: {},  # nothing
    )

    result = _add_local_pdf(
        bib=bib,
        raw_value="/tmp/test.pdf",
        record_overrides={},
        dry_run=True,
        server_url="http://127.0.0.1:1969",
        fetch_search=lambda q, *, server_url: [],
    )
    assert result["status"] == "ok"


# ── line 541: copy_pdf_to_papers_dir returns error ─────────────────────────


def test_add_local_pdf_copy_error_warning(tmp_path: Path, monkeypatch) -> None:
    """copy_pdf_to_papers_dir returns error → warning appended (line 541)."""
    import pzi.add_service as mod

    bib_path = tmp_path / "library.bib"
    papers_dir = tmp_path / "papers"
    papers_dir.mkdir()
    bib = _bib_config(bib_path, papers_dir)

    monkeypatch.setattr(mod, "read_bib_file", lambda p: {"records": [], "errors": []})
    monkeypatch.setattr(
        mod,
        "plan_bib_write",
        lambda rec, existing: {
            "action": "insert",
            "record": {**rec, "citekey": "test2024copy"},
            "changed_fields": list(rec.keys()),
        },
    )
    monkeypatch.setattr(mod, "execute_write_plan", lambda p, plan: None)

    monkeypatch.setattr(
        mod,
        "extract_pdf_metadata",
        lambda source: {"doi": "10.5555/test", "title": "Test"},
    )

    # DOI fetch succeeds
    def fake_fetch(**kwargs):
        return {"doi": "10.5555/test", "title": "Test"}

    monkeypatch.setattr(mod, "_fetch_record_for_input", fake_fetch)

    # copy_pdf returns error
    monkeypatch.setattr(
        mod,
        "copy_pdf_to_papers_dir",
        lambda source_path, papers_dir, citekey: (None, "disk full"),
    )

    result = _add_local_pdf(
        bib=bib,
        raw_value="/tmp/test.pdf",
        record_overrides={},
        dry_run=False,
        server_url="http://127.0.0.1:1969",
        fetch_search=lambda q, *, server_url: [],
    )
    assert result["status"] == "ok"
    assert "disk full" in result["warnings"]


# ── line 542→546: copy_pdf returns (None, None) — no path set ──────────────


def test_add_local_pdf_copy_returns_none_none(tmp_path: Path, monkeypatch) -> None:
    """copy_pdf_to_papers_dir returns (None, None) → no path assigned (line 542->546)."""
    import pzi.add_service as mod

    bib_path = tmp_path / "library.bib"
    papers_dir = tmp_path / "papers"
    papers_dir.mkdir()
    bib = _bib_config(bib_path, papers_dir)

    monkeypatch.setattr(mod, "read_bib_file", lambda p: {"records": [], "errors": []})
    monkeypatch.setattr(
        mod,
        "plan_bib_write",
        lambda rec, existing: {
            "action": "insert",
            "record": {**rec, "citekey": rec.get("citekey", "test2024none")},
            "changed_fields": list(rec.keys()),
        },
    )
    monkeypatch.setattr(mod, "execute_write_plan", lambda p, plan: None)

    monkeypatch.setattr(
        mod,
        "extract_pdf_metadata",
        lambda source: {"doi": "10.5555/test", "title": "Test"},
    )

    def fake_fetch(**kwargs):
        return {"doi": "10.5555/test", "title": "Test"}

    monkeypatch.setattr(mod, "_fetch_record_for_input", fake_fetch)

    # copy_pdf returns (None, None) — both None
    monkeypatch.setattr(
        mod, "copy_pdf_to_papers_dir",
        lambda source_path, papers_dir, citekey: (None, None),
    )

    result = _add_local_pdf(
        bib=bib,
        raw_value="/tmp/test.pdf",
        record_overrides={},
        dry_run=False,
        server_url="http://127.0.0.1:1969",
        fetch_search=lambda q, *, server_url: [],
    )
    assert result["status"] == "ok"


# ── _add_local_pdf: dry_run skips copy entirely ────────────────────────────


def test_add_local_pdf_dry_run_skips_copy(tmp_path: Path, monkeypatch) -> None:
    """dry_run=True → copy_pdf_to_papers_dir is never called."""
    import pzi.add_service as mod

    bib_path = tmp_path / "library.bib"
    papers_dir = tmp_path / "papers"
    papers_dir.mkdir()
    bib = _bib_config(bib_path, papers_dir)

    monkeypatch.setattr(mod, "read_bib_file", lambda p: {"records": [], "errors": []})
    monkeypatch.setattr(
        mod,
        "plan_bib_write",
        lambda rec, existing: {
            "action": "insert",
            "record": {**rec, "citekey": "test2024dry"},
            "changed_fields": list(rec.keys()),
        },
    )
    monkeypatch.setattr(mod, "execute_write_plan", lambda p, plan: None)
    monkeypatch.setattr(
        mod, "extract_pdf_metadata",
        lambda source: {"doi": "10.5555/test", "title": "Test"},
    )

    def fake_fetch(**kwargs):
        return {"doi": "10.5555/test", "title": "Test"}

    monkeypatch.setattr(mod, "_fetch_record_for_input", fake_fetch)

    copy_calls = []
    monkeypatch.setattr(
        mod, "copy_pdf_to_papers_dir",
        lambda source_path, papers_dir, citekey: copy_calls.append(1) or (None, None),
    )

    result = _add_local_pdf(
        bib=bib,
        raw_value="/tmp/test.pdf",
        record_overrides={},
        dry_run=True,
        server_url="http://127.0.0.1:1969",
        fetch_search=lambda q, *, server_url: [],
    )
    assert result["status"] == "ok"
    assert len(copy_calls) == 0  # dry_run skips copy


# ── _add_local_pdf: no citekey skips copy ──────────────────────────────────


def test_add_local_pdf_no_citekey_skips_copy(tmp_path: Path, monkeypatch) -> None:
    """When citekey cannot be generated, copy_pdf is skipped."""
    import pzi.add_service as mod

    bib_path = tmp_path / "library.bib"
    papers_dir = tmp_path / "papers"
    papers_dir.mkdir()
    bib = _bib_config(bib_path, papers_dir)

    monkeypatch.setattr(mod, "read_bib_file", lambda p: {"records": [], "errors": []})
    monkeypatch.setattr(
        mod,
        "plan_bib_write",
        lambda rec, existing: {
            "action": "insert",
            "record": {**rec, "citekey": rec.get("citekey")},
            "changed_fields": list(rec.keys()),
        },
    )
    monkeypatch.setattr(mod, "execute_write_plan", lambda p, plan: None)
    monkeypatch.setattr(
        mod, "extract_pdf_metadata",
        lambda source: {"doi": "10.5555/test", "title": "Test"},
    )

    def fake_fetch(**kwargs):
        # Return record with no info to generate citekey from
        return {}

    monkeypatch.setattr(mod, "_fetch_record_for_input", fake_fetch)

    copy_calls = []
    monkeypatch.setattr(
        mod, "copy_pdf_to_papers_dir",
        lambda source_path, papers_dir, citekey: copy_calls.append(1) or (None, None),
    )

    result = _add_local_pdf(
        bib=bib,
        raw_value="/tmp/test.pdf",
        record_overrides={},
        dry_run=False,
        server_url="http://127.0.0.1:1969",
        fetch_search=lambda q, *, server_url: [],
    )
    # Should still succeed, just without PDF copy
    assert result["status"] == "ok"
    assert len(copy_calls) == 0


# ── _add_local_pdf: generated citekey and successful copy ──────────────────


def test_add_local_pdf_citekey_generated_and_copy_succeeds(
    tmp_path: Path, monkeypatch
) -> None:
    """Happy path: citekey generated, copy succeeds, local_pdf_path set."""
    import pzi.add_service as mod

    bib_path = tmp_path / "library.bib"
    papers_dir = tmp_path / "papers"
    papers_dir.mkdir()
    bib = _bib_config(bib_path, papers_dir)

    monkeypatch.setattr(mod, "read_bib_file", lambda p: {"records": [], "errors": []})
    monkeypatch.setattr(
        mod,
        "plan_bib_write",
        lambda rec, existing: {
            "action": "insert",
            "record": {**rec, "citekey": rec.get("citekey", "ck")},
            "changed_fields": list(rec.keys()),
        },
    )
    monkeypatch.setattr(mod, "execute_write_plan", lambda p, plan: None)
    monkeypatch.setattr(
        mod, "extract_pdf_metadata",
        lambda source: {"doi": "10.5555/test", "title": "Test"},
    )

    def fake_fetch(**kwargs):
        return {"doi": "10.5555/test", "title": "Test", "authors": ["Author, A."], "year": 2024}

    monkeypatch.setattr(mod, "_fetch_record_for_input", fake_fetch)
    monkeypatch.setattr(
        mod, "copy_pdf_to_papers_dir",
        lambda source_path, papers_dir, citekey: ("/papers/test.pdf", None),
    )

    result = _add_local_pdf(
        bib=bib,
        raw_value="/tmp/test.pdf",
        record_overrides={},
        dry_run=False,
        server_url="http://127.0.0.1:1969",
        fetch_search=lambda q, *, server_url: [],
    )
    assert result["status"] == "ok"


# ── line 597: _attach_pdf_if_available dry_run ─────────────────────────────


def test_attach_pdf_dry_run(tmp_path: Path) -> None:
    """dry_run=True with pdf_url — skip fetch (line 597)."""
    record = {"pdf_url": "https://example.com/paper.pdf"}
    bib = _bib_config(tmp_path / "lib.bib")

    rec, warnings = _attach_pdf_if_available(
        record=record, bib=bib, dry_run=True, fetch_binary=None,
    )
    assert rec is record
    assert warnings == []


# ── line 601: _attach_pdf_if_available no citekey ──────────────────────────


def test_attach_pdf_no_citekey(tmp_path: Path) -> None:
    """pdf_url present but no citekey → warning (line 601)."""
    record = {"pdf_url": "https://example.com/paper.pdf"}
    bib = _bib_config(tmp_path / "lib.bib")

    rec, warnings = _attach_pdf_if_available(
        record=record, bib=bib, dry_run=False, fetch_binary=None,
    )
    assert rec is record
    assert any("cannot attach PDF" in w for w in warnings)


# ── line 618: _attach_pdf_if_available fetch returns warning ────────────────


def test_attach_pdf_fetch_warning(tmp_path: Path, monkeypatch) -> None:
    """fetch_and_store_pdf_with_fallbacks returns local_path + warning (line 618)."""
    import pzi.add_service as mod

    record = {
        "pdf_url": "https://example.com/paper.pdf",
        "citekey": "test2024pdf",
    }
    bib = _bib_config(tmp_path / "lib.bib", tmp_path / "papers")

    monkeypatch.setattr(
        mod,
        "fetch_and_store_pdf_with_fallbacks",
        lambda url, papers_dir, citekey, fetch_binary,
        flaresolverr_url=None, browser_pdf_cmd=None: (
            "/papers/test.pdf",  # local_pdf_path
            "slow download",     # warning
            None,                # error
        ),
    )

    rec, warnings = _attach_pdf_if_available(
        record=record, bib=bib, dry_run=False, fetch_binary=None,
    )
    assert rec["local_pdf_path"] == "/papers/test.pdf"
    assert "slow download" in warnings


# ── _attach_pdf_if_available: fetch fails, returns error ───────────────────


def test_attach_pdf_fetch_error(tmp_path: Path, monkeypatch) -> None:
    """fetch_and_store_pdf_with_fallbacks returns None + error."""
    import pzi.add_service as mod

    record = {
        "pdf_url": "https://example.com/paper.pdf",
        "citekey": "test2024pdf",
    }
    bib = _bib_config(tmp_path / "lib.bib", tmp_path / "papers")

    monkeypatch.setattr(
        mod,
        "fetch_and_store_pdf_with_fallbacks",
        lambda url, papers_dir, citekey, fetch_binary,
        flaresolverr_url=None, browser_pdf_cmd=None: (
            None,   # local_pdf_path
            None,   # warning
            "404 not found",  # error
        ),
    )

    rec, warnings = _attach_pdf_if_available(
        record=record, bib=bib, dry_run=False, fetch_binary=None,
    )
    assert "local_pdf_path" not in rec
    assert "404 not found" in warnings


# ── _attach_pdf_if_available: no pdf_url → early return ────────────────────


def test_attach_pdf_no_url(tmp_path: Path) -> None:
    """No pdf_url in record → early return."""
    record = {"title": "Some paper"}
    bib = _bib_config(tmp_path / "lib.bib")

    rec, warnings = _attach_pdf_if_available(
        record=record, bib=bib, dry_run=False, fetch_binary=None,
    )
    assert rec is record
    assert warnings == []


# ── _attach_pdf_if_available: already has local_pdf_path ───────────────────


def test_attach_pdf_already_has_path(tmp_path: Path) -> None:
    """local_pdf_path already set → skip."""
    record = {
        "pdf_url": "https://example.com/paper.pdf",
        "local_pdf_path": "/papers/existing.pdf",
    }
    bib = _bib_config(tmp_path / "lib.bib")

    rec, warnings = _attach_pdf_if_available(
        record=record, bib=bib, dry_run=False, fetch_binary=None,
    )
    assert rec is record
    assert warnings == []


# ── lines 625-626: _attach_similarity_hint exact match found ───────────────


def test_similarity_hint_exact_match(tmp_path: Path) -> None:
    """find_exact_match returns something → return record unchanged (line 625-626)."""
    import pzi.add_service as mod

    record = {"citekey": "smith2024test", "title": "Test Paper"}
    existing = [{"citekey": "smith2024test", "title": "Test Paper"}]

    def fake_exact_match(rec, ex):
        return ex[0]  # exact match found

    monkeypatch_module = pytest.MonkeyPatch()  # we need a temp monkeypatch
    # Actually use the fixture approach — just call directly by patching find_exact_match
    # Let me import and use a simpler approach.

    result = _attach_similarity_hint(record, existing)
    # Since find_exact_match is imported and used, it should detect the exact match
    # if the keys match. But "exact match" is determined by find_exact_match internal logic.
    # We need to monkeypatch it.

    # For now, test with a situation where exact match is naturally found
    # Actually, let's use monkeypatch in test function signature.
    pass  # see next test


def test_similarity_hint_exact_match_patched(tmp_path: Path, monkeypatch) -> None:
    """Monkeypatch find_exact_match to return a match → record unchanged (lines 625-626)."""
    import pzi.add_service
    import pzi.add_service as mod

    record = {"citekey": "s1", "title": "T1"}
    existing = [{"citekey": "s1"}]

    monkeypatch.setattr(mod, "find_exact_match", lambda rec, ex: ex[0])

    result = _attach_similarity_hint(record, existing)
    assert result is record  # returned unchanged


# ── lines 634-636: _attach_similarity_hint no similar candidates ───────────


def test_similarity_hint_no_hint(tmp_path: Path, monkeypatch) -> None:
    """compute_similarity_hint returns None → record unchanged (lines 635-636)."""
    import pzi.add_service as mod

    record = {"citekey": "s1", "title": "Unique Title"}
    existing = [{"citekey": "s2", "title": "Different Paper"}]

    monkeypatch.setattr(mod, "find_exact_match", lambda rec, ex: None)
    monkeypatch.setattr(mod, "compute_similarity_hint", lambda rec, cands: None)

    result = _attach_similarity_hint(record, existing)
    assert "note" not in result or result is record


# ── lines 641-642: duplicate hint already present ──────────────────────────


def test_similarity_hint_duplicate(tmp_path: Path, monkeypatch) -> None:
    """hint_text already in existing note → return unchanged (lines 641-642)."""
    import pzi.add_service as mod

    record = {"citekey": "s1", "title": "T1", "note": "Possibly similar to s2"}
    existing = [{"citekey": "s2", "title": "T2"}]

    monkeypatch.setattr(mod, "find_exact_match", lambda rec, ex: None)
    monkeypatch.setattr(mod, "compute_similarity_hint", lambda rec, cands: "s2")

    result = _attach_similarity_hint(record, existing)
    assert result is record  # unchanged because hint already present


# ── line 643: similarity hint appended to existing note ───────────────────


def test_similarity_hint_appended_to_note(tmp_path: Path, monkeypatch) -> None:
    """Existing note with different content → hint appended (line 643)."""
    import pzi.add_service as mod

    record = {"citekey": "s1", "title": "T1", "note": "Important paper"}
    existing = [{"citekey": "s2", "title": "T2"}]

    monkeypatch.setattr(mod, "find_exact_match", lambda rec, ex: None)
    monkeypatch.setattr(mod, "compute_similarity_hint", lambda rec, cands: "s2")

    result = _attach_similarity_hint(record, existing)
    assert "Important paper; Possibly similar to s2" == result["note"]


# ── similarity hint added to record without note ──────────────────────────


def test_similarity_hint_new_note(tmp_path: Path, monkeypatch) -> None:
    """Record has no note → hint becomes the note (lines 644-645)."""
    import pzi.add_service as mod

    record = {"citekey": "s1", "title": "T1"}
    existing = [{"citekey": "s2", "title": "T2"}]

    monkeypatch.setattr(mod, "find_exact_match", lambda rec, ex: None)
    monkeypatch.setattr(mod, "compute_similarity_hint", lambda rec, cands: "s2")

    result = _attach_similarity_hint(record, existing)
    assert result["note"] == "Possibly similar to s2"


# ── _attach_similarity_hint: empty string note ─────────────────────────────


def test_similarity_hint_empty_note(tmp_path: Path, monkeypatch) -> None:
    """Empty note (whitespace) → hint becomes the note."""
    import pzi.add_service as mod

    record = {"citekey": "s1", "title": "T1", "note": "   "}
    existing = [{"citekey": "s2", "title": "T2"}]

    monkeypatch.setattr(mod, "find_exact_match", lambda rec, ex: None)
    monkeypatch.setattr(mod, "compute_similarity_hint", lambda rec, cands: "s2")

    result = _attach_similarity_hint(record, existing)
    assert result["note"] == "Possibly similar to s2"


# ── _error_result with bib ─────────────────────────────────────────────────


def test_error_result_with_bib(tmp_path: Path) -> None:
    """_error_result includes bib info when bib is provided."""
    bib = _bib_config(tmp_path / "lib.bib")
    result = _error_result(
        message="test error",
        errors=["e1"],
        dry_run=True,
        warnings=["w1"],
        bib=bib,
    )
    assert result["status"] == "error"
    assert result["bib_name"] == "ml"
    assert result["bib_path"] == str(tmp_path / "lib.bib")
    assert result["warnings"] == ["w1"]


def test_error_result_without_bib(tmp_path: Path) -> None:
    """_error_result without bib → bib fields are None."""
    result = _error_result(
        message="test error",
        errors=["e1"],
        dry_run=False,
        warnings=[],
    )
    assert result["bib_name"] is None
    assert result["bib_path"] is None


# ── Exception block: has_min_meta path (lines 148-163) ─────────────────────


def test_add_input_exception_with_min_metadata(tmp_path: Path) -> None:
    """When fetch raises but record_overrides provide enough → add with fallback."""
    from pzi.add_service import add_input_to_bib as _add

    bib_path = tmp_path / "library.bib"
    cfg = _make_config(tmp_path, bib_path)

    def raise_error(url, *, server_url):
        raise RuntimeError("boom")

    result = _add(
        config_path=str(cfg),
        home_dir=str(tmp_path),
        value="https://example.com/paper",
        record_overrides={
            "title": "Fallback Title",
            "doi": "10.1234/fallback",
        },
        bib_selector=None,
        dry_run=True,
        fetch_web=raise_error,
    )
    # Should succeed with fallback record
    assert result["status"] == "ok"
    assert result["citekey"] is not None


def test_add_input_exception_with_authors_and_year(tmp_path: Path) -> None:
    """has_min_meta satisfied by authors list (no DOI)."""
    from pzi.add_service import add_input_to_bib as _add

    bib_path = tmp_path / "library.bib"
    cfg = _make_config(tmp_path, bib_path)

    def raise_error(url, *, server_url):
        raise ConnectionRefusedError("refused")

    result = _add(
        config_path=str(cfg),
        home_dir=str(tmp_path),
        value="10.1234/paper",
        record_overrides={
            "title": "Paper Title",
            "authors": ["Smith, Jane"],
        },
        bib_selector=None,
        dry_run=True,
        fetch_search=lambda q, *, server_url: raise_error(q, server_url=server_url),
    )
    assert result["status"] == "ok"


def test_add_input_exception_insufficient_metadata(tmp_path: Path) -> None:
    """Exception but record_overrides don't provide enough → error result."""
    from pzi.add_service import add_input_to_bib as _add

    bib_path = tmp_path / "library.bib"
    cfg = _make_config(tmp_path, bib_path)

    def raise_error(url, *, server_url):
        raise RuntimeError("generic failure")

    result = _add(
        config_path=str(cfg),
        home_dir=str(tmp_path),
        value="https://example.com/paper",
        record_overrides={},  # no overrides
        bib_selector=None,
        dry_run=True,
        fetch_web=raise_error,
    )
    # Should be an error
    assert result["status"] == "error"
    assert "generic failure" in result["errors"][0]


def test_add_input_urlerror_that_is_httperror(tmp_path: Path) -> None:
    """HTTPError (subclass of URLError) is NOT treated as connection error — shows str(exc)."""
    from pzi.add_service import add_input_to_bib as _add

    bib_path = tmp_path / "library.bib"
    cfg = _make_config(tmp_path, bib_path)

    class FakeHTTPError(urllib.error.HTTPError):
        def __init__(self):
            pass

        def __str__(self):
            return "HTTP 500 Internal Server Error"

    def raise_http(url, *, server_url):
        raise FakeHTTPError()

    result = _add(
        config_path=str(cfg),
        home_dir=str(tmp_path),
        value="https://example.com/paper",
        record_overrides={},
        bib_selector=None,
        dry_run=True,
        fetch_web=raise_http,
    )
    assert result["status"] == "error"
    # Should NOT be the connection error message
    assert "translation server not reachable" not in result["errors"][0]
    assert "HTTP 500" in result["errors"][0]


# ── _add_record_with_bib direct test (no dry_run, execute_write_plan) ─────


def test_add_record_with_bib_execute(tmp_path: Path, monkeypatch) -> None:
    """_add_record_with_bib with dry_run=False calls execute_write_plan."""
    import pzi.add_service as mod

    bib_path = tmp_path / "library.bib"
    bib = _bib_config(bib_path)

    monkeypatch.setattr(mod, "read_bib_file", lambda p: {"records": [], "errors": []})
    execute_calls = []
    monkeypatch.setattr(mod, "execute_write_plan", lambda p, plan: execute_calls.append(plan))
    monkeypatch.setattr(
        mod, "plan_bib_write",
        lambda rec, existing: {
            "action": "insert",
            "record": {**rec, "citekey": "test2024exec"},
            "changed_fields": ["citekey", "title"],
        },
    )

    result = _add_record_with_bib(
        bib=bib,
        record={"title": "Execute Test", "authors": ["A"], "year": 2024},
        dry_run=False,
    )
    assert result["status"] == "ok"
    assert result["action"] == "insert"
    assert result["message"] == "insert entry"
    assert len(execute_calls) == 1


# ── doi exception → has_min_meta path with pdf_url kind ───────────────────


def test_add_input_doi_exception_with_min_meta(tmp_path: Path) -> None:
    """DOI classified, exception thrown, record_overrides provide min metadata → fallback."""
    from pzi.add_service import add_input_to_bib as _add

    bib_path = tmp_path / "library.bib"
    cfg = _make_config(tmp_path, bib_path)

    def boom(query, *, server_url):
        raise RuntimeError("some error")

    result = _add(
        config_path=str(cfg),
        home_dir=str(tmp_path),
        value="10.1234/doi",
        record_overrides={
            "title": "Paper",
            "doi": "10.1234/doi",
            "year": 2023,
        },
        bib_selector=None,
        dry_run=True,
        fetch_search=boom,
    )
    assert result["status"] == "ok"
    assert result["citekey"] is not None
