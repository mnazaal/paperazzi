"""Measure the resident set size of an idle `pzi server` process.

Local only: a throwaway HOME, no config file (so no translation-server child
and no browser session is started), loopback bind, `--no-auth`. Reads VmRSS from
/proc after the server answers one `GET /` request, over several samples.

    python scripts/measure_server_rss.py [--samples N]

The figure it prints is what docs/reference.md-style prose may cite for
"`pzi server` alone". It is Linux-only (/proc) and varies with the Python
version and installed extras, so quote it with the platform it was taken on.
"""

from __future__ import annotations

import argparse
import os
import platform
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _vmrss_kb(pid: int) -> int:
    with open(f"/proc/{pid}/status", encoding="ascii") as fh:
        for line in fh:
            if line.startswith("VmRSS:"):
                return int(line.split()[1])
    raise RuntimeError("VmRSS not found")


def measure_once() -> int:
    with tempfile.TemporaryDirectory() as home:
        # Drop every PZI_* variable: PZI_CONFIG would point the child at the
        # caller's real config, contradicting "no config file".
        env = {
            **{k: v for k, v in os.environ.items() if not k.startswith("PZI_")},
            "HOME": home,
            "XDG_CONFIG_HOME": f"{home}/config",
            "XDG_DATA_HOME": f"{home}/data",
            "XDG_CACHE_HOME": f"{home}/cache",
            "PZI_SKIP_AUTO_START": "1",
        }
        port = _free_port()
        proc = subprocess.Popen(
            [sys.executable, "-m", "pzi", "server", "--no-auth",
             "--host", "127.0.0.1", "--port", str(port)],
            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        )
        try:
            deadline = time.monotonic() + 30
            while True:
                if proc.poll() is not None:
                    err = proc.stderr.read().decode() if proc.stderr else ""
                    raise RuntimeError(f"server exited early: {err}")
                try:
                    urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=2).read()
                    break
                except urllib.error.HTTPError:
                    break  # it answered; any status proves it is serving
                except OSError:
                    if time.monotonic() > deadline:
                        raise RuntimeError("server never answered")
                    time.sleep(0.2)
            time.sleep(1)
            return _vmrss_kb(proc.pid)
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--samples", type=int, default=5)
    args = ap.parse_args()
    kb = sorted(measure_once() for _ in range(args.samples))
    print(f"platform: {platform.platform()} python {platform.python_version()}")
    print(f"VmRSS kB samples: {kb}")
    print(f"median: {kb[len(kb) // 2] / 1024:.1f} MiB")


if __name__ == "__main__":
    main()
