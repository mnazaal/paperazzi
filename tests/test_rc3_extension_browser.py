"""Real-extension checks: the built extension loaded into real Chromium.

The Node tests in ``test_browser_extension_js.py`` stub ``fetch`` and
``chrome``, so they cannot say what the browser does with a redirect, a
chunked body or a second module instance. These run the extension's own
modules from an extension page, against local fixture listeners only.

Chromium needs the full browser (``channel="chromium"``) to load an
extension; the headless shell does not. Where the sandbox forbids Unix
sockets, full Chromium cannot start (its profile lock is a Unix socket); the
test then skips locally and fails under ``CI``.
"""

from __future__ import annotations

import json
import os
import shutil
import threading
import time
from collections.abc import Callable, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.browser

EXTENSION_SRC = Path(__file__).resolve().parents[1] / "browser-extension"
MAX_ATTACH_PDF_BYTES = 47 * 1024 * 1024
PUBLIC_HOST = "public.test"


class Listener:
    """A loopback HTTP listener that records every request it receives."""

    def __init__(self, handler: Callable[[BaseHTTPRequestHandler, Listener], None]) -> None:
        self.hits: list[str] = []
        self.posts: list[tuple[str, bytes]] = []
        self.bytes_sent = 0
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
                pass

            def do_GET(self) -> None:  # noqa: N802
                outer.hits.append(self.path)
                try:
                    handler(self, outer)
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def do_POST(self) -> None:  # noqa: N802
                length = int(self.headers.get("Content-Length") or 0)
                outer.posts.append((self.path, self.rfile.read(length)))
                body = b'{"status": "ok"}'
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


def _pdf(handler: BaseHTTPRequestHandler, body: bytes = b"%PDF-1.4 fixture") -> None:
    handler.send_response(200)
    handler.send_header("Content-Type", "application/pdf")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _redirect_to(location: str) -> Callable[[BaseHTTPRequestHandler, Listener], None]:
    def handler(h: BaseHTTPRequestHandler, _listener: Listener) -> None:
        h.send_response(302)
        h.send_header("Location", location)
        h.send_header("Content-Length", "0")
        h.end_headers()

    return handler


def _pdf_or_page(h: BaseHTTPRequestHandler, _listener: Listener) -> None:
    if h.path.endswith(".pdf"):
        _pdf(h)
        return
    h.send_response(200)
    h.send_header("Content-Type", "text/html")
    h.send_header("Content-Length", "2")
    h.end_headers()
    h.wfile.write(b"ok")


class Rig:
    def __init__(self, ctx: Any, ext_id: str, tmp: Path) -> None:
        self.ctx = ctx
        self.ext_id = ext_id
        self.tmp = tmp
        self.listeners: list[Listener] = []

    def listener(self, handler: Callable[[BaseHTTPRequestHandler, Listener], None]) -> Listener:
        found = Listener(handler)
        self.listeners.append(found)
        return found

    def public_url(self, listener: Listener, path: str) -> str:
        return f"http://{PUBLIC_HOST}:{listener.port}{path}"

    def extension_page(self) -> Any:
        page = self.ctx.new_page()
        page.goto(f"chrome-extension://{self.ext_id}/onboarding.html")
        return page

    def close_listeners(self) -> None:
        for found in self.listeners:
            found.close()
        self.listeners.clear()


def _build_extension(dest: Path) -> None:
    shutil.copytree(EXTENSION_SRC, dest)
    manifest = json.loads((dest / "manifest.base.json").read_text())
    # `PUBLIC_HOST` stands in for a publisher the user has granted: the
    # permission flow grants an origin pattern, and a match pattern ignores the
    # port, so one entry covers every fixture listener.
    manifest["host_permissions"] = [*manifest["host_permissions"], f"http://{PUBLIC_HOST}/*"]
    manifest["background"] = {"service_worker": "background.js", "type": "module"}
    (dest / "manifest.json").write_text(json.dumps(manifest))


@pytest.fixture(scope="module")
def rig(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Rig]:
    sync_api = pytest.importorskip("playwright.sync_api")
    sync_playwright = sync_api.sync_playwright

    tmp = tmp_path_factory.mktemp("extension-rig")
    ext = tmp / "ext"
    _build_extension(ext)
    with sync_playwright() as playwright:
        try:
            ctx = playwright.chromium.launch_persistent_context(
                str(tmp / "profile"),
                channel="chromium",
                headless=True,
                args=[
                    f"--disable-extensions-except={ext}",
                    f"--load-extension={ext}",
                    # The sandbox's egress proxy would otherwise swallow the
                    # fixture hosts; and `public.test` has to resolve to the
                    # loopback listeners.
                    "--no-proxy-server",
                    f"--host-resolver-rules=MAP {PUBLIC_HOST} 127.0.0.1",
                ],
            )
        except Exception as error:  # noqa: BLE001
            message = f"full Chromium with extensions did not start: {str(error)[:300]}"
            if os.environ.get("CI"):
                raise
            pytest.skip(message)
        try:
            worker = ctx.service_workers[0] if ctx.service_workers else ctx.wait_for_event("serviceworker")
            rig = Rig(ctx, worker.url.split("/")[2], tmp)
            yield rig
        finally:
            ctx.close()


@pytest.fixture(autouse=True)
def _clean(rig: Rig) -> Iterator[None]:
    yield
    rig.close_listeners()
    for page in rig.ctx.pages:
        page.close()


def _stream_forever(handler: BaseHTTPRequestHandler, listener: Listener) -> None:
    """A chunked PDF that never ends: 1 MiB per chunk until the client leaves."""
    handler.send_response(200)
    handler.send_header("Content-Type", "application/pdf")
    handler.send_header("Transfer-Encoding", "chunked")
    handler.end_headers()
    chunk = b"%PDF-1.4" + b"0" * (1024 * 1024 - 8)
    deadline = time.monotonic() + 20
    # Capped so a regression costs the test run a few hundred MiB, not the host.
    while time.monotonic() < deadline and listener.bytes_sent < 400 * 1024 * 1024:
        handler.wfile.write(b"%x\r\n" % len(chunk) + chunk + b"\r\n")
        handler.wfile.flush()
        listener.bytes_sent += len(chunk)


def _stall_after_header(handler: BaseHTTPRequestHandler, listener: Listener) -> None:
    """Headers and a PDF prefix, then silence for longer than the test lasts."""
    handler.send_response(200)
    handler.send_header("Content-Type", "application/pdf")
    handler.send_header("Transfer-Encoding", "chunked")
    handler.end_headers()
    handler.wfile.write(b"8\r\n%PDF-1.4\r\n")
    handler.wfile.flush()
    time.sleep(20)


_RUN_CAPTURE = """
async ({ endpoint, candidate, pageUrl, shrinkTimers }) => {
  if (shrinkTimers) {
    const realSetTimeout = globalThis.setTimeout.bind(globalThis);
    globalThis.setTimeout = (fn, ms, ...rest) => realSetTimeout(fn, ms >= 1000 ? 300 : ms, ...rest);
  }
  await chrome.storage.local.set({ endpoint });
  const { maybeStreamPdfBytes } = await import("./background/pdf_fetch.js");
  const started = performance.now();
  const outcome = await maybeStreamPdfBytes({
    endpoint,
    citekey: "smith2024paper",
    bib: null,
    pdfUrlCandidates: [{ url: candidate }],
    pageUrl,
    originPermissions: null,
    pdfRequest: null,
  });
  return { outcome, elapsedMs: performance.now() - started };
}
"""


def test_a_public_pdf_redirecting_to_loopback_never_reaches_the_private_listener(rig: Rig) -> None:
    """The extension follows redirects itself and checked nothing along the way.

    The user has granted the publisher's origin, which is the normal flow, so the
    browser treats the fetch as same-permission. A public candidate that answers
    302 to a loopback address would otherwise make the browser issue the GET.
    Checking ``response.url`` after the download cannot undo that request.
    """
    private = rig.listener(lambda h, o: _pdf(h, b"%PDF-1.4 private"))
    public = rig.listener(_redirect_to(f"http://127.0.0.1:{private.port}/secret.pdf"))
    pzi = rig.listener(lambda h, o: None)

    page = rig.extension_page()
    result = page.evaluate(
        _RUN_CAPTURE,
        {
            "endpoint": f"http://127.0.0.1:{pzi.port}/capture",
            "candidate": rig.public_url(public, "/paper.pdf"),
            "pageUrl": rig.public_url(public, "/article"),
            "shrinkTimers": False,
        },
    )

    assert public.hits, "the public candidate must actually have been fetched"
    assert private.hits == [], f"private listener was contacted: {private.hits}"
    assert result["outcome"]["status"] == "error", result
    assert pzi.posts == [], "no bytes may be attached for a refused redirect"


def test_a_public_redirect_to_a_public_pdf_still_downloads(rig: Rig) -> None:
    """The guard is for private destinations; ordinary publisher redirects must work."""
    final = rig.listener(lambda h, o: _pdf(h))
    public = rig.listener(_redirect_to(f"http://{PUBLIC_HOST}:{final.port}/real.pdf"))
    pzi = rig.listener(lambda h, o: None)

    page = rig.extension_page()
    result = page.evaluate(
        _RUN_CAPTURE,
        {
            "endpoint": f"http://127.0.0.1:{pzi.port}/capture",
            "candidate": rig.public_url(public, "/paper.pdf"),
            "pageUrl": rig.public_url(public, "/article"),
            "shrinkTimers": False,
        },
    )

    assert result["outcome"]["status"] == "ok", result
    assert final.hits == ["/real.pdf"], final.hits
    body = json.loads(pzi.posts[0][1])
    # Provenance names where the bytes came from, the plan names what authorised.
    assert body["source_url"] == f"http://{PUBLIC_HOST}:{final.port}/real.pdf", body["source_url"]
    assert body["origin_candidate"] == rig.public_url(public, "/paper.pdf"), body


def test_an_oversized_chunked_body_is_cut_off_at_the_byte_budget(rig: Rig) -> None:
    """No Content-Length, so only reading the stream can enforce the limit."""
    public = rig.listener(_stream_forever)
    pzi = rig.listener(lambda h, o: None)

    page = rig.extension_page()
    result = page.evaluate(
        _RUN_CAPTURE,
        {
            "endpoint": f"http://127.0.0.1:{pzi.port}/capture",
            "candidate": rig.public_url(public, "/big.pdf"),
            "pageUrl": rig.public_url(public, "/article"),
            "shrinkTimers": False,
        },
    )

    assert result["outcome"]["status"] == "error", result
    attempts = result["outcome"]["pdf_attach_attempts"]
    assert any(a["status"] == "too_large" for a in attempts), attempts
    # The server was stopped near the budget rather than drained to its own
    # 20 s limit (which would be several GiB on loopback; the 400 MiB cap above bounds a regression).
    assert public.bytes_sent < MAX_ATTACH_PDF_BYTES + 32 * 1024 * 1024, public.bytes_sent
    assert pzi.posts == []
    print(f"server sent {public.bytes_sent / 2**20:.0f} MiB before the client left")


def test_a_stalled_body_ends_at_the_time_budget(rig: Rig) -> None:
    """Headers arrive, then nothing: the timer used to be cleared at the headers."""
    public = rig.listener(_stall_after_header)
    pzi = rig.listener(lambda h, o: None)

    page = rig.extension_page()
    result = page.evaluate(
        _RUN_CAPTURE,
        {
            "endpoint": f"http://127.0.0.1:{pzi.port}/capture",
            "candidate": rig.public_url(public, "/stall.pdf"),
            "pageUrl": rig.public_url(public, "/article"),
            "shrinkTimers": True,
        },
    )

    assert result["outcome"]["status"] == "error", result
    attempts = result["outcome"]["pdf_attach_attempts"]
    assert any(a["status"] == "fetch_error" for a in attempts), attempts
    # The listener sleeps 20 s; a bounded read returns long before that.
    assert result["elapsedMs"] < 10_000, result["elapsedMs"]


def test_the_popup_does_not_see_a_pdf_the_service_worker_observed(rig: Rig) -> None:
    """The popup and the worker are separate realms with separate module state.

    A PDF the worker saw on the wire is invisible to ``extractPdfUrlCandidates``
    running in the popup's realm, so a cache filled by the worker's listener
    cannot serve the popup. The cache was deleted rather than persisted.
    """
    public = rig.listener(_pdf_or_page)
    # A real navigation: the worker's listener (if any) sees this response.
    viewer = rig.ctx.new_page()
    try:
        viewer.goto(rig.public_url(public, "/seen.pdf"), wait_until="commit")
    except Exception:  # noqa: BLE001 - a PDF navigation may surface as a download
        pass
    time.sleep(0.5)

    page = rig.extension_page()
    candidates = page.evaluate(
        """async (pageUrl) => {
          const { extractPdfUrlCandidates } = await import("./background/pdf_discovery.js");
          return await extractPdfUrlCandidates(null, pageUrl);
        }""",
        rig.public_url(public, "/article"),
    )

    assert public.hits, "the PDF must actually have been requested"
    assert rig.public_url(public, "/seen.pdf") not in candidates, candidates
