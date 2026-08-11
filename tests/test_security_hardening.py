"""Security items: what a hostile or merely unlucky input could reach.

Several of these are defence in depth — the attacker generally needs to control
a metadata record or a captured page first — but each one is a boundary that
existed elsewhere in pzi and not here.
"""

from __future__ import annotations

import time
from pathlib import Path


def test_flaresolverr_will_not_fetch_a_private_destination() -> None:
    """The forwarded URL made the local headless Chrome an open proxy.

    Reproduced against the cloud metadata endpoint: pzi asked FlareSolverr for
    `169.254.169.254/latest/meta-data/…`, which every other fetch path refuses.
    """
    from pzi.flaresolverr import fetch_html_via_flaresolverr, fetch_pdf_via_flaresolverr

    posted: list[str] = []

    def _spy(endpoint: str, payload: dict) -> str:
        posted.append(payload.get("url", ""))
        return '{"status": "ok", "solution": {"response": "<html></html>"}}'

    for url in (
        "http://169.254.169.254/latest/meta-data/iam/security-credentials/",
        "http://127.0.0.1:8765/delete",
        "http://[::1]/admin",
        "file:///etc/passwd",
    ):
        assert fetch_html_via_flaresolverr(
            url, server_url="http://127.0.0.1:8191", post_json=_spy
        ) is None, url
        assert fetch_pdf_via_flaresolverr(
            url, server_url="http://127.0.0.1:8191", post_json=_spy
        ) is None, url

    # Nothing was ever forwarded.
    assert posted == []


def test_cookies_are_not_sent_to_every_candidate_host() -> None:
    """One cookie string was reused across every landing-page candidate.

    Those candidates come from provider-supplied `canonical_url` and
    `source_url`, so a session cookie captured for the user's institutional
    proxy went to whatever host a metadata record happened to name.
    """
    from pzi.pdf_discovery import cookies_for_url

    context = {
        "cookies": "session=secret",
        "raw_value": "https://proxy.university.example/article/1",
    }

    assert cookies_for_url(context, "https://proxy.university.example/pdf/1") == "session=secret"
    assert cookies_for_url(context, "https://elsewhere.example/pdf/1") is None
    # Scheme and port are part of the origin.
    assert cookies_for_url(context, "http://proxy.university.example/pdf/1") is None
    assert cookies_for_url(context, "https://proxy.university.example:8443/pdf/1") is None


def test_the_attach_token_is_not_accepted_from_the_query_string() -> None:
    """`_attach_url` deliberately keeps it out of the URL and returns a header.

    A URL lands in access logs, `Referer` and shell history, so accepting the
    token from the query string reintroduced the leak the other half avoids.
    """
    source = Path("src/pzi/http_api.py").read_text(encoding="utf-8")
    assert 'query.get("attach_token"' not in source
    assert 'request.headers.get("X-Pzi-Attach-Token")' in source


def test_stale_chrome_profile_clones_are_swept(tmp_path: Path) -> None:
    """The clone is a full copy of the real profile, cookie database included.

    Cleanup lived only in `close()`, which a hook child killed at its 180 s
    timeout never reaches — so each timeout left another readable copy behind.
    """
    from pzi.browser_session import _CLONE_PREFIX, _sweep_stale_profile_clones

    stale = tmp_path / f"{_CLONE_PREFIX}old"
    stale.mkdir()
    (stale / "Cookies").write_bytes(b"sqlite")
    fresh = tmp_path / f"{_CLONE_PREFIX}new"
    fresh.mkdir()
    unrelated = tmp_path / "someone-elses-tmpdir"
    unrelated.mkdir()

    old_time = time.time() - 48 * 60 * 60
    import os

    os.utime(stale, (old_time, old_time))

    removed = _sweep_stale_profile_clones(temp_root=tmp_path)

    assert stale in removed
    assert not stale.exists()
    # A live session's clone and anything not ours are untouched.
    assert fresh.exists()
    assert unrelated.exists()


def test_the_headful_flow_fits_inside_its_own_timeout() -> None:
    """60 s navigating plus a full 120 s challenge wait, inside 180 s.

    The wait was killed at the moment it became useful — and that is before
    counting browser startup or a first-run profile copy.
    """
    from pzi.browser_pdf import _HOOK_DEFAULT_TIMEOUT_SECONDS, _hook_timeout_seconds

    headful = ["python", "-m", "pzi.browser_pdf_hook", "--headful", "--challenge-timeout", "120"]
    assert _hook_timeout_seconds(headful) > 120 + 60

    assert _hook_timeout_seconds(["python", "-m", "pzi.browser_pdf_hook"]) == (
        _HOOK_DEFAULT_TIMEOUT_SECONDS
    )
    assert _hook_timeout_seconds(
        ["python", "-m", "pzi.browser_pdf_hook", "--challenge-timeout=200"]
    ) > 200


def test_node_resolves_a_cached_binary_without_the_network(tmp_path: Path) -> None:
    """`_latest_node_version()` is a fetch and ran *before* the cache check.

    A working Node in the data home plus no network resolved to `None`, and any
    upstream 22.x point release forced a fresh download of a runtime already
    there.
    """
    from pzi import node_runtime

    dist = node_runtime._node_dist_name()
    node_dir = tmp_path / "node"
    binary = node_dir / f"node-v22.11.0-{dist}" / "bin" / "node"
    binary.parent.mkdir(parents=True)
    binary.write_text("#!/bin/sh\n", encoding="utf-8")

    original = node_runtime._node_binary_runs
    node_runtime._node_binary_runs = lambda _p: True
    try:
        found = node_runtime._cached_node_binary(node_dir, dist, version=None)
    finally:
        node_runtime._node_binary_runs = original

    assert found == str(binary)


def test_the_mirror_reads_are_capped() -> None:
    """Both were a bare `resp.read()`, so a mirror could stream until OOM."""
    import pytest

    from pzi.node_runtime import _read_capped

    class _Endless:
        def read(self, n: int) -> bytes:
            return b"x" * n

    with pytest.raises(RuntimeError, match="oversized"):
        _read_capped(_Endless(), 1024, "https://nodejs.org/dist/index.json")


def test_npm_uses_ci_when_a_lockfile_is_present(tmp_path: Path) -> None:
    """`npm install --production` may resolve outside the lockfile and rewrite it."""
    from pzi.ts_backend import _npm_install_argv

    (tmp_path / "package-lock.json").write_text("{}", encoding="utf-8")
    assert "ci" in _npm_install_argv("/usr/bin/node", tmp_path)

    without = tmp_path / "nolock"
    without.mkdir()
    assert "install" in _npm_install_argv("/usr/bin/node", without)


def test_the_firefox_minimum_matches_what_the_manifest_needs() -> None:
    """109 could not load `background.js`; 109–127 lost cross-origin PDFs silently."""
    import json
    import sys

    sys.path.insert(0, "tools")
    from build_extension import _build_firefox_manifest

    manifest = _build_firefox_manifest({"manifest_version": 3, "name": "pzi"})
    gecko = manifest["browser_specific_settings"]["gecko"]
    assert gecko["strict_min_version"] == "128.0"
    # And the two features that set the floor are still the reason.
    assert manifest["background"]["type"] == "module"
    assert json.dumps(manifest)
