"""The shared title-search provider cascade: crossref -> openalex -> dblp -> openreview.

`check_service` (``library check``) and `promote_planning` (``update
--promote``) each walk this same four-provider cascade before falling back to
Semantic Scholar. It used to be hand-copied into both modules; a provider
added, reordered, or dropped in one silently disagreed with the other, and
both consumers are hours-long commands where a diverged provider list is
expensive to run and invisible until read closely. Defining it once here is
what makes that impossible instead of merely unlikely.

Semantic Scholar is deliberately *not* in this table: it needs an
error-returning fetcher (`fetch_semantic_scholar_record_by_title_with_error`
in `promote_planning`, plain-return in `check_service`) and its own breaker
wiring in each caller, so it stays local rather than forced into a shape it
does not fit.

Import-safety: this module only imports from `pzi.metadata_sources` and
`pzi.protocols`, both leaves with no dependency back on `check_service`,
`promote_planning`, or `promote_service` — so either consumer can import this
without risking a cycle, and neither drags the other's (or a service-layer)
imports in through it.
"""

from __future__ import annotations

from pzi.metadata_sources import (
    fetch_crossref_record_by_title,
    fetch_dblp_record_by_title,
    fetch_openalex_record_by_title,
    fetch_openreview_record_by_title,
)
from pzi.protocols import MetadataRecordFetcher

#: (name, base fetcher) pairs, in cascade order: polite-pool DOI sources first,
#: then the CS/ML authorities that confirm proceedings versions the DOI-based
#: sources leave unresolved. Order matters — it is also the tie-break in
#: `promote_planning._select_best_published_candidate` — so this is the one
#: place it is decided.
TITLE_SEARCH_PROVIDERS: tuple[tuple[str, MetadataRecordFetcher], ...] = (
    ("crossref", fetch_crossref_record_by_title),
    ("openalex", fetch_openalex_record_by_title),
    ("dblp", fetch_dblp_record_by_title),
    ("openreview", fetch_openreview_record_by_title),
)
