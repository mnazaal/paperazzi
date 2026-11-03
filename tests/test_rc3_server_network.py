"""rc3 server-network lane: install-lock deadline, header deadline, redirects."""

import io
import time
from pathlib import Path
from unittest.mock import patch

import portalocker

from pzi import ts_backend


def _hold_install_lock(data_home: Path):
    data_home.mkdir(parents=True, exist_ok=True)
    handle = open(data_home / "ts.install.lock", "a")
    portalocker.lock(handle, portalocker.LOCK_EX | portalocker.LOCK_NB)
    return handle


def test_install_waiter_gives_up_with_actionable_message(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(ts_backend, "_INSTALL_LOCK_WAIT_SECONDS", 0.4)
    holder = _hold_install_lock(tmp_path)
    err = io.StringIO()
    built: list[int] = []
    try:
        with patch.object(ts_backend.shutil, "which", return_value="/usr/bin/git"), \
             patch.object(ts_backend, "_build_translation_server",
                          side_effect=lambda *a, **k: built.append(1)):
            start = time.monotonic()
            result = ts_backend.ensure_translation_server(
                tmp_path, "node", stdout=io.StringIO(), stderr=err
            )
            elapsed = time.monotonic() - start
    finally:
        holder.close()
    assert result is None
    assert not built
    assert elapsed < 5
    assert "ts.install.lock" in err.getvalue()
    assert "another pzi process" in err.getvalue()


def test_uncontended_install_still_proceeds(tmp_path: Path) -> None:
    sentinel = tmp_path / "ts"
    with patch.object(ts_backend.shutil, "which", return_value="/usr/bin/git"), \
         patch.object(ts_backend, "_build_translation_server", return_value=sentinel) as build:
        result = ts_backend.ensure_translation_server(
            tmp_path, "node", stdout=io.StringIO(), stderr=io.StringIO()
        )
    assert result == sentinel
    build.assert_called_once()


def test_waiter_proceeds_once_lock_is_released(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(ts_backend, "_INSTALL_LOCK_WAIT_SECONDS", 10)
    holder = _hold_install_lock(tmp_path)
    import threading
    threading.Timer(0.5, holder.close).start()
    sentinel = tmp_path / "ts"
    with patch.object(ts_backend.shutil, "which", return_value="/usr/bin/git"), \
         patch.object(ts_backend, "_build_translation_server", return_value=sentinel):
        result = ts_backend.ensure_translation_server(
            tmp_path, "node", stdout=io.StringIO(), stderr=io.StringIO()
        )
    assert result == sentinel
