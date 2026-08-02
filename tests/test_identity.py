from pzi.similarity import build_identity_index, extract_identities, find_exact_match


def test_extract_identities_returns_present_exact_keys() -> None:
    assert extract_identities(
        {
            "doi": "10.1145/3368089.3409741",
            "arxiv_id": "2401.12345",
            "canonical_url": "https://example.com/paper",
        }
    ) == [
        {"kind": "doi", "value": "10.1145/3368089.3409741"},
        {"kind": "arxiv", "value": "2401.12345"},
        {"kind": "url", "value": "https://example.com/paper"},
    ]


def test_extract_identities_skips_empty_values() -> None:
    assert extract_identities(
        {
            "doi": "",
            "arxiv_id": None,
            "canonical_url": "https://example.com/paper",
        }
    ) == [{"kind": "url", "value": "https://example.com/paper"}]


def test_extract_identities_deduplicates_repeated_values_of_same_kind() -> None:
    assert extract_identities(
        {
            "doi": "10.1145/3368089.3409741",
            "canonical_url": "https://example.com/paper",
        }
    ) == [
        {"kind": "doi", "value": "10.1145/3368089.3409741"},
        {"kind": "url", "value": "https://example.com/paper"},
    ]


def test_build_identity_index_groups_record_positions_by_identity() -> None:
    records = [
        {"doi": "10.1000/foo"},
        {"arxiv_id": "2401.12345", "canonical_url": "https://example.com/a"},
        {"doi": "10.1000/foo", "canonical_url": "https://example.com/b"},
    ]

    assert build_identity_index(records) == {
        ("doi", "10.1000/foo"): [0, 2],
        ("arxiv", "2401.12345"): [1],
        ("url", "https://example.com/a"): [1],
        ("url", "https://example.com/b"): [2],
    }


def test_find_exact_match_prefers_first_matching_identity_position() -> None:
    existing_records = [
        {"doi": "10.1000/foo", "canonical_url": "https://example.com/a"},
        {"arxiv_id": "2401.12345"},
    ]

    assert (
        find_exact_match(
            {
                "doi": "10.1000/foo",
                "arxiv_id": "2401.12345",
            },
            existing_records,
        )
        == 0
    )


def test_find_exact_match_returns_none_when_absent() -> None:
    assert (
        find_exact_match(
            {"doi": "10.1000/bar"},
            [{"doi": "10.1000/foo"}, {"canonical_url": "https://example.com/a"}],
        )
        is None
    )


def test_doi_identities_are_normalized_so_variants_are_one_paper() -> None:
    """Case and trailing-slash variants of a DOI must be the same identity.

    extract_identities used the record's DOI verbatim, so a library holding
    `10.1145/abc` did not match an incoming `10.1145/ABC` — the capture became a
    second entry for the same paper.
    """
    from pzi.similarity import extract_identities, find_exact_match

    identities = extract_identities({"doi": "10.1145/ABC"})
    assert identities == [{"kind": "doi", "value": "10.1145/abc"}]

    existing = [{"citekey": "a2024", "doi": "10.1145/abc"}]
    assert find_exact_match({"doi": "10.1145/ABC"}, existing) == 0
    assert find_exact_match({"doi": "10.1145/abc/"}, existing) == 0
    assert find_exact_match({"doi": "https://doi.org/10.1145/ABC"}, existing) == 0
    assert find_exact_match({"doi": "10.1145/different"}, existing) is None


def test_a_value_that_is_not_a_doi_yields_no_doi_identity() -> None:
    """It used to index under its own lowercased text so it "matched itself".

    What actually reaches this is a *placeholder* — `n/a`, `-`, `TBD` — and every
    entry sharing one then shared an identity, so `pzi import` folded two
    unrelated papers into a single entry and reported a duplicate skipped.
    """
    from pzi.similarity import extract_identities

    for value in ("  Not-A-Doi  ", "n/a", "N/A", "-", "TBD", "in press", "10.xxxx/xxxxx"):
        assert extract_identities({"doi": value}) == [], value


def test_arxiv_ids_are_normalized_so_variants_are_one_paper() -> None:
    """Zotero emits `archiveID: "arXiv:2301.12345"`; arXiv itself serves `v2`.

    Compared verbatim, the same paper carried a different identity depending on
    which route captured it: dedupe missed it, and the entry rendered as
    `eprint = {arXiv:2301.12345}` — a citation reading "arXiv:arXiv:2301.12345",
    since `archiveprefix` supplies the prefix.
    """
    from pzi.similarity import find_exact_match

    existing = [{"citekey": "a2023", "arxiv_id": "2301.12345"}]
    for spelling in ("arXiv:2301.12345", "arxiv:2301.12345", "2301.12345v2", "  2301.12345  "):
        assert find_exact_match({"arxiv_id": spelling}, existing) == 0, spelling
    assert find_exact_match({"arxiv_id": "2301.99999"}, existing) is None


def test_an_old_style_arxiv_id_keeps_its_subject_class() -> None:
    from pzi.similarity import extract_identities

    assert extract_identities({"arxiv_id": "arXiv:math.GT/0309136v1"}) == [
        {"kind": "arxiv", "value": "math.GT/0309136"}
    ]
