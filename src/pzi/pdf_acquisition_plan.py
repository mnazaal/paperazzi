"""Pure PDF acquisition planning for browser-mediated PDF capture."""

from __future__ import annotations

from collections.abc import Iterable
from urllib.parse import urlencode

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
    timeout, reason = _gateway_timeout(url)
    if reason is not None:
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

# Master table: (hostname regex, path pattern) → timeout_ms
# Path patterns are matched against the URL path (case-insensitive).
# Listed in order of specificity — first match wins.
_GATEWAY_PATTERNS: tuple[tuple[str, str, int], ...] = (
    # -- major publishers ------------------------------------------------------------------
    (r"dl\.acm\.org$",         r"/doi/pdf/",        20000),  # ACM
    (r"sciencedirect\.com$",   r"/pdfft",            15000),  # ScienceDirect
    (r"onlinelibrary\.wiley\.com$", r"/doi/epdf/",   20000),  # Wiley ePDF
    (r"onlinelibrary\.wiley\.com$", r"/doi/pdf/",    20000),  # Wiley PDF
    (r"onlinelibrary\.wiley\.com$", r"/doi/pdfdirect/", 20000),  # Wiley PDF Direct
    (r"tandfonline\.com$",     r"/doi/pdf/",         15000),  # Taylor & Francis
    (r"sagepub\.com$",         r"/doi/pdf/",         15000),  # SAGE
    (r"academic\.oup\.com$",   r"/article-pdf/",     15000),  # Oxford
    (r"academic\.oup\.com$",   r"/pdf/",             15000),  # Oxford (alt)

    # -- generic catch-all: /doi/pdf/ on any host -----------------------------------------
    # Many smaller publishers use the same /doi/pdf/ gateway convention.
    (r".",                     r"/doi/pdf/",         15000),  # generic DOI PDF gateway
    (r".",                     r"/doi/epdf/",        15000),  # generic ePDF gateway
    (r".",                     r"/pdfft",             15000),  # generic /pdfft gateway
    (r".",                     r"/doi/pdfdirect/",   15000),  # generic PDF Direct
)


def _gateway_timeout(url: str) -> tuple[int, str | None]:
    """Return (timeout_ms, reason_string) if *url* is a publisher gateway.

    ``reason_string`` is a short label used for diagnostics/debugging.
    Returns ``(0, None)`` when the URL does not match any known gateway.
    """
    try:
        from urllib.parse import urlsplit
        parts = urlsplit(url)
        hostname = parts.hostname or ""
        path = parts.path.lower() if parts.path else ""
    except ValueError:
        return (0, None)

    import re
    for host_re, path_fragment, timeout_ms in _GATEWAY_PATTERNS:
        if re.search(host_re, hostname) and path_fragment in path:
            return (timeout_ms, f"{host_re}:{path_fragment}")
    return (0, None)
