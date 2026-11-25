"""Browser session abstraction — unified Playwright lifecycle management.

A BrowserSession wraps the Playwright (playwright instance, browser/context, page)
triple into a single typed object with a context-manager-based lifecycle.

Usage:
    with open_browser_session(browser="chromium") as session:
        session.navigate("https://example.com")
        data = session.evaluate("document.title")
        pdf = session.fetch_direct("https://example.com/paper.pdf")
    # automatically closed even on exception
"""

from __future__ import annotations

import contextlib
import os
import shutil
import socket
import tempfile
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

from pzi.pinning_proxy import DEFAULT_MAX_RESPONSE_BYTES, PinningProxy

#: `fetch_direct` follows redirects manually (see below) rather than letting
#: Playwright's `request.get` do it, so this bounds how many hops a
#: pathological or malicious redirect chain gets before it is refused.
_MAX_FETCH_DIRECT_REDIRECTS = 10

_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})

#: Prefix for cloned-profile temp dirs, shared by the cleanup sweep below.
_CLONE_PREFIX = "pzi-chrome-"

#: Age after which a clone with no live owner is swept. Age alone is not proof
#: of abandonment: a `pzi server` run without `--stop-after` keeps one session,
#: and its clone, for as long as the server runs. The owner lock below is what
#: keeps a live session's clone from being swept.
_CLONE_MAX_AGE_SECONDS = 6 * 60 * 60

#: File inside each clone that its owning process holds an exclusive `flock` on.
#: The kernel drops the lock when that process exits or is killed, so a clone
#: whose lock can be taken has no owner and is safe to sweep once it is old.
_CLONE_OWNER_FILE = ".pzi-owner.lock"

#: Descriptors holding the owner lock of this process's clones, by clone path.
#: Python opens them close-on-exec, so the browser child does not inherit them.
_OWNED_CLONE_FDS: dict[Path, int] = {}


#: Shared by the two places that report a missing Playwright. The project is
#: not on PyPI, so the install line names the GitHub distribution; see README.
PLAYWRIGHT_INSTALL_HINT = (
    "The browser-profile PDF fallback is optional. pzi is not published on "
    "PyPI; reinstall it from GitHub with the extra, e.g. "
    "uv tool install 'pzi[playwright] @ git+https://github.com/mnazaal/paperazzi.git' "
    "(or pipx install 'pzi[playwright] @ git+https://github.com/mnazaal/paperazzi.git'), "
    "then run: playwright install"
)


#: Sent on direct PDF reads (`fetch_direct`) only; navigations keep the
#: browser's default headers. The pinning proxy caps wire bytes, and the driver
#: decodes after that, so an encoded body can inflate past the cap. HTTPS is
#: opaque to the proxy, so ask for none and refuse any PDF body that still declares
#: one. A non-PDF response is never read for its encoding's sake.
_IDENTITY_HEADERS = {"Accept-Encoding": "identity"}


def _claims_pdf(content_type: str) -> bool:
    """Whether a Content-Type names PDF as a media type, case-insensitively.

    Each media type is compared before its parameters, so `text/html;
    note="application/pdf"` is not a PDF claim. Playwright joins repeated
    Content-Type headers with ", ", and a PDF among them still counts, as it
    did before parameters were parsed.
    """
    return any(
        part.split(";", 1)[0].strip().lower() == "application/pdf"
        for part in content_type.split(",")
    )


def _declared_encoding(headers: Any) -> str | None:
    """The response's non-identity `Content-Encoding`, from headers alone.

    None when absent or every coding is `identity`. A stacked value
    (`gzip, br`) is returned whole so the refusal names it.
    """
    raw = (headers.get("content-encoding", "") or "").strip()
    codings = [c.strip().lower() for c in raw.split(",") if c.strip()]
    if all(c == "identity" for c in codings):
        return None
    return raw


def _encoding_refusal(encoding: str) -> str:
    return f"response declares Content-Encoding: {encoding}; only identity is accepted"


def _dispose_response(response: Any) -> None:
    """Release an API response's driver-side buffer; never raise."""
    try:
        response.dispose()
    except Exception:
        pass


def _claim_profile_clone(clone: Path) -> None:
    """Take the owner lock on a fresh clone. No lock where `fcntl` is missing."""
    try:
        import fcntl

        fd = os.open(clone / _CLONE_OWNER_FILE, os.O_RDWR | os.O_CREAT, 0o600)
    except (ImportError, OSError):
        return
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        return
    _OWNED_CLONE_FDS[clone] = fd


def _release_profile_clone(clone: Path) -> None:
    """Drop this process's owner lock on `clone`, if it holds one."""
    fd = _OWNED_CLONE_FDS.pop(clone, None)
    if fd is not None:
        with contextlib.suppress(OSError):
            os.close(fd)


def _discard_profile_clone(clone: Path) -> None:
    """Delete a clone and release its owner lock."""
    shutil.rmtree(clone, ignore_errors=True)
    _release_profile_clone(clone)


def _clone_has_live_owner(clone: Path) -> bool:
    """True when some process still holds `clone`'s owner lock.

    A clone this process owns is live by definition and is never probed: on NFS
    Linux emulates flock with per-process POSIX locks, so the LOCK_SH probe
    would not conflict with our own owner lock and its `os.close` would drop it.
    The probe is therefore only meaningful across processes.
    """
    if clone in _OWNED_CLONE_FDS:
        return True
    try:
        import fcntl

        fd = os.open(clone / _CLONE_OWNER_FILE, os.O_RDONLY)
    except (ImportError, OSError):
        return False
    try:
        fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
    except OSError:
        return True
    finally:
        os.close(fd)
    return False


def _sweep_stale_profile_clones(
    *, now: float | None = None, temp_root: Path | None = None
) -> list[Path]:
    """Remove abandoned clones of the user's Chrome profile from $TMPDIR.

    The clone is a full copy of the real profile, cookie database included, and
    cleanup lived only in `BrowserSession.close()` — which a browser-hook child
    killed at its 180 s timeout never reaches. There is no `atexit` and nothing
    else sweeps, so every hook timeout left another readable copy of the user's
    logged-in sessions behind until the machine was rebooted.

    A clone is removed only when it is older than `_CLONE_MAX_AGE_SECONDS` and
    no process holds its owner lock (`_CLONE_OWNER_FILE`). Returns what it
    removed, so a test can assert on it.
    """
    root = temp_root or Path(tempfile.gettempdir())
    cutoff = (now if now is not None else time.time()) - _CLONE_MAX_AGE_SECONDS
    removed: list[Path] = []
    try:
        candidates = list(root.glob(f"{_CLONE_PREFIX}*"))
    except OSError:  # pragma: no cover — an unreadable $TMPDIR is the OS's problem
        return removed
    for candidate in candidates:
        try:
            if not candidate.is_dir() or candidate.stat().st_mtime > cutoff:
                continue
        except OSError:
            continue
        if _clone_has_live_owner(candidate):
            continue
        shutil.rmtree(candidate, ignore_errors=True)
        if not candidate.exists():
            removed.append(candidate)
    return removed


def _clone_chrome_profile(profile: Path) -> Path:
    """Clone a Chrome user-data dir to a temporary location.

    Chrome refuses to enable remote debugging on its *default* user-data
    directory. Copying to a temp dir satisfies the "non-default" requirement
    while preserving cookies and session state.
    """
    _sweep_stale_profile_clones()
    temp_dir = Path(tempfile.mkdtemp(prefix=_CLONE_PREFIX))
    # Tighten permissions so other users on the system cannot read
    # the cloned profile (which contains cookies and session tokens).
    os.chmod(temp_dir, 0o700)
    _claim_profile_clone(temp_dir)

    def _ignore(_dir: str, contents: list[str]) -> list[str]:
        return [
            c
            for c in contents
            if c
            in {
                "Cache",
                "Code Cache",
                "GPUCache",
                "Service Worker",
                "SingletonLock",
                "SingletonSocket",
                "SingletonCookie",
                "lockfile",
            }
        ]

    try:
        shutil.copytree(profile, temp_dir, dirs_exist_ok=True, ignore=_ignore)
    except BaseException:
        # The caller never receives `temp_dir`, so nothing else could remove
        # the partial copy of the cookie database or release its owner lock.
        _discard_profile_clone(temp_dir)
        raise
    return temp_dir


def browser_launch_options(browser: str, *, headless: bool = True) -> dict[str, Any]:
    """Return Playwright launch kwargs for browser name."""
    options: dict[str, Any] = {"headless": headless}
    if browser == "firefox":
        options["firefox_user_prefs"] = {
            "browser.download.folderList": 2,
            "browser.download.manager.showWhenStarting": False,
            "pdfjs.disabled": True,
        }
    return options


def default_url_allowed(url: str) -> bool:
    """The production predicate: a public http(s) destination and nothing else."""
    from pzi.url_safety import safe_public_http_url

    return safe_public_http_url(url)


# ------------------------------------------------------------------
# Connected-address pinning
# ------------------------------------------------------------------

#: Wall-clock budget for one `fetch_direct` call, redirects included.
FETCH_DIRECT_TIMEOUT_SECONDS = 60.0


def _is_close_delimited(headers: dict[str, str]) -> bool:
    """True when only the connection closing marks where the body ends.

    `chunked` frames a body only as the last coding: Node reads `chunked, identity`
    to the close. Playwright joins a repeated header with ", ".
    """
    if "content-length" in headers:
        return False
    codings = headers.get("transfer-encoding", "").lower().split(",")
    return codings[-1].strip() != "chunked"


@dataclass
class BrowserSession:
    """Unified browser session wrapping Playwright lifecycle.

    All interaction methods delegate to the underlying Playwright page
    so callers never touch playwright/browser/context objects directly.
    """

    playwright: Any = field(repr=False)
    browser_ref: Any = field(repr=False)
    page: Any = field(repr=False)
    #: What counts as a destination this session may reach. Injected rather
    #: than read from config or the environment: a switch that turns SSRF
    #: protection off would be reachable from `config.toml` and from the HTTP
    #: API, which is precisely what the guard exists to prevent. The only
    #: caller that overrides it is the test suite, whose fixture servers are on
    #: loopback — and a test that has to reach into the process to change it
    #: cannot be triggered from outside.
    url_allowed: Callable[[str], bool] = field(default=default_url_allowed, repr=False)
    _closed: bool = field(default=False, init=False)
    _temp_profile: Path | None = field(default=None, init=False, repr=False)
    _proxy: PinningProxy | None = field(default=None, init=False, repr=False)

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    def navigate(
        self,
        url: str,
        *,
        wait_until: str = "domcontentloaded",
        timeout: int = 30000,
    ) -> Any:
        """Navigate to URL, returning the Playwright response object.

        The *landing* URL is checked as well as the one asked for: the guard
        installed by :func:`install_request_guard` refuses each hop, and this
        catches anything that still arrives somewhere non-public (a
        ``file:``/``about:`` redirect, or a guard that failed to install).
        """
        self._check_open()
        from pzi.safe_http import SsrfBlocked

        if not self.url_allowed(url):
            # Before `goto`: the landing check below runs after the request
            # was sent, and the route guard may be absent.
            raise SsrfBlocked(f"refusing to navigate to a non-public URL: {url}")
        response = self.page.goto(url, wait_until=wait_until, timeout=timeout)
        self._reject_non_public_landing()
        return response

    def _reject_non_public_landing(self) -> None:
        from pzi.safe_http import SsrfBlocked

        landed = ""
        with contextlib.suppress(Exception):
            landed = self.page.url or ""
        if landed and not self.url_allowed(landed):
            raise SsrfBlocked(f"browser landed on a non-public URL: {landed}")

    def current_url(self) -> str:
        """Return the current page URL."""
        self._check_open()
        return self.page.url

    # ------------------------------------------------------------------
    # JavaScript evaluation
    # ------------------------------------------------------------------

    def evaluate(self, js: str) -> Any:
        """Evaluate JavaScript in the page and return the result."""
        self._check_open()
        return self.page.evaluate(js)

    # ------------------------------------------------------------------
    # Direct HTTP requests (through browser's network stack)
    # ------------------------------------------------------------------

    def fetch_direct(
        self, url: str, *, timeout_seconds: float = FETCH_DIRECT_TIMEOUT_SECONDS
    ) -> FetchResult:
        """Perform a direct HTTP GET through the browser's request context.

        Returns a FetchResult with status, content_type, and body bytes.
        Does NOT navigate the page — uses the browser's HTTP stack directly.

        Follows redirects itself (``max_redirects=0`` on every request) rather
        than letting Playwright's ``request.get`` follow them internally: that
        only re-checks the *final* URL, so a validated public URL could 302
        through a private hop (``127.0.0.1``, cloud metadata) that was never
        requested through this guard — the hop is real regardless of whether
        the response body is ever read. Each hop is validated with
        ``url_allowed`` before it is requested, matching what
        :func:`install_request_guard` does for page navigation, and bounded at
        :data:`_MAX_FETCH_DIRECT_REDIRECTS` hops.
        """
        self._check_open()
        if not self.url_allowed(url):
            return FetchResult(status=-1, content_type=None, body=b"")
        deadline = time.monotonic() + timeout_seconds
        try:
            current = url
            for _ in range(_MAX_FETCH_DIRECT_REDIRECTS + 1):
                remaining_ms = (deadline - time.monotonic()) * 1000
                if remaining_ms <= 0:
                    return FetchResult(status=-1, content_type=None, body=b"")
                cuts_before = self._cut_responses()
                response = self.page.request.get(
                    current,
                    max_redirects=0,
                    timeout=remaining_ms,
                    headers=_IDENTITY_HEADERS,
                )
                try:
                    if response.status not in _REDIRECT_STATUSES:
                        ct = response.headers.get("content-type", "")
                        encoding = _declared_encoding(response.headers)
                        if encoding is not None and not _claims_pdf(ct):
                            # Not a PDF either way, so it is answered unread and
                            # unrefused: a refusal note turned a compressed HTML
                            # page's "no PDF" into an error, and reading it would
                            # let the driver decode it past the cap.
                            return FetchResult(
                                status=response.status, content_type=ct, body=b""
                            )
                        if encoding is not None:
                            # Refused from the headers: `body()` would make the
                            # driver buffer the decoded bytes, past the cap.
                            return FetchResult(
                                status=-1,
                                content_type=None,
                                body=b"",
                                refused=_encoding_refusal(encoding),
                            )
                        body = response.body() if response.status == 200 else b""
                        # The driver can report a cut body as a clean end, and with
                        # no framing that looks complete
                        # (`PinningProxy.cut_responses`). The count covers every
                        # connection, so another one's cut can fail this fetch too.
                        # That is why framed responses, which the driver rejects by
                        # itself, do not consult it.
                        if (
                            _is_close_delimited(response.headers)
                            and self._cut_responses() != cuts_before
                        ):
                            return FetchResult(status=-1, content_type=None, body=b"")
                        return FetchResult(
                            status=response.status, content_type=ct, body=body
                        )
                    location = response.headers.get("location")
                    if not location:
                        return FetchResult(status=-1, content_type=None, body=b"")
                    next_url = urljoin(current, location)
                    if not self.url_allowed(next_url):
                        # Refused before the hop is ever requested — the point of
                        # following redirects manually instead of letting
                        # `request.get` do it.
                        return FetchResult(status=-1, content_type=None, body=b"")
                finally:
                    # Playwright keeps an API response's body in the driver until
                    # it is disposed; `body` above is already a copy.
                    _dispose_response(response)
                current = next_url
            # Exhausted the hop budget without a non-redirect response.
            return FetchResult(status=-1, content_type=None, body=b"")
        except Exception:
            return FetchResult(status=-1, content_type=None, body=b"")

    def _cut_responses(self) -> int:
        return self._proxy.cut_responses if self._proxy is not None else 0

    # ------------------------------------------------------------------
    # Wait helpers
    # ------------------------------------------------------------------

    def wait_network_idle(self, *, timeout: int = 5000) -> None:
        """Wait for network idle, silently swallowing timeout."""
        self._check_open()
        try:
            self.page.wait_for_load_state("networkidle", timeout=timeout)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def close(self) -> None:
        """Close browser resources.  Idempotent — safe to call multiple times."""
        if self._closed:
            return
        self._closed = True
        try:
            if isinstance(self.browser_ref, tuple):
                browser, context = self.browser_ref
                context.close()
                browser.close()
            else:
                self.browser_ref.close()
        except Exception:
            pass
        try:
            self.playwright.stop()
        except Exception:
            pass
        if self._proxy is not None:
            self._proxy.close()
        if self._temp_profile is not None:
            try:
                _discard_profile_clone(self._temp_profile)
            except Exception:
                pass

    def _check_open(self) -> None:
        """Raise unless this session can still be used.

        `_closed` alone was not enough: it is set only by `close()`, so a
        browser killed underneath us — OOM, a crash, the user quitting it —
        left the flag False and the dead session was handed back forever.
        `BrowserSessionManager` treats the `RuntimeError` as "relaunch", which
        its docstring already promised, so the only thing missing was noticing.
        """
        if self._closed:
            raise RuntimeError("BrowserSession is closed")
        if not self._browser_is_connected():
            raise RuntimeError("BrowserSession's browser is no longer running")

    def _browser_is_connected(self) -> bool:
        """Whether Playwright still has a live connection to the browser.

        A local boolean on the Playwright object, so it is cheap enough for the
        per-operation check. Anything that cannot answer is treated as alive:
        this is a liveness probe, not an excuse to refuse work.
        """
        browser = self.browser_ref
        if isinstance(browser, tuple):
            browser = browser[0]
        is_connected = getattr(browser, "is_connected", None)
        if not callable(is_connected):
            return True
        try:
            return bool(is_connected())
        except Exception:
            return False


@dataclass
class FetchResult:
    """Result of a direct HTTP request through the browser's network stack."""
    status: int
    content_type: str | None
    body: bytes
    #: Why the response was refused before its body was read, else None.
    refused: str | None = None

    def is_pdf(self) -> bool:
        """True if the response looks like a PDF."""
        if self.status != 200:
            return False
        if self.content_type and _claims_pdf(self.content_type):
            return self.body.startswith(b"%PDF-")
        return False

    @classmethod
    def from_response(cls, response: Any) -> FetchResult:
        """Wrap a Playwright *navigation* response so ``is_pdf`` can judge it.

        "Content-type says PDF and the body starts with ``%PDF-``" was written
        out longhand at two more places in :mod:`pzi.browser_pdf_hook`; a change
        to what counts as a PDF had three sites to land at. The body is read
        only when the content type claims a PDF, because reading it is a network
        round trip that can itself raise on a response that carries none.
        """
        content_type = response.headers.get("content-type", "")
        claims_pdf = _claims_pdf(content_type)
        # Only a body pzi would read is refused for its encoding. A compressed
        # HTML landing page is ordinary since navigations keep Chrome's default
        # Accept-Encoding, and its body is never read here.
        encoding = _declared_encoding(response.headers) if claims_pdf else None
        if encoding is not None:
            return cls(
                status=-1,
                content_type=None,
                body=b"",
                refused=_encoding_refusal(encoding),
            )
        body = response.body() if claims_pdf else b""
        return cls(
            status=getattr(response, "status", 200),
            content_type=content_type,
            body=body,
        )


# ------------------------------------------------------------------
# Context manager entry point
# ------------------------------------------------------------------


def install_request_guard(
    page: Any, url_allowed: Callable[[str], bool] = default_url_allowed
) -> None:
    """Refuse browser requests to non-public destinations.

    ``safe_http`` protects everything pzi fetches itself, but a Playwright page
    fetches through the *browser's* stack: it follows redirects and resolves DNS
    on its own, so validating only the URL handed in leaves
    ``https://public.example/x`` → ``http://127.0.0.1:8765/`` (or a DNS name
    that resolves to a private address) entirely unguarded. Routing every
    request through the same public-URL predicate closes redirect and
    rebinding, because each hop is a request of its own.

    Fails closed: if Playwright cannot install the route, this raises and the
    launch is abandoned. A session without the route would send the first
    request before any check could refuse it.
    """
    def _guard(route: Any, request: Any) -> None:
        url = getattr(request, "url", "") or ""
        try:
            if url_allowed(url):
                route.continue_()
                return
            route.abort()
        except Exception:  # pragma: no cover — Playwright teardown races
            with contextlib.suppress(Exception):
                route.abort()

    try:
        page.route("**/*", _guard)
    except Exception as exc:
        from pzi.safe_http import SsrfBlocked

        raise SsrfBlocked(f"browser request guard could not be installed: {exc}") from exc


def launch_browser(
    browser: str,
    profile_path: str | None,
    *,
    headless: bool = True,
    url_allowed: Callable[[str], bool] = default_url_allowed,
    getaddrinfo: Callable[..., Any] = socket.getaddrinfo,
    connect: Callable[..., socket.socket] = socket.create_connection,
    max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
) -> BrowserSession:
    """Launch a browser and return a BrowserSession.

    Every non-loopback connection goes through a :class:`PinningProxy`, so the
    address the browser connects to is the one that was validated.
    ``getaddrinfo`` and ``connect`` are test seams for that proxy.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:  # pragma: no cover — playwright installed in dev/test
        raise ImportError(
            "playwright is required for browser PDF features. "
            + PLAYWRIGHT_INSTALL_HINT
        )

    playwright = sync_playwright().start()
    options = browser_launch_options(browser, headless=headless)
    try:
        pinning = PinningProxy(
            getaddrinfo=getaddrinfo, connect=connect, max_response_bytes=max_response_bytes
        )
    except BaseException:
        with contextlib.suppress(Exception):
            playwright.stop()
        raise
    options["proxy"] = {"server": pinning.url}
    if url_allowed is not default_url_allowed:
        # Playwright forces loopback through the proxy, which refuses it. Only
        # a caller that replaced the production predicate (the test suite, for
        # its loopback fixture servers) reaches loopback directly; the
        # production predicate and the proxy both refuse it.
        options["proxy"]["bypass"] = "127.0.0.1,localhost,[::1]"

    temp_profile: Path | None = None
    if profile_path:
        profile = Path(profile_path).expanduser()
        # Everything past the clone is wrapped: the clone is a full copy of the
        # user's Chrome profile, cookie database included, and a failed launch
        # used to abandon it in $TMPDIR where nothing ever removed it — along
        # with the Playwright driver process it had already started.
        try:
            if browser in ("chrome", "chromium"):
                # Chrome refuses remote debugging on its default user-data dir.
                # Clone to a temp location to satisfy the "non-default" requirement.
                temp_profile = _clone_chrome_profile(profile)
                profile = temp_profile
            if browser == "firefox":
                ctx = playwright.firefox.launch_persistent_context(
                    user_data_dir=str(profile), **options
                )
            elif browser == "chrome":
                ctx = playwright.chromium.launch_persistent_context(
                    user_data_dir=str(profile), channel="chrome", **options
                )
            else:
                ctx = playwright.chromium.launch_persistent_context(
                    user_data_dir=str(profile), **options
                )
            page = ctx.new_page()
            install_request_guard(page, url_allowed)
        except BaseException:
            if temp_profile is not None:
                _discard_profile_clone(temp_profile)
            pinning.close()
            with contextlib.suppress(Exception):
                playwright.stop()
            raise
        session = BrowserSession(
            playwright=playwright, browser_ref=ctx, page=page, url_allowed=url_allowed,
        )
        session._temp_profile = temp_profile
        session._proxy = pinning
        return session

    # Headless — no persistent profile. Wrapped for the same reason the
    # `profile_path` branch above is: `sync_playwright().start()` has already
    # spawned the driver process, and a launch that raises here left it running
    # with nothing holding a reference. In server mode `ensure_session`
    # re-launches per request, so a browser that cannot start leaked one driver
    # per attempt until the machine ran out.
    try:
        if browser == "firefox":
            browser_instance = playwright.firefox.launch(**options)
            context = browser_instance.new_context()
        elif browser == "chrome":
            browser_instance = playwright.chromium.launch(channel="chrome", **options)
            context = browser_instance.new_context()
        else:
            browser_instance = playwright.chromium.launch(**options)
            context = browser_instance.new_context()
        page = context.new_page()
        install_request_guard(page, url_allowed)
    except BaseException:
        pinning.close()
        with contextlib.suppress(Exception):
            playwright.stop()
        raise
    session = BrowserSession(
        playwright=playwright,
        browser_ref=(browser_instance, context),
        page=page,
        url_allowed=url_allowed,
    )
    session._proxy = pinning
    return session


@contextmanager
def open_browser_session(
    browser: str = "chromium",
    profile_path: str | None = None,
    *,
    headless: bool = True,
    url_allowed: Callable[[str], bool] = default_url_allowed,
    getaddrinfo: Callable[..., Any] = socket.getaddrinfo,
    connect: Callable[..., socket.socket] = socket.create_connection,
    max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
) -> Iterator[BrowserSession]:
    """Context manager: guaranteed cleanup even on exception.

    Usage:
        with open_browser_session() as session:
            session.navigate("https://example.com")
            ...
    """
    session = launch_browser(
        browser,
        profile_path,
        headless=headless,
        url_allowed=url_allowed,
        getaddrinfo=getaddrinfo,
        connect=connect,
        max_response_bytes=max_response_bytes,
    )
    try:
        yield session
    finally:
        session.close()
