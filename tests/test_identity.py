from pzi.identity import build_identity_index, extract_identities, find_exact_match


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
