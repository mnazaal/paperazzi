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

import inspect

from pzi import http_security
from pzi.http_get_routes import BINARY_GET_ROUTES, GET_PREFIX_ROUTES, GET_ROUTES
from pzi.http_post_routes import POST_ROUTES

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
