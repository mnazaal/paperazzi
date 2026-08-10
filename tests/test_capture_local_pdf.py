from pathlib import Path

from pzi.capture_local_pdf import attach_pdf_if_available


def test_attach_pdf_if_available_copies_local_pdf_candidate(tmp_path: Path) -> None:
    source_pdf = tmp_path / "source.pdf"
    source_pdf.write_bytes(b"%PDF-1.4\n%test\n")
    papers_dir = tmp_path / "papers"

    record, warnings = attach_pdf_if_available(
        record={"citekey": "smith2024paper", "pdf_url": str(source_pdf)},
        papers_dir=str(papers_dir),
        dry_run=False,
        fetch_binary=None,
    )

    assert warnings == []
    local_pdf_path = record["local_pdf_path"]
    assert isinstance(local_pdf_path, str)
    assert Path(local_pdf_path).exists()
    assert Path(local_pdf_path).read_bytes() == source_pdf.read_bytes()


def test_a_working_first_candidate_asks_discovery_for_nothing(tmp_path: Path) -> None:
    """The fallback must cost nothing when the first URL downloads.

    Re-running discovery means fresh calls to Crossref, Unpaywall and the
    translation server. Paying for those on every add — the overwhelmingly
    common case where the first candidate works — would be a worse bug than
    the one the fallback fixes.
    """
    asked: list[frozenset[str]] = []

    def next_candidate(record, tried):
        asked.append(tried)
        raise AssertionError("discovery must not run when the download succeeded")

    record, warnings = attach_pdf_if_available(
        record={"citekey": "smith2024paper", "pdf_url": "https://oa.example.org/a.pdf"},
        papers_dir=str(tmp_path / "papers"),
        dry_run=False,
        fetch_binary=None,
        fetch_pdf=lambda **kwargs: ("/papers/smith2024paper.pdf", None, None),
        next_candidate=next_candidate,
    )

    assert asked == []
    assert record["local_pdf_path"] == "/papers/smith2024paper.pdf"
    assert warnings == []


def test_a_blocked_candidate_is_replaced_by_the_next_source(tmp_path: Path) -> None:
    """The whole point of item 198: one 403 must not end acquisition."""
    attempted: list[str] = []
    asked: list[frozenset[str]] = []

    def fetch_pdf(**kwargs):
        url = kwargs["url"]
        attempted.append(url)
        if "blocked.example.com" in url:
            return None, None, f"all download methods failed for {url}"
        return "/papers/smith2024paper.pdf", None, None

    def next_candidate(record, tried):
        asked.append(tried)
        return {**record, "pdf_url": "https://oa.example.org/mirror.pdf", "pdf_source": "unpaywall"}

    record, warnings = attach_pdf_if_available(
        record={
            "citekey": "smith2024paper",
            "pdf_url": "https://blocked.example.com/a.pdf",
            "pdf_source": "publisher",
        },
        papers_dir=str(tmp_path / "papers"),
        dry_run=False,
        fetch_binary=None,
        fetch_pdf=fetch_pdf,
        next_candidate=next_candidate,
    )

    assert attempted == [
        "https://blocked.example.com/a.pdf",
        "https://oa.example.org/mirror.pdf",
    ]
    assert asked == [frozenset({"https://blocked.example.com/a.pdf"})]
    assert record["local_pdf_path"] == "/papers/smith2024paper.pdf"
    # The stored source is the one that produced the file, not the one that 403'd.
    assert record["pdf_url"] == "https://oa.example.org/mirror.pdf"
    assert record["pdf_source"] == "unpaywall"
    assert warnings == []


def test_every_source_failing_reports_every_source(tmp_path: Path) -> None:
    """A caller that tried three sources must not be told about only the last."""
    mirrors = iter(["https://second.example/b.pdf", "https://third.example/c.pdf"])

    def fetch_pdf(**kwargs):
        return None, None, f"all download methods failed for {kwargs['url']}"

    def next_candidate(record, tried):
        following = next(mirrors, None)
        return {**record, "pdf_url": following} if following else None

    record, warnings = attach_pdf_if_available(
        record={"citekey": "smith2024paper", "pdf_url": "https://first.example/a.pdf"},
        papers_dir=str(tmp_path / "papers"),
        dry_run=False,
        fetch_binary=None,
        fetch_pdf=fetch_pdf,
        next_candidate=next_candidate,
    )

    assert "local_pdf_path" not in record
    assert [w.rsplit(" ", 1)[-1] for w in warnings] == [
        "https://first.example/a.pdf",
        "https://second.example/b.pdf",
        "https://third.example/c.pdf",
    ]


def test_a_source_that_never_runs_out_is_still_bounded(tmp_path: Path) -> None:
    """Discovery terminates on its own; a hostile provider need not."""
    from pzi.pdf import MAX_PDF_SOURCE_ATTEMPTS

    attempted: list[str] = []

    def fetch_pdf(**kwargs):
        attempted.append(kwargs["url"])
        return None, None, "nope"

    def next_candidate(record, tried):
        return {**record, "pdf_url": f"https://endless.example/{len(tried)}.pdf"}

    attach_pdf_if_available(
        record={"citekey": "smith2024paper", "pdf_url": "https://endless.example/start.pdf"},
        papers_dir=str(tmp_path / "papers"),
        dry_run=False,
        fetch_binary=None,
        fetch_pdf=fetch_pdf,
        next_candidate=next_candidate,
    )

    assert len(attempted) == MAX_PDF_SOURCE_ATTEMPTS


def test_no_next_candidate_keeps_the_old_single_attempt_behaviour(tmp_path: Path) -> None:
    """Callers that cannot re-run discovery must be unaffected."""
    attempted: list[str] = []

    def fetch_pdf(**kwargs):
        attempted.append(kwargs["url"])
        return None, None, "blocked"

    record, warnings = attach_pdf_if_available(
        record={"citekey": "smith2024paper", "pdf_url": "https://blocked.example.com/a.pdf"},
        papers_dir=str(tmp_path / "papers"),
        dry_run=False,
        fetch_binary=None,
        fetch_pdf=fetch_pdf,
    )

    assert attempted == ["https://blocked.example.com/a.pdf"]
    assert "local_pdf_path" not in record
    assert warnings == ["blocked"]


def test_local_pdf_base_record_reports_provider_failures(tmp_path: Path) -> None:
    """Provider failures here were swallowed, leaving `--strict-metadata` blind."""
    from pzi.capture_local_pdf import local_pdf_base_record

    def failing_search(_title, *, server_url):
        raise OSError("connection refused")

    errors: list[str] = []
    record = local_pdf_base_record(
        raw_value=str(tmp_path / "paper.pdf"),
        extracted={"title": "Graph Parsers"},
        server_url="http://127.0.0.1:1",
        fetch_search=failing_search,
        errors=errors,
    )

    # Best-effort record is still returned...
    assert record["title"] == "Graph Parsers"
    # ...but the failure is no longer invisible.
    assert any("connection refused" in e for e in errors)


def test_add_local_pdf_honors_strict_metadata_and_writes_nothing(
    tmp_path: Path, monkeypatch
) -> None:
    """`--strict-metadata` was silently ignored for local-PDF input.

    The gate must also run before the write: reporting the failure after the
    entry and its copied PDF had landed would leave exactly the unverified
    record the flag exists to prevent.
    """
    import pzi.capture_local_pdf as clp

    source_pdf = tmp_path / "paper.pdf"
    source_pdf.write_bytes(b"%PDF-1.4\n")
    papers_dir = tmp_path / "papers"
    papers_dir.mkdir()
    bib_path = tmp_path / "library.bib"
    bib_path.write_text("")

    monkeypatch.setattr(
        clp, "extract_pdf_metadata", lambda _p: {"title": "Graph Parsers"}
    )

    def failing_search(_title, *, server_url):
        raise OSError("connection refused")

    def _unused_add_record(**_kw):  # pragma: no cover — must not be reached
        raise AssertionError("strict gate must run before anything is written")

    result = clp.add_local_pdf(
        bib={"name": "main", "path": str(bib_path),
             "papers_dir": str(papers_dir), "default": True},
        raw_value=str(source_pdf),
        record_overrides={},
        dry_run=False,
        server_url="http://127.0.0.1:1",
        fetch_search=failing_search,
        ensure_citekey=lambda record, existing, **_kw: record,
        add_record=_unused_add_record,
        strict_metadata=True,
    )

    assert result["status"] == "error"
    assert "strict-metadata" in result.get("message", "")
    # Nothing written: no entry, and the PDF was not copied into papers_dir.
    assert bib_path.read_text() == ""
    assert list(papers_dir.iterdir()) == []


# ---------------------------------------------------------------------------
# Resolving a local PDF's metadata
# ---------------------------------------------------------------------------


def _search_returning(record, **extra):
    def _search(_title, *, server_url):
        return [{"record": record, "attachments": [], **extra}]

    return _search


def test_a_scraped_doi_is_normalized_before_it_is_resolved() -> None:
    """A DOI lifted out of PDF text arrives with whatever the page had around it.

    It was passed through as already-`normalized`, so a trailing period or a
    `https://doi.org/` prefix was resolved verbatim — and stored that way.
    """
    from pzi.capture_local_pdf import local_pdf_base_record

    seen: list[object] = []

    def _fetch_record(*, classified, **_kw):
        seen.append(classified["normalized"])
        return {"citekey": "k", "doi": classified["normalized"]}, [], []

    local_pdf_base_record(
        raw_value="/tmp/paper.pdf",
        extracted={"doi": "https://doi.org/10.1145/3372297."},
        server_url="http://127.0.0.1:1969",
        fetch_record=_fetch_record,
        fetch_search=_search_returning({}),
    )

    assert seen == ["10.1145/3372297"]


def test_a_scraped_value_that_is_not_a_doi_falls_through_to_the_title() -> None:
    """Resolving junk as a DOI wastes the request and can adopt a wrong record."""
    from pzi.capture_local_pdf import local_pdf_base_record

    def _fetch_record(**_kw):
        raise AssertionError("should not resolve a non-DOI as a DOI")

    record = local_pdf_base_record(
        raw_value="/tmp/paper.pdf",
        extracted={"doi": "see front matter", "title": "Graph Parsers"},
        server_url="http://127.0.0.1:1969",
        fetch_record=_fetch_record,
        fetch_search=_search_returning(
            {"title": "Graph Parsers", "doi": "10.1145/3372297"}
        ),
    )

    assert record["doi"] == "10.1145/3372297"


def test_a_title_search_hit_that_is_a_different_paper_is_not_adopted() -> None:
    """The first search result was taken whatever it was.

    A title-only match is the weakest evidence there is, and adopting its DOI
    attaches a *different paper's* identifier to the user's PDF — which then
    dedupes against that paper and resolves to it forever after.
    """
    from pzi.capture_local_pdf import local_pdf_base_record

    record = local_pdf_base_record(
        raw_value="/tmp/paper.pdf",
        extracted={"title": "Deep Residual Learning for Image Recognition"},
        server_url="http://127.0.0.1:1969",
        fetch_search=_search_returning(
            {
                "title": "Attention Is All You Need",
                "doi": "10.5555/3295222",
                "authors": ["Vaswani, Ashish"],
            }
        ),
    )

    assert record.get("doi") is None
    assert record["title"] == "Deep Residual Learning for Image Recognition"


def test_a_title_search_hit_for_the_same_paper_is_adopted_with_its_item_type() -> None:
    from pzi.capture_local_pdf import local_pdf_base_record

    record = local_pdf_base_record(
        raw_value="/tmp/paper.pdf",
        extracted={"title": "Deep Residual Learning for Image Recognition"},
        server_url="http://127.0.0.1:1969",
        fetch_search=_search_returning(
            {
                "title": "Deep Residual Learning for Image Recognition",
                "doi": "10.1109/CVPR.2016.90",
                "authors": ["He, Kaiming"],
            },
            item_type="conferencePaper",
        ),
    )

    assert record["doi"] == "10.1109/CVPR.2016.90"
    # Without this the entry became `@article` with `journal = {CVPR}`.
    assert record["item_type"] == "conferencePaper"


def test_the_best_search_hit_wins_not_the_first() -> None:
    from pzi.capture_local_pdf import local_pdf_base_record

    def _search(_title, *, server_url):
        return [
            {"record": {"title": "Deep Residual Learning for Image Recognition"},
             "attachments": []},
            {"record": {"title": "Deep Residual Learning for Image Recognition",
                        "doi": "10.1109/CVPR.2016.90", "year": 2016,
                        "authors": ["He, Kaiming"], "venue": "CVPR"},
             "attachments": []},
        ]

    record = local_pdf_base_record(
        raw_value="/tmp/paper.pdf",
        extracted={"title": "Deep Residual Learning for Image Recognition"},
        server_url="http://127.0.0.1:1969",
        fetch_search=_search,
    )

    assert record["doi"] == "10.1109/CVPR.2016.90"
