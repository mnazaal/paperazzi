"""Keep the translation-server child on the public web.

Every outbound HTTP(S) request from that Node process goes through a
:class:`pzi.pinning_proxy.PinningProxy`, because `request` re-evaluates
`HTTP(S)_PROXY` for every redirect and meta-refresh hop. A server pzi did not
start (one already reachable at `translation_server_url`) does not inherit this.
"""

from __future__ import annotations

import threading

from pzi.pinning_proxy import PinningProxy

_shared: PinningProxy | None = None
_shared_lock = threading.Lock()


def shared_guard() -> PinningProxy:
    """One guard per pzi process; its daemon threads die with the process."""
    global _shared
    with _shared_lock:
        if _shared is None:
            _shared = PinningProxy()
        return _shared


def reset_shared_guard(guard: PinningProxy | None = None) -> None:
    """Replace (and close) the shared guard. Tests inject a fake resolver."""
    global _shared
    with _shared_lock:
        if _shared is not None:
            _shared.close()
        _shared = guard


def proxy_env(guard: PinningProxy) -> dict[str, str]:
    """Child environment sending all outbound HTTP(S) through *guard*."""
    return {
        "HTTP_PROXY": guard.url, "http_proxy": guard.url,
        "HTTPS_PROXY": guard.url, "https_proxy": guard.url,
        "NO_PROXY": "", "no_proxy": "",
    }
