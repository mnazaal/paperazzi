"""A loopback stand-in for the Zotero translation-server.

`pzi add` routes identifiers through translation-server, so end-to-end tests of
the capture path used to need the real one — a git clone plus `npm install`,
which no test can reasonably do. This answers the two endpoints pzi actually
calls (`/web`, `/search`) well enough to drive a capture to completion, and can
be told which inputs should fail, which is what makes a *partly* failed batch
reproducible.

Loopback only, so it passes the autouse socket guard in `conftest.py`.
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
            # is_ts_reachable() probes with a plain GET.
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"ok")

        def do_POST(self) -> None:
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
    doi: str,
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
        "DOI": doi,
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
