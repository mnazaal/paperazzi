

def test_a_rate_limit_on_the_title_search_path_is_reported() -> None:
    """The guard returned one line before the reporter could read the body.

    `if not isinstance(data, dict) or "error" in data or "message" in data:
    return None` ran *before* `_s2_data_error`, so the reporting branch was
    unreachable for exactly the bodies it was written to catch. This is the
    title-search path, which is what `check` uses — so an S2 rate limit was
    invisible there while the by-DOI path reported it correctly.
    """
    from pzi.metadata_sources import fetch_semantic_scholar_record_by_title

    errors: list[str] = []
    record = fetch_semantic_scholar_record_by_title(
        "Attention Is All You Need",
        fetch_text=lambda url, **_kw: '{"message": "Too Many Requests"}',
        errors=errors,
    )

    assert record is None
    assert errors == ["semantic-scholar: Too Many Requests"]


def test_every_s2_entry_point_reads_both_error_keys() -> None:
    """S2 uses `error` and `message`; one reader handled only `error`."""
    from pzi.metadata_sources import (
        fetch_semantic_scholar_record,
        fetch_semantic_scholar_record_by_title,
        fetch_semantic_scholar_record_by_title_with_error,
    )

    for key in ("error", "message"):
        body = f'{{"{key}": "quota exceeded"}}'

        by_doi_errors: list[str] = []
        fetch_semantic_scholar_record(
            "10.1/x", fetch_text=lambda url, **_kw: body, errors=by_doi_errors
        )
        by_title_errors: list[str] = []
        fetch_semantic_scholar_record_by_title(
            "A Paper", fetch_text=lambda url, **_kw: body, errors=by_title_errors
        )
        _record, tuple_error = fetch_semantic_scholar_record_by_title_with_error(
            "A Paper", fetch_text=lambda url, **_kw: body
        )

        assert by_doi_errors == ["semantic-scholar: quota exceeded"], key
        assert by_title_errors == ["semantic-scholar: quota exceeded"], key
        assert tuple_error == "quota exceeded", key


def test_every_public_api_fetcher_reports_the_failure_it_swallowed() -> None:
    """A permanently broken provider must not look like a paper nobody indexed.

    `_api_json` records a provider failure only when it is handed an `errors`
    list, and three of its callers — the Crossref, DOAJ and Europe PMC PDF-URL
    lookups — did not hand it one. All three return `None` on a 500 and on a
    working provider that has nothing, so a Europe PMC outage was indistinct
    from "this paper has no open-access copy".

    Spans every public caller rather than the three that were wrong, and asserts
    the roster is complete, so the next fetcher added is caught here.
    """
    import ast
    import inspect
    import urllib.error

    from pzi import metadata_sources

    def failing_fetch(_url: str, **_kwargs) -> str:
        raise urllib.error.HTTPError(
            "http://x", 503, "Service Unavailable", {}, None  # type: ignore[arg-type]
        )

    # name -> the one positional argument it takes (a DOI or a title).
    callers: dict[str, str] = {
        "fetch_crossref_record": "10.1234/x",
        "fetch_crossref_record_by_title": "Attention Is All You Need",
        "fetch_crossref_pdf_url": "10.1234/x",
        "fetch_openalex_record": "10.1234/x",
        "fetch_openalex_record_by_title": "Attention Is All You Need",
        "fetch_semantic_scholar_record": "10.1234/x",
        "fetch_semantic_scholar_record_by_title": "Attention Is All You Need",
        "fetch_dblp_record_by_title": "Attention Is All You Need",
        "fetch_openreview_record_by_title": "Attention Is All You Need",
        "fetch_doaj_pdf_url": "10.1234/x",
        "fetch_europepmc_pdf_url": "10.1234/x",
    }
    #: `probe_s2_api` asks *whether* S2 answers and returns that as its result,
    #: so the failure is its return value, not something it could swallow.
    exempt = {"probe_s2_api"}

    tree = ast.parse(inspect.getsource(metadata_sources))
    reaches_api_json = {
        node.name
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and not node.name.startswith("_")
        and any(
            isinstance(call, ast.Call)
            and isinstance(call.func, ast.Name)
            and call.func.id == "_api_json"
            for call in ast.walk(node)
        )
    }
    assert reaches_api_json - exempt == set(callers), (
        "a public `_api_json` caller is not covered here: "
        f"{reaches_api_json - exempt - set(callers)}"
    )

    for name, argument in callers.items():
        errors: list[str] = []
        result = getattr(metadata_sources, name)(
            argument, fetch_text=failing_fetch, errors=errors
        )
        assert result is None, name
        assert errors == ["HTTP 503"], f"{name} swallowed its provider failure"
