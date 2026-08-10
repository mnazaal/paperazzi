"""Tests for Tier 4: search result detection and multi-item capture."""
import json
import re
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKGROUND_JS = PROJECT_ROOT / "browser-extension" / "background.js"
BACKGROUND_DIR = PROJECT_ROOT / "browser-extension" / "background"
POPUP_FORMAT_JS = PROJECT_ROOT / "browser-extension" / "popup_format.js"

# Rewrite relative ESM imports of local ``.js`` modules to ``.mjs`` so Node can
# resolve the copied test modules (matches any "./…/<name>.js").
_LOCAL_JS_IMPORT = re.compile(r'"(\./[^"]+?)\.js"')


def _rewrite_local_imports(text: str) -> str:
    return _LOCAL_JS_IMPORT.sub(r'"\1.mjs"', text)


def _run_background_module(script: str, tmp_path: Path) -> dict:
    module_path = tmp_path / "background.mjs"
    module_path.write_text(_rewrite_local_imports(BACKGROUND_JS.read_text()))
    # Copy background/ subdirectory with .mjs rewrites
    if BACKGROUND_DIR.is_dir():
        dest_dir = tmp_path / "background"
        dest_dir.mkdir(exist_ok=True)
        for f in BACKGROUND_DIR.iterdir():
            if f.suffix == ".js":
                (dest_dir / f"{f.stem}.mjs").write_text(
                    _rewrite_local_imports(f.read_text())
                )
    runner_path = tmp_path / "runner.mjs"
    runner_path.write_text(script.replace("./background.js", "./background.mjs"))
    result = subprocess.run(
        ["node", str(runner_path)],
        check=True,
        text=True,
        capture_output=True,
    )
    return json.loads(result.stdout)


def _run_popup_format_module(script: str, tmp_path: Path) -> dict:
    module_path = tmp_path / "popup_format.mjs"
    module_path.write_text(POPUP_FORMAT_JS.read_text())
    runner_path = tmp_path / "runner.mjs"
    runner_path.write_text(script.replace("./popup_format.js", "./popup_format.mjs"))
    result = subprocess.run(
        ["node", str(runner_path)],
        check=True,
        text=True,
        capture_output=True,
    )
    return json.loads(result.stdout)


# ── Detection ────────────────────────────────────────────────────────────


def test_search_detection_finds_google_scholar(tmp_path: Path) -> None:
    """detectAndExtractSearchResults detects a Google Scholar page by URL pattern."""
    result = _run_background_module(
        r'''
globalThis.chrome = {
  runtime: { onInstalled: { addListener: () => {} } },
  scripting: {
    executeScript: async (_injection) => ([{
      result: { detected: true, patternName: "Google Scholar", count: 15, items: [] }
    }]),
  },
};
const mod = await import("./background.js");
const outcome = await mod.detectAndExtractSearchResults(7, "https://scholar.google.com/scholar?q=test");
console.log(JSON.stringify(outcome));
''',
        tmp_path,
    )

    assert result["detected"] is True
    assert result["patternName"] == "Google Scholar"
    assert result["count"] == 15


def test_search_detection_returns_no_match_for_article_page(tmp_path: Path) -> None:
    """detectAndExtractSearchResults returns {detected:false} for regular article pages."""
    result = _run_background_module(
        r'''
globalThis.chrome = {
  runtime: { onInstalled: { addListener: () => {} } },
  scripting: {
    executeScript: async (_injection) => ([{
      result: { detected: false, items: [] }
    }]),
  },
};
const mod = await import("./background.js");
const outcome = await mod.detectAndExtractSearchResults(7, "https://nature.com/articles/s41586");
console.log(JSON.stringify(outcome));
''',
        tmp_path,
    )

    assert result["detected"] is False
    assert result["items"] == []


def test_search_detection_handles_no_tab(tmp_path: Path) -> None:
    """detectAndExtractSearchResults returns {detected:false} when no tabId."""
    result = _run_background_module(
        r'''
globalThis.chrome = { runtime: { onInstalled: { addListener: () => {} } } };
const mod = await import("./background.js");
const outcome = await mod.detectAndExtractSearchResults(0, "");
console.log(JSON.stringify(outcome));
''',
        tmp_path,
    )

    assert result["detected"] is False


# ── Capture ──────────────────────────────────────────────────────────────


def test_formatMultiCaptureResult_all_success(tmp_path: Path) -> None:
    """formatMultiCaptureResult shows success count for all-ok batch."""
    result = _run_popup_format_module(
        r'''
const mod = await import("./popup_format.js");
const message = mod.formatMultiCaptureResult({
  status: "complete",
  total: 3,
  results: [
    { status: "ok", citekey: "smith2024a" },
    { status: "ok", citekey: "jones2024b" },
    { status: "ok", citekey: "lee2024c" },
  ],
});
console.log(JSON.stringify({ message }));
''',
        tmp_path,
    )

    assert "Captured 3/3 items" in result["message"]
    assert "failed" not in result["message"]


def test_formatMultiCaptureResult_mixed(tmp_path: Path) -> None:
    """formatMultiCaptureResult shows mixed success/failure."""
    result = _run_popup_format_module(
        r'''
const mod = await import("./popup_format.js");
const message = mod.formatMultiCaptureResult({
  status: "complete",
  total: 4,
  results: [
    { status: "ok", citekey: "a" },
    { status: "error", message: "not found" },
    { status: "ok", citekey: "b" },
    { status: "error", message: "timeout" },
  ],
});
console.log(JSON.stringify({ message }));
''',
        tmp_path,
    )

    assert "Captured 2/4 items" in result["message"]
    assert "2 failed" in result["message"]
    assert "not found" in result["message"]
    assert "timeout" in result["message"]


def test_formatMultiCaptureResult_none_result(tmp_path: Path) -> None:
    """formatMultiCaptureResult handles null/empty input."""
    result = _run_popup_format_module(
        r'''
const mod = await import("./popup_format.js");
const nullMsg = mod.formatMultiCaptureResult(null);
const emptyMsg = mod.formatMultiCaptureResult({});
console.log(JSON.stringify({ nullMsg, emptyMsg }));
''',
        tmp_path,
    )

    assert "failed" in result["nullMsg"]
    assert "failed" in result["emptyMsg"]
