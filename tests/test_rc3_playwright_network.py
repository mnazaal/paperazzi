"""Playwright network boundaries: connected address, route install, body bounds.

Browser-marked tests drive a real Chromium against loopback listeners that
count what reaches them. The listener is the oracle: an assertion on a return
value alone would also pass when the request failed for an unrelated reason.
"""

from __future__ import annotations

import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

import pzi.browser_session as bs
from pzi.url_safety import safe_public_http_url

PUBLIC_IP = "93.184.216.34"


def _answers(*ips: str):
    """A getaddrinfo stand-in returning *ips* for every name."""

    def getaddrinfo(host, port, *args, **kwargs):
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port or 0)) for ip in ips
        ]

    return getaddrinfo


class _Listener:
    """A loopback HTTP server recording every request line that reaches it."""

    def __init__(self, body: bytes = b"<html>private</html>", ctype: str = "text/html"):
        self.hits: list[tuple[str | None, str]] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                outer.hits.append((self.headers.get("Host"), self.path))
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def listener():
    srv = _Listener()
    yield srv
    srv.close()


# ---------------------------------------------------------------------------
# Connected address: the proxy dials only an address it validated
# ---------------------------------------------------------------------------


def _proxy_get(proxy_port: int, absolute_url: str) -> bytes:
    host = absolute_url.split("/")[2]
    with socket.create_connection(("127.0.0.1", proxy_port), timeout=5) as s:
        s.sendall(
            f"GET {absolute_url} HTTP/1.1\r\nHost: {host}\r\n\r\n".encode()
        )
        chunks = []
        while True:
            data = s.recv(4096)
            if not data:
                break
            chunks.append(data)
    return b"".join(chunks)


def test_pinning_proxy_dials_the_validated_public_address(listener) -> None:
    dialed: list[tuple[str, int]] = []

    def connect(address, timeout=None):
        dialed.append(address)
        return socket.create_connection(("127.0.0.1", listener.port), timeout=timeout)

    proxy = bs.PinningProxy(getaddrinfo=_answers(PUBLIC_IP), connect=connect)
    try:
        reply = _proxy_get(proxy.port, "http://pub.test:80/doc")
    finally:
        proxy.close()
    assert dialed == [(PUBLIC_IP, 80)]
    assert b"200 OK" in reply and b"private" in reply
    assert listener.hits and listener.hits[0][1] == "/doc"


@pytest.mark.parametrize("answers", [("127.0.0.1",), (PUBLIC_IP, "127.0.0.1"), ("169.254.169.254",)])
def test_pinning_proxy_refuses_when_any_answer_is_private(listener, answers) -> None:
    dialed: list[object] = []

    def connect(address, timeout=None):
        dialed.append(address)
        return socket.create_connection(("127.0.0.1", listener.port), timeout=timeout)

    proxy = bs.PinningProxy(getaddrinfo=_answers(*answers), connect=connect)
    try:
        reply = _proxy_get(proxy.port, "http://rebind.test:80/secret")
    finally:
        proxy.close()
    assert reply.startswith(b"HTTP/1.1 403")
    assert dialed == []
    assert listener.hits == []


def test_pinning_proxy_refuses_connect_to_private_address(listener) -> None:
    proxy = bs.PinningProxy(getaddrinfo=_answers("127.0.0.1"))
    try:
        with socket.create_connection(("127.0.0.1", proxy.port), timeout=5) as s:
            s.sendall(b"CONNECT rebind.test:443 HTTP/1.1\r\nHost: rebind.test:443\r\n\r\n")
            reply = s.recv(4096)
    finally:
        proxy.close()
    assert reply.startswith(b"HTTP/1.1 403")


# ---------------------------------------------------------------------------
# The same two-answer DNS case on the real browser path
# ---------------------------------------------------------------------------


def _rebind_session(monkeypatch, *, proxy_answers):
    """A real Chromium whose own resolver maps rebind.test to loopback.

    `url_allowed` sees a public answer (the validation lookup); the connect
    lookup answers private. Before the fix Chromium resolved the name itself
    (the host-resolver-rules flag makes that deterministic); after it the
    name is resolved only by the pinning proxy.
    """
    # An ambient proxy would route (and so break) the unfixed path before it
    # could reach the listener, hiding the very defect this test pins.
    for var in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.delenv(var, raising=False)
    orig = bs.browser_launch_options

    def opts(browser, *, headless=True):
        o = orig(browser, headless=headless)
        o["args"] = ["--host-resolver-rules=MAP rebind.test 127.0.0.1"]
        return o

    monkeypatch.setattr(bs, "browser_launch_options", opts)

    def allowed(url: str) -> bool:
        return safe_public_http_url(url, resolve_host=lambda h, p, timeout=None: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", (PUBLIC_IP, p))
        ])

    return bs.open_browser_session(
        "chromium", url_allowed=allowed, getaddrinfo=_answers(*proxy_answers)
    )


@pytest.mark.browser
def test_browser_navigation_never_reaches_a_rebound_private_address(
    monkeypatch, listener
) -> None:
    from tests.browser_probe import require_browser

    require_browser()
    with _rebind_session(monkeypatch, proxy_answers=("127.0.0.1",)) as session:
        try:
            response = session.navigate(f"http://rebind.test:{listener.port}/x")
        except Exception:
            response = None  # a refused connection is an equally good outcome
        assert response is None or response.status != 200
    assert listener.hits == []


@pytest.mark.browser
def test_fetch_direct_never_reaches_a_rebound_private_address(
    monkeypatch, listener
) -> None:
    from tests.browser_probe import require_browser

    require_browser()
    with _rebind_session(monkeypatch, proxy_answers=(PUBLIC_IP, "127.0.0.1")) as session:
        result = session.fetch_direct(f"http://rebind.test:{listener.port}/x")
    assert result.status != 200
    assert listener.hits == []


@pytest.mark.browser
def test_public_name_still_loads_through_the_pinning_proxy(listener) -> None:
    """Both browser paths work through the proxy and dial the validated IP."""
    from tests.browser_probe import require_browser

    require_browser()
    dialed: list[tuple[str, int]] = []

    def connect(address, timeout=None):
        dialed.append(address)
        return socket.create_connection(("127.0.0.1", listener.port), timeout=timeout)

    with bs.open_browser_session(
        "chromium",
        url_allowed=lambda u: True,
        getaddrinfo=_answers(PUBLIC_IP),
        connect=connect,
    ) as session:
        session.navigate("http://pub.test/page")
        assert "private" in session.evaluate("document.body.innerText")
        result = session.fetch_direct("http://pub.test/other")
    assert result.status == 200 and b"private" in result.body
    assert {a for a in dialed} == {(PUBLIC_IP, 80)}
    assert sorted(h[1] for h in listener.hits) == ["/other", "/page"]


@pytest.mark.browser
def test_fetch_direct_connects_only_through_the_pinning_proxy(listener) -> None:
    """Playwright's request client resolves in Node, outside Chromium's flags.

    A name nothing can resolve is reachable only if the request went through
    the proxy, which is the only place the name is looked up.
    """
    from tests.browser_probe import require_browser

    require_browser()
    dialed: list[tuple[str, int]] = []

    def connect(address, timeout=None):
        dialed.append(address)
        return socket.create_connection(("127.0.0.1", listener.port), timeout=timeout)

    with bs.open_browser_session(
        "chromium",
        url_allowed=lambda u: True,
        getaddrinfo=_answers(PUBLIC_IP),
        connect=connect,
    ) as session:
        result = session.fetch_direct("http://never-resolves.test/doc")
    assert result.status == 200 and b"private" in result.body
    assert dialed == [(PUBLIC_IP, 80)]


# ---------------------------------------------------------------------------
# A page route that cannot install must not leave a browser that navigates
# ---------------------------------------------------------------------------


@pytest.mark.browser
def test_launch_refuses_when_the_page_route_cannot_install(monkeypatch, listener) -> None:
    from playwright.sync_api import Page

    from pzi.safe_http import SsrfBlocked
    from tests.browser_probe import require_browser

    require_browser()

    def broken_route(self, *args, **kwargs):
        raise RuntimeError("route unavailable")

    monkeypatch.setattr(Page, "route", broken_route)
    with pytest.raises(SsrfBlocked):
        with bs.open_browser_session("chromium"):
            pytest.fail("a session without the request guard was handed out")
    assert listener.hits == []


@pytest.mark.browser
def test_navigate_refuses_a_private_url_before_the_browser_requests_it(listener) -> None:
    """The landing check ran after `goto`, i.e. after the request was sent."""
    from pzi.safe_http import SsrfBlocked
    from tests.browser_probe import require_browser

    require_browser()
    with bs.open_browser_session("chromium", url_allowed=lambda u: True) as session:
        # Remove the route to model a guard that is absent, then narrow the
        # session's own predicate to the production one.
        session.page.unroute_all()
        session.url_allowed = bs.default_url_allowed
        with pytest.raises(SsrfBlocked):
            session.navigate(f"http://127.0.0.1:{listener.port}/secret")
    assert listener.hits == []
