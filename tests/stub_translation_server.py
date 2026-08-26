"""A loopback stand-in for the Zotero translation-server.

`pzi add` routes identifiers through translation-server, so end-to-end tests of
the capture path used to need the real one — a git clone plus `npm install`,
which no test can reasonably do. This answers the two endpoints pzi actually
calls (`/web`, `/search`) well enough to drive a capture to completion, and can
be told which inputs should fail, which is what makes a *partly* failed batch
reproducible.

Loopback only, so it passes the autouse socket guard in `conftest.py`.

**What this cannot catch.** It reproduces the contract as pzi understands it
today: a GET health probe, and a POST to `/web` or `/search` answering with a
JSON array of Zotero-shaped items. Tests built on it therefore verify *pzi's*
handling, not the real server's behaviour — if upstream changes its response
shape, endpoints, or error codes, the stub keeps answering the old way and every
test here stays green while `pzi add` breaks against the real thing.

Two consequences worth acting on:

* Treat a green run as "pzi still handles this shape correctly", never as "pzi
  still works with translation-server".
* The pinned upstream commits in `ts_backend._TS_REPOS` are what actually fix
  the contract. When you bump them, re-run a capture against the real server by
  hand and update this stub if the shape moved — nothing here will tell you.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer


def _handler_class(resolvable: Mapping[str, dict]) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            # is_ts_reachable() and the startup health-check both probe the
            # root with a plain GET — never `/web` or `/search`, which only
            # ever receive POST in the real client. Answering every GET path
            # the same way would hide a client that started sending GET to a
            # POST-only endpoint.
            if self.path not in {"/", ""}:
                self.send_response(404)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"ok")

        def do_POST(self) -> None:
            # The real server answers `/web` and `/search`, and nothing else —
            # dispatching on the needle alone (the previous version) meant a
            # client bug that posted an identifier to the wrong path, or a
            # typo'd endpoint string, still got a 200 with the matching item.
            # That is exactly the shape this stub exists to catch, since the
            # translation-server path has no other live coverage.
            if self.path not in {"/web", "/search"}:
                self.send_response(404)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length).decode("utf-8")
            for needle, item in resolvable.items():
                if needle in body:
                    payload = json.dumps([item]).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                    return
            # What the real server returns when no translator matches.
            self.send_response(500)
            self.send_header("Content-Length", "14")
            self.end_headers()
            self.wfile.write(b"no translator\n")

        def log_message(self, *_args: object) -> None:
            """Silence the default stderr access log."""

    return Handler


def translation_item(
    *,
    title: str,
    doi: str | None = None,
    first_name: str = "Jane",
    last_name: str = "Smith",
    year: str = "2024",
    venue: str = "Journal of Stubs",
) -> dict:
    """A Zotero-shaped item, the form translation-server returns."""
    return {
        "itemType": "journalArticle",
        "title": title,
        "creators": [
            {"creatorType": "author", "firstName": first_name, "lastName": last_name}
        ],
        "date": year,
        # Omitted when None. A DOI sends `doi_pdf_step` to Crossref, Europe PMC
        # and DOAJ — three real hosts — from a test whose subject is the exit
        # code, so the tests that do not need one pass None and stay offline.
        **({"DOI": doi} if doi else {}),
        "publicationTitle": venue,
    }


@contextmanager
def stub_translation_server(resolvable: Mapping[str, dict]) -> Iterator[str]:
    """Serve *resolvable* on loopback, yielding the base URL.

    Any request body containing one of the keys gets the matching item; every
    other request gets a 500, so a batch mixing known and unknown inputs
    produces a mix of successes and failures.
    """
    server = HTTPServer(("127.0.0.1", 0), _handler_class(resolvable))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
