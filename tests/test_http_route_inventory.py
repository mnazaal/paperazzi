"""The HTTP surface, pinned — item 424.

`docs/security.md` opens with "Twenty-one routes, not three", written after an
earlier version of that diagram showed only the three capture routes and
understated what a caller holding the API token can do. Nothing checked the
number, and while planning this file I counted the route tables, got 19, and was
about to correct the documentation — the two binary GETs are not in a table at
all. Miscounting this surface is evidently easy, which is the argument for the
test.
"""

from __future__ import annotations

import http.client
import inspect
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import HTTPServer
from pathlib import Path

from pzi import http_security
from pzi.http_api import build_handler_class
from pzi.http_get_routes import BINARY_GET_ROUTES, GET_PREFIX_ROUTES, GET_ROUTES
from pzi.http_post_routes import POST_ROUTES
from pzi.http_security import build_http_security_config

#: Derived, not hand-written. The two binary GETs used to be matched by inline
#: conditionals in `http_api`'s dispatcher, so no introspection could find them
#: and this file kept its own copy of the pair — the one part of the inventory
#: that could rot, guarded by grepping the dispatcher's source for the literals.
#: They are declared in `BINARY_GET_ROUTES` now, beside the other two tables.
BINARY_GETS = tuple(route.path for route in BINARY_GET_ROUTES)

#: Every route pzi serves. Frozen at 1.0: the extension is built against this
#: set, and a route that quietly appears is new attack surface for a caller
#: holding the token.
EXPECTED_ROUTES = {
    "GET": (
        "/health",
        "/bibs",
        "/search",
        "/entries",
        "/tags",
        "/export",
    ),
    "GET (prefix)": (
        "/detail/",
        "/tags/",
    ),
    "GET (binary)": BINARY_GETS,
    "POST": (
        "/capture",
        "/attach-pdf-bytes",
        "/attach-pdf-raw",
        "/tags/add",
        "/tags/remove",
        "/update",
        "/promote",
        "/browser/discover",
        "/browser/download",
        "/delete",
        "/inbox/drain",
    ),
}

DOCUMENTED_TOTAL = 21


def test_the_route_tables_match_the_expected_inventory() -> None:
    """A route added or removed without deciding to is a contract change."""
    assert tuple(route.path for route in GET_ROUTES) == EXPECTED_ROUTES["GET"]
    assert tuple(route.prefix for route in GET_PREFIX_ROUTES) == EXPECTED_ROUTES[
        "GET (prefix)"
    ]
    assert tuple(route.path for route in POST_ROUTES) == EXPECTED_ROUTES["POST"]


#: Enough of English to render the route count as the docs spell it. Small on
#: purpose: if the surface ever grows past this, the failure is a `KeyError`
#: here rather than a silently unchecked document.
_NUMBER_WORDS: dict[int, str] = {
    18: "eighteen", 19: "nineteen", 20: "twenty", 21: "twenty-one",
    22: "twenty-two", 23: "twenty-three", 24: "twenty-four",
    25: "twenty-five", 26: "twenty-six",
}

#: Every file that states the number in prose. Read, not trusted.
_DOCS_CLAIMING_THE_COUNT = ("docs/security.md", "README.md", "docs/reference.md")


def test_the_documented_route_count_is_the_real_one() -> None:
    """`docs/security.md` says twenty-one; this is what makes that stay true."""
    total = sum(len(paths) for paths in EXPECTED_ROUTES.values())
    assert total == DOCUMENTED_TOTAL, (
        f"pzi now serves {total} routes, but docs/security.md documents "
        f"{DOCUMENTED_TOTAL}. Update the doc and the security review that rests "
        "on it — the count is the claim about what a token holder can reach."
    )
    tabled = (
        len(GET_ROUTES) + len(GET_PREFIX_ROUTES) + len(POST_ROUTES)
        + len(BINARY_GET_ROUTES)
    )
    # Every route pzi serves is now in one of four tables, so the count is
    # derived end to end rather than partly transcribed.
    assert tabled == DOCUMENTED_TOTAL


def test_the_documents_that_state_the_count_are_read_not_trusted() -> None:
    """`DOCUMENTED_TOTAL` was a hand-copied literal, and nothing opened a file.

    The test above compared the route tables against `21` and its message told
    you to go update `docs/security.md` — but changing that document to say
    "Thirty routes" left the whole suite green, which means the number was
    pinned to a copy of itself. Three prose statements assert it (the
    `docs/security.md` heading and two lines of README) and no test read any of
    them.

    Spelled out in words in all three, and left that way: flattening the prose
    to a digit to make a test easier is the tail wagging the dog.
    """
    repo_root = Path(__file__).resolve().parent.parent
    assert DOCUMENTED_TOTAL in _NUMBER_WORDS, (
        f"the route count reached {DOCUMENTED_TOTAL}, past what _NUMBER_WORDS "
        "can spell — extend it, or this check stops reading the documents"
    )

    found = 0
    for relative in _DOCS_CLAIMING_THE_COUNT:
        text = (repo_root / relative).read_text(encoding="utf-8")
        for line in text.splitlines():
            lowered = line.lower()
            if "route" not in lowered:
                continue
            # Longest spelling first: "twenty" is a substring of "twenty-one",
            # so a shortest-match scan reads the right number as the wrong one.
            for number, spelling in sorted(
                _NUMBER_WORDS.items(), key=lambda pair: -len(pair[1])
            ):
                if spelling in lowered:
                    found += 1
                    assert number == DOCUMENTED_TOTAL, (
                        f"{relative} says {spelling!r} routes but pzi serves "
                        f"{DOCUMENTED_TOTAL}: {line.strip()}"
                    )
                    break

    assert found == 3, (
        f"expected 3 prose statements of the route count across "
        f"{list(_DOCS_CLAIMING_THE_COUNT)}, found {found} — if one was removed "
        "or reworded, say so here rather than leaving the check to shrink "
        "quietly"
    )


def test_each_binary_route_declares_how_it_matches() -> None:
    """`/pdf/` takes a citekey after it; `/export/raw` is exact.

    The dispatcher reads `is_prefix` from the table rather than knowing which
    is which, so getting this wrong would make `/export/rawXYZ` a valid export
    or `/pdf/smith2020` a 404.
    """
    by_name = {route.name: route for route in BINARY_GET_ROUTES}
    assert by_name["pdf"].is_prefix is True
    assert by_name["export_raw"].is_prefix is False
    assert by_name["pdf"].matches("/pdf/smith2020")
    assert not by_name["export_raw"].matches("/export/rawXYZ")
    assert by_name["export_raw"].matches("/export/raw")


def test_authentication_is_one_gate_in_front_of_every_route() -> None:
    """There is no per-route auth flag, and that is the stronger property.

    `request_security_error` takes a method and headers and nothing else — it
    cannot vary by route, so no route can be given an exemption by forgetting
    one. Pinning that is worth more than a per-route table would be, because a
    table has to be maintained and this cannot drift.

    If a `path` parameter ever appears here, auth has become route-dependent and
    every route needs its own assertion.
    """
    parameters = set(inspect.signature(http_security.request_security_error).parameters)
    assert parameters == {"method", "headers", "security"}, (
        "the request gate's signature changed. If it now varies by route, "
        "authentication is no longer one gate in front of everything and this "
        "file must pin each route's requirement individually."
    )


#: The HTTP methods the handler class installs. Frozen for the same reason the
#: route set is: a `do_PUT` added without wiring the gate is a hole, and the
#: signature check above stays green for it because it never looks at the
#: handler class at all.
EXPECTED_HTTP_METHODS = {"do_GET", "do_POST", "do_OPTIONS"}

#: CORS preflight cannot carry `X-Pzi-Token` — the browser sends it before the
#: real request, without the custom headers — so OPTIONS is exempt from the
#: token check by design. It is *not* exempt from the host and origin checks,
#: which is what the second half of the test below pins.
_TOKEN_EXEMPT = {"do_OPTIONS"}


@contextmanager
def _running(handler_cls: type) -> Iterator[int]:
    server = HTTPServer(("127.0.0.1", 0), handler_cls)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()


def _status(port: int, method: str, path: str, *, host: str | None = None) -> int:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        conn.request(
            method,
            path,
            body=b"" if method == "POST" else None,
            headers={"Host": host} if host is not None else {},
        )
        return conn.getresponse().status
    finally:
        conn.close()


def test_every_installed_method_is_refused_without_credentials(tmp_path: Path) -> None:
    """The wiring, not the signature — a `do_PUT` added without the gate fails here.

    Two assertions, because the gate has two halves and only one applies to
    every method. Without a token, GET and POST answer 401 and OPTIONS answers
    204 (preflight is token-exempt by design). A `Host` the server does not
    bind is refused for all three, preflight included.
    """
    handler_cls = build_handler_class(
        config_path=str(tmp_path / "config.toml"),
        home_dir=str(tmp_path),
        security=build_http_security_config(auth_token="secret"),
    )
    installed = {name for name in vars(handler_cls) if name.startswith("do_")}
    assert installed == EXPECTED_HTTP_METHODS, (
        f"the handler class installs {sorted(installed)}. Every method it "
        "handles must go through `request_security_error` — add it to "
        "EXPECTED_HTTP_METHODS once it does."
    )

    with _running(handler_cls) as port:
        for name in sorted(installed):
            method = name.removeprefix("do_")
            expected = 204 if name in _TOKEN_EXEMPT else 401
            assert _status(port, method, "/bibs") == expected, name
            assert _status(port, method, "/bibs", host="evil.example") == 403, name
