import json
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKGROUND_JS = PROJECT_ROOT / "browser-extension" / "background.js"


def _run_background_module(script: str, tmp_path: Path) -> dict:
    module_path = tmp_path / "background.mjs"
    module_path.write_text(BACKGROUND_JS.read_text())
    runner_path = tmp_path / "runner.mjs"
    runner_path.write_text(script.replace("./background.js", "./background.mjs"))
    result = subprocess.run(
        ["node", str(runner_path)],
        check=True,
        text=True,
        capture_output=True,
    )
    return json.loads(result.stdout)


def test_browser_extension_fetches_bibs_with_token_and_endpoint(tmp_path: Path) -> None:
    result = _run_background_module(
        r'''
globalThis.calls = [];
globalThis.chrome = {
  storage: {
    local: {
      get: async (key) => ({ endpoint: "http://pzi.test/capture", authToken: "tok" })
    }
  },
  runtime: { onInstalled: { addListener: () => {} } },
};
globalThis.fetch = async (url, options) => {
  globalThis.calls.push({ url, options });
  return { ok: true, json: async () => ({ status: "ok", bibs: [{ name: "ml", default: true }] }) };
};
const mod = await import("./background.js");
const bibs = await mod.fetchBibs();
console.log(JSON.stringify({ bibs, calls: globalThis.calls }));
''',
        tmp_path,
    )

    assert result["bibs"] == [{"name": "ml", "default": True}]
    assert result["calls"] == [
        {
            "url": "http://pzi.test/bibs",
            "options": {"headers": {"X-Pzi-Token": "tok"}},
        }
    ]


def test_browser_extension_capture_posts_metadata_and_streams_pdf(tmp_path: Path) -> None:
    result = _run_background_module(
        r'''
const pdfBytes = new Uint8Array([37, 80, 68, 70, 45, 49]).buffer;
globalThis.fetchCalls = [];
globalThis.chrome = {
  storage: {
    local: {
      get: async () => ({ endpoint: "http://pzi.test/capture", authToken: "tok" })
    }
  },
  runtime: { onInstalled: { addListener: () => {} } },
  tabs: { query: async () => [{ id: 7, url: "https://paper.test/article" }] },
  scripting: {
    executeScript: async ({ func, args }) => {
      const source = String(func);
      if (source.includes("citation_doi")) {
        return [{ result: {
          pageTitle: "Paper Title",
          canonicalUrl: "https://paper.test/canonical",
          sourceUrl: args[0],
          abstractUrl: "https://paper.test/abstract",
          doi: "10.123/example",
        }}];
      }
      return [{ result: ["https://paper.test/paper.pdf"] }];
    }
  },
};
globalThis.fetch = async (url, options = {}) => {
  globalThis.fetchCalls.push({ url, options });
  if (url.endsWith("/capture")) {
    return { ok: true, json: async () => ({ status: "ok", citekey: "smith2024paper" }) };
  }
  if (url.endsWith(".pdf")) {
    return {
      ok: true,
      headers: { get: () => "application/pdf" },
      arrayBuffer: async () => pdfBytes
    };
  }
  return { ok: true, json: async () => ({ status: "ok" }) };
};
globalThis.btoa = (value) => Buffer.from(value, "binary").toString("base64");
const mod = await import("./background.js");
const capture = await mod.captureCurrentTab({ tags: ["ml"], bib: "main", dryRun: false });
const simplified = globalThis.fetchCalls.map((call) => ({
  url: call.url,
  method: call.options.method || "GET",
  headers: call.options.headers || null,
  body: call.options.body ? JSON.parse(call.options.body) : null,
  credentials: call.options.credentials || null,
}));
console.log(JSON.stringify({ capture, calls: simplified }));
''',
        tmp_path,
    )

    assert result["capture"] == {
        "status": "ok",
        "citekey": "smith2024paper",
        "pdf_attach": {
            "status": "ok",
        },
    }
    assert result["calls"][0] == {
        "url": "http://pzi.test/capture",
        "method": "POST",
        "headers": {"Content-Type": "application/json", "X-Pzi-Token": "tok"},
        "body": {
            "url": "https://paper.test/article",
            "tags": ["ml"],
            "bib": "main",
            "dry_run": False,
            "pdf_url_candidates": ["https://paper.test/paper.pdf"],
            "page_title": "Paper Title",
            "canonical_url": "https://paper.test/canonical",
            "source_url": "https://paper.test/article",
            "abstract_url": "https://paper.test/abstract",
            "doi": "10.123/example",
        },
        "credentials": None,
    }
    assert result["calls"][1] == {
        "url": "https://paper.test/paper.pdf",
        "method": "GET",
        "headers": None,
        "body": None,
        "credentials": "include",
    }
    assert result["calls"][2] == {
        "url": "http://pzi.test/attach-pdf-bytes",
        "method": "POST",
        "headers": {"Content-Type": "application/json", "X-Pzi-Token": "tok"},
        "body": {
            "citekey": "smith2024paper",
            "bib": "main",
            "source_url": "https://paper.test/paper.pdf",
            "pdf_base64": "JVBERi0x",
        },
        "credentials": None,
    }
