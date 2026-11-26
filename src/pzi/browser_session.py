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

import asyncio
import contextlib
import os
import re
import shutil
import socket
import stat
import tempfile
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit, urlunsplit

from pzi.pinning_proxy import DEFAULT_MAX_RESPONSE_BYTES, PinningProxy

#: `fetch_direct` follows redirects manually (see below) rather than letting
#: Playwright's `request.get` do it, so this bounds how many hops a
#: pathological or malicious redirect chain gets before it is refused.
_MAX_FETCH_DIRECT_REDIRECTS = 10

_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})

#: Bound on closing pages, and on moving the session's page to `about:blank`.
_PAGE_CLEANUP_SECONDS = 1.0

#: After pages are closed, how long to wait for a popup Playwright had not
#: reported yet, so it is closed too. Chromium reported one while the page
#: that opened it was closing, Firefox 11-20 ms after (navdl `exp_popup_race`).
_POPUP_SETTLE_SECONDS = 0.2

#: Rounds `leave_page` makes of "close the others, then blank the session's
#: page". A popup opened between the two steps is caught by the next round.
_LEAVE_PAGE_ROUNDS = 3

#: Prefix for cloned-profile temp dirs, shared by the cleanup sweep below.
_CLONE_PREFIX = "pzi-chrome-"
#: The same, for a Firefox profile's copy.
_FIREFOX_CLONE_PREFIX = "pzi-firefox-"

#: What a Firefox profile's copy leaves out: its locks (a copy holding them
#: looks in use), caches, and service-worker registrations. Each worker's
#: Cache API storage, `storage/<kind>/<origin>/cache`, is left out too.
_FIREFOX_SKIPPED = frozenset(
    {
        "lock",
        ".parentlock",
        "parent.lock",
        "cache2",
        "startupCache",
        "OfflineCache",
        "shader-cache",
        "jumpListCache",
        "serviceworker.txt",
    }
)

#: Age after which a clone with no live owner is swept. Age alone is not proof
#: of abandonment: a `pzi server` run without `--stop-after` keeps one session,
#: and its clone, for as long as the server runs. The owner lock below is what
#: keeps a live session's clone from being swept.
_CLONE_MAX_AGE_SECONDS = 6 * 60 * 60

#: File inside each clone that its owning process holds an exclusive `flock` on.
#: The kernel drops the lock when that process exits or is killed, so a clone
#: whose lock can be taken has no owner and is safe to sweep once it is old.
#: Once locked, it holds the owner's process ID, so a clone whose owner is
#: gone is swept at once (`_owner_is_gone`).
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
        for part in _split_header_values(content_type)
    )


def _split_header_values(value: str) -> list[str]:
    """Split a header on commas outside quoted strings (RFC 9110 §5.6)."""
    parts: list[str] = []
    current: list[str] = []
    quoted = escaped = False
    for char in value:
        if escaped:
            escaped = False
        elif quoted and char == "\\":
            escaped = True
        elif char == '"':
            quoted = not quoted
        elif char == "," and not quoted:
            parts.append("".join(current))
            current = []
            continue
        current.append(char)
    parts.append("".join(current))
    return parts


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


def _bounded_evaluate(page: Any, js: str, seconds: float) -> Any:
    """`page.evaluate(js)`, abandoned with `TimeoutError` after *seconds*.

    Awaited on Playwright's loop through the sync wrapper's private `_sync`
    and `_impl_obj`, as `_close_pages` does. An abandoned call is left to
    finish or fail when its page closes; its outcome is collected then, so
    asyncio reports nothing.
    """

    async def evaluate() -> Any:
        task = asyncio.ensure_future(page._impl_obj.evaluate(js))
        done, _ = await asyncio.wait({task}, timeout=max(seconds, 0.0))
        if not done:
            task.add_done_callback(_collect_outcome)
            raise TimeoutError(f"evaluate did not answer within {max(seconds, 0.0):.1f} s")
        return task.result()

    return page._sync(evaluate())


def _collect_outcome(task: asyncio.Future[Any]) -> None:
    if not task.cancelled():
        task.exception()


def _close_pages(pages: list[Any], seconds: float) -> None:
    """Close *pages* together, waiting at most *seconds* in all; never raise.

    `Page.close` has no timeout, and Chromium takes 0.5 s over a page whose
    script is busy or spins in `unload` (navdl `exp_page_costs`). The closes
    are awaited at once on Playwright's loop, through the sync wrapper's
    private `_sync` and `_impl_obj`, so a page that opened many popups costs
    *seconds*, not 0.5 s per popup. A page not closed in time is still
    closing.
    """
    if not pages:
        return

    async def close_all() -> None:
        tasks = [asyncio.ensure_future(page._impl_obj.close()) for page in pages]
        _done, pending = await asyncio.wait(tasks, timeout=max(seconds, 0.0))
        for task in pending:
            task.cancel()

    try:
        pages[0]._sync(close_all())
    except Exception:
        for page in pages:
            with contextlib.suppress(Exception):
                page.close()


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
    # Written only once the lock is held: a sweep that finds an ID finds a
    # lock that was taken, so a free lock then means the owner is gone.
    with contextlib.suppress(OSError):
        os.write(fd, str(os.getpid()).encode())


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


def _owner_is_gone(clone: Path) -> bool:
    """True when *clone* is pzi's own copy and the process its owner lock
    names no longer exists.

    pzi's own: a real directory (not a link), this user's, mode 0700, whose
    owner lock file names a process ID. Anything else waits for the age gate.
    A process ID that exists, under any owner, counts as alive.
    """
    try:
        info = clone.lstat()
    except OSError:
        return False
    if not stat.S_ISDIR(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o700:
        return False
    getuid = getattr(os, "getuid", None)
    if getuid is not None and info.st_uid != getuid():
        return False
    try:
        named = (clone / _CLONE_OWNER_FILE).read_text(encoding="ascii").strip()
    except (OSError, UnicodeDecodeError):
        return False
    if not named.isdigit() or int(named) in (0, os.getpid()):
        return False
    try:
        os.kill(int(named), 0)
    except ProcessLookupError:
        return True
    except (OSError, OverflowError):
        return False
    return False


def _sweep_stale_profile_clones(
    *, now: float | None = None, temp_root: Path | None = None
) -> list[Path]:
    """Remove abandoned clones of the user's Chrome or Firefox profile from $TMPDIR.

    The clone is a full copy of the real profile, cookie database included, and
    cleanup lived only in `BrowserSession.close()` — which a browser-hook child
    killed at its 180 s timeout never reaches. There is no `atexit` and nothing
    else sweeps, so every hook timeout left another readable copy of the user's
    logged-in sessions behind until the machine was rebooted.

    A clone is removed only when no process holds its owner lock
    (`_CLONE_OWNER_FILE`), and it is older than `_CLONE_MAX_AGE_SECONDS` or
    its owner is gone (`_owner_is_gone`). A SIGKILLed hook's copy holds the
    user's logins, and the age gate alone left it for six hours (review-nd9
    `probe_ffprofile9`). Returns what it removed, so a test can assert on it.
    """
    root = temp_root or Path(tempfile.gettempdir())
    cutoff = (now if now is not None else time.time()) - _CLONE_MAX_AGE_SECONDS
    removed: list[Path] = []
    try:
        candidates = [
            path
            for prefix in (_CLONE_PREFIX, _FIREFOX_CLONE_PREFIX)
            for path in root.glob(f"{prefix}*")
        ]
    except OSError:  # pragma: no cover — an unreadable $TMPDIR is the OS's problem
        return removed
    for candidate in candidates:
        try:
            if not candidate.is_dir():
                continue
            if candidate.stat().st_mtime > cutoff and not _owner_is_gone(candidate):
                continue
        except OSError:
            continue
        if _clone_has_live_owner(candidate):
            continue
        shutil.rmtree(candidate, ignore_errors=True)
        if not candidate.exists():
            removed.append(candidate)
    return removed


#: Where in a clone the profile's copy goes, and the browser runs. The
#: clone's root holds only it and the owner lock, so `copytree` never sets the
#: root's mode or mtime: the root stays 0700 throughout, and its mtime is when
#: pzi made the copy, which the sweep's age gate reads (review-st1
#: `probe_copystat_window`, `probe_sweep`).
_CLONE_PROFILE_DIR = "profile"
#: In a clone's root: each link the copy left out, and why. Kept beside the
#: copy, not in it, and removed with it.
_CLONE_LOG_FILE = "copy.log"


def _clone_profile(
    profile: Path, prefix: str, ignore: Callable[[str, list[str]], list[str]]
) -> Path:
    """Copy a browser profile into a fresh private (0700) temp directory
    that holds its owner lock, leaving out what *ignore* names.

    Returns the clone's root; the browser runs on `_clone_profile_dir` of
    it. The copy holds no links, so the browser never writes outside it: a
    link copied as a link let it write through a cookie database linked out
    of the profile (review-st1 `probe_symlink_write`). See `_link_left_out`
    for what a link becomes.
    """
    _sweep_stale_profile_clones()
    temp_dir = Path(tempfile.mkdtemp(prefix=prefix))
    # Tighten permissions so other users on the system cannot read
    # the cloned profile (which contains cookies and session tokens).
    os.chmod(temp_dir, 0o700)
    _claim_profile_clone(temp_dir)
    source = profile.resolve()
    left_out: list[str] = []

    def _ignore_unsafe_links(directory: str, contents: list[str]) -> list[str]:
        skipped = ignore(directory, contents)
        for name in contents:
            path = Path(directory) / name
            if name in skipped or not path.is_symlink():
                continue
            reason = _link_left_out(path, profile, source)
            if reason is not None:
                skipped.append(name)
                left_out.append(f"left out {path.relative_to(profile)}: {reason}\n")
        return skipped

    try:
        shutil.copytree(profile, _clone_profile_dir(temp_dir), ignore=_ignore_unsafe_links)
        if left_out:
            (temp_dir / _CLONE_LOG_FILE).write_text("".join(left_out), encoding="utf-8")
    except BaseException:
        # The caller never receives `temp_dir`, so nothing else could remove
        # the partial copy of the cookie database or release its owner lock.
        _discard_profile_clone(temp_dir)
        raise
    return temp_dir


def _link_left_out(link: Path, profile: Path, source: Path) -> str | None:
    """Why the copy leaves out *link*, or None to copy what it names.

    A link to a file is copied as the file. A link to a directory inside
    *source* (*profile*, resolved) is copied as the directory, unless that
    loops. Anything else is left out: a link to a
    directory outside the profile, which may be any size, one that dangles
    or loops, and one to neither a file nor a directory (`/dev/zero` would
    never end).
    """
    try:
        target = link.resolve(strict=True)
    except (OSError, RuntimeError):
        return "a link that dangles or loops"
    if target.is_file():
        return None
    if not target.is_dir():
        return "a link to neither a file nor a directory"
    if not target.is_relative_to(source):
        return "a link to a directory outside the profile"
    # The copy reaches *link* through the links it followed: each directory
    # on that path, resolved, must not lie inside what *link* names. One that
    # names a directory it is in, or two that name each other, would recurse
    # forever.
    walked = [link.parent, *link.parent.parents]
    if any(d.resolve().is_relative_to(target) for d in walked if d.is_relative_to(profile)):
        return "a link that loops"
    return None


def _clone_profile_dir(clone: Path) -> Path:
    """The profile's copy inside *clone*, which the browser runs on."""
    return clone / _CLONE_PROFILE_DIR


def _clone_firefox_profile(profile: Path) -> Path:
    """Copy a Firefox profile, as `_clone_chrome_profile` copies Chrome's.

    Firefox ran on the user's profile in place, so the browser wrote into it
    (owner decision, "Copy it like Chrome"). Logins come along
    (`cookies.sqlite`, `key4.db`, `logins.json`); `_FIREFOX_SKIPPED` does not.
    """

    def _ignore(directory: str, contents: list[str]) -> list[str]:
        skipped = [c for c in contents if c in _FIREFOX_SKIPPED]
        parts = Path(directory).relative_to(profile).parts
        if len(parts) >= 2 and parts[0] == "storage" and "cache" in contents:
            skipped.append("cache")
        return skipped

    return _clone_profile(profile, _FIREFOX_CLONE_PREFIX, _ignore)


def _clone_chrome_profile(profile: Path) -> Path:
    """Clone a Chrome user-data dir to a temporary location.

    Chrome refuses to enable remote debugging on its *default* user-data
    directory. Copying to a temp dir satisfies the "non-default" requirement
    while preserving cookies and session state.
    """

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

    return _clone_profile(profile, _CLONE_PREFIX, _ignore)


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


def _without_query(url: str) -> str:
    """*url* reduced to scheme, host, port and path, for a refusal reason.

    A landed URL is chosen by the redirect, not the user. An SSO hop puts its
    ticket in the query, a session id in a path parameter (`;jsessionid=`),
    and a login can carry `user:password@`. The reason is recorded, sent back
    in the server's error body and printed, so the path is cut at its first
    `;` and the userinfo is dropped.
    """
    try:
        parts = urlsplit(url)
        host = parts.hostname or ""
        port = parts.port
    except ValueError:
        # Unparseable (an unclosed `[`): cut by hand, the same way.
        head = re.split(r"[?#;]", url, maxsplit=1)[0]
        scheme, sep, rest = head.partition("//")
        if not sep:
            return head
        authority, slash, path = rest.partition("/")
        return f"{scheme}//{authority.rpartition('@')[2]}{slash}{path}"
    if ":" in host:
        host = f"[{host}]"
    netloc = host if port is None else f"{host}:{port}"
    return urlunsplit((parts.scheme, netloc, parts.path.split(";", 1)[0], "", ""))


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
            raise SsrfBlocked(f"refusing to navigate to a non-public URL: {_without_query(url)}")
        response = self.page.goto(url, wait_until=wait_until, timeout=timeout)
        if response is None:
            # `goto` loads nothing for a same-document navigation: the URL
            # differs from the showing one only by its fragment, or the page
            # already `pushState`d to it. The document is then an earlier
            # request's; refusing it answered "no PDF" to every repeat of a URL
            # (review of 658d4f9). `reload()` is no fix: it replays the stale
            # document's own history entry, which may be a form POST or a URL
            # its script swapped in (review of 22d91d7). Leave the document,
            # then load `url` itself with a plain GET.
            self.page.goto("about:blank")
            response = self.page.goto(url, wait_until=wait_until, timeout=timeout)
        self._reject_non_public_landing()
        return response

    def _reject_non_public_landing(self) -> None:
        from pzi.safe_http import SsrfBlocked

        landed = ""
        with contextlib.suppress(Exception):
            landed = self.page.url or ""
        if landed and not self.url_allowed(landed):
            raise SsrfBlocked(
                f"browser landed on a non-public URL: {_without_query(landed)}"
            )

    def leave_page(self) -> None:
        """Close every other page, then leave the session's page on
        `about:blank`; never raise.

        A document left loaded, or a popup it opened, can download while pzi
        is idle, where nothing cancels it (review-nd5 `idle`, review-nd7
        `probe_idle7`: 768 MiB on disk). The other pages close FIRST: a popup
        left open while the session's page goes blank can navigate its opener
        back to a downloading page (review-nd9 `probe_reopen9`). A popup the
        session's page opens between the two steps is caught by the next
        round. A page whose script spins cannot leave in time, so it is
        closed and a fresh page, covered by the context's request guard,
        becomes the session's page. Bounded by about
        `_LEAVE_PAGE_ROUNDS * 3 * _PAGE_CLEANUP_SECONDS`.
        """
        for _ in range(_LEAVE_PAGE_ROUNDS):
            self._close_other_pages(_PAGE_CLEANUP_SECONDS, settle=_POPUP_SETTLE_SECONDS)
            try:
                self.page.goto("about:blank", timeout=_PAGE_CLEANUP_SECONDS * 1000)
            except Exception:
                with contextlib.suppress(Exception):
                    stuck, self.page = self.page, self.page.context.new_page()
                    _close_pages([stuck], _PAGE_CLEANUP_SECONDS)
            if not self._other_pages():
                return

    def _close_other_pages(self, seconds: float, *, settle: float = 0.0) -> None:
        """Close every page of the context but the session's own, within
        *seconds* in all.

        A popup is reported to Playwright a moment after it opens, so a page
        may appear while others close, or up to *settle* seconds after; it is
        closed too.
        """
        ends = time.monotonic() + seconds
        while time.monotonic() < ends:
            others = self._other_pages()
            if others:
                _close_pages(others, ends - time.monotonic())
                continue
            wait = min(settle, ends - time.monotonic())
            if wait <= 0:
                return
            try:
                self.page.wait_for_timeout(wait * 1000)
            except Exception:
                return
            if not self._other_pages():
                return

    def _other_pages(self) -> list[Any]:
        try:
            return [other for other in self.page.context.pages if other is not self.page]
        except Exception:
            return []

    def current_url(self) -> str:
        """Return the current page URL."""
        self._check_open()
        return self.page.url

    # ------------------------------------------------------------------
    # JavaScript evaluation
    # ------------------------------------------------------------------

    def evaluate(self, js: str, *, timeout: float | None = None) -> Any:
        """Evaluate JavaScript in the page and return the result.

        *timeout*, in seconds, bounds the call: `Page.evaluate` has none, and
        a page whose script spins never answers, so discovery hung with the
        session's lock held (review-nd6 `probe_close busy`). Past it this
        raises `TimeoutError`; the page stays as it is, so the caller leaves
        it (`leave_page`).
        """
        self._check_open()
        if timeout is None:
            return self.page.evaluate(js)
        return _bounded_evaluate(self.page, js, timeout)

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
    target: Any, url_allowed: Callable[[str], bool] = default_url_allowed
) -> None:
    """Refuse browser requests to non-public destinations.

    *target* is the browser context, so every page of it is covered: a page's
    own route never sees a popup's first request, which reached a refused URL
    (review-nd5 `probe_nd5 popguard`).

    Known limit: a SharedWorker's, a WebSocket's or a service worker's own
    fetches do not pass through any Playwright route. Only the pinning proxy
    (`launch_browser`) stops those from reaching a non-public address.

    ``safe_http`` protects everything pzi fetches itself, but a Playwright page
    fetches through the *browser's* stack: it follows redirects and resolves DNS
    on its own, so validating only the URL handed in leaves
    ``https://public.example/x`` → ``http://127.0.0.1:8765/`` (or a DNS name
    that resolves to a private address) entirely unguarded. Routing every
    request through the same public-URL predicate refuses each new request
    to a non-public URL. It does NOT see a navigation's redirect hops: the
    browser follows a 302 without asking the route (review-w4
    `probe_redirect_hits`). The pinning proxy refuses such a hop by
    address, and the landing check refuses a navigation that ends on one.

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
        target.route("**/*", _guard)
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
        # user's profile, cookie database included, and a failed launch
        # used to abandon it in $TMPDIR where nothing ever removed it — along
        # with the Playwright driver process it had already started.
        try:
            if browser == "firefox":
                # Never the user's profile in place: the browser writes to it
                # (owner decision, "Copy it like Chrome").
                temp_profile = _clone_firefox_profile(profile)
            else:
                # Chrome refuses remote debugging on its default user-data dir.
                # Clone to a temp location to satisfy the "non-default"
                # requirement. Every other name, `webkit` included, launches
                # Chromium below, so it gets the same copy (review-st1
                # `probe_webkit_inplace`).
                temp_profile = _clone_chrome_profile(profile)
            profile = _clone_profile_dir(temp_profile)
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
            install_request_guard(ctx, url_allowed)
            page = ctx.new_page()
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
        install_request_guard(context, url_allowed)
        page = context.new_page()
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
