"""Tests for browser_session.py — BrowserSession and lifecycle management."""


import pytest

from pzi.browser_session import (
    BrowserSession,
    FetchResult,
    browser_launch_options,
)

# === FetchResult ===


def test_fetch_result_pdf() -> None:
    r = FetchResult(status=200, content_type="application/pdf", body=b"%PDF-1.4 ok")
    assert r.is_pdf()

    r2 = FetchResult(status=200, content_type="text/html", body=b"<html>")
    assert not r2.is_pdf()

    r3 = FetchResult(status=404, content_type="application/pdf", body=b"%PDF-")
    assert not r3.is_pdf()

    r4 = FetchResult(status=200, content_type="application/pdf", body=b"not pdf")
    assert not r4.is_pdf()


# === browser_launch_options ===


def test_launch_options_chromium() -> None:
    opts = browser_launch_options("chromium")
    assert opts == {"headless": True}


def test_launch_options_firefox() -> None:
    opts = browser_launch_options("firefox")
    assert opts["headless"] is True
    assert opts["firefox_user_prefs"]["pdfjs.disabled"] is True


# === BrowserSession basic operations ===


def test_session_navigate_and_url() -> None:
    class FakePage:
        url = "https://example.com/article"
        def goto(self, url, **kw):
            return "response"

    s = BrowserSession(playwright="pw", browser_ref="br", page=FakePage())
    resp = s.navigate("https://example.com")
    assert resp == "response"
    assert s.current_url() == "https://example.com/article"


def test_session_evaluate() -> None:
    class FakePage:
        def evaluate(self, js):
            return ["result1", "result2"]

    s = BrowserSession(playwright="pw", browser_ref="br", page=FakePage())
    result = s.evaluate("some js")
    assert result == ["result1", "result2"]


def test_session_fetch_direct_pdf() -> None:
    class FakeResponse:
        status = 200
        headers = {"content-type": "application/pdf"}
        def body(self):
            return b"%PDF-1.4 direct"

    class FakeRequest:
        def get(self, url, **kwargs):
            return FakeResponse()

    class FakePage:
        request = FakeRequest()

    s = BrowserSession(playwright="pw", browser_ref="br", page=FakePage())
    result = s.fetch_direct("https://example.com/paper.pdf")
    assert result.status == 200
    assert result.is_pdf()
    assert result.body == b"%PDF-1.4 direct"


def test_session_fetch_direct_exception() -> None:
    class FakeRequest:
        def get(self, url, **kwargs):
            raise RuntimeError("network failure")

    class FakePage:
        request = FakeRequest()

    s = BrowserSession(playwright="pw", browser_ref="br", page=FakePage())
    result = s.fetch_direct("https://example.com/paper.pdf")
    assert result.status == -1
    assert not result.is_pdf()


def test_session_fetch_direct_never_requests_a_redirect_to_loopback() -> None:
    """`request.get` following redirects itself only re-checks the final URL.

    A validated public start URL that 302s to a loopback target must have that
    target refused *before* it is ever requested — not merely excluded from
    the result once fetched.
    """
    requested: list[str] = []

    class FakeResponse:
        def __init__(self, status, headers):
            self.status = status
            self.headers = headers

        def body(self):
            raise AssertionError("body() must not be called on a refused hop")

    class FakeRequest:
        def get(self, url, **kwargs):
            requested.append(url)
            assert kwargs.get("max_redirects") == 0
            return FakeResponse(302, {"location": "http://127.0.0.1:8765/admin"})

    class FakePage:
        request = FakeRequest()

    s = BrowserSession(playwright="pw", browser_ref="br", page=FakePage())
    result = s.fetch_direct("https://example.com/paper.pdf")

    assert requested == ["https://example.com/paper.pdf"]
    assert "http://127.0.0.1:8765/admin" not in requested
    assert result.status == -1
    assert not result.is_pdf()


def test_session_fetch_direct_follows_a_safe_redirect_chain() -> None:
    """The contrast: a redirect to another public host is still followed."""

    class FakeResponse:
        def __init__(self, status, headers, body_bytes=b""):
            self.status = status
            self.headers = headers
            self._body = body_bytes

        def body(self):
            return self._body

    responses = {
        "https://example.com/paper.pdf": FakeResponse(
            302, {"location": "https://mirror.example/paper.pdf"}
        ),
        "https://mirror.example/paper.pdf": FakeResponse(
            200, {"content-type": "application/pdf"}, b"%PDF-1.4 mirrored"
        ),
    }

    class FakeRequest:
        def get(self, url, **kwargs):
            return responses[url]

    class FakePage:
        request = FakeRequest()

    s = BrowserSession(playwright="pw", browser_ref="br", page=FakePage())
    result = s.fetch_direct("https://example.com/paper.pdf")

    assert result.status == 200
    assert result.body == b"%PDF-1.4 mirrored"


class _DisposableResponse:
    def __init__(self, status, headers, body_bytes=b"", body_error=None):
        self.status = status
        self.headers = headers
        self._body = body_bytes
        self._body_error = body_error
        self.disposed = 0

    def body(self):
        if self._body_error is not None:
            raise self._body_error
        return self._body

    def dispose(self):
        self.disposed += 1


def _fetch_with(responses_by_call, *, allowed=lambda u: True):
    """Run fetch_direct against scripted responses; return result and responses."""
    issued = []
    calls = iter(responses_by_call)

    class FakeRequest:
        def get(self, url, **kwargs):
            response = next(calls)
            issued.append(response)
            return response

    class FakePage:
        request = FakeRequest()

    s = BrowserSession(
        playwright="pw", browser_ref="br", page=FakePage(), url_allowed=allowed
    )
    return s.fetch_direct("https://example.com/paper.pdf"), issued


def test_fetch_direct_disposes_success_response_and_keeps_bytes() -> None:
    ok = _DisposableResponse(200, {"content-type": "application/pdf"}, b"%PDF-1.4 ok")
    result, issued = _fetch_with([ok])
    assert result.body == b"%PDF-1.4 ok"
    assert [r.disposed for r in issued] == [1]


def test_fetch_direct_disposes_non_200_response() -> None:
    nf = _DisposableResponse(404, {"content-type": "text/html"}, b"nope")
    result, issued = _fetch_with([nf])
    assert result.status == 404
    assert [r.disposed for r in issued] == [1]


def test_fetch_direct_disposes_every_redirect_hop() -> None:
    hop = _DisposableResponse(302, {"location": "https://mirror.example/p.pdf"})
    ok = _DisposableResponse(200, {"content-type": "application/pdf"}, b"%PDF-1.4 m")
    result, issued = _fetch_with([hop, ok])
    assert result.body == b"%PDF-1.4 m"
    assert [r.disposed for r in issued] == [1, 1]


def test_fetch_direct_disposes_rejected_redirect_hop() -> None:
    hop = _DisposableResponse(302, {"location": "http://127.0.0.1/admin"})
    result, issued = _fetch_with([hop], allowed=lambda u: "127.0.0.1" not in u)
    assert result.status == -1
    assert [r.disposed for r in issued] == [1]


def test_fetch_direct_disposes_redirect_without_location() -> None:
    hop = _DisposableResponse(302, {})
    result, issued = _fetch_with([hop])
    assert result.status == -1
    assert [r.disposed for r in issued] == [1]


def test_fetch_direct_disposes_when_body_raises() -> None:
    bad = _DisposableResponse(200, {}, body_error=RuntimeError("boom"))
    result, issued = _fetch_with([bad])
    assert result.status == -1
    assert [r.disposed for r in issued] == [1]


def test_fetch_direct_disposes_every_response_on_exhausted_hops() -> None:
    from pzi.browser_session import _MAX_FETCH_DIRECT_REDIRECTS

    hops = [
        _DisposableResponse(302, {"location": f"https://h{i}.example/p"})
        for i in range(_MAX_FETCH_DIRECT_REDIRECTS + 1)
    ]
    result, issued = _fetch_with(hops)
    assert result.status == -1
    assert len(issued) == _MAX_FETCH_DIRECT_REDIRECTS + 1
    assert all(r.disposed == 1 for r in issued)


def test_fetch_direct_survives_a_dispose_that_raises() -> None:
    ok = _DisposableResponse(200, {"content-type": "application/pdf"}, b"%PDF-1.4 ok")

    def boom():
        raise RuntimeError("driver gone")

    ok.dispose = boom  # type: ignore[method-assign]
    result, _ = _fetch_with([ok])
    assert result.body == b"%PDF-1.4 ok"


def test_session_wait_network_idle() -> None:
    class FakePage:
        def wait_for_load_state(self, state, timeout=None):
            pass

    s = BrowserSession(playwright="pw", browser_ref="br", page=FakePage())
    s.wait_network_idle()
    # Should not raise


# === BrowserSession lifecycle ===


def test_session_close_idempotent() -> None:
    events = []

    class FakePlaywright:
        def stop(self):
            events.append("stop")

    class FakeContext:
        def close(self):
            events.append("ctx_close")

    class FakeBrowser:
        def close(self):
            events.append("browser_close")

    class FakePage:
        pass

    s = BrowserSession(
        playwright=FakePlaywright(),
        browser_ref=(FakeBrowser(), FakeContext()),
        page=FakePage(),
    )
    s.close()
    s.close()  # idempotent
    assert events == ["ctx_close", "browser_close", "stop"]


def test_session_close_persistent() -> None:
    events = []

    class FakePlaywright:
        def stop(self):
            events.append("stop")

    class FakePersistent:
        def close(self):
            events.append("persist_close")

    s = BrowserSession(
        playwright=FakePlaywright(),
        browser_ref=FakePersistent(),
        page=object(),
    )
    s.close()
    assert events == ["persist_close", "stop"]


def test_session_close_after_closed() -> None:
    s = BrowserSession(playwright=object(), browser_ref=object(), page=object())
    s.close()
    with pytest.raises(RuntimeError, match="closed"):
        s.navigate("https://example.com")


# === open_browser_session context manager ===
#
# The permanently-skipped placeholder that used to sit here ("needs full
# Playwright mock for launch") had an empty body and could never run. The
# context manager is covered below through an injected session instead.


def test_open_browser_session_closes_on_exception(monkeypatch) -> None:
    """Guaranteed cleanup is the whole point of the context manager."""
    from pzi import browser_session

    closed: list[bool] = []

    class _Session:
        def close(self) -> None:
            closed.append(True)

    monkeypatch.setattr(
        browser_session, "launch_browser", lambda *_a, **_k: _Session()
    )

    try:
        with browser_session.open_browser_session():
            raise RuntimeError("boom")
    except RuntimeError:
        pass

    assert closed == [True]


# === A failed launch must not leave a copy of the user's profile behind ===


def test_a_failed_launch_removes_the_cloned_chrome_profile(tmp_path, monkeypatch) -> None:
    """The clone is a full copy of the user's Chrome profile — cookie DB included.

    `launch_persistent_context` failing after the copy left it in `$TMPDIR`,
    where nothing ever removed it, and abandoned the Playwright driver process
    with it.
    """
    from unittest.mock import MagicMock

    from pzi import browser_session as module

    clone = tmp_path / "clone"
    clone.mkdir()
    (clone / "Cookies").write_bytes(b"secrets")
    monkeypatch.setattr(module, "_clone_chrome_profile", lambda _p: clone)

    playwright = MagicMock()
    playwright.chromium.launch_persistent_context.side_effect = RuntimeError("no browser")
    monkeypatch.setitem(
        __import__("sys").modules,
        "playwright.sync_api",
        MagicMock(sync_playwright=MagicMock(return_value=MagicMock(start=lambda: playwright))),
    )

    with pytest.raises(RuntimeError):
        module.launch_browser("chromium", str(tmp_path / "profile"))

    assert not clone.exists()
    playwright.stop.assert_called_once()


def test_a_browser_killed_underneath_us_is_not_reused() -> None:
    """`_closed` is set only by `close()`.

    So a browser that died on its own — OOM, a crash, the user quitting it —
    left the flag False, `_check_open` said the session was fine, and
    `BrowserSessionManager` handed the corpse back on every later call.
    `download_pdf`'s blanket except then returned None until the server was
    restarted: three failed downloads for one launch.
    """
    from pzi.browser_session import BrowserSession

    class _DeadBrowser:
        def is_connected(self):
            return False

    session = BrowserSession.__new__(BrowserSession)
    session._closed = False
    session.browser_ref = _DeadBrowser()

    with pytest.raises(RuntimeError, match="no longer running"):
        session._check_open()


def test_a_live_browser_still_passes_the_check() -> None:
    """The probe must not refuse work on a healthy session."""
    from pzi.browser_session import BrowserSession

    class _LiveBrowser:
        def is_connected(self):
            return True

    session = BrowserSession.__new__(BrowserSession)
    session._closed = False
    session.browser_ref = _LiveBrowser()

    session._check_open()  # does not raise


def test_a_browser_handle_that_cannot_answer_is_treated_as_alive() -> None:
    """This is a liveness probe, not an excuse to refuse work — a handle
    without `is_connected` (or one that raises) must not block the session."""
    from pzi.browser_session import BrowserSession

    class _Opaque:
        pass

    session = BrowserSession.__new__(BrowserSession)
    session._closed = False
    session.browser_ref = _Opaque()

    session._check_open()  # does not raise


# === profile-clone sweep ===


def _old_clone(root, name: str):
    import os

    clone = root / f"pzi-chrome-{name}"
    clone.mkdir()
    (clone / "Cookies").write_bytes(b"secrets")
    os.utime(clone, (1_000, 1_000))
    return clone


def test_sweep_removes_an_over_age_clone_with_no_owner(tmp_path) -> None:
    from pzi.browser_session import _sweep_stale_profile_clones

    clone = _old_clone(tmp_path, "orphan")
    assert _sweep_stale_profile_clones(temp_root=tmp_path) == [clone]
    assert not clone.exists()


def test_sweep_keeps_an_over_age_clone_a_live_session_owns(tmp_path, monkeypatch) -> None:
    """`pzi server` run without `--stop-after` keeps a session past six hours."""
    from pzi import browser_session as module

    monkeypatch.setattr(module.tempfile, "tempdir", str(tmp_path))
    profile = tmp_path / "profile"
    profile.mkdir()
    (profile / "Cookies").write_bytes(b"secrets")
    clone = module._clone_chrome_profile(profile)
    assert clone.parent == tmp_path and (clone / "Cookies").read_bytes() == b"secrets"
    import os

    os.utime(clone, (1_000, 1_000))

    assert module._sweep_stale_profile_clones(temp_root=tmp_path) == []
    assert (clone / "Cookies").exists()

    # Once the owner releases it (session close / process exit) it is swept.
    module._discard_profile_clone(clone)
    assert not clone.exists()


def test_sweep_removes_an_over_age_clone_whose_owner_died(tmp_path, monkeypatch) -> None:
    """The lock dies with its process, so a killed hook child leaves a sweepable clone."""
    from pzi import browser_session as module

    monkeypatch.setattr(module.tempfile, "tempdir", str(tmp_path))
    profile = tmp_path / "profile"
    profile.mkdir()
    clone = module._clone_chrome_profile(profile)
    import os

    os.utime(clone, (1_000, 1_000))
    # Simulate owner death: drop the held descriptor without removing the dir.
    module._release_profile_clone(clone)

    assert module._sweep_stale_profile_clones(temp_root=tmp_path) == [clone]
    assert not clone.exists()


def test_failed_profile_copy_leaves_no_clone_and_no_lock(tmp_path, monkeypatch) -> None:
    """A copytree that raises must not leak a locked partial copy of the cookie DB."""
    import shutil

    from pzi import browser_session as module

    monkeypatch.setattr(module.tempfile, "tempdir", str(tmp_path))
    profile = tmp_path / "profile"
    profile.mkdir()
    (profile / "Cookies").write_bytes(b"secrets")

    def failing_copytree(src, dst, *args, **kwargs):
        (dst / "Cookies").write_bytes(b"partial")
        raise shutil.Error([("Cookies-journal", "x", "No such file or directory")])

    monkeypatch.setattr(module.shutil, "copytree", failing_copytree)
    owned_before = set(module._OWNED_CLONE_FDS)
    with pytest.raises(shutil.Error):
        module._clone_chrome_profile(profile)

    assert sorted(p.name for p in tmp_path.glob("pzi-chrome-*")) == []
    assert set(module._OWNED_CLONE_FDS) == owned_before


def test_sweep_never_probes_a_clone_this_process_owns(tmp_path, monkeypatch) -> None:
    """flock on NFS is a per-process POSIX lock: probing our own clone would drop it."""
    import fcntl
    import os

    from pzi import browser_session as module

    monkeypatch.setattr(module.tempfile, "tempdir", str(tmp_path))
    profile = tmp_path / "profile"
    profile.mkdir()
    clone = module._clone_chrome_profile(profile)
    os.utime(clone, (1_000, 1_000))
    calls: list[int] = []
    real_flock = fcntl.flock
    monkeypatch.setattr(fcntl, "flock", lambda fd, op: (calls.append(op), real_flock(fd, op))[1])

    assert module._sweep_stale_profile_clones(temp_root=tmp_path) == []
    assert calls == []
    assert clone in module._OWNED_CLONE_FDS
    module._discard_profile_clone(clone)


def test_navigate_loads_the_url_afresh_after_a_same_document_navigation() -> None:
    """`goto` answers None when only the fragment differs: nothing was loaded.

    The showing document is then an earlier request's. `navigate` leaves it and
    loads *url* itself; a `reload()` would replay that document's own history
    entry (a POST, or a URL its script swapped in).
    """

    class Page:
        url = "https://journal.test/b-landing#main"

        def __init__(self) -> None:
            self.calls: list[str] = []

        def goto(self, url, **kwargs):
            self.calls.append(url)
            if len(self.calls) == 1:
                return None
            return None if url == "about:blank" else "fresh-response"

        def reload(self, **kwargs):
            raise AssertionError("reload replays the stale history entry")

    page = Page()
    s = BrowserSession(playwright="pw", browser_ref="br", page=page, url_allowed=lambda u: True)
    assert s.navigate("https://journal.test/b-landing#main") == "fresh-response"
    assert page.calls == [
        "https://journal.test/b-landing#main",
        "about:blank",
        "https://journal.test/b-landing#main",
    ]


# === profile copies: mode, links, Firefox, dead owners ===


def _scratch_profile(root, mode: int = 0o755):
    import os

    profile = root / "profile"
    profile.mkdir()
    (profile / "cookies.sqlite").write_bytes(b"login")
    os.chmod(profile / "cookies.sqlite", 0o600)
    outside = root / "outside"
    outside.mkdir()
    (outside / "sentinel.txt").write_text("outside")
    (profile / "linkdir").symlink_to(outside)
    os.chmod(profile, mode)
    return profile


@pytest.mark.parametrize("clone_name", ["_clone_chrome_profile", "_clone_firefox_profile"])
def test_a_profile_copy_is_private_even_when_the_profile_is_not(
    tmp_path, monkeypatch, clone_name: str
) -> None:
    """`copytree` gives the copy's root the source's mode, after the 0700
    chmod, so a 0755 profile left a readable copy of its logins
    (review-nd9 `probe_ffprofile9 755`)."""
    import stat

    from pzi import browser_session as module

    temp_root = tmp_path / "tmp"
    temp_root.mkdir()
    monkeypatch.setattr(module.tempfile, "tempdir", str(temp_root))
    profile = _scratch_profile(tmp_path)
    clone = getattr(module, clone_name)(profile)
    try:
        assert stat.S_IMODE(clone.stat().st_mode) == 0o700
        assert (clone / "cookies.sqlite").read_bytes() == b"login"
    finally:
        module._discard_profile_clone(clone)
    assert not clone.exists()


@pytest.mark.parametrize("clone_name", ["_clone_chrome_profile", "_clone_firefox_profile"])
def test_a_profile_copy_keeps_a_link_out_of_the_profile_a_link(
    tmp_path, monkeypatch, clone_name: str
) -> None:
    """Following it copied whatever the link named into pzi's copy."""
    from pzi import browser_session as module

    monkeypatch.setattr(module.tempfile, "tempdir", str(tmp_path))
    profile = _scratch_profile(tmp_path)
    clone = getattr(module, clone_name)(profile)
    try:
        assert (clone / "linkdir").is_symlink()
    finally:
        module._discard_profile_clone(clone)
    # Removing the copy removes the link, never what it names.
    assert (tmp_path / "outside" / "sentinel.txt").read_text() == "outside"


def test_a_firefox_copy_leaves_out_locks_caches_and_service_workers(
    tmp_path, monkeypatch
) -> None:
    from pzi import browser_session as module

    monkeypatch.setattr(module.tempfile, "tempdir", str(tmp_path))
    profile = tmp_path / "ff"
    profile.mkdir()
    for name in ("lock", ".parentlock", "parent.lock", "serviceworker.txt"):
        (profile / name).write_text("x")
    for name in ("cache2", "startupCache"):
        (profile / name).mkdir()
        (profile / name / "entry").write_text("x")
    worker = profile / "storage" / "default" / "https+++pub.example"
    (worker / "cache").mkdir(parents=True)
    (worker / "cache" / "caches.sqlite").write_text("x")
    (worker / "idb").mkdir()
    for name in ("cookies.sqlite", "key4.db", "logins.json"):
        (profile / name).write_text("login")
    clone = module._clone_firefox_profile(profile)
    try:
        copied = sorted(
            str(p.relative_to(clone)) for p in clone.rglob("*") if p.name != module._CLONE_OWNER_FILE
        )
    finally:
        module._discard_profile_clone(clone)
    assert copied == [
        "cookies.sqlite",
        "key4.db",
        "logins.json",
        "storage",
        "storage/default",
        "storage/default/https+++pub.example",
        "storage/default/https+++pub.example/idb",
    ]


def test_launch_runs_firefox_on_a_copy_of_the_profile(tmp_path, monkeypatch) -> None:
    """Firefox ran on the user's profile in place, and wrote into it
    (owner decision: "Copy it like Chrome")."""
    from unittest.mock import MagicMock

    from pzi import browser_session as module

    temp_root = tmp_path / "tmp"
    temp_root.mkdir()
    monkeypatch.setattr(module.tempfile, "tempdir", str(temp_root))
    profile = _scratch_profile(tmp_path, mode=0o700)
    playwright = MagicMock()
    monkeypatch.setitem(
        __import__("sys").modules,
        "playwright.sync_api",
        MagicMock(sync_playwright=MagicMock(return_value=MagicMock(start=lambda: playwright))),
    )
    session = module.launch_browser("firefox", str(profile))
    used = playwright.firefox.launch_persistent_context.call_args.kwargs["user_data_dir"]
    assert used != str(profile)
    assert used.startswith(str(temp_root / "pzi-firefox-"))
    session.close()
    assert not module.Path(used).exists()


def test_a_killed_owners_copy_is_swept_at_once(tmp_path) -> None:
    """A SIGKILLed hook's copy holds the user's logins; the 6 h age gate
    left it readable for hours after its owner lock was free
    (review-nd9 `probe_ffprofile9`)."""
    import os
    import signal
    import subprocess
    import sys

    from pzi import browser_session as module

    profile = _scratch_profile(tmp_path, mode=0o700)
    temp_root = tmp_path / "tmp"
    temp_root.mkdir()
    child = subprocess.run(
        [
            sys.executable,
            "-c",
            "import os, signal, sys, tempfile\n"
            f"tempfile.tempdir = {str(temp_root)!r}\n"
            "import pzi.browser_session as bs\n"
            f"print(bs._clone_chrome_profile(bs.Path({str(profile)!r})), flush=True)\n"
            "os.kill(os.getpid(), signal.SIGKILL)\n",
        ],
        capture_output=True,
        text=True,
        timeout=60,
        env={**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)},
    )
    assert child.returncode == -signal.SIGKILL, child.stderr
    left = module.Path(child.stdout.strip())
    assert left.is_dir()
    assert module._sweep_stale_profile_clones(temp_root=temp_root) == [left]
    assert not left.exists()


def test_the_sweep_keeps_a_young_copy_that_is_not_pzis_own(tmp_path) -> None:
    """Only a pzi-owned 0700 directory whose owner lock is free and whose
    owner is gone goes before it is old."""
    import os

    from pzi import browser_session as module

    dead = subprocess_pid_that_exited()
    for name, mode in (("pzi-firefox-open", 0o755), ("pzi-firefox-private", 0o700)):
        clone = tmp_path / name
        clone.mkdir()
        os.chmod(clone, mode)
        (clone / module._CLONE_OWNER_FILE).write_text(str(dead))
    (tmp_path / "pzi-firefox-link").symlink_to(tmp_path / "pzi-firefox-private")
    assert module._sweep_stale_profile_clones(temp_root=tmp_path) == [
        tmp_path / "pzi-firefox-private"
    ]
    assert (tmp_path / "pzi-firefox-open").is_dir()


def subprocess_pid_that_exited() -> int:
    import subprocess
    import sys

    child = subprocess.run([sys.executable, "-c", "import os; print(os.getpid())"],
                           capture_output=True, text=True, check=True)
    return int(child.stdout)
