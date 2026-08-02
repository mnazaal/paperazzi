"""Setup, credential and HTTP-boundary properties.

Two of these could not have been caught by the existing suite: `conftest`
deletes `XDG_DATA_HOME`/`XDG_CONFIG_HOME`, which is what makes the suite
hermetic *and* what hid the whole "writes to the real data home" class. The
token tests set it explicitly.
"""

from __future__ import annotations

import socket
from pathlib import Path
from unittest.mock import patch

import pytest

from pzi import exit_codes
from pzi.errors import PziError

# ---------------------------------------------------------------------------
# `pzi init` and the API token
# ---------------------------------------------------------------------------


def _init_args(**overrides):
    class Args:
        force = False
        setup = False
        bib = None
        name = None
        papers_dir = None
        browser = None
        rotate_token = False

    args = Args()
    for key, value in overrides.items():
        setattr(args, key, value)
    return args


def test_init_reuses_an_existing_token(tmp_path: Path, monkeypatch, capsys) -> None:
    """Rotating on every run de-paired the browser extension from the server —
    including when the docs' own smoke test ran `init` against a temp dir."""
    from pzi.commands.init import run_init_command

    home = tmp_path / "home"
    data_home = home / ".local" / "share" / "pzi"
    data_home.mkdir(parents=True)
    (data_home / "api_token").write_text("original-token\n", encoding="utf-8")
    monkeypatch.setenv("XDG_DATA_HOME", str(home / ".local" / "share"))

    import sys

    code = run_init_command(
        _init_args(),
        home_dir=str(home),
        config_path=str(tmp_path / "config.toml"),
        stdout=sys.stdout,
        stderr=sys.stderr,
    )

    assert code == exit_codes.OK
    assert (data_home / "api_token").read_text().strip() == "original-token"
    assert "reusing the existing API auth token" in capsys.readouterr().out


def test_init_rotate_token_replaces_it(tmp_path: Path, monkeypatch) -> None:
    from pzi.commands.init import run_init_command

    home = tmp_path / "home"
    data_home = home / ".local" / "share" / "pzi"
    data_home.mkdir(parents=True)
    (data_home / "api_token").write_text("original-token\n", encoding="utf-8")
    monkeypatch.setenv("XDG_DATA_HOME", str(home / ".local" / "share"))

    import sys

    run_init_command(
        _init_args(rotate_token=True),
        home_dir=str(home),
        config_path=str(tmp_path / "config.toml"),
        stdout=sys.stdout,
        stderr=sys.stderr,
    )

    assert (data_home / "api_token").read_text().strip() != "original-token"


def test_init_refuses_library_flags_without_setup(tmp_path: Path, capsys) -> None:
    """They were accepted and dropped, and `pzi init --bib …` is what the docs
    tell people to run."""
    import sys

    from pzi.commands.init import run_init_command

    code = run_init_command(
        _init_args(bib="~/bibs/ml.bib"),
        home_dir=str(tmp_path),
        config_path=str(tmp_path / "config.toml"),
        stdout=sys.stdout,
        stderr=sys.stderr,
    )

    assert code == exit_codes.USAGE
    assert "--bib" in capsys.readouterr().err
    assert not (tmp_path / "config.toml").exists()


def test_init_writes_the_token_where_the_reader_looks(tmp_path: Path) -> None:
    """`resolve_api_auth_token` reads `<pzi_data_home>/api_token`; writing to
    the XDG default regardless left the token orphaned and the server
    unauthenticated."""
    import sys

    from pzi.capture_context import resolve_api_auth_token
    from pzi.commands.init import run_init_command

    custom_data_home = tmp_path / "custom-data"
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        'api_listen_host = "127.0.0.1"\n'
        "api_listen_port = 8765\n"
        f'pzi_data_home = "{custom_data_home}"\n'
        "\n[[bibs]]\n"
        'name = "main"\n'
        f'path = "{tmp_path / "main.bib"}"\n'
        f'papers_dir = "{tmp_path / "papers"}"\n'
        "default = true\n",
        encoding="utf-8",
    )

    run_init_command(
        _init_args(force=True),
        home_dir=str(tmp_path),
        config_path=str(config_path),
        stdout=sys.stdout,
        stderr=sys.stderr,
    )

    token_file = custom_data_home / "api_token"
    assert token_file.exists()
    assert resolve_api_auth_token({"pzi_data_home": str(custom_data_home)}) == (
        token_file.read_text().strip()
    )


# ---------------------------------------------------------------------------
# Credentials
# ---------------------------------------------------------------------------


def test_the_s2_api_key_goes_only_to_semantic_scholar(monkeypatch) -> None:
    from pzi import fetch_helpers

    seen: list[tuple[str, dict]] = []

    def _capture(url, *, headers, timeout, max_retries, allow_host=None, extract):
        seen.append((url, dict(headers)))
        return ""

    monkeypatch.setattr(fetch_helpers, "_fetch_with_retries", _capture)

    for url in (
        "https://api.semanticscholar.org/graph/v1/paper/10.1/x",
        "https://api.crossref.org/works/10.1/x",
        "https://api.openalex.org/works/doi:10.1/x",
        "https://dblp.org/search/publ/api?q=x",
        "https://api.openreview.net/notes?id=x",
    ):
        fetch_helpers.fetch_text(url, api_key="SECRET-KEY")

    with_key = [url for url, headers in seen if "x-api-key" in headers]
    assert with_key == ["https://api.semanticscholar.org/graph/v1/paper/10.1/x"]


def test_a_failing_secret_command_does_not_echo_the_command_or_its_stderr(
    tmp_path: Path,
) -> None:
    from pzi.capture_context import run_shell_command

    script = tmp_path / "leaky.sh"
    script.write_text(
        "#!/bin/sh\necho 'my-actual-password' >&2\nexit 3\n", encoding="utf-8"
    )
    script.chmod(0o755)

    with pytest.raises(PziError) as excinfo:
        run_shell_command(f"{script} --account work", config_key="contact_email_cmd")

    message = str(excinfo.value)
    assert "my-actual-password" not in message
    assert "--account work" not in message
    assert "contact_email_cmd" in message
    assert "exited with code 3" in message


# ---------------------------------------------------------------------------
# HTTP boundary
# ---------------------------------------------------------------------------


CONFIGURED = {
    "config": {
        "bibs": [
            {
                "name": "main",
                "path": "/tmp/main.bib",
                "papers_dir": "/tmp/papers",
                "default": True,
            }
        ]
    }
}


def test_get_routes_refuse_an_unconfigured_bib_path(monkeypatch) -> None:
    """The POST side had this gate; every GET route accepted any existing .bib
    path, so `/export?bib=/elsewhere/private.bib` read a library the config had
    never heard of."""
    from pzi import http_get_routes

    monkeypatch.setattr(
        http_get_routes, "load_config_file", lambda config_path, home_dir: CONFIGURED
    )

    status, body = http_get_routes.process_get_request(
        "/export?bib=/tmp/attacker-chosen.bib", "/tmp/c.toml", "/tmp"
    )

    assert status == 400
    assert "configured" in body["error"]


def test_binary_routes_refuse_an_unconfigured_bib_path(monkeypatch) -> None:
    from pzi import http_binary_routes

    monkeypatch.setattr(
        http_binary_routes, "load_config_file", lambda config_path, home_dir: CONFIGURED
    )

    status, body = http_binary_routes.build_export_bytes_response(
        config_path="/tmp/c.toml",
        home_dir="/tmp",
        fmt="bibtex",
        bib_selector="/tmp/attacker-chosen.bib",
    )

    assert status == 400
    assert "configured" in body["error"]  # type: ignore[index]


@pytest.mark.parametrize(
    ("value", "expected"),
    [(None, True), ("false", True), ("", True), (0, True), (True, True), (False, False)],
)
def test_a_non_boolean_dry_run_falls_back_to_the_safe_default(value, expected) -> None:
    """`bool(body.get("dry_run", True))` read `null` as False — i.e. authorized a
    real write — and `"false"` as True."""
    from pzi.http_post_routes import body_flag

    assert body_flag({"dry_run": value}, "dry_run", default=True) is expected


def test_replace_mode_is_not_selected_by_the_string_false() -> None:
    from pzi.http_post_routes import body_flag

    assert body_flag({"replace": "false"}, "replace", default=False) is False


def test_attach_pdf_raw_requires_a_capture_session() -> None:
    """The TTL / size / source-URL / citekey checks are the documented control
    on this route; they used to run only if the caller supplied a request_id."""
    from pzi import http_post_routes

    status, body = http_post_routes.process_post_request(
        "/attach-pdf-raw",
        {"citekey": "anything", "pdf_bytes": b"%PDF-1.4 x"},
        "/tmp/c.toml",
        "/tmp",
    )

    assert status == 403
    assert "request_id" in body["error"]


def test_embedded_pdf_url_is_validated_like_every_other_url() -> None:
    from pzi.http_post_routes import metadata_url_override_error

    error = metadata_url_override_error(
        {"embedded_pdf_url": "http://127.0.0.1:9/secret.pdf"},
        safe_url=lambda value: value.startswith("https://example.com"),
    )

    assert error == "embedded_pdf_url must be a public http(s) URL"


# ---------------------------------------------------------------------------
# Request framing
# ---------------------------------------------------------------------------


def test_a_chunked_body_is_refused_rather_than_read_as_empty() -> None:
    """No Content-Length meant zero bytes read, so a real body was processed as
    `{}` and answered 200."""
    from pzi.http_security import validated_content_length

    result = validated_content_length(
        None, max_body_bytes=1024, transfer_encoding="chunked"
    )

    assert result == (411, "chunked request bodies are not supported; send Content-Length")


def test_a_normal_content_length_still_works() -> None:
    from pzi.http_security import validated_content_length

    assert validated_content_length("12", max_body_bytes=1024) == 12


# ---------------------------------------------------------------------------
# Browser requests are held to the same URL policy as everything else
# ---------------------------------------------------------------------------


def test_the_browser_request_guard_aborts_a_private_destination(monkeypatch) -> None:
    """`safe_http` protects what pzi fetches itself; a Playwright page fetches
    through the browser's own stack, following redirects and resolving DNS."""
    from pzi import browser_session

    routes: dict[str, object] = {}

    class _Page:
        def route(self, pattern, handler):
            routes["handler"] = handler

    class _Route:
        def __init__(self):
            self.action = None

        def continue_(self):
            self.action = "continue"

        def abort(self):
            self.action = "abort"

    browser_session.install_request_guard(_Page())
    handler = routes["handler"]

    public, private = _Route(), _Route()
    handler(public, type("R", (), {"url": "https://example.com/paper.pdf"})())
    handler(private, type("R", (), {"url": "http://127.0.0.1:8765/capture"})())

    assert public.action == "continue"
    assert private.action == "abort"


def test_fetch_direct_refuses_a_redirect_to_a_private_address() -> None:
    from pzi.browser_session import BrowserSession

    read_attempts: list[str] = []

    class _Response:
        status = 200
        url = "http://127.0.0.1:8765/secret"
        headers = {"content-type": "application/pdf"}

        def body(self):
            # Recorded rather than raised: `fetch_direct` swallows exceptions
            # into a status -1 result, so raising here would look like the fix
            # working when it is not.
            read_attempts.append(self.url)
            return b"%PDF-1.4 private"

    class _Request:
        def get(self, _url):
            return _Response()

    class _Page:
        request = _Request()
        url = "https://example.com/"

    session = BrowserSession(playwright=None, browser_ref=None, page=_Page())
    result = session.fetch_direct("https://example.com/paper.pdf")

    assert result.status == -1
    assert result.body == b""
    assert read_attempts == []


# ---------------------------------------------------------------------------
# A malformed request header is a rejection, not a crash
# ---------------------------------------------------------------------------


def _security(**overrides):
    base = {
        "listen_host": "127.0.0.1",
        "allowed_origins": ("http://localhost", "chrome-extension://"),
        "auth_token": "correct-token",
    }
    base.update(overrides)
    return base


@pytest.mark.parametrize(
    "host", ["[", "a]b[", "::1]:8", "[::1", "http://[/", "[]:99999"]
)
def test_a_malformed_host_header_is_refused_not_a_500(host: str) -> None:
    """`urlsplit` raises `Invalid IPv6 URL` on an unbalanced bracket.

    Raised out of the request gate, it became an *unauthenticated* 500 — a
    remote-triggerable crash on the one code path that runs before the token is
    checked.
    """
    from pzi.http_security import request_security_error

    result = request_security_error(
        method="GET", headers={"Host": host}, security=_security()
    )

    assert result is not None
    status, _message = result
    assert status == 403


@pytest.mark.parametrize("origin", ["[[[", "http://[", "https://[::1", "//["])
def test_a_malformed_origin_header_is_refused_not_a_500(origin: str) -> None:
    from pzi.http_security import request_security_error

    result = request_security_error(
        method="GET",
        headers={"Host": "127.0.0.1", "Origin": origin},
        security=_security(),
    )

    assert result is not None
    status, _message = result
    assert status == 403


def test_a_non_ascii_token_is_a_401_not_a_500() -> None:
    """`hmac.compare_digest` raises `TypeError` on non-ASCII strings.

    An unauthenticated caller could crash the server with one header value, and
    the answer had to be 401 in any case: a token that is not the token is
    simply wrong.
    """
    from pzi.http_security import request_security_error

    result = request_security_error(
        method="GET",
        headers={"Host": "127.0.0.1", "X-Pzi-Token": "tökén"},
        security=_security(),
    )

    assert result == (401, "invalid API token")


def test_a_non_ascii_bearer_token_is_a_401_not_a_500() -> None:
    from pzi.http_security import request_security_error

    result = request_security_error(
        method="GET",
        headers={"Host": "127.0.0.1", "Authorization": "Bearer tökén"},
        security=_security(),
    )

    assert result == (401, "invalid API token")


def test_the_correct_token_is_still_accepted() -> None:
    from pzi.http_security import request_security_error

    assert request_security_error(
        method="GET",
        headers={"Host": "127.0.0.1", "X-Pzi-Token": "correct-token"},
        security=_security(),
    ) is None


def test_building_cors_headers_cannot_fault_on_a_malformed_origin() -> None:
    """The 500 handler sends CORS headers too, so a throwing check faulted
    twice and the caller got zero bytes instead of an error document."""
    from pzi.http_security import origin_allowed

    for origin in ("[[[", "http://[", "//["):
        assert origin_allowed(origin, ("http://localhost",)) is False


def test_a_nul_byte_in_a_path_is_refused_not_a_500() -> None:
    """`Path.resolve` raises `ValueError`, not `OSError`, on an embedded NUL.

    The confinement helper caught only `OSError`, so a request naming such a
    path crashed instead of being refused — and it is reached from routes that
    take a path out of the request.
    """
    from pzi.http_binary_routes import path_confined_to

    assert path_confined_to("a\x00b", "/tmp") is None
    assert path_confined_to("/tmp", "a\x00b") is None


def test_the_sessionless_attach_path_enforces_the_pdf_byte_cap(tmp_path) -> None:
    """The session path checks `max_bytes`; the sessionless one checked nothing.

    The sessionless upload is deliberate and documented — a capture that never
    opened a session still has to be able to attach — but arriving without a
    session is not a reason to accept an unbounded PDF.
    """
    import base64

    from pzi.http_post_routes import MAX_BROWSER_PDF_BYTES, _handle_attach_pdf_post

    oversized = b"%PDF-1.4\n" + b"A" * (MAX_BROWSER_PDF_BYTES + 1)
    status, response = _handle_attach_pdf_post(
        {
            "citekey": "smith2024",
            "pdf_base64": base64.b64encode(oversized).decode(),
        },
        config_path=str(tmp_path / "config.toml"),
        home_dir=str(tmp_path),
        attach_session_store=None,
        now=lambda: 0.0,
    )

    assert status == 413
    assert "too large" in response["error"]


@pytest.mark.parametrize(
    "url",
    ["file:///etc/passwd", "javascript:alert(1)", "data:text/html,<script>x</script>",
     "vbscript:x", "smb://host/share"],
)
def test_the_desktop_browser_fallback_refuses_a_non_http_url(url: str, tmp_path) -> None:
    """`webbrowser.open` hands the string to the OS handler.

    The URL reaching this is one a *provider* supplied, so a `file:` or
    `javascript:` URL would have been opened as-is by whatever the desktop is
    configured to run for that scheme.
    """
    from pzi.pdf import fetch_pdf_via_desktop_browser_download
    from pzi.pdf_planning import PdfFallbackSettings

    opened: list[str] = []
    settings = PdfFallbackSettings(
        disable_desktop_browser=False,
        download_dir=tmp_path / "downloads",
        desktop_timeout=1,
    )

    with patch("webbrowser.open", side_effect=lambda u: opened.append(u) or True):
        path, error = fetch_pdf_via_desktop_browser_download(
            url=url, papers_dir=str(tmp_path), citekey="k1", settings=settings,
        )

    assert opened == []
    assert path is None
    assert error is not None and "http" in error


def test_a_metacharacter_rejection_names_the_key_not_the_command() -> None:
    """Every other failure path here already refuses to quote the command.

    A `*_cmd` line is a command the user wrote to *fetch a secret*, so its text
    can carry one — and this rejection printed it verbatim to stderr, where it
    lands in scrollback, logs and bug reports.
    """
    from pzi.capture_context import run_shell_command

    with pytest.raises(PziError) as excinfo:
        run_shell_command(
            "vault read --token hunter2 secret/pzi && echo x",
            config_key="api_auth_token_cmd",
        )

    message = str(excinfo.value)
    assert "api_auth_token_cmd" in message
    assert "hunter2" not in message
    assert "vault" not in message
    # It still says what is wrong.
    assert "&&" in message


# ---------------------------------------------------------------------------
# DNS rebinding: a public *name* that resolves somewhere private
# ---------------------------------------------------------------------------


def test_a_public_hostname_resolving_to_a_private_address_is_refused() -> None:
    """The literal-address tests cannot cover this: `evil.example.com` looks
    entirely public until it is resolved, which is the whole point of a
    rebinding attack. Every other test in the suite runs against the hermetic
    resolver, which answers with a *public* IP — so the branch that rejects a
    private answer had no test standing on it.
    """
    from pzi import url_safety

    for private_ip in ("127.0.0.1", "10.0.0.5", "192.168.1.1", "169.254.169.254", "::1"):
        family = socket.AF_INET6 if ":" in private_ip else socket.AF_INET

        def _resolves_private(host, port, *, timeout, _ip=private_ip, _family=family):
            return [(_family, socket.SOCK_STREAM, 6, "", (_ip, port))]

        assert not url_safety.safe_public_http_url(
            "https://evil.example.com/paper.pdf", resolve_host=_resolves_private
        ), private_ip


def test_a_public_hostname_resolving_to_a_public_address_is_allowed() -> None:
    from pzi import url_safety

    def _resolves_public(host, port, *, timeout):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]

    assert url_safety.safe_public_http_url(
        "https://example.com/paper.pdf", resolve_host=_resolves_public
    )


def test_a_name_resolving_to_both_is_refused() -> None:
    """One private answer is enough: the browser or the HTTP stack may pick it."""
    from pzi import url_safety

    def _resolves_both(host, port, *, timeout):
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port)),
        ]

    assert not url_safety.safe_public_http_url(
        "https://evil.example.com/x", resolve_host=_resolves_both
    )
