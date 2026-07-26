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
        {"doi": "10.1/foo"},
        {"arxiv_id": "2401.12345", "canonical_url": "https://example.com/a"},
        {"doi": "10.1/foo", "canonical_url": "https://example.com/b"},
    ]

    assert build_identity_index(records) == {
        ("doi", "10.1/foo"): [0, 2],
        ("arxiv", "2401.12345"): [1],
        ("url", "https://example.com/a"): [1],
        ("url", "https://example.com/b"): [2],
    }


def test_find_exact_match_prefers_first_matching_identity_position() -> None:
    existing_records = [
        {"doi": "10.1/foo", "canonical_url": "https://example.com/a"},
        {"arxiv_id": "2401.12345"},
    ]

    assert (
        find_exact_match(
            {
                "doi": "10.1/foo",
                "arxiv_id": "2401.12345",
            },
            existing_records,
        )
        == 0
    )


def test_find_exact_match_returns_none_when_absent() -> None:
    assert (
        find_exact_match(
            {"doi": "10.1/bar"},
            [{"doi": "10.1/foo"}, {"canonical_url": "https://example.com/a"}],
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


def test_unparseable_doi_still_indexes_consistently() -> None:
    """A stored value normalize_doi cannot parse must still match itself."""
    from pzi.similarity import extract_identities

    assert extract_identities({"doi": "  Not-A-Doi  "}) == [
        {"kind": "doi", "value": "not-a-doi"}
    ]
