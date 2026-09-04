"""Tests for the negative-lookup ledger behind `update --promote`."""

from datetime import UTC, datetime, timedelta

from pzi import ledger

_NOW = datetime(2026, 8, 24, 12, 0, 0, tzinfo=UTC)


def _state_with(citekey: str, checked_at: datetime, bib: str = "ml"):
    return ledger.record_checked({}, bib, citekey, now=checked_at)


def test_a_freshly_recorded_citekey_is_recently_checked():
    state = _state_with("yeh-decoupled-2021", _NOW)

    assert ledger.is_recently_checked(
        state, "ml", "yeh-decoupled-2021", now=_NOW, horizon_days=30
    )


def test_the_horizon_boundary_is_exclusive_on_both_sides():
    """A day inside the horizon is skipped; a day outside it is re-checked.

    Pinned on both sides of the same boundary, because an off-by-one here is
    invisible: it just means the sweep silently re-asks (or silently does not).
    """
    state = _state_with("yeh-decoupled-2021", _NOW - timedelta(days=30))

    just_inside = _NOW - timedelta(hours=1)
    assert ledger.is_recently_checked(
        state, "ml", "yeh-decoupled-2021", now=just_inside, horizon_days=30
    )
    assert not ledger.is_recently_checked(
        state, "ml", "yeh-decoupled-2021", now=_NOW, horizon_days=30
    )


def test_an_unknown_citekey_or_bib_is_not_recently_checked():
    state = _state_with("yeh-decoupled-2021", _NOW)

    assert not ledger.is_recently_checked(
        state, "ml", "someone-else-2020", now=_NOW, horizon_days=30
    )
    # Same citekey, different bib: the ledger is keyed per bib, so two libraries
    # holding the same preprint are tracked independently.
    assert not ledger.is_recently_checked(
        state, "other", "yeh-decoupled-2021", now=_NOW, horizon_days=30
    )


def test_a_zero_horizon_disables_the_ledger():
    """`0` means off, so a recorded entry stops suppressing anything."""
    state = _state_with("yeh-decoupled-2021", _NOW)

    assert not ledger.is_enabled(0)
    assert not ledger.is_recently_checked(
        state, "ml", "yeh-decoupled-2021", now=_NOW, horizon_days=0
    )


def test_record_checked_does_not_mutate_its_input():
    """Purity is the reason a run can accumulate negatives and persist once."""
    state = _state_with("first-2020", _NOW)
    before = {"version": state["version"], "bibs": {"ml": dict(state["bibs"]["ml"])}}

    ledger.record_checked(state, "ml", "second-2021", now=_NOW)

    assert state == before


def test_record_checked_keeps_other_bibs_and_other_citekeys():
    state = _state_with("first-2020", _NOW)
    state = ledger.record_checked(state, "other", "third-2019", now=_NOW)

    state = ledger.record_checked(state, "ml", "second-2021", now=_NOW)

    assert sorted(state["bibs"]["ml"]) == ["first-2020", "second-2021"]
    assert sorted(state["bibs"]["other"]) == ["third-2019"]


def test_prune_drops_expired_entries_and_the_bibs_they_emptied():
    """What bounds the file: an expired entry would be re-checked anyway."""
    state = _state_with("stale-2019", _NOW - timedelta(days=90))
    state = ledger.record_checked(state, "ml", "fresh-2026", now=_NOW)
    state = ledger.record_checked(
        state, "archive", "also-stale-2018", now=_NOW - timedelta(days=90)
    )

    pruned = ledger.prune(state, now=_NOW, horizon_days=30)

    assert pruned["bibs"] == {"ml": {"fresh-2026": pruned["bibs"]["ml"]["fresh-2026"]}}
    assert "archive" not in pruned["bibs"]


def test_prune_leaves_state_alone_when_the_ledger_is_disabled():
    """Turning the horizon off must not be a way to silently erase the file."""
    state = _state_with("stale-2019", _NOW - timedelta(days=900))

    assert ledger.prune(state, now=_NOW, horizon_days=0) == state


def test_a_round_trip_through_disk_preserves_the_decision(tmp_path):
    path = ledger.ledger_path(tmp_path, ledger.PROMOTE_FILENAME)
    state = _state_with("yeh-decoupled-2021", _NOW)

    ledger.save(path, state)

    assert path.name == "promote-checked.json"
    assert ledger.is_recently_checked(
        ledger.load(path), "ml", "yeh-decoupled-2021",
        now=_NOW, horizon_days=30,
    )


def test_save_creates_the_data_home_directory(tmp_path):
    path = ledger.ledger_path(tmp_path / "does" / "not" / "exist", ledger.PROMOTE_FILENAME)

    ledger.save(path, _state_with("yeh-decoupled-2021", _NOW))

    assert ledger.load(path)["bibs"]["ml"]


def test_a_missing_ledger_reads_as_empty(tmp_path):
    assert ledger.load(tmp_path / "nothing-here.json") == {}


def test_corrupt_or_foreign_state_reads_as_empty_rather_than_raising(tmp_path):
    """A malformed cache must cost one redundant sweep, never a failed run."""
    path = tmp_path / "promote-checked.json"

    path.write_text("{not json at all", encoding="utf-8")
    assert ledger.load(path) == {}

    path.write_text('["a", "list"]', encoding="utf-8")
    assert ledger.load(path) == {}

    # A version this code does not know is discarded, not migrated.
    path.write_text('{"version": 99, "bibs": {"ml": {"x-2020": "2026-08-24"}}}',
                    encoding="utf-8")
    assert ledger.load(path) == {}


def test_a_mangled_timestamp_means_not_checked():
    """Hand-editable implies hand-manglable; the entry is simply looked up again."""
    for value in ("yesterday", "", 7, None, []):
        state = {"version": 1, "bibs": {"ml": {"x-2020": value}}}
        assert not ledger.is_recently_checked(
            state, "ml", "x-2020", now=_NOW, horizon_days=30
        )


def test_a_naive_timestamp_is_read_as_utc():
    """Rejecting it would silently re-check that entry on every future sweep."""
    state = {"version": 1, "bibs": {"ml": {"x-2020": "2026-08-24T11:00:00"}}}

    assert ledger.is_recently_checked(
        state, "ml", "x-2020", now=_NOW, horizon_days=30
    )


def test_a_timestamp_in_the_future_counts_as_fresh():
    """Clock skew must not defeat the horizon it exists to enforce."""
    state = _state_with("x-2020", _NOW + timedelta(days=5))

    assert ledger.is_recently_checked(
        state, "ml", "x-2020", now=_NOW, horizon_days=30
    )
