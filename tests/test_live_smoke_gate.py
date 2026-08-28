"""The live job's honesty gate must count only tests that prove the path.

`tests/live/conftest.py` reddens the live step when no real translation-server
capture happened, and it decides that from `_CAPTURE_TESTS` — a hand-maintained
set of test names. A name in that set whose test does *not* pin the provider
silently turns the gate off: `test_live_add_arxiv_url_metadata` passes on a
Crossref fallback by design, and while it was counted the gate reported
"real translation-server capture: yes" for runs in which the server answered
nothing. That is the exact failure the gate exists to make visible.

Checked from the main suite rather than from `tests/live/`, because everything
under `tests/live/` is skipped unless `PZI_LIVE=1` — a guard that only runs in
the job it guards is not a guard.
"""

from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIVE_DIR = REPO_ROOT / "tests" / "live"

#: What "proves a real translation-server capture" means, in source terms: the
#: test pins the answering provider rather than merely asserting that some
#: provider answered.
PROOF = 'provider == "translation_server"'


def _capture_tests() -> frozenset[str]:
    """`_CAPTURE_TESTS` as the gate itself sees it."""
    spec = importlib.util.spec_from_file_location(
        "pzi_live_conftest_under_test", LIVE_DIR / "conftest.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module._CAPTURE_TESTS


def _live_test_sources() -> dict[str, str]:
    """Source of every `test_*` function defined under `tests/live/`."""
    sources: dict[str, str] = {}
    for path in sorted(LIVE_DIR.glob("test_*.py")):
        text = path.read_text(encoding="utf-8")
        for node in ast.parse(text).body:
            if isinstance(node, ast.FunctionDef) and node.name.startswith("test_"):
                sources[node.name] = ast.get_source_segment(text, node) or ""
    return sources


def test_every_counted_capture_test_exists() -> None:
    """A renamed or moved test must not empty the set silently."""
    sources = _live_test_sources()
    missing = sorted(name for name in _capture_tests() if name not in sources)
    assert not missing, (
        f"_CAPTURE_TESTS names tests that do not exist under tests/live/: {missing}. "
        "The gate counts passes by name, so a stale name can never be satisfied."
    )


def test_every_counted_capture_test_pins_the_provider() -> None:
    """Counting a test that tolerates a fallback disables the gate."""
    sources = _live_test_sources()
    unproven = sorted(
        name for name in _capture_tests() if PROOF not in sources.get(name, "")
    )
    assert not unproven, (
        f"_CAPTURE_TESTS counts {unproven}, which do not assert {PROOF!r}. "
        "Such a test passes on a Crossref/OpenAlex fallback, so counting it lets "
        "the gate report a translation-server capture that never happened."
    )


def test_the_gate_counts_at_least_one_test() -> None:
    """The set is what the gate measures; empty means it measures nothing."""
    assert _capture_tests(), "_CAPTURE_TESTS is empty — the live gate can never fire"
