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

from pzi import http_api, http_security
from pzi.http_get_routes import GET_PREFIX_ROUTES, GET_ROUTES
from pzi.http_post_routes import POST_ROUTES

#: The two binary GETs are matched by inline conditionals in `http_api`
#: (`if p.startswith("/pdf/")`, `if p == "/export/raw"`) rather than declared in
#: a table, so no amount of introspection finds them — they are named here, and
#: `test_the_untabled_binary_routes_are_still_dispatched` is what keeps this
#: hand-written pair honest. Unifying the registry is its own change; see the
#: follow-up item in PLAN.md.
UNTABLED_BINARY_GETS = ("/pdf/", "/export/raw")

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
    "GET (binary, untabled)": UNTABLED_BINARY_GETS,
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
    tabled = len(GET_ROUTES) + len(GET_PREFIX_ROUTES) + len(POST_ROUTES)
    assert tabled + len(UNTABLED_BINARY_GETS) == DOCUMENTED_TOTAL


def test_the_untabled_binary_routes_are_still_dispatched() -> None:
    """The hand-written half of the inventory, kept honest.

    `UNTABLED_BINARY_GETS` is the one part of this file that introspection
    cannot derive, so it is the part that can rot. Reading the dispatcher's
    source for the literals is crude, and it is the only thing that fails if
    `/pdf/` or `/export/raw` is renamed or dropped.
    """
    source = inspect.getsource(http_api)
    for path in UNTABLED_BINARY_GETS:
        assert f'"{path}"' in source, (
            f"{path} is in the route inventory but no longer appears in "
            "http_api's dispatcher — either it moved into a route table (good: "
            "derive it there and delete it here) or it is gone (a contract "
            "change)."
        )


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
