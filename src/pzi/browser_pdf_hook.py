#!/usr/bin/env python3
"""Playwright-based PDF discovery and download hook for pzi.

Supports Firefox and Chromium-based browsers with profile reuse.

Auto-installs browser binaries on first use if missing.

Invoked internally by pzi via the browser_pdf_cmd config option.
Normally run as: python -m pzi.browser_pdf_hook [--profile PATH] [--browser chromium|firefox]

Reads JSON on stdin:
  {"page_url": "...", "doi": "..."}  # discover PDF URL
  {"action": "download_pdf", "pdf_url": "..."}  # download PDF bytes

Writes JSON on stdout on success:
  {"pdf_url": "https://...pdf"}  # for discovery
  {"pdf_base64": "..."}  # for download (base64-encoded PDF)

Writes {} on stdout when no PDF URL is discovered or download fails.
"""

from __future__ import annotations

import argparse
import base64
import ipaddress
import json
import re
import subprocess
import sys
import time
from collections.abc import Callable
from typing import Any
from urllib.parse import urljoin

from pzi.browser_session import (
    PLAYWRIGHT_INSTALL_HINT,
    BrowserSession,
    FetchResult,
    default_url_allowed,
    launch_browser,
)
from pzi.safe_http import SsrfBlocked

PDF_HINT_RE = re.compile(r"pdf|download", re.IGNORECASE)

DISCOVERY_JS = r"""
() => {
  // A Set keeps the read linear in the links: `out.includes` made it
  // quadratic, 11.6 s for 40000 links in Chromium (review-st1). A Set
  // iterates in insertion order, so first occurrences keep their order.
  const out = new Set();
  const add = (value) => {
    if (typeof value !== 'string') return;
    const trimmed = value.trim();
    if (!trimmed) return;
    if (!/^https?:\/\//i.test(trimmed)) return;
    out.add(trimmed);
  };

  document
    .querySelectorAll(
      'meta[name="citation_pdf_url"], meta[name="wkhealth_pdf_url"]'
    )
    .forEach((node) => add(node.getAttribute('content')));

  document
    .querySelectorAll('a[href], link[href], iframe[src], embed[src]')
    .forEach((node) => {
      add(
        node.href
        || node.src
        || node.getAttribute('href')
        || node.getAttribute('src')
      );
    });

  return Array.from(out);
}
"""

POST_CLICK_JS = r"""
() => Array.from(
  document.querySelectorAll('a[href], iframe[src], embed[src]')
)
  .map((node) => (
    node.href
    || node.src
    || node.getAttribute('href')
    || node.getAttribute('src')
  ))
  .filter(Boolean)
"""

#: How many PDF candidate links to follow from one page. The list is produced
#: by JavaScript running in the fetched page, so the page chooses its length;
#: each entry costs a navigation with a 30s timeout, taken while the server's
#: single browser lock is held.
MAX_PDF_CANDIDATES = 10
#: Wall-clock bound on the whole candidate sweep, so ten slow navigations
#: cannot add up to five minutes of held lock either.
CANDIDATE_DEADLINE_SECONDS = 60.0

#: Discovery's deadline, from before its navigation: `BrowserSession.navigate`'s
#: default timeout. Everything discovery waits for after the navigation (the
#: network-idle waits, the click's download wait, the link reads) is bounded
#: by what is left of it, so a page cannot hold the session's lock longer.
DISCOVERY_TIMEOUT_SECONDS = 30.0
#: Time a link read keeps when the deadline has passed or is close: a page
#: that arrived in time has its links read, as at e190718. Reading 15000
#: links took 1.3-2.5 s in Chromium; a 1 s floor lost such a page's PDF
#: link (review-nd10 `probe_disc_big chromium 27 15000 1`).
_LINK_READ_FLOOR_SECONDS = 5.0
#: Longest single network-idle wait, as `BrowserSession.wait_network_idle`.
_NETWORK_IDLE_MS = 5000

DOWNLOAD_CANDIDATES_JS = r"""
() => {
  const out = [];
  document.querySelectorAll('a[href]').forEach((node) => {
    const href = node.href || node.getAttribute('href');
    if (href && href.toLowerCase().includes('.pdf')) {
      out.push(href);
    }
  });
  return out;
}
"""

COOKIE_BANNER_SELECTORS = [
    "button:has-text('Accept')",
    "button:has-text('I agree')",
    "button:has-text('Allow all')",
    "button:has-text('Continue')",
]

DOWNLOADISH_SELECTORS = [
    "a:has-text('PDF')",
    "a:has-text('Download PDF')",
    "button:has-text('PDF')",
    "button:has-text('Download PDF')",
]

HookRequest = tuple[str, str]


def parse_hook_request(payload: Any) -> HookRequest | None:
    """Return normalized hook action and URL from decoded JSON payload."""
    if not isinstance(payload, dict):
        return None

    action = payload.get("action", "discover")
    if action == "download_pdf":
        raw_url = payload.get("pdf_url")
        if isinstance(raw_url, str) and raw_url.strip():
            return "download_pdf", raw_url.strip()
        return None

    raw_url = payload.get("page_url")
    if isinstance(raw_url, str) and raw_url.strip():
        return "discover", raw_url.strip()
    return None


def encode_hook_response(
    *,
    pdf_url: str | None = None,
    pdf_bytes: bytes | None = None,
) -> str:
    """Encode hook stdout response without side effects."""
    if pdf_bytes:
        return json.dumps({"pdf_base64": base64.b64encode(pdf_bytes).decode()})
    if pdf_url:
        return json.dumps({"pdf_url": pdf_url})
    return "{}"


def _ensure_browser(browser: str = "firefox") -> bool:  # pragma: no cover — system I/O
    """Ensure Playwright package is importable and browser binaries exist.

    Returns True if ready to use, False otherwise.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print(
            "playwright package not found. " + PLAYWRIGHT_INSTALL_HINT,
            file=sys.stderr,
        )
        return False

    # Check if browser binaries are installed by trying to launch.
    playwright = None
    try:
        playwright = sync_playwright().start()
        if browser == "firefox":
            playwright.firefox.launch(headless=True).close()
        else:
            playwright.chromium.launch(headless=True).close()
        return True
    except Exception:
        # Browser binaries not installed, try to install them
        print(f"Installing {browser} browser binaries...", file=sys.stderr)
    finally:
        if playwright is not None:
            try:
                playwright.stop()
            except Exception:
                pass

    try:
        subprocess.run(
            [sys.executable, "-m", "playwright", "install", browser],
            check=True,
            capture_output=True,
        )
        print(f"{browser} browser binaries installed.", file=sys.stderr)
        return True
    except subprocess.CalledProcessError:
        print(
            f"Failed to install {browser} browser binaries.",
            file=sys.stderr,
        )
        print("Run 'playwright install chromium' manually.", file=sys.stderr)
        return False


def main() -> int:  # pragma: no cover — CLI entry point
    parser = argparse.ArgumentParser(description="pzi browser PDF hook")
    parser.add_argument(
        "--profile",
        type=str,
        default=None,
        help="Path to browser profile directory (uses cookies/session from your browser)",
    )
    parser.add_argument(
        "--browser",
        type=str,
        choices=["firefox", "chromium", "chrome"],
        default="chromium",
        help="Browser engine to use (default: chromium)",
    )
    parser.add_argument(
        "--headful",
        action="store_true",
        help="open a visible browser window for sites that require human verification",
    )
    parser.add_argument(
        "--challenge-timeout",
        type=int,
        default=0,
        metavar="SECONDS",
        help="wait up to SECONDS for browser verification before giving up",
    )
    args = parser.parse_args()

    # Ensure browser binaries are installed
    if not _ensure_browser(args.browser):
        print("{}")
        return 1

    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError:
        print("{}")
        return 0

    request = parse_hook_request(payload)
    if request is None:
        print("{}")
        return 0

    action, url = request
    if action == "download_pdf":
        pdf_bytes = download_pdf(
            url,
            browser=args.browser,
            profile_path=args.profile,
            headless=not args.headful,
            challenge_timeout=args.challenge_timeout,
        )
        if pdf_bytes is None and args.headful:
            print(
                "browser PDF hook: visible browser fallback did not obtain a PDF. "
                "If a verification page appeared, complete it before the timeout; "
                "otherwise configure browser_pdf_cmd with your regular browser profile.",
                file=sys.stderr,
            )
        print(encode_hook_response(pdf_bytes=pdf_bytes))
        return 0

    # Default: discover PDF URL
    discover_errors: list[str] = []
    pdf_url = discover_pdf_url(
        url,
        browser=args.browser,
        profile_path=args.profile,
        headless=not args.headful,
        errors=discover_errors,
    )
    if pdf_url is None and discover_errors:
        # The stage broke rather than genuinely finding nothing — worth a line
        # on stderr since the caller (browser_pdf.discover_pdf_url_with_browser)
        # only sees the JSON `{}` on stdout, same as a clean "no PDF" result.
        for detail in discover_errors:
            print(f"browser PDF hook: {detail}", file=sys.stderr)
    print(encode_hook_response(pdf_url=pdf_url))
    return 0


#: `discover_pdf_url`'s read of a page whose links could not be read.
_UNREAD = object()


def discover_pdf_url(
    page_url: str,
    *,
    browser: str = "chromium",
    profile_path: str | None = None,
    _dismiss=None,
    _click=None,
    _resolve=None,
    _session: BrowserSession | None = None,
    headless: bool = True,
    url_allowed: Callable[[str], bool] | None = None,
    errors: list[str] | None = None,
) -> str | None:
    """Discover PDF URL from a page using browser.

    All I/O edges are injectable via _session for testing, including the
    URL predicate — see :class:`BrowserSession.url_allowed` for why that is a
    parameter and not a config key.

    *errors* collects why this stage produced nothing, the same shape as
    ``server_browser.discover_via_server_api``. Without it, ``RuntimeError``
    from ``BrowserSession._check_open`` — raised when the underlying browser
    died mid-operation (crash, OOM, the session manager tearing it down under
    us) — fell into the blanket ``except Exception`` below and came back
    identical to "this page has no PDF". That is the same silent-failure shape
    tier 1 fixed for a dead capture server in ``discover_via_server_api`` (A6):
    a caller that cares (``_handle_browser_discover_post``) can now tell
    "broken" apart from "nothing found".
    """
    close_on_exit = _session is None
    if _session is not None:  # pragma: no branch
        session = _session
    else:  # pragma: no cover
        session = launch_browser(
            browser,
            profile_path,
            headless=headless,
            url_allowed=url_allowed or default_url_allowed,
        )

    dismiss_fn = _dismiss or _dismiss_cookie_banners
    click_fn = _click or _click_downloadish_links
    resolve_fn = _resolve or resolve_pdf_candidate_urls

    deadline = time.monotonic() + DISCOVERY_TIMEOUT_SECONDS

    def left_ms() -> int:
        return round((deadline - time.monotonic()) * 1000)

    def wait_network_idle() -> None:
        # Never 0: Playwright reads a 0 timeout as none at all.
        if left_ms() > 0:
            session.wait_network_idle(timeout=min(_NETWORK_IDLE_MS, left_ms()))

    def read_links(js: str) -> Any:
        """The page's links, or `_UNREAD` once recorded why they were not read.

        `Page.evaluate` has no timeout, and a page whose script spins never
        answers: discovery hung with the session's lock held (review-nd6
        `probe_close busy`). The read keeps a floor past the deadline, so a
        large page that arrived in time is still read. The page is left, and
        a spinning one replaced, on return.
        """
        try:
            return session.evaluate(
                js, timeout=max(left_ms() / 1000, _LINK_READ_FLOOR_SECONDS)
            )
        except RuntimeError:
            raise
        except Exception as exc:
            if errors is not None:
                errors.append(f"discovery links not read: {_describe_failure(exc, page_url)}")
            return _UNREAD

    def note_cut() -> None:
        if errors is not None:
            cut = TimeoutError(f"Timeout {round(DISCOVERY_TIMEOUT_SECONDS * 1000)}ms exceeded")
            errors.append(f"discovery cut at its deadline: {_describe_failure(cut, page_url)}")

    try:
        if session.navigate(page_url, timeout=round(DISCOVERY_TIMEOUT_SECONDS * 1000)) is None:
            # A same-document navigation (only the fragment differs, or the
            # page already `pushState`d to this URL) loads nothing: the page is
            # still the earlier request's, and its links answered paper A's PDF
            # for paper B (review of `8441cab`).
            return None
        if left_ms() > 0:
            dismiss_fn(session.page)
        wait_network_idle()

        candidates = read_links(DISCOVERY_JS)
        if candidates is _UNREAD:
            return None
        resolved = resolve_fn(page_url, candidates)
        if resolved:
            return resolved[0]

        if left_ms() <= 0:
            # Past the deadline: the click and its download wait would hold
            # the session's lock for up to 6 s more (review-nd10). Noted, so
            # a cut is not read as "no PDF here" (review-st1).
            note_cut()
            return None
        clicked, downloaded_url = _click_capturing_download(
            session.page, click_fn, timeout_ms=min(_DOWNLOAD_EVENT_TIMEOUT_MS, left_ms())
        )
        if downloaded_url and _is_pdf_url(downloaded_url):
            return downloaded_url
        if clicked and _is_pdf_url(session.current_url()):
            # Checked before the cut: a click that navigated to the PDF inline
            # (no download event) is a find, even when the download wait used
            # up the deadline (review-w4 of `160e12a`).
            return session.current_url()
        if clicked and left_ms() <= 0:
            # The click's download wait used up what was left: the same cut
            # as above, which went unnoted (review-st2 `cut_after_click_no_pdf`;
            # the intermittent Firefox failure of the deadline test). Only
            # after a click: with nothing clicked, no later step was skipped,
            # and a cut note would turn "no PDF" into a 503 "retry later"
            # (review-w4b `probe_branches` case A).
            note_cut()
            return None
        if clicked:
            wait_network_idle()
            if _is_pdf_url(session.current_url()):
                return session.current_url()
            candidates = read_links(POST_CLICK_JS)  # pragma: no cover — integration path
            if candidates is _UNREAD:  # pragma: no cover — integration path
                return None
            resolved = resolve_fn(page_url, candidates)  # pragma: no cover — integration path
            if resolved:  # pragma: no cover — integration path
                return resolved[0]

        return None
    except RuntimeError as exc:
        if errors is not None:
            errors.append(f"browser session: {_describe_failure(exc, page_url)}")
        return None
    except Exception:
        return None
    finally:
        if close_on_exit:  # pragma: no cover — browser integration path
            session.close()
        else:
            # Discovery reads the session's page; leave it, and close any
            # popup it opened, so nothing downloads while pzi is idle
            # (review-nd5 `idle`, review-nd7 `probe_idle7 discover`).
            session.leave_page()


#: A code at the start of a message, after its API name (`_API_PREFIX_RE`):
#: `Page.goto: net::ERR_ABORTED at <url>`, `Page.goto: NS_BINDING_ABORTED`,
#: `Page.goto: Timeout 60000ms exceeded.` Matched there only, so a code-like
#: run inside a URL further on is never taken for one.
_ERROR_CODE_RE = re.compile(r"net::ERR_[A-Z0-9_]+\b|NS_[A-Z0-9_]+\b|Timeout \d+ms exceeded")

#: Network error codes a note may name. A code-shaped run is named only when
#: it is one of these: a page's script can throw any text, and taking
#: `NS_PAGETEXT_42` for a code put the page's own words in a note
#: (review-nd9 `probe_pagecode9`). Chromium's names (`net_error_list.h`) and
#: the Necko codes Firefox reports for a failed load.
_KNOWN_ERROR_CODES = frozenset(
    [
        f"net::ERR_{name}"
        for name in """
        FAILED ABORTED INVALID_ARGUMENT INVALID_HANDLE FILE_NOT_FOUND TIMED_OUT
        FILE_TOO_BIG UNEXPECTED ACCESS_DENIED NOT_IMPLEMENTED INSUFFICIENT_RESOURCES
        OUT_OF_MEMORY UPLOAD_FILE_CHANGED SOCKET_NOT_CONNECTED FILE_EXISTS
        FILE_PATH_TOO_LONG FILE_NO_SPACE FILE_VIRUS_INFECTED BLOCKED_BY_CLIENT
        NETWORK_CHANGED BLOCKED_BY_ADMINISTRATOR SOCKET_IS_CONNECTED
        CONTEXT_SHUT_DOWN BLOCKED_BY_RESPONSE CLEARTEXT_NOT_PERMITTED BLOCKED_BY_CSP
        BLOCKED_BY_ORB NETWORK_ACCESS_REVOKED CONNECTION_CLOSED CONNECTION_RESET
        CONNECTION_REFUSED CONNECTION_ABORTED CONNECTION_FAILED NAME_NOT_RESOLVED
        INTERNET_DISCONNECTED SSL_PROTOCOL_ERROR ADDRESS_INVALID ADDRESS_UNREACHABLE
        SSL_CLIENT_AUTH_CERT_NEEDED TUNNEL_CONNECTION_FAILED NO_SSL_VERSIONS_ENABLED
        SSL_VERSION_OR_CIPHER_MISMATCH SSL_RENEGOTIATION_REQUESTED
        PROXY_AUTH_UNSUPPORTED BAD_SSL_CLIENT_AUTH_CERT CONNECTION_TIMED_OUT
        HOST_RESOLVER_QUEUE_TOO_LARGE SOCKS_CONNECTION_FAILED
        SOCKS_CONNECTION_HOST_UNREACHABLE ALPN_NEGOTIATION_FAILED SSL_NO_RENEGOTIATION
        SSL_DECOMPRESSION_FAILURE_ALERT SSL_BAD_RECORD_MAC_ALERT PROXY_AUTH_REQUESTED
        PROXY_CONNECTION_FAILED MANDATORY_PROXY_CONFIGURATION_FAILED
        PROXY_CERTIFICATE_INVALID NAME_RESOLUTION_FAILED NETWORK_ACCESS_DENIED
        TEMPORARILY_THROTTLED HTTPS_PROXY_TUNNEL_RESPONSE_REDIRECT MSG_TOO_BIG
        ADDRESS_IN_USE SSL_HANDSHAKE_NOT_COMPLETED SSL_BAD_PEER_PUBLIC_KEY
        SSL_PINNED_KEY_NOT_IN_CERT_CHAIN SSL_DECRYPT_ERROR_ALERT
        SSL_SERVER_CERT_CHANGED SSL_UNRECOGNIZED_NAME_ALERT ICANN_NAME_COLLISION
        SSL_SERVER_CERT_BAD_FORMAT SSL_OBSOLETE_CIPHER NO_BUFFER_SPACE
        EARLY_DATA_REJECTED WRONG_VERSION_ON_EARLY_DATA TLS13_DOWNGRADE_DETECTED
        SSL_KEY_USAGE_INCOMPATIBLE INVALID_ECH_CONFIG_LIST ECH_NOT_NEGOTIATED
        ECH_FALLBACK_CERTIFICATE_INVALID CERT_COMMON_NAME_INVALID CERT_DATE_INVALID
        CERT_AUTHORITY_INVALID CERT_CONTAINS_ERRORS CERT_NO_REVOCATION_MECHANISM
        CERT_UNABLE_TO_CHECK_REVOCATION CERT_REVOKED CERT_INVALID
        CERT_WEAK_SIGNATURE_ALGORITHM CERT_NON_UNIQUE_NAME CERT_WEAK_KEY
        CERT_NAME_CONSTRAINT_VIOLATION CERT_VALIDITY_TOO_LONG
        CERTIFICATE_TRANSPARENCY_REQUIRED CERT_SYMANTEC_LEGACY
        CERT_KNOWN_INTERCEPTION_BLOCKED INVALID_URL DISALLOWED_URL_SCHEME
        UNKNOWN_URL_SCHEME INVALID_REDIRECT TOO_MANY_REDIRECTS UNSAFE_REDIRECT
        UNSAFE_PORT INVALID_RESPONSE INVALID_CHUNKED_ENCODING METHOD_NOT_SUPPORTED
        UNEXPECTED_PROXY_AUTH EMPTY_RESPONSE RESPONSE_HEADERS_TOO_BIG
        PAC_SCRIPT_FAILED REQUEST_RANGE_NOT_SATISFIABLE MALFORMED_IDENTITY
        CONTENT_DECODING_FAILED NETWORK_IO_SUSPENDED ENCODING_CONVERSION_FAILED
        NO_SUPPORTED_PROXIES HTTP2_PROTOCOL_ERROR INVALID_AUTH_CREDENTIALS
        UNSUPPORTED_AUTH_SCHEME ENCODING_DETECTION_FAILED MISSING_AUTH_CREDENTIALS
        MISCONFIGURED_AUTH_ENVIRONMENT RESPONSE_BODY_TOO_BIG_TO_DRAIN
        RESPONSE_HEADERS_MULTIPLE_CONTENT_LENGTH INCOMPLETE_HTTP2_HEADERS
        RESPONSE_HEADERS_MULTIPLE_CONTENT_DISPOSITION
        RESPONSE_HEADERS_MULTIPLE_LOCATION HTTP2_SERVER_REFUSED_STREAM
        HTTP2_PING_FAILED CONTENT_LENGTH_MISMATCH INCOMPLETE_CHUNKED_ENCODING
        QUIC_PROTOCOL_ERROR RESPONSE_HEADERS_TRUNCATED QUIC_HANDSHAKE_FAILED
        HTTP2_INADEQUATE_TRANSPORT_SECURITY HTTP2_FLOW_CONTROL_ERROR
        HTTP2_FRAME_SIZE_ERROR HTTP2_COMPRESSION_ERROR HTTP_1_1_REQUIRED
        PROXY_HTTP_1_1_REQUIRED INVALID_HTTP_RESPONSE CONTENT_DECODING_INIT_FAILED
        HTTP2_RST_STREAM_NO_ERROR_RECEIVED TOO_MANY_RETRIES HTTP2_STREAM_CLOSED
        HTTP_RESPONSE_CODE_FAILURE INCONSISTENT_IP_ADDRESS_SPACE
        BLOCKED_BY_PRIVATE_NETWORK_ACCESS_CHECKS CACHE_MISS CACHE_READ_FAILURE
        CACHE_WRITE_FAILURE CACHE_OPEN_FAILURE CACHE_CREATE_FAILURE CACHE_RACE
        INSECURE_RESPONSE DNS_MALFORMED_RESPONSE DNS_SERVER_REQUIRES_TCP
        DNS_SERVER_FAILED DNS_TIMED_OUT DNS_CACHE_MISS DNS_SEARCH_EMPTY
        DNS_SORT_ERROR DNS_NAME_HTTPS_ONLY DNS_REQUEST_CANCELLED
        """.split()
    ]
    + [
        f"NS_{name}"
        for name in """
        BINDING_ABORTED BINDING_FAILED BINDING_REDIRECTED BINDING_RETARGETED
        ERROR_ABORT ERROR_FAILURE ERROR_NOT_AVAILABLE ERROR_UNEXPECTED
        ERROR_OUT_OF_MEMORY ERROR_ILLEGAL_VALUE ERROR_CONNECTION_REFUSED
        ERROR_NET_TIMEOUT ERROR_NET_RESET ERROR_NET_INTERRUPT
        ERROR_NET_PARTIAL_TRANSFER ERROR_NET_INADEQUATE_SECURITY
        ERROR_NET_HTTP2_SENT_GOAWAY ERROR_NET_HTTP3_PROTOCOL_ERROR
        ERROR_NET_TIMEOUT_EXTERNAL ERROR_NET_EMPTY_RESPONSE ERROR_NET_ERROR_RESPONSE
        ERROR_UNKNOWN_HOST ERROR_UNKNOWN_PROXY_HOST ERROR_PROXY_CONNECTION_REFUSED
        ERROR_PROXY_AUTHENTICATION_FAILED ERROR_PROXY_BAD_GATEWAY
        ERROR_PROXY_GATEWAY_TIMEOUT ERROR_OFFLINE ERROR_PORT_ACCESS_NOT_ALLOWED
        ERROR_MALFORMED_URI ERROR_UNKNOWN_PROTOCOL ERROR_REDIRECT_LOOP
        ERROR_CORRUPTED_CONTENT ERROR_INVALID_CONTENT_ENCODING
        ERROR_DOCUMENT_NOT_CACHED ERROR_CONTENT_BLOCKED ERROR_CONTENT_BLOCKED_SHOW_ALT
        ERROR_FILE_NOT_FOUND ERROR_FILE_ACCESS_DENIED ERROR_DOM_BAD_URI
        ERROR_DOM_SECURITY_ERR ERROR_INTERCEPTION_FAILED ERROR_UNSAFE_CONTENT_TYPE
        ERROR_PARSED_DATA_CACHED ERROR_DOCSHELL_DYING ERROR_SOCKET_CREATE_FAILED
        ERROR_SOCKET_ADDRESS_IN_USE ERROR_ALREADY_CONNECTED ERROR_NOT_CONNECTED
        ERROR_IN_PROGRESS ERROR_GENERATE_FAILURE ERROR_SOCKET_FAIL
        """.split()
    ]
)

#: Fixed phrases of Playwright's and pzi's own messages, and the reason a
#: note gives for each. The note gives this text, never the message's.
_FIXED_REASONS = (
    ("is interrupted by another navigation", "interrupted by another navigation"),
    ("Download is starting", "download is starting"),
    ("Execution context was destroyed", "the page navigated during the read"),
    ("has been closed", "the page was closed"),
    ("evaluate did not answer within", "Timeout"),
    ("session is closed", "browser session is closed"),
    ("no longer running", "browser is no longer running"),
    ("landed on a non-public URL", "landed on a non-public URL"),
    ("navigate to a non-public URL", "non-public URL"),
)

#: APIs whose failure message continues with whatever the page's script threw.
_SCRIPT_APIS = ("evaluate", "evaluate_handle", "evaluate_all")

#: A host a note may name: ASCII letter-digit-hyphen labels. Anything else
#: (an underscore, a percent sign, non-ASCII, an IPv6 zone) is `<url>`.
_PLAIN_LABEL_RE = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")


#: What a browser strips from both ends of a URL before parsing it: C0
#: controls and space (WHATWG URL standard).
_URL_EDGE = "".join(chr(code) for code in range(0x21))

#: A Playwright API name at the start of a message: `Page.goto: `.
_API_PREFIX_RE = re.compile(r"[A-Za-z]+\.([A-Za-z_]+): ")


def _url_origin(url: str) -> str:
    """`scheme://host[:port]` of *url*, read as a browser reads it, else
    `<url>`; never its userinfo, path, query or fragment.

    `urlsplit` read `https://pub.example.org\\@x.example/` as host
    `x.example`; a browser reads `\\` as `/` in an http(s) URL, so the host is
    `pub.example.org`, and a URL could choose the origin a note named
    (review-nd9 `probe_origin9`). So the URL is prepared as a browser
    prepares it: C0 controls and spaces stripped from both ends, every tab
    and newline removed, `\\` read as `/`, any slashes after the scheme
    skipped. The host is named only when it is plain, ASCII labels of
    letters, digits and hyphens, a dotted-quad IPv4 address, or a bracketed
    IPv6 address: anything a browser would decode, map or refuse is `<url>`.
    """
    text = re.sub(r"[\t\n\r]", "", url.strip(_URL_EDGE))
    scheme, colon, rest = text.partition(":")
    scheme = scheme.lower()
    if not colon or scheme not in ("http", "https"):
        return "<url>"
    authority = re.split(r"[/?#]", rest.replace("\\", "/").lstrip("/"), maxsplit=1)[0]
    host_port = authority.rpartition("@")[2]
    if host_port.startswith("["):
        host, bracket, port = host_port[1:].partition("]")
        if not bracket or (port and not port.startswith(":")):
            return "<url>"
        if not re.fullmatch(r"[0-9A-Fa-f:.]+", host):
            # A zone ID (`%25…`) or an IPvFuture: a browser refuses both.
            return "<url>"
        try:
            host = f"[{ipaddress.IPv6Address(host).compressed}]"
        except ValueError:
            return "<url>"
        port = port[1:]
    else:
        host, _, port = host_port.partition(":")
        host = host.lower()
        if not _is_plain_host(host):
            return "<url>"
    if port:
        if not port.isascii() or not port.isdigit() or int(port) > 65535:
            return "<url>"
        if int(port) != {"http": 80, "https": 443}[scheme]:
            host = f"{host}:{int(port)}"
    return f"{scheme}://{host}"


def _is_plain_host(host: str) -> bool:
    """A host of ASCII letter-digit-hyphen labels, or a dotted-quad IPv4
    address. A last label a browser would read as a number (`0x7f`, `1`)
    must be part of a plain dotted quad; an `xn--` label is not plain."""
    labels = host.removesuffix(".").split(".")
    if not all(_PLAIN_LABEL_RE.fullmatch(label) for label in labels):
        return False
    if any(label[2:4] == "--" for label in labels):
        return False
    if re.fullmatch(r"0x[0-9a-f]*|[0-9]+", labels[-1]):
        try:
            return str(ipaddress.IPv4Address(host)) == host
        except ValueError:
            return False
    return True


def _describe_failure(exc: BaseException, url: str | None = None) -> str:
    """The exception's type, why it failed, and the origin of *url*, the URL
    pzi asked for.

    Why is a known network error code (`_KNOWN_ERROR_CODES`), `Timeout`, or a
    fixed phrase (`_FIXED_REASONS`), and nothing else of the message: the
    message names URLs, and a publisher's PDF URL can carry a session token
    in its path or query. Text after an `evaluate` API name is whatever the
    page's script threw, so no code is read from it.
    """
    first_line = (str(exc).splitlines() or [""])[0]
    described = f"{type(exc).__name__}: {_failure_reason(first_line)}"
    return described if url is None else f"{described} at {_url_origin(url)}"


def _failure_reason(line: str) -> str:
    prefix = _API_PREFIX_RE.match(line)
    rest = line[prefix.end():] if prefix else line
    if prefix is None or prefix.group(1) not in _SCRIPT_APIS:
        code = _ERROR_CODE_RE.match(rest)
        if code and code.group(0) in _KNOWN_ERROR_CODES:
            return code.group(0)
        if code and code.group(0).startswith("Timeout "):
            return "Timeout"
    lowered = line.lower()
    for phrase, reason in _FIXED_REASONS:
        if phrase.lower() in lowered:
            return reason
    if prefix is not None and prefix.group(1) in _SCRIPT_APIS:
        return "the page's script threw"
    return "unrecognised"


def _note_refusal(
    result: FetchResult,
    errors: list[str] | None,
    url: str | None = None,
    refusals: list[str] | None = None,
) -> None:
    """Record why a response was refused before its body was read, naming
    only the origin of the URL refused: a candidate's path or query can
    carry a session token."""
    if result.refused:
        _record_refusal(
            f"{_url_origin(url)}: {result.refused}" if url else result.refused, errors, refusals
        )


def _record_refusal(
    reason: str, errors: list[str] | None, refusals: list[str] | None
) -> None:
    """Record a refusal in *errors*, and in *refusals*, which holds refusals only.

    A refusal is pzi declining what the browser reached (a compressed body, a
    URL the guard blocks); the session is fine. *refusals* lets the server
    answer it with a status that does not mean "the session is dead".
    """
    if errors is not None:
        errors.append(reason)
    if refusals is not None:
        refusals.append(reason)


def download_pdf(
    pdf_url: str,
    *,
    browser: str = "chromium",
    profile_path: str | None = None,
    _dismiss=None,
    _session: BrowserSession | None = None,
    headless: bool = True,
    challenge_timeout: int = 0,
    candidate_deadline_seconds: float = CANDIDATE_DEADLINE_SECONDS,
    url_allowed: Callable[[str], bool] | None = None,
    errors: list[str] | None = None,
    refusals: list[str] | None = None,
) -> bytes | None:
    """Download PDF bytes using browser, with optional profile reuse.

    All browser I/O edges are injectable via _session for testing.

    The candidate sweep is bounded by ``MAX_PDF_CANDIDATES`` and
    *candidate_deadline_seconds*: the list comes from the page being fetched, so
    without a bound the page decides how long the server's single browser lock
    is held — one hostile document could stall every other capture indefinitely,
    30 seconds at a time.

    *errors* is the same sibling-symmetric addition as on ``discover_pdf_url``
    above: a crashed session (``RuntimeError`` from ``_check_open``) outside
    the per-candidate loop is recorded rather than silently read as "no PDF".
    The per-candidate loop's own ``except Exception: continue`` is untouched —
    it exists specifically because Chromium turns a PDF navigation into a
    download exception (see the comment at that site), which is a normal
    outcome, not a broken session.

    *refusals* receives the subset of *errors* that are refusals: a response
    pzi would not read, or a URL the SSRF guard blocked. A guard block used to
    record nothing, so it read as "no PDF".
    """
    close_on_exit = _session is None
    if _session is not None:  # pragma: no branch
        session = _session
    else:  # pragma: no cover
        session = launch_browser(
            browser,
            profile_path,
            headless=headless,
            url_allowed=url_allowed or default_url_allowed,
        )

    dismiss_fn = _dismiss or _dismiss_cookie_banners

    try:
        # 1. Try direct request
        result = session.fetch_direct(pdf_url)
        _note_refusal(result, errors, refusals=refusals)
        if result.is_pdf():
            return result.body

        # 2. Navigate to URL (fallback — browser handles auth via profile cookies)
        response = None
        navigation_failed = False
        try:
            response = session.navigate(pdf_url, wait_until="domcontentloaded", timeout=60000)
        except SsrfBlocked as exc:
            # The guard refused this URL or where it landed: never read that
            # page, and say so rather than answer "no PDF".
            _record_refusal(f"{_url_origin(pdf_url)}: {exc.reason}", errors, refusals)
            navigation_failed = True
        except Exception:
            # The shared session's page may still be whatever it last loaded,
            # usually another request's landing page; sweeping its links
            # returned paper A's PDF for a request about paper B. Nothing
            # observable after the error proves this request loaded the page:
            # the URL can match an earlier load of the same address, or change
            # because the stale document rewrote its own history. So a failed
            # navigation never sweeps (a landing page slower than the timeout
            # is lost; review of `a322bf9`).
            navigation_failed = True
        else:
            # `goto` answers None for a same-document navigation (a URL that
            # differs from the showing one only by its fragment, or one the
            # page itself already `pushState`d to): nothing was loaded, so the
            # document is still the earlier request's (review of `9161bd0`).
            navigation_failed = response is None

        if response is not None:
            navigated = FetchResult.from_response(response)
            _note_refusal(navigated, errors, refusals=refusals)
            if navigated.is_pdf():
                return navigated.body

        if challenge_timeout > 0:
            pdf_bytes = _wait_for_verified_pdf(session, pdf_url, timeout=challenge_timeout)
            if pdf_bytes is not None:
                return pdf_bytes

        if navigation_failed:
            return None

        # 3. HTML page — look for PDF candidate links
        dismiss_fn(session.page)
        session.wait_network_idle()

        candidates = session.evaluate(DOWNLOAD_CANDIDATES_JS)
        if isinstance(candidates, list):  # pragma: no branch — covered by integration/browser tests
            deadline = time.monotonic() + max(0.0, candidate_deadline_seconds)
            tried = 0
            for candidate in candidates:
                if tried >= MAX_PDF_CANDIDATES or time.monotonic() >= deadline:
                    break
                if not isinstance(candidate, str):
                    continue
                if not candidate.startswith(("http://", "https://")):
                    continue
                tried += 1
                # `fetch_direct`, not `navigate`: Chromium treats a navigation to
                # `application/pdf` as a download, so `page.goto` raises
                # "Download is starting" and the candidate was swallowed by the
                # `except` below — every candidate link failed this way. Step 1
                # above already uses this request path, which is why the direct
                # download worked while following a link never did.
                try:
                    result = session.fetch_direct(candidate)
                    _note_refusal(result, errors, candidate, refusals)
                    if result.is_pdf():
                        return result.body
                except Exception:
                    continue

        return None
    except SsrfBlocked as exc:
        # From the challenge loop: a guard refusal, not a dead session. It
        # subclasses `URLError`, so the bare `except Exception` below took it
        # and recorded nothing.
        _record_refusal(f"{_url_origin(pdf_url)}: {exc.reason}", errors, refusals)
        return None
    except RuntimeError as exc:
        if errors is not None:
            errors.append(f"browser session: {_describe_failure(exc, pdf_url)}")
        return None
    except Exception:
        return None
    finally:
        if close_on_exit:  # pragma: no cover — browser integration path
            session.close()
        else:
            # Step 2, the challenge wait and step 3 all use the session's
            # page; leave it, and close any popup they opened, so nothing
            # downloads while pzi is idle (review-nd7 `probe_idle7 fallback`).
            session.leave_page()


def _wait_for_verified_pdf(
    session: BrowserSession,
    pdf_url: str,
    *,
    timeout: int,
) -> bytes | None:
    """Poll browser context after a challenge page, allowing cookies to settle."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            session.page.wait_for_timeout(2000)
        except Exception:
            time.sleep(2)
        result = session.fetch_direct(pdf_url)
        if result.is_pdf():
            return result.body
        try:
            response = session.navigate(pdf_url, wait_until="domcontentloaded", timeout=30000)
            if response is not None:
                navigated = FetchResult.from_response(response)
                if navigated.is_pdf():
                    return navigated.body
        except SsrfBlocked:
            # The guard refused where this landed. Swallowing it let the
            # caller's candidate sweep read the refused page.
            raise
        except Exception:
            pass
    return None


def resolve_pdf_candidate_urls(page_url: str, candidates: Any) -> list[str]:
    if not isinstance(candidates, list):
        return []
    resolved: list[str] = []
    for raw in candidates:
        if not isinstance(raw, str):
            continue
        absolute = urljoin(page_url, raw.strip())
        if not absolute.startswith(("http://", "https://")):
            continue
        lower = absolute.lower()
        if lower.endswith(".pdf") or PDF_HINT_RE.search(lower):
            if absolute not in resolved:
                resolved.append(absolute)
    return resolved


def _is_pdf_url(url: str) -> bool:
    """Pure: check if a URL likely points to a PDF."""
    return url.lower().endswith(".pdf")


def _dismiss_cookie_banners(page: Any) -> None:
    for selector in COOKIE_BANNER_SELECTORS:
        try:
            page.locator(selector).first.click(timeout=300)
            return
        except Exception:
            continue


#: How long to wait for a click to turn into a download before concluding it
#: was an ordinary in-page click. The navigation is local in the cases this
#: serves — the button already knows the URL — so this is short on purpose.
_DOWNLOAD_EVENT_TIMEOUT_MS = 5000


def _click_capturing_download(
    page: Any,
    click_fn: Callable[[Any], bool],
    *,
    timeout_ms: int = _DOWNLOAD_EVENT_TIMEOUT_MS,
) -> tuple[bool, str | None]:
    """Click, and catch the download that a PDF navigation turns into.

    Chromium treats navigating to `application/pdf` as a download rather than a
    navigation: `page.goto` raises "Download is starting" and `page.url` never
    moves. So a click that successfully reached the PDF was indistinguishable
    from a click that did nothing, and click-based discovery returned `None`
    for every page whose button navigates — which is what such buttons do.

    The download event carries the URL discovery was looking for.
    """
    expect_download = getattr(page, "expect_download", None)
    if expect_download is None:
        return click_fn(page), None
    clicked = False
    try:
        with expect_download(timeout=max(timeout_ms, 1)) as info:
            clicked = click_fn(page)
        return clicked, getattr(info.value, "url", None)
    except Exception:
        # No download started: an ordinary in-page click, which the caller
        # handles by re-scanning the page. `clicked` is still what the click
        # itself reported.
        return clicked, None


def _click_downloadish_links(page: Any) -> bool:
    for selector in DOWNLOADISH_SELECTORS:
        try:
            page.locator(selector).first.click(timeout=300)
            return True
        except Exception:
            continue
    return False


if __name__ == "__main__":
    raise SystemExit(main())
