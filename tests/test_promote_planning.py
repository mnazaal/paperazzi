"""Tests for `--best-of N` and candidate selection in promote's discovery.

`best_of` counts candidates *good enough to promote*, not answers received.
That distinction is the whole safety argument: the cascade stops only once it
already holds a promotable candidate, so short-circuiting cannot resolve fewer
entries than an exhaustive search would.
"""

from pzi.promote_planning import (
    AcceptanceGate,
    _select_best_published_candidate,
    find_published_candidate_with_diagnostics,
)

#: The gate `promote_service` really applies: `promote_confidence_threshold`
#: (60 by default) and `_MIN_TITLE_SIMILARITY` (85).
_GATE = AcceptanceGate(min_score=60, min_title_similarity=85)

_PREPRINT = {
    "citekey": "smith2024graph",
    "title": "Graph Parsers for Structured Prediction",
    "authors": ["Smith, Jane", "Doe, John"],
    "year": 2024,
    "arxiv_id": "2401.12345",
}

#: Measured with `score_match`: 100/100, comfortably over the gate.
_PUBLISHED = {
    "title": "Graph Parsers for Structured Prediction",
    "authors": ["Jane Smith", "John Doe"],
    "year": 2024,
    "venue": "Proceedings of ACL",
}

#: Measured: 0/0. A real answer from a provider, and not the same paper.
_WRONG_PAPER = {
    "title": "A Totally Unrelated Study of Marine Biology",
    "authors": ["Other, Person"],
    "year": 2019,
    "venue": "Some Journal",
}

_PROVIDER_ORDER = ("crossref", "openalex", "dblp", "openreview", "s2")


def _run(*, best_of=1, gate=_GATE, answers=None):
    """Run discovery with every provider stubbed, recording who was asked.

    The translation server answers with nothing, so the fallback cascade is
    what runs — that is where `best_of` applies.
    """
    answers = answers or {}
    asked: list[str] = []

    def _provider(name):
        def fn(title):
            asked.append(name)
            return answers.get(name)
        return fn

    def _s2(title):
        asked.append("s2")
        return (answers.get("s2"), None)

    result = find_published_candidate_with_diagnostics(
        record=_PREPRINT,
        server_url="http://localhost:1969",
        fetch_search=lambda query, *, server_url: [],
        fetch_crossref=_provider("crossref"),
        fetch_openalex=_provider("openalex"),
        fetch_dblp=_provider("dblp"),
        fetch_openreview=_provider("openreview"),
        fetch_s2=_s2,
        s2_api_key=None,
        best_of=best_of,
        gate=gate,
    )
    return result, asked


def test_best_of_one_stops_at_the_first_acceptable_candidate():
    """The win: a crossref hit means Semantic Scholar is never dialled.

    Keyless S2 is gated at 6 s and retries a 429 twice, so it sets the floor
    for a whole sweep. Skipping it on resolving candidates is most of what
    `--best-of 1` buys.
    """
    result, asked = _run(best_of=1, answers={"crossref": _PUBLISHED})

    assert result["candidate"] is not None
    assert result["candidate"]["venue"] == "Proceedings of ACL"
    assert asked == ["crossref"]


def test_best_of_one_keeps_looking_past_a_candidate_it_could_not_promote():
    """A poor answer is not an answer.

    This is why `best_of` counts *acceptable* candidates. Counting every reply
    would stop here on the wrong paper and report low confidence, while the
    real published version sat one provider further down the cascade.
    """
    result, asked = _run(
        best_of=1, answers={"crossref": _WRONG_PAPER, "openalex": _PUBLISHED}
    )

    assert result["candidate"]["venue"] == "Proceedings of ACL"
    assert asked == ["crossref", "openalex"]


def test_a_run_that_never_finds_an_acceptable_candidate_asks_everyone():
    """No short-circuit without a hit, so a negative still costs a full sweep.

    The unacceptable candidate is still handed back rather than dropped: the
    caller applies the same gate and needs to name the paper it rejected, which
    is the difference between "nothing found" and "found the wrong paper".
    """
    result, asked = _run(best_of=1, answers={"crossref": _WRONG_PAPER})

    assert result["candidate"]["title"] == _WRONG_PAPER["title"]
    assert asked == list(_PROVIDER_ORDER)


def test_best_of_five_asks_every_provider_even_after_a_hit():
    """`N=5` reproduces the exhaustive behaviour that predated the flag."""
    result, asked = _run(best_of=5, answers={"crossref": _PUBLISHED})

    assert result["candidate"] is not None
    assert asked == list(_PROVIDER_ORDER)


def test_without_a_gate_nothing_can_be_counted_so_everyone_is_asked():
    """Callers that pass no gate get the old exhaustive search, not an empty one."""
    _, asked = _run(best_of=1, gate=None, answers={"crossref": _PUBLISHED})

    assert asked == list(_PROVIDER_ORDER)


class _StubGate:
    """Accepts exactly the candidates named, whatever they score.

    Used so the selection test states the situation directly rather than
    reverse-engineering candidates that land either side of `score_match`'s
    arithmetic — which would pin the fix to numbers it does not depend on.
    """

    def __init__(self, *accepted_titles):
        self._accepted = set(accepted_titles)

    def accepts(self, preprint, candidate):
        return candidate.get("title") in self._accepted


def test_selection_prefers_a_candidate_the_caller_will_accept():
    """Ranking and acceptance are different numbers, and could disagree.

    `_score_published_candidate` adds up to five points of completeness bonuses
    over the raw score the gate tests, so a candidate below the threshold could
    out-rank one above it, win selection, and then be rejected — reporting "low
    confidence" while a promotable candidate sat in the same list.
    """
    ranks_high_but_rejected = {
        "title": "Graph Parsers for Structured Prediction",
        "authors": ["Jane Smith", "John Doe"],
        "year": 2024,
        "venue": "Proceedings of ACL",
        "doi": "10.1000/real",   # +2
        "pdf_url": "https://example.org/p.pdf",  # +1
    }
    ranks_low_but_accepted = {
        "title": "Graph Parsers for Structured Prediction (Extended)",
        "authors": ["Jane Smith", "John Doe"],
        "year": 2024,
        "venue": "Proceedings of ACL",
    }
    candidates = [ranks_high_but_rejected, ranks_low_but_accepted]

    # Without a gate, the bonuses decide.
    assert _select_best_published_candidate(_PREPRINT, candidates) is ranks_high_but_rejected

    chosen = _select_best_published_candidate(
        _PREPRINT, candidates, _StubGate("Graph Parsers for Structured Prediction (Extended)")
    )

    assert chosen is ranks_low_but_accepted


def test_selection_falls_back_to_ranking_when_nothing_is_acceptable():
    """Narrowing must not turn "the best of a bad lot" into "nothing found".

    The caller still needs a candidate to report as low-confidence, so the
    diagnostics can say which paper was rejected and why.
    """
    candidates = [dict(_PUBLISHED), dict(_WRONG_PAPER)]

    chosen = _select_best_published_candidate(
        _PREPRINT, candidates, _StubGate()  # accepts nothing
    )

    assert chosen is not None
    assert chosen["title"] == _PUBLISHED["title"]
