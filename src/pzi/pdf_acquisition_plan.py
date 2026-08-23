"""Pure PDF acquisition planning for browser-mediated PDF capture.

Sole caller: ``_maybe_add_pdf_request`` in :mod:`pzi.http_post_routes`, on the
``POST /capture`` path, when a capture succeeded but the server could not
fetch the PDF itself — the resulting plan is handed to the browser extension
to execute in the user's logged-in session.

The single caller is deliberate rather than incidental: the publisher gateway
table below is policy that changes whenever a publisher moves a URL, and
keeping it out of the route handler is what lets
``tests/test_pdf_acquisition_plan.py`` exercise every publisher pattern
directly — no HTTP request, body parsing, or auth in the way. Inlining it
would bury ~230 lines of matching rules in an 860-line routing module and
turn each new publisher into a route-level test.

Tiered CORE (see ``tests/test_layer_boundaries.py``) because it imports
nothing from pzi at all; that its only consumer today is a front-end module
does not make it front-end code.
"""

from __future__ import annotations

from collections.abc import Iterable
from urllib.parse import urlencode, urlsplit

CandidatePlan = dict[str, object]
AcquisitionPlan = dict[str, object]


def classify_pdf_candidate(url: str, *, page_url: str) -> CandidatePlan:
    """Classify one URL into a browser acquisition method.

    Pure: no network, no filesystem, no global state.
    """
    clean_url = url.strip()
    clean_page_url = page_url.strip()

    if _is_ieee_stamp_url(clean_url):
        return {
            "url": clean_url,
            "kind": "pdf_gateway",
            "method": "navigate_monitor",
            "referrer": clean_page_url,
            "requires_navigation": True,
            "timeout_ms": 15000,
        }

    if _is_ieee_article_url(clean_url):
        return {
            "url": clean_url,
            "kind": "article_page",
            "method": "discover_from_page",
            "referrer": clean_page_url,
            "requires_navigation": False,
            "timeout_ms": 10000,
        }

    # Publisher-specific PDF gateways — HTML pages that JS-redirect to PDF.
    gateway = _maybe_publisher_gateway(clean_url, clean_page_url)
    if gateway is not None:
        return gateway

    if _looks_like_direct_pdf(clean_url):
        return {
            "url": clean_url,
            "kind": "direct_pdf",
            "method": "direct_fetch",
            "referrer": clean_page_url,
            "requires_navigation": False,
            "timeout_ms": 10000,
        }

    return {
        "url": clean_url,
        "kind": "article_page",
        "method": "discover_from_page",
        "referrer": clean_page_url,
        "requires_navigation": False,
        "timeout_ms": 10000,
    }


def _maybe_publisher_gateway(url: str, referrer: str) -> CandidatePlan | None:
    """Detect publisher PDF gateway pages and return a candidate plan.

    Gateway pages serve HTML that redirects to the real PDF after
    JavaScript execution in a logged-in browser session.
    """
    timeout = _gateway_timeout(url)
    if timeout is not None:
        return {
            "url": url,
            "kind": "pdf_gateway",
            "method": "navigate_monitor",
            "referrer": referrer,
            "requires_navigation": True,
            "timeout_ms": timeout,
        }
    return None


def build_pdf_acquisition_plan(
    *,
    citekey: str,
    bib: str | None,
    page_url: str,
    pdf_urls: Iterable[str],
    attach_base_url: str,
    request_id: str,
    attach_token: str,
) -> AcquisitionPlan | None:
    """Build extension-executable PDF acquisition plan.

    Pure contract: caller supplies IDs/tokens. This function only normalizes,
    classifies, orders, and serializes plan data.
    """
    candidates = [
        classify_pdf_candidate(url, page_url=page_url)
        for url in _unique_nonempty(pdf_urls)
    ]
    if not candidates:
        return None

    return {
        "request_id": request_id,
        "citekey": citekey,
        "bib": bib,
        "attach": {
            "url": _attach_url(
                attach_base_url,
                request_id=request_id,
                citekey=citekey,
                bib=bib,
            ),
            "token": attach_token,
        },
        "candidates": sorted(candidates, key=_candidate_sort_key),
    }


def _unique_nonempty(urls: Iterable[str]) -> tuple[str, ...]:
    """Deliberately *not* shared with :func:`pzi.url_safety.unique_nonempty`.

    That is the copy every other caller uses. This module is tiered CORE
    precisely because it imports nothing from pzi (see the module docstring and
    ``tests/test_layer_boundaries.py``), so importing the shared one to save
    nine lines would trade a documented architectural property for a
    deduplication. The other three duplicates of this helper *were* merged.
    """
    seen: set[str] = set()
    result: list[str] = []
    for url in urls:
        clean_url = url.strip()
        if not clean_url or clean_url in seen:
            continue
        seen.add(clean_url)
        result.append(clean_url)
    return tuple(result)


def _attach_url(
    base_url: str,
    *,
    request_id: str,
    citekey: str,
    bib: str | None,
) -> str:
    params = [("request_id", request_id), ("citekey", citekey)]
    if bib is not None:
        params.append(("bib", bib))
    separator = "&" if "?" in base_url else "?"
    return f"{base_url}{separator}{urlencode(params)}"


def _candidate_sort_key(candidate: CandidatePlan) -> tuple[int, str]:
    priority = {
        "pdf_gateway": 0,
        "direct_pdf": 1,
        "article_page": 2,
    }
    return (priority.get(str(candidate["kind"]), 99), str(candidate["url"]))


def _is_ieee_stamp_url(url: str) -> bool:
    return "ieeexplore.ieee.org/stamp/stamp.jsp" in url


def _is_ieee_article_url(url: str) -> bool:
    return "ieeexplore.ieee.org/document/" in url


def _looks_like_direct_pdf(url: str) -> bool:
    lower_url = url.lower()
    return lower_url.endswith(".pdf") or ".pdf?" in lower_url


# ── Publisher gateway detection ────────────────────────────────────────────
# Each publisher has a known URL pattern for its PDF gateway page:
# the HTML page that, when loaded in a logged-in browser, serves or
# redirects to the actual PDF.  This is *not* the same as a direct PDF
# URL — the gateway requires browser navigation + JS execution.

# Master table: (base domain, path pattern) → timeout_ms
# Path patterns are matched against the URL path (case-insensitive).
# ``None`` as the domain is the generic catch-all (any host).
# Listed in order of specificity — first match wins.  A publisher row is
# only worth its line when its timeout differs from the generic row that
# would otherwise match the same path; ScienceDirect, Taylor & Francis and
# SAGE each named a timeout the catch-all already gave them.
_GATEWAY_PATTERNS: tuple[tuple[str | None, str, int], ...] = (
    # -- major publishers ------------------------------------------------------------------
    ("dl.acm.org",            r"/doi/pdf/",        20000),  # ACM
    ("onlinelibrary.wiley.com", r"/doi/epdf/",      20000),  # Wiley ePDF
    ("onlinelibrary.wiley.com", r"/doi/pdf/",       20000),  # Wiley PDF
    ("onlinelibrary.wiley.com", r"/doi/pdfdirect/", 20000),  # Wiley PDF Direct
    ("academic.oup.com",      r"/article-pdf/",     15000),  # Oxford
    ("academic.oup.com",      r"/pdf/",             15000),  # Oxford (alt)

    # -- generic catch-all: /doi/pdf/ on any host -----------------------------------------
    # Many smaller publishers use the same /doi/pdf/ gateway convention.
    (None,                    r"/doi/pdf/",         15000),  # generic DOI PDF gateway
    (None,                    r"/doi/epdf/",        15000),  # generic ePDF gateway
    (None,                    r"/pdfft",             15000),  # generic /pdfft gateway
    (None,                    r"/doi/pdfdirect/",   15000),  # generic PDF Direct
)


def _host_matches_domain(hostname: str, domain: str) -> bool:
    """True if *hostname* is exactly *domain* or a subdomain of it.

    A substring/suffix check alone (e.g. ``hostname.endswith(domain)``) would
    also match an unrelated lookalike host like ``evilsciencedirect.com`` for
    domain ``sciencedirect.com`` — there's no ``.`` requiring a real
    subdomain boundary. This enforces that boundary.
    """
    return hostname == domain or hostname.endswith("." + domain)


def _gateway_timeout(url: str) -> int | None:
    """Navigation timeout for *url*, or None when it is not a known gateway."""
    try:
        parts = urlsplit(url)
        hostname = parts.hostname or ""
        path = parts.path.lower() if parts.path else ""
    except ValueError:
        return None

    for domain, path_fragment, timeout_ms in _GATEWAY_PATTERNS:
        host_ok = domain is None or _host_matches_domain(hostname, domain)
        if host_ok and path_fragment in path:
            return timeout_ms
    return None
