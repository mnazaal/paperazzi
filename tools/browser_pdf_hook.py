#!/usr/bin/env python3
"""Thin source-tree wrapper for ``python -m pzi.browser_pdf_hook``."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if SRC.is_dir():
    sys.path.insert(0, str(SRC))


def main() -> int:
    from pzi.browser_pdf_hook import main as run

    return run()


if __name__ == "__main__":
    raise SystemExit(main())
