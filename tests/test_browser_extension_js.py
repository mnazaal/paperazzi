import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

# Skipping locally is a convenience; skipping in CI is how 52 tests went
# unrun in the release gate while it reported success. Under `CI` a missing
# Node is a failure, matching `test_error_channels.py`'s asserted Node call.
if shutil.which("node") is None and os.environ.get("CI"):
    raise RuntimeError(
        "Node.js is required to run the browser-extension tests, and this is "
        "CI — a silent skip here means the gate certified nothing"
    )

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None, reason="Node.js not found on PATH"
)

# Rewrite relative ESM imports of local ``.js`` modules to ``.mjs`` so Node can
# resolve the copied test modules. Matches e.g. "./utils.js" and
# "./background/pdf_fetch.js" but leaves bare specifiers untouched.
_LOCAL_JS_IMPORT = re.compile(r'"(\./[^"]+?)\.js"')


def _rewrite_local_imports(text: str) -> str:
    return _LOCAL_JS_IMPORT.sub(r'"\1.mjs"', text)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKGROUND_JS = PROJECT_ROOT / "browser-extension" / "background.js"
BACKGROUND_DIR = PROJECT_ROOT / "browser-extension" / "background"
POPUP_JS = PROJECT_ROOT / "browser-extension" / "popup.js"
ONBOARDING_JS = PROJECT_ROOT / "browser-extension" / "onboarding.js"


def _run_background_module(script: str, tmp_path: Path) -> dict:
    module_path = tmp_path / "background.mjs"
    # Copy background.js to .mjs, rewriting its imports of split modules.
    module_path.write_text(_rewrite_local_imports(BACKGROUND_JS.read_text()))
    # Copy background/ subdirectory (split modules) to .mjs files, rewriting
    # their inter-module imports too.
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
        text=True,
        capture_output=True,
    )
    if result.returncode != 0:
        raise AssertionError(
            f"node runner failed with {result.returncode}\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )
    return json.loads(result.stdout)


# A DOM stub that materialises the buttons `_renderRecent` writes as HTML, so
# the recent list can be clicked rather than read as source text.
_RECENT_LIST_DOM = r'''
const elements = new Map();
const makeElement = (id) => {
  let html = "";
  const el = {
    id, value: "", textContent: "", className: "", type: "", disabled: false,
    style: { cssText: "" }, children: [], handlers: {}, buttons: [],
    appendChild(child) { this.children.push(child); },
    addEventListener(event, handler) { (this.handlers[event] ??= []).push(handler); },
    querySelectorAll(selector) {
      const wanted = /data-action='([^']+)'/.exec(selector);
      return this.buttons.filter((b) => !wanted || b.dataset.action === wanted[1]);
    },
  };
  Object.defineProperty(el, "innerHTML", {
    get: () => html,
    set: (value) => {
      html = value;
      el.buttons = [...value.matchAll(/<button([^>]*)>/g)].map((tag) => {
        const dataset = {};
        for (const attr of tag[1].matchAll(/data-([a-z]+)="([^"]*)"/g)) dataset[attr[1]] = attr[2];
        return {
          dataset,
          handlers: {},
          addEventListener(event, handler) { (this.handlers[event] ??= []).push(handler); },
        };
      });
    },
  });
  return el;
};
globalThis.document = {
  getElementById: (id) => {
    if (!elements.has(id)) elements.set(id, makeElement(id));
    return elements.get(id);
  },
  createElement: () => makeElement("created"),
};
globalThis.window = { open: () => {} };
globalThis.requests = [];
globalThis.fetch = async (url, options = {}) => {
  globalThis.requests.push({ url, method: options.method || "GET" });
  return { ok: true, blob: async () => new Blob(["%PDF-1.4"], { type: "application/pdf" }), json: async () => ({}) };
};
const NativeURL = URL;
globalThis.URL = class extends NativeURL {
  static createObjectURL() { return "blob:pzi-pdf"; }
  static revokeObjectURL() {}
};
// The endpoint has a path of its own. A URL built by concatenation rather than
// by `endpointFor` keeps that path and asks the wrong place.
const RECENT = [
  { citekey: "smith:2024", title: "First", bib: "ml", ts: 1 },
  { citekey: "jones2023", title: "Second", bib: "main", ts: 2 },
];
globalThis.chrome = {
  storage: {
    // `pzi:recent` is durable: the list is a history, and living in the
    // session area meant it emptied every time the browser closed.
    local: {
      get: async (keys) => (String(keys).includes("pzi:recent") ? { "pzi:recent": RECENT } : {}),
      set: async () => ({}),
      remove: async () => ({}),
    },
    session: { get: async () => ({}), set: async () => ({}), remove: async () => ({}) },
  },
  tabs: { query: async () => [] },
  runtime: { sendMessage: () => {} },
};
'''


def test_popup_recent_actions_use_endpoint_path_helper(tmp_path: Path) -> None:
    """A recent entry's PDF button must resolve against the endpoint's origin.

    Driven rather than grepped: `endpointFor` is a means, and concatenating
    `base + "/pdf/"` onto an endpoint that has a path of its own is the failure
    it exists to prevent — which only a real URL shows.
    """
    result = _run_popup_js_test(
        _RECENT_LIST_DOM
        + r'''
await import("./popup.js");
for (let i = 0; i < 20; i += 1) await new Promise((r) => setTimeout(r, 0));
const buttons = elements.get("recent-list").buttons;
for (const button of buttons) await button.handlers.click[0]();
for (let i = 0; i < 20; i += 1) await new Promise((r) => setTimeout(r, 0));
console.log(JSON.stringify({ requests: globalThis.requests }));
''',
        tmp_path,
    )

    # Resolved against the origin, with the citekey's colon percent-encoded.
    assert [r["url"] for r in result["requests"]] == [
        "http://127.0.0.1:8765/pdf/smith%3A2024?bib=ml",
        "http://127.0.0.1:8765/pdf/jones2023",
    ], result["requests"]


def test_popup_recent_list_is_a_capture_log_without_delete(tmp_path: Path) -> None:
    """The recent list is a read-only capture log (open-PDF only); no delete UI.

    Driven rather than grepped: clicking every affordance the list renders must
    produce reads and nothing else. The assertions this replaced named three
    identifiers a reintroduced delete need not reuse.
    """
    result = _run_popup_js_test(
        _RECENT_LIST_DOM
        + r'''
await import("./popup.js");
for (let i = 0; i < 20; i += 1) await new Promise((r) => setTimeout(r, 0));
const list = elements.get("recent-list");
const actions = list.buttons.map((b) => b.dataset.action);
for (const button of list.buttons) {
  for (const handler of button.handlers.click || []) await handler();
}
for (let i = 0; i < 20; i += 1) await new Promise((r) => setTimeout(r, 0));
console.log(JSON.stringify({ actions, html: list.innerHTML, requests: globalThis.requests }));
''',
        tmp_path,
    )

    # One affordance per entry, and it is the read-only one.
    assert result["actions"] == ["pdf", "pdf"], result["actions"]

    # Exercising the whole list issues reads only.
    assert all(r["method"] == "GET" for r in result["requests"]), result["requests"]
    assert not any("delete" in r["url"] for r in result["requests"]), result["requests"]
    assert "delete" not in result["html"].lower()


def test_browser_extension_fetches_bibs_with_token_and_endpoint(tmp_path: Path) -> None:
    result = _run_background_module(
        r'''
globalThis.calls = [];
globalThis.chrome = {
  storage: {
    local: {
      get: async (key) => ({ endpoint: "http://127.0.0.1:8765/capture", authToken: "tok" })
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
            "url": "http://127.0.0.1:8765/bibs",
            "options": {"headers": {"X-Pzi-Token": "tok"}},
        }
    ]


def test_capture_generic_page_metadata_not_marked_ieee_trusted(tmp_path: Path) -> None:
    result = _run_background_module(
        r'''
globalThis.chrome = {
  storage: {
    local: { get: async () => ({ endpoint: "http://127.0.0.1:8765/capture" }) },
    session: { get: async () => ({}), set: () => {} },
  },
  runtime: { onInstalled: { addListener: () => {} } },
  tabs: { query: async () => [{ id: 7, url: "https://example.com/paper" }] },
  scripting: {
    executeScript: async (opts) => {
      globalThis.__worlds = globalThis.__worlds || [];
      globalThis.__worlds.push(opts.world);
      if (typeof opts.func === "function") {
        globalThis.location = { hostname: "example.com" };
        globalThis.window = {};
        globalThis.document = {
          title: "Generic Paper",
          head: { innerHTML: "" },
          querySelector: () => null,
          querySelectorAll: () => [],
        };
        return [{ result: opts.func(...(opts.args || [])) }];
      }
      return [{ result: [] }];
    },
  },
};
globalThis.fetch = async (_url, _options) => ({
  ok: true,
  json: async () => ({ status: "ok", citekey: "generic2024" }),
});
const mod = await import("./background.js");
const result = await mod.captureCurrentTab({ dryRun: true });
console.log(JSON.stringify({ capture_body: result.capture_body, worlds: globalThis.__worlds }));
''',
        tmp_path,
    )

    # `trusted_fields`, not `metadata_source`: the server reads the former
    # (`http_post_routes.py:138`) and never read the latter, which is why the
    # label was dropped from the wire. A generic DOM scan trusts nothing.
    assert result["capture_body"]["trusted_fields"] is None
    assert "metadata_source" not in result["capture_body"]
    # And it is not read in the page's own realm at all. The publisher check ran
    # *inside* the page as `location.hostname.endsWith(...)`, where
    # `String.prototype.endsWith` is page-overridable — so any page could pass
    # the gate and have nine forged `trusted_fields` promoted by the server into
    # authoritative overrides beating the real Crossref lookup.
    assert "MAIN" not in (result["worlds"] or []), result["worlds"]


def test_bot_bypass_uses_visible_helper_tab_when_hidden_iframe_observes_nothing(tmp_path: Path) -> None:
    result = _run_background_module(
        r'''
let listener = null;
globalThis.chrome = {
  runtime: { onInstalled: { addListener: () => {} } },
  webRequest: {
    onHeadersReceived: {
      addListener: (fn) => { listener = fn; },
      removeListener: () => {},
    },
  },
  scripting: { executeScript: async () => [{ result: null }] },
  tabs: {
    create: async () => {
      setTimeout(() => listener?.({
        tabId: 99,
        url: "https://ieeexplore.ieee.org/stampPDF/getPDF.jsp?tp=&arnumber=9840963",
        responseHeaders: [{ name: "Content-Type", value: "application/pdf" }],
      }), 0);
      return { id: 99 };
    },
    remove: async () => {},
  },
};
const mod = await import("./background.js");
const observed = await mod.botBypassPdfUrl(
  7,
  "https://ieeexplore.ieee.org/stamp/stamp.jsp?tp=&arnumber=9840963",
  { visibleTimeoutMs: 20 }
);
console.log(JSON.stringify({ observed }));
''',
        tmp_path,
    )

    assert result["observed"] == "https://ieeexplore.ieee.org/stampPDF/getPDF.jsp?tp=&arnumber=9840963"


def test_bot_bypass_arms_observer_before_visible_helper_navigation(tmp_path: Path) -> None:
    result = _run_background_module(
        r'''
let listener = null;
let createSawListener = false;
let updateCalled = false;
globalThis.chrome = {
  runtime: { onInstalled: { addListener: () => {} } },
  webRequest: {
    onHeadersReceived: {
      addListener: (fn) => { listener = fn; },
      removeListener: () => {},
    },
  },
  scripting: { executeScript: async () => [{ result: null }] },
  tabs: {
    create: async () => {
      createSawListener = Boolean(listener);
      return { id: 99 };
    },
    update: async (tabId, props) => {
      updateCalled = true;
      listener?.({
        tabId,
        url: "https://ieeexplore.ieee.org/stampPDF/getPDF.jsp?tp=&arnumber=9840963",
        responseHeaders: [{ name: "Content-Type", value: "application/pdf" }],
      });
      return { id: tabId, url: props.url };
    },
    remove: async () => {},
  },
};
const mod = await import("./background.js");
const observed = await mod.botBypassPdfUrl(
  7,
  "https://ieeexplore.ieee.org/stamp/stamp.jsp?tp=&arnumber=9840963",
  { visibleTimeoutMs: 20 }
);
console.log(JSON.stringify({ observed, createSawListener, updateCalled }));
''',
        tmp_path,
    )

    assert result["createSawListener"] is True
    assert result["updateCalled"] is True
    assert result["observed"] == "https://ieeexplore.ieee.org/stampPDF/getPDF.jsp?tp=&arnumber=9840963"


def test_bot_bypass_detects_pdf_from_content_disposition_header(tmp_path: Path) -> None:
    result = _run_background_module(
        r'''
let listener = null;
globalThis.chrome = {
  runtime: { onInstalled: { addListener: () => {} } },
  webRequest: {
    onHeadersReceived: {
      addListener: (fn) => { listener = fn; },
      removeListener: () => {},
    },
  },
  scripting: { executeScript: async () => [{ result: null }] },
  tabs: {
    create: async () => ({ id: 99 }),
    update: async (tabId, props) => {
      listener?.({
        tabId,
        url: props.url,
        responseHeaders: [
          { name: "Content-Type", value: "application/octet-stream" },
          { name: "Content-Disposition", value: "inline; filename=paper.pdf" },
        ],
      });
      return { id: tabId, url: props.url };
    },
    remove: async () => {},
  },
};
const mod = await import("./background.js");
const observed = await mod.botBypassPdfUrl(
  7,
  "https://ieeexplore.ieee.org/stamp/stamp.jsp?tp=&arnumber=9840963",
  { visibleTimeoutMs: 20 }
);
console.log(JSON.stringify({ observed }));
''',
        tmp_path,
    )

    assert result["observed"] == "https://ieeexplore.ieee.org/stamp/stamp.jsp?tp=&arnumber=9840963"


def test_pdf_observer_does_not_filter_out_firefox_pdf_viewer_resource_types(tmp_path: Path) -> None:
    result = _run_background_module(
        r'''
let filters = [];
globalThis.chrome = {
  runtime: { onInstalled: { addListener: () => {} } },
  webRequest: {
    onHeadersReceived: {
      addListener: (_fn, filter) => { filters.push(filter); },
      removeListener: () => {},
    },
  },
  scripting: { executeScript: async () => [{ result: null }] },
  tabs: {
    create: async () => ({ id: 99 }),
    update: async () => ({ id: 99 }),
    remove: async () => {},
  },
};
const mod = await import("./background.js");
await mod.botBypassPdfUrl(
  7,
  "https://ieeexplore.ieee.org/stamp/stamp.jsp?tp=&arnumber=9840963",
  { visibleTimeoutMs: 1 }
);
console.log(JSON.stringify({ filters, keys: filters.map((f) => Object.keys(f)) }));
''',
        tmp_path,
    )

    assert result["filters"]
    assert all("types" not in keys for keys in result["keys"]), result["keys"]


def test_pdf_observer_records_diagnostic_when_webrequest_unavailable(tmp_path: Path) -> None:
    result = _run_background_module(
        r'''
globalThis.chrome = {
  runtime: { onInstalled: { addListener: () => {} } },
  scripting: { executeScript: async () => [{ result: null }] },
  tabs: {
    create: async () => ({ id: 99 }),
    update: async () => ({ id: 99 }),
    remove: async () => {},
  },
  // webRequest intentionally absent — simulate Firefox without webRequest permission
};
const mod = await import("./background.js");
await mod.botBypassPdfUrl(
  7,
  "https://ieeexplore.ieee.org/stamp/stamp.jsp?tp=&arnumber=9840963",
);
const events = mod.collectPdfObserverEvents();
console.log(JSON.stringify({ events }));
''',
        tmp_path,
    )
    assert result["events"]
    assert any(e.get("note") == "webRequest_unavailable" for e in result["events"])


def test_pdf_observer_registers_listener_when_webrequest_available(tmp_path: Path) -> None:
    result = _run_background_module(
        r'''
let filters = [];
let listener = null;
globalThis.chrome = {
  runtime: { onInstalled: { addListener: () => {} } },
  webRequest: {
    onHeadersReceived: {
      addListener: (fn, filter, extra) => {
        listener = fn;
        filters.push({ filter, extra });
      },
      removeListener: () => {},
      hasListener: () => Boolean(listener),
    },
  },
  scripting: { executeScript: async () => [{ result: null }] },
  tabs: {
    create: async () => ({ id: 99 }),
    update: async () => ({ id: 99 }),
    remove: async () => {},
  },
};
const mod = await import("./background.js");
await mod.startPdfObserver(99);
const events = mod.collectPdfObserverEvents();
const hasListener = filters.length > 0;
console.log(JSON.stringify({ filters, hasListener, events }));
''',
        tmp_path,
    )
    assert result["hasListener"] is True
    assert len(result["filters"]) >= 1


def test_visible_helper_waits_for_tab_complete_not_blind_timeout(tmp_path: Path) -> None:
    """botBypassViaVisibleTab must use tabs.onUpdated to wait for navigation, not blind setTimeout."""
    result = _run_background_module(
        r'''
let tabUpdatedListener = null;
let resolveComplete = null;
globalThis.chrome = {
  runtime: { onInstalled: { addListener: () => {} } },
  webRequest: {
    onHeadersReceived: {
      addListener: () => {},
      removeListener: () => {},
    },
  },
  scripting: { executeScript: async () => [{ result: null }] },
  tabs: {
    create: async () => ({ id: 99 }),
    update: async () => ({ id: 99 }),
    remove: async () => {},
    onUpdated: {
      addListener: (fn) => { tabUpdatedListener = fn; },
      removeListener: () => {},
      hasListener: (fn) => fn === tabUpdatedListener,
    },
  },
};
const mod = await import("./background.js");
const observed = await mod.botBypassPdfUrl(
  7,
  "https://ieeexplore.ieee.org/stamp/stamp.jsp?tp=&arnumber=9840963",
  { visibleTimeoutMs: 20 }
);
console.log(JSON.stringify({ observed, tabUpdatedListenerRegistered: tabUpdatedListener !== null }));
''',
        tmp_path,
    )
    assert result["tabUpdatedListenerRegistered"] is True


def test_browser_extension_capture_posts_metadata_and_streams_pdf(tmp_path: Path) -> None:
    result = _run_background_module(
        r'''
const pdfBytes = new Uint8Array([37, 80, 68, 70, 45, 49]).buffer;
globalThis.fetchCalls = [];
globalThis.chrome = {
  storage: {
    local: {
      get: async () => ({ endpoint: "http://127.0.0.1:8765/capture", authToken: "tok" })
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
          headHtml: "<head><meta name=\"citation_title\" content=\"Paper Title\"></head>",
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
  body: call.options.body ? (typeof call.options.body === "string" ? JSON.parse(call.options.body) : "<binary>") : null,
  credentials: call.options.credentials || null,
}));
console.log(JSON.stringify({ capture, calls: simplified }));
''',
        tmp_path,
    )

    assert result["capture"]["status"] == "ok"
    assert result["capture"]["citekey"] == "smith2024paper"
    assert result["capture"]["pdf_attach"]["status"] == "ok"
    assert result["calls"][0]["url"] == "http://127.0.0.1:8765/capture"
    assert result["calls"][0]["method"] == "POST"
    assert result["calls"][0]["headers"] == {"Content-Type": "application/json", "X-Pzi-Token": "tok"}
    body = result["calls"][0]["body"]
    assert body["url"] == "https://paper.test/article"
    assert body["tags"] == ["ml"]
    assert body["bib"] == "main"
    assert body["dry_run"] is False
    assert "https://paper.test/paper.pdf" in body["pdf_url_candidates"]
    assert body["page_title"] == "Paper Title"
    assert body["canonical_url"] == "https://paper.test/canonical"
    assert body["source_url"] == "https://paper.test/article"
    assert body["abstract_url"] == "https://paper.test/abstract"
    assert body["head_html"] == "<head><meta name=\"citation_title\" content=\"Paper Title\"></head>"
    assert body["doi"] == "10.123/example"
    assert result["calls"][0]["credentials"] is None
    # PDF fetch calls — order depends on candidate ranking (active_tab before dom)
    fetch_calls = [c for c in result["calls"] if c["method"] == "GET" and c["url"].endswith(".pdf")]
    assert len(fetch_calls) >= 1
    assert any(c["url"] == "https://paper.test/paper.pdf" for c in fetch_calls)

    attach_calls = [c for c in result["calls"] if "/attach-pdf" in c["url"]]
    assert len(attach_calls) >= 1
    attach_call = attach_calls[-1]  # last one is the successful fallback
    assert attach_call["method"] == "POST"
    assert attach_call["headers"]["X-Pzi-Token"] == "tok"
    if "attach-pdf-bytes" in attach_call["url"]:
        assert attach_call["body"]["citekey"] == "smith2024paper"
        assert attach_call["body"]["bib"] == "main"
        assert attach_call["body"]["pdf_base64"] == "JVBERi0x"


def test_browser_extension_pdf_bytes_fallback_includes_attach_session(tmp_path: Path) -> None:
    result = _run_background_module(
        r'''
const pdfBytes = new Uint8Array([37, 80, 68, 70, 45, 49]).buffer;
globalThis.fetchCalls = [];
globalThis.chrome = {
  storage: { local: { get: async () => ({ endpoint: "http://127.0.0.1:8765/capture" }) } },
  runtime: { onInstalled: { addListener: () => {} } },
  tabs: { query: async () => [{ id: 7, url: "https://paper.test/article" }] },
  scripting: {
    executeScript: async ({ func, args }) => {
      if (String(func).includes("citation_doi")) return [{ result: { pageTitle: "Paper", sourceUrl: args[0] } }];
      return [{ result: ["https://paper.test/paper.pdf"] }];
    }
  },
};
globalThis.fetch = async (url, options = {}) => {
  globalThis.fetchCalls.push({ url, options });
  if (url.endsWith("/capture")) {
    return { ok: true, json: async () => ({
      status: "ok",
      citekey: "smith2024paper",
      pdf_request: {
        request_id: "req-1",
        candidates: [{ url: "https://paper.test/paper.pdf" }],
        attach: {
          url: "http://127.0.0.1:8765/attach-pdf-raw?request_id=req-1&citekey=smith2024paper",
          token: "tok-1"
        }
      }
    }) };
  }
  if (url.includes("/attach-pdf-raw")) {
    return { ok: false, status: 403, json: async () => ({ error: "raw failed" }) };
  }
  if (url.includes("/attach-pdf-bytes")) {
    return { ok: true, json: async () => ({ status: "ok" }) };
  }
  if (url.endsWith(".pdf")) {
    return { ok: true, headers: { get: () => "application/pdf" }, arrayBuffer: async () => pdfBytes };
  }
  return { ok: true, json: async () => ({ status: "ok" }) };
};
globalThis.btoa = (value) => Buffer.from(value, "binary").toString("base64");
const mod = await import("./background.js");
const capture = await mod.captureCurrentTab({ dryRun: false });
const calls = globalThis.fetchCalls.map((call) => ({
  url: call.url,
  method: call.options.method || "GET",
  headers: call.options.headers || null,
  body: call.options.body ? (typeof call.options.body === "string" ? JSON.parse(call.options.body) : "<binary>") : null,
}));
console.log(JSON.stringify({ capture, calls }));
''',
        tmp_path,
    )

    fallback = next((c for c in result["calls"] if "/attach-pdf-bytes" in c["url"]), None)
    assert fallback is not None, result
    assert fallback["body"]["request_id"] == "req-1"
    assert fallback["body"]["attach_token"] == "tok-1"
    assert fallback["body"]["citekey"] == "smith2024paper"
    assert result["capture"]["pdf_attach"]["status"] == "ok"


def test_capture_posts_ieee_xplore_embedded_metadata(tmp_path: Path) -> None:
    result = _run_background_module(
        r'''
globalThis.fetchCalls = [];
const fakeDocument = {
  title: "IEEE Page Title",
  head: { innerHTML: "" },
  querySelector: (selector) => {
    if (selector === 'link[rel="canonical"]') return { getAttribute: () => "https://ieeexplore.ieee.org/document/9840963" };
    if (selector === 'meta[property="og:title"]') return { getAttribute: () => "IEEE OG Title" };
    return null;
  },
  querySelectorAll: () => [],
};
globalThis.chrome = {
  storage: {
    local: { get: async () => ({ endpoint: "http://127.0.0.1:8765/capture" }) },
    session: { get: async () => ({}), set: async () => ({}) },
  },
  runtime: { onInstalled: { addListener: () => {} } },
  tabs: { query: async () => [{ id: 7, url: "https://ieeexplore.ieee.org/document/9840963" }] },
  scripting: {
    executeScript: async ({ func, args }) => {
      if (String(func).includes("citation_doi")) {
        const oldDocument = globalThis.document;
        const oldLocation = globalThis.location;
        const oldWindow = globalThis.window;
        globalThis.document = fakeDocument;
        globalThis.location = { hostname: "ieeexplore.ieee.org" };
        globalThis.window = {
          xplGlobal: { document: { metadata: {
            displayDocTitle: "Analysis of the Use of the Kalman Filter",
            authors: [{ name: "N. E. Poborchaya" }, { name: "E. O. Lobova" }],
            publicationYear: "2022",
            publicationTitle: "2022 Systems of Signal Synchronization",
            abstract: "IEEE abstract text",
            startPage: "1",
            endPage: "5",
            pdfUrl: "/stamp/stamp.jsp?tp=&arnumber=9840963",
            doi: "10.1109/SYNCHROINFO55067.2022.9840963"
          } } }
        };
        try { return [{ result: func(...args) }]; }
        finally {
          globalThis.document = oldDocument;
          globalThis.location = oldLocation;
          globalThis.window = oldWindow;
        }
      }
      return [{ result: [] }];
    }
  },
};
globalThis.fetch = async (url, options = {}) => {
  globalThis.fetchCalls.push({ url, options });
  return { ok: true, json: async () => ({ status: "ok", citekey: "poborchaya2022analysis", pdf_path: "/tmp/a.pdf" }) };
};
globalThis.btoa = (value) => Buffer.from(value, "binary").toString("base64");
const mod = await import("./background.js");
const capture = await mod.captureCurrentTab({ dryRun: false });
const body = JSON.parse(globalThis.fetchCalls[0].options.body);
console.log(JSON.stringify({ capture, body }));
''',
        tmp_path,
    )

    assert result["capture"]["status"] == "ok"
    body = result["body"]
    assert body["page_title"] == "Analysis of the Use of the Kalman Filter"
    assert body["doi"] == "10.1109/SYNCHROINFO55067.2022.9840963"
    assert body["embedded_authors"] == ["N. E. Poborchaya", "E. O. Lobova"]
    assert body["embedded_year"] == "2022"
    assert body["embedded_venue"] == "2022 Systems of Signal Synchronization"
    assert body["embedded_abstract"] == "IEEE abstract text"
    assert body["embedded_pages"] == "1--5"
    assert body["embedded_pdf_url"] == "https://ieeexplore.ieee.org/stamp/stamp.jsp?tp=&arnumber=9840963"
    assert "https://ieeexplore.ieee.org/stamp/stamp.jsp?tp=&arnumber=9840963" in body["pdf_url_candidates"]
    # The IEEE extractor's claim is carried by `trusted_fields`, which the
    # server reads; the `metadata_source` label beside it never was.
    assert "metadata_source" not in body
    assert isinstance(body["trusted_fields"], list)
    assert "authors" in body["trusted_fields"]
    assert "doi" in body["trusted_fields"]


def test_capture_does_not_request_same_origin_permission_before_pdf_fetch(tmp_path: Path) -> None:
    result = _run_background_module(
        r'''
const pdfBytes = new Uint8Array([37, 80, 68, 70, 45, 49]).buffer;
globalThis.events = [];
globalThis.chrome = {
  storage: {
    local: { get: async () => ({ endpoint: "http://127.0.0.1:8765/capture" }) },
    session: { get: async () => ({}), set: async () => ({}) },
  },
  runtime: { onInstalled: { addListener: () => {} } },
  tabs: { query: async () => [{ id: 7, url: "https://ieeexplore.ieee.org/document/9840963" }] },
  scripting: {
    executeScript: async ({ func }) => {
      if (String(func).includes("citation_doi")) {
        return [{ result: {
          pageTitle: "IEEE",
          sourceUrl: "https://ieeexplore.ieee.org/document/9840963",
          embedded_pdf_url: "https://ieeexplore.ieee.org/stamp/stamp.jsp?tp=&arnumber=9840963",
        }}];
      }
      return [{ result: [] }];
    }
  },
  permissions: {
    contains: async (request) => {
      globalThis.events.push({ type: "contains", request });
      return false;
    },
    request: async (request) => {
      globalThis.events.push({ type: "permission", request });
      return true;
    },
    remove: async (request) => {
      globalThis.events.push({ type: "remove", request });
      return true;
    },
  },
};
globalThis.fetch = async (url, options = {}) => {
  globalThis.events.push({ type: "fetch", url, method: options.method || "GET" });
  if (url.endsWith("/capture")) {
    return { ok: true, json: async () => ({ status: "ok", citekey: "poborchaya2022analysis" }) };
  }
  if (url.includes("/stamp/")) {
    return { ok: true, status: 200, headers: { get: () => "application/pdf" }, arrayBuffer: async () => pdfBytes };
  }
  if (url.includes("/attach-pdf-raw")) {
    return { ok: true, json: async () => ({ status: "ok", pdf_path: "/tmp/a.pdf" }) };
  }
  return { ok: true, json: async () => ({ status: "ok" }) };
};
globalThis.btoa = (value) => Buffer.from(value, "binary").toString("base64");
const mod = await import("./background.js");
const capture = await mod.captureCurrentTab({ dryRun: false });
console.log(JSON.stringify({ capture, events: globalThis.events }));
''',
        tmp_path,
    )

    events = result["events"]
    capture_fetch_index = next(i for i, e in enumerate(events) if e["type"] == "fetch" and e["url"].endswith("/capture"))
    stamp_fetch_index = next(i for i, e in enumerate(events) if e["type"] == "fetch" and "/stamp/" in e["url"])
    assert not [e for e in events if e["type"] == "permission"]
    assert capture_fetch_index < stamp_fetch_index
    assert result["capture"]["pdf_attach"]["status"] == "ok"


# ── popup_format.js unit tests ──────────────────────────────────────────

POPUP_FORMAT_JS = PROJECT_ROOT / "browser-extension" / "popup_format.js"


def _run_popup_format_module(script: str, tmp_path: Path) -> dict:
    """Import popup_format.js in Node, run script, return JSON stdout."""
    module_path = tmp_path / "popup_format.mjs"
    module_path.write_text(POPUP_FORMAT_JS.read_text())
    runner_path = tmp_path / "runner.mjs"
    runner_path.write_text(script.replace("./popup_format.js", "./popup_format.mjs"))
    result = subprocess.run(
        ["node", str(runner_path)],
        text=True,
        capture_output=True,
    )
    if result.returncode != 0:
        raise AssertionError(
            f"node runner failed with {result.returncode}\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )
    return json.loads(result.stdout)


def test_format_capture_result_ok(tmp_path: Path) -> None:
    result = _run_popup_format_module(
        r'''
const mod = await import("./popup_format.js");
const out = mod.formatCaptureResult({
  status: "ok", citekey: "smith2024paper", bib: "main",
  title: "A Great Paper", dry_run: false,
  pdf_attach: { status: "ok" },
});
console.log(JSON.stringify({ ok: true, output: out }));
''',
        tmp_path,
    )
    assert result["ok"] is True
    assert "smith2024paper" in result["output"]
    assert "main" in result["output"]


def test_format_capture_result_error(tmp_path: Path) -> None:
    result = _run_popup_format_module(
        r'''
const mod = await import("./popup_format.js");
const out = mod.formatCaptureResult({
  status: "error", message: "bad request", errors: ["timeout"],
});
console.log(JSON.stringify({ ok: true, output: out }));
''',
        tmp_path,
    )
    assert "bad request" in result["output"] or "timeout" in result["output"]


def test_format_capture_result_pdf_permission(tmp_path: Path) -> None:
    result = _run_popup_format_module(
        r'''
const mod = await import("./popup_format.js");
const out = mod.formatCaptureResult({
  status: "ok", citekey: "test2024", bib: "main", title: "T", dry_run: false,
  pdf_attach: { status: "permission_denied", message: "need permission" },
});
console.log(JSON.stringify({ ok: true, output: out }));
''',
        tmp_path,
    )
    assert result["ok"] is True


def test_format_capture_result_pdf_html_login(tmp_path: Path) -> None:
    result = _run_popup_format_module(
        r'''
const mod = await import("./popup_format.js");
const out = mod.formatCaptureResult({
  status: "ok", citekey: "test2024", bib: "main", title: "T", dry_run: false,
  pdf_attach: { status: "error", message: "browser PDF fetch failed" },
  pdf_attach_attempts: [
    { url: "https://ieeexplore.ieee.org/stamp/stamp.jsp", mode: "browser_fetch", status: "html_login", http_status: 200, content_type: "text/html", byte_count: 4000, text_snippet: "<html>Sign in</html>" },
  ],
});
console.log(JSON.stringify({ ok: true, output: out }));
''',
        tmp_path,
    )
    assert result["ok"] is True
    assert "login" in result["output"].lower()
    assert "sign in" in result["output"].lower()


def test_format_capture_result_pdf_html_access_denied(tmp_path: Path) -> None:
    result = _run_popup_format_module(
        r'''
const mod = await import("./popup_format.js");
const out = mod.formatCaptureResult({
  status: "ok", citekey: "test2024", bib: "main", title: "T", dry_run: false,
  pdf_attach: { status: "error", message: "browser PDF fetch failed" },
  pdf_attach_attempts: [
    { url: "https://example.com/paper", mode: "browser_fetch", status: "html_access_denied", http_status: 403, content_type: "text/html", byte_count: 500, text_snippet: "<html>Access denied</html>" },
  ],
});
console.log(JSON.stringify({ ok: true, output: out }));
''',
        tmp_path,
    )
    assert result["ok"] is True
    assert "access denied" in result["output"].lower()


def test_format_multi_capture_success(tmp_path: Path) -> None:
    result = _run_popup_format_module(
        r'''
const mod = await import("./popup_format.js");
const out = mod.formatMultiCaptureResult({
  status: "complete", total: 3,
  results: [
    { status: "ok", citekey: "a2024", title: "A" },
    { status: "ok", citekey: "b2024", title: "B" },
    { status: "ok", citekey: "c2024", title: "C" },
  ],
});
console.log(JSON.stringify({ ok: true, output: out }));
''',
        tmp_path,
    )
    assert result["ok"] is True
    assert "3" in result["output"]


def test_format_multi_capture_mixed(tmp_path: Path) -> None:
    result = _run_popup_format_module(
        r'''
const mod = await import("./popup_format.js");
const out = mod.formatMultiCaptureResult({
  status: "complete", total: 3,
  results: [
    { status: "ok", citekey: "a2024", title: "A" },
    { status: "error", message: "fail", item_title: "Broken" },
    { status: "ok", citekey: "c2024", title: "C" },
  ],
});
console.log(JSON.stringify({ ok: true, output: out }));
''',
        tmp_path,
    )
    assert result["ok"] is True
    assert "2" in result["output"] or "Captured" in result["output"]


# ── popup.js unit tests ─────────────────────────────────────────────────

POPUP_JS = PROJECT_ROOT / "browser-extension" / "popup.js"


def _write_ui_module_mocks(tmp_path: Path) -> None:
    """Write the split modules the popup and onboarding page import.

    They no longer import `background.js` — that module registers a
    `contextMenus.onClicked` listener and an always-on `webRequest` observer at
    load, so every extension page importing it added another copy. The mocks
    therefore stand in for `background/config.js`, `background/search.js` and
    `background/capture.js` individually.

    `isLoopbackEndpoint` is re-exported from the *real* config module rather
    than restated: onboarding rejects an endpoint using the same predicate
    `getEndpoint` enforces, so testing it against a copy would only prove the
    copy agreed with itself. `config.js` imports nothing, so it drops in whole.
    """
    destination = tmp_path / "background"
    destination.mkdir(exist_ok=True)
    (destination / "config_real.mjs").write_text(
        _rewrite_local_imports((BACKGROUND_DIR / "config.js").read_text())
    )
    (destination / "config.mjs").write_text(_MOCK_CONFIG_MODULE)
    # `search.js` imports `config.js` and `utils.js`; both are copied in beside
    # it so the real pattern-matching half can be re-exported from the mock.
    # Real modules copied in point at `config_real.mjs`, never the mock: the
    # mock only carries what the *UI* imports, so a real module reaching it
    # would fail on the first constant the UI happens not to use.
    (destination / "search_real.mjs").write_text(
        _rewrite_local_imports((BACKGROUND_DIR / "search.js").read_text())
        .replace('from "./config.mjs"', 'from "./config_real.mjs"')
    )
    (destination / "utils.mjs").write_text(
        _rewrite_local_imports((BACKGROUND_DIR / "utils.js").read_text())
        .replace('from "./config.mjs"', 'from "./config_real.mjs"')
    )
    (destination / "search.mjs").write_text(_MOCK_SEARCH_MODULE)
    (destination / "capture.mjs").write_text(_MOCK_CAPTURE_MODULE)
    (destination / "permissions.mjs").write_text(_MOCK_PERMISSIONS_MODULE)


# Every export keeps the return value it has always had; the `__`-prefixed
# globals are opt-in overrides, so a test that sets none behaves as before.
_MOCK_CONFIG_MODULE = (
    "export async function fetchBibs() { if (globalThis.__fetchBibsError) throw new Error(globalThis.__fetchBibsError); return globalThis.__bibs ?? []; }\n"
    "export async function getEndpoint() { return 'http://127.0.0.1:8765/capture'; }\n"
    'export const EXTENSION_VERSION = "1.2.3-test";\n'
    "export async function getAuthHeaders() { return globalThis.__authHeaders || {}; }\n"
    "export function endpointFor(rawEndpoint, path) { const base = new URL(rawEndpoint); const target = new URL(path, base); target.search = ''; return target.href.replace(/\\/$/, ''); }\n"
    # The real rule, not a restatement of it.
    'export { isLoopbackEndpoint } from "./config_real.mjs";\n'
)

_MOCK_SEARCH_MODULE = (
    "export async function detectAndExtractSearchResults() { return globalThis.__searchResults ?? null; }\n"
    # The real gate, not a stub. A stub that answered `true` would let the popup
    # pass these tests while gating on a pattern list that had drifted again —
    # which is the defect this export exists to close.
    'export { matchesAnySearchPattern, SEARCH_URL_PATTERNS } from "./search_real.mjs";\n'
)

_MOCK_CAPTURE_MODULE = (
    # Writes the same `pzi:captureStage` values the real capture does, one per
    # await, so a test can watch the popup react to them.
    "export async function captureCurrentTab() { for (const stage of (globalThis.__captureStages || [])) { await chrome.storage.session.set({ 'pzi:captureStage': stage }); } if (globalThis.__captureError) throw new Error(globalThis.__captureError); return globalThis.__captureResult ?? { status: 'ok' }; }\n"
)

_MOCK_PERMISSIONS_MODULE = (
    # Present so a mutation that reintroduces cookie forwarding has something to
    # import; `__cookieCalls` is how a test asserts the popup never reached for
    # cookies at all.
    "export async function cookieHeaderForUrl(url) { (globalThis.__cookieCalls ??= []).push(url); return globalThis.__cookieHeader ?? ''; }\n"
)


def _run_onboarding_module(script: str, tmp_path: Path) -> dict:
    """Run a test script that imports onboarding.js, the first-run settings page."""
    (tmp_path / "onboarding_test.mjs").write_text(
        _rewrite_local_imports(ONBOARDING_JS.read_text())
    )
    _write_ui_module_mocks(tmp_path)
    runner_path = tmp_path / "runner.mjs"
    runner_path.write_text(script.replace("./onboarding.js", "./onboarding_test.mjs"))
    result = subprocess.run(
        ["node", str(runner_path)],
        text=True,
        capture_output=True,
    )
    if result.returncode != 0:
        raise AssertionError(
            f"node runner failed with {result.returncode}\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )
    return json.loads(result.stdout)


def _run_popup_js_test(script: str, tmp_path: Path) -> dict:
    """Run a test script that imports popup.js functions."""
    module_path = tmp_path / "popup_test.mjs"
    module_path.write_text(_rewrite_local_imports(POPUP_JS.read_text()))
    # `.mjs`, because `_rewrite_local_imports` now rewrites popup.js's import
    # of it along with everything else.
    (tmp_path / "popup_format.mjs").write_text(_rewrite_local_imports(POPUP_FORMAT_JS.read_text()))
    # popup.js imports from background.js — mock the imports
    _write_ui_module_mocks(tmp_path)
    runner_path = tmp_path / "runner.mjs"
    runner_script = script.replace("./popup.js", "./popup_test.mjs")
    # Also replace background.js references with mock
    runner_script = runner_script.replace("./background.js", "./background.mjs")
    runner_path.write_text(runner_script)
    result = subprocess.run(
        ["node", str(runner_path)],
        text=True,
        capture_output=True,
    )
    if result.returncode != 0:
        raise AssertionError(
            f"node runner failed with {result.returncode}\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )
    return json.loads(result.stdout)


def test_esc_html_basic(tmp_path: Path) -> None:
    result = _run_popup_js_test(
        r'''
// Extract escHtml from popup.js (it's a module-level function)
// Since popup.js does DOM operations, we test escHtml and escAttr
// by redefining them in the test since they're inline in popup.js
function escHtml(s) {
  return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}
function escAttr(s) {
  return String(s).replace(/&/g, "&amp;").replace(/"/g, "&quot;");
}
const r1 = escHtml("<script>alert('xss')</script>");
const r2 = escHtml("Tom & Jerry");
const r3 = escAttr('href="javascript:evil()"');
console.log(JSON.stringify({ r1, r2, r3 }));
''',
        tmp_path,
    )
    assert "&lt;script&gt;" in result["r1"]
    assert "&amp;" in result["r2"]
    assert "&quot;" in result["r3"]
    assert "<" not in result["r1"]


def test_url_matches_search_pattern_scholar(tmp_path: Path) -> None:
    """Drives the real `matchesAnySearchPattern`, not a copy of it.

    This test used to redefine the function inside its own script, so it
    asserted that its copy agreed with itself — which is how the popup's
    divergent copy of the pattern list survived long enough to make four site
    detectors unreachable.
    """
    result = _run_background_module(
        r'''
globalThis.chrome = { runtime: { onInstalled: { addListener: () => {} } } };
const { matchesAnySearchPattern } = await import("./background/search.mjs");
console.log(JSON.stringify({
  scholar: matchesAnySearchPattern("https://scholar.google.com/scholar?q=test"),
  pubmed: matchesAnySearchPattern("https://pubmed.ncbi.nlm.nih.gov/?term=covid"),
  arxiv: matchesAnySearchPattern("https://arxiv.org/search/?query=ml"),
  semanticScholar: matchesAnySearchPattern("https://semanticscholar.org/search?q=transformers"),
  dblp: matchesAnySearchPattern("https://dblp.org/search/publ?q=ai"),
  article: matchesAnySearchPattern("https://example.com/paper"),
}));
''',
        tmp_path,
    )

    assert result["scholar"] is True
    assert result["pubmed"] is True
    assert result["arxiv"] is True
    assert result["semanticScholar"] is True
    assert result["dblp"] is True
    assert result["article"] is False


def test_popup_requests_active_tab_origin_permission(tmp_path: Path) -> None:
    result = _run_popup_js_test(
        r'''
globalThis.events = [];
const element = () => ({
  value: "",
  checked: false,
  disabled: false,
  textContent: "",
  innerHTML: "",
  style: {},
  appendChild: () => {},
  addEventListener: () => {},
  querySelectorAll: () => [],
});
globalThis.document = {
  getElementById: () => element(),
  createElement: () => element(),
};
globalThis.chrome = {
  storage: {
    local: { get: async () => ({}), set: async () => ({}), remove: async () => ({}) },
    session: { get: async () => ({}), set: async () => ({}), remove: async () => ({}) },
  },
  tabs: { query: async () => [{ id: 7, url: "https://ieeexplore.ieee.org/document/9840963" }] },
  runtime: { sendMessage: () => {} },
  permissions: {
    contains: async (request) => { globalThis.events.push({ type: "contains", request }); return false; },
    request: async (request) => { globalThis.events.push({ type: "permission", request }); return true; },
  },
};
globalThis.window = { open: () => {} };
const mod = await import("./popup.js");
const permission = await mod.requestActiveTabOriginPermission("https://ieeexplore.ieee.org/document/9840963");
console.log(JSON.stringify({ permission, events: globalThis.events }));
''',
        tmp_path,
    )

    assert result["permission"] == {"status": "granted", "origin": "https://ieeexplore.ieee.org"}
    assert result["events"] == [
        {"type": "contains", "request": {"origins": ["https://ieeexplore.ieee.org/*"]}},
        {"type": "permission", "request": {"origins": ["https://ieeexplore.ieee.org/*"]}},
    ]


# Drives a real single capture through the popup's "go" button, with the origin
# permission not already held. `_CAPTURE_DOM` leaves `__captureError` unset, so
# the capture succeeds unless a test sets it.
_CAPTURE_DOM = r'''
const elements = new Map();
const makeElement = (id) => ({
  id, value: "", checked: false, disabled: false, textContent: "", innerHTML: "",
  className: "", type: "", style: { cssText: "" }, children: [], handlers: {},
  appendChild(child) { this.children.push(child); },
  addEventListener(event, handler) { (this.handlers[event] ??= []).push(handler); },
  querySelectorAll: () => [],
});
globalThis.document = {
  getElementById: (id) => {
    if (!elements.has(id)) elements.set(id, makeElement(id));
    return elements.get(id);
  },
  createElement: () => makeElement("created"),
};
globalThis.window = { open: () => {} };
globalThis.fetch = async () => ({ ok: true, json: async () => ({}) });
globalThis.events = [];
globalThis.chrome = {
  storage: {
    local: { get: async () => ({}), set: async () => ({}), remove: async () => ({}) },
    session: { get: async () => ({}), set: async () => ({}), remove: async () => ({}) },
  },
  tabs: { query: async () => [{ id: 7, url: "https://ieeexplore.ieee.org/document/9840963" }] },
  runtime: { sendMessage: () => {} },
  permissions: {
    contains: async () => Boolean(globalThis.__alreadyGranted),
    request: async (request) => {
      globalThis.events.push({ type: "request", request });
      return true;
    },
    remove: async (request) => {
      globalThis.events.push({ type: "remove", request });
      return true;
    },
  },
};
const runCapture = async () => {
  await import("./popup.js");
  for (let i = 0; i < 20; i += 1) await new Promise((r) => setTimeout(r, 0));
  const go = elements.get("go").handlers.click[0];
  await go();
  for (let i = 0; i < 20; i += 1) await new Promise((r) => setTimeout(r, 0));
};
'''


def test_a_capture_gives_back_the_origin_permission_it_borrowed(tmp_path: Path) -> None:
    """The grant was requested for one capture and never handed back.

    `doSingleCapture` discarded the return value of
    `requestActiveTabOriginPermission` outright, so the permission was not even
    reachable to release. The extension therefore accumulated a permanent host
    permission for every site ever captured from — which also widens what the
    always-on `webRequest` observer can see.
    """
    result = _run_popup_js_test(
        _CAPTURE_DOM
        + r'''
await runCapture();
console.log(JSON.stringify({ events: globalThis.events }));
''',
        tmp_path,
    )

    pattern = {"origins": ["https://ieeexplore.ieee.org/*"]}
    assert {"type": "request", "request": pattern} in result["events"], result["events"]
    assert {"type": "remove", "request": pattern} in result["events"], result["events"]
    # Borrowed, then returned — in that order.
    kinds = [event["type"] for event in result["events"]]
    assert kinds.index("request") < kinds.index("remove")


def test_a_permission_the_user_already_granted_is_not_taken_away(tmp_path: Path) -> None:
    """Releasing must not revoke a grant the user made deliberately.

    `removeTemporaryOriginPermission` draws this line with `already_granted`;
    the popup has to draw it in the same place, or capturing once from a site
    the user had permanently allowed would silently downgrade it.
    """
    result = _run_popup_js_test(
        _CAPTURE_DOM
        + r'''
globalThis.__alreadyGranted = true;
await runCapture();
console.log(JSON.stringify({ events: globalThis.events }));
''',
        tmp_path,
    )

    assert not any(event["type"] == "remove" for event in result["events"]), result["events"]
    # And nothing was requested either, since it was already held.
    assert not any(event["type"] == "request" for event in result["events"]), result["events"]


def test_a_capture_that_throws_still_gives_the_permission_back(tmp_path: Path) -> None:
    """The release has to sit in a `finally`.

    `maybeStreamPdfBytes` shipped this exact bug — the release placed after the
    call it protects, so a throw left the user holding a host permission
    indefinitely. Repeating it here would be unforced.
    """
    result = _run_popup_js_test(
        _CAPTURE_DOM
        + r'''
globalThis.__captureError = "capture blew up";
await runCapture();
console.log(JSON.stringify({ events: globalThis.events }));
''',
        tmp_path,
    )

    pattern = {"origins": ["https://ieeexplore.ieee.org/*"]}
    assert {"type": "remove", "request": pattern} in result["events"], result["events"]


def test_a_dry_run_borrows_no_permission_at_all(tmp_path: Path) -> None:
    """Nothing is fetched on a preview, so nothing needs granting."""
    result = _run_popup_js_test(
        _CAPTURE_DOM
        + r'''
const dry = document.getElementById("dry");
dry.checked = true;
await runCapture();
console.log(JSON.stringify({ events: globalThis.events }));
''',
        tmp_path,
    )

    assert result["events"] == [], result["events"]


# A popup DOM whose summary records every value written to it, over a storage
# stub that raises change events the way a browser does.
_PROGRESS_DOM = r'''
const elements = new Map();
globalThis.summaryWrites = [];
const makeElement = (id) => {
  const el = {
    id, value: "", checked: false, disabled: false, innerHTML: "",
    className: "", type: "", style: { cssText: "" }, children: [], handlers: {},
    appendChild(child) { this.children.push(child); },
    addEventListener(event, handler) { (this.handlers[event] ??= []).push(handler); },
    querySelectorAll: () => [],
  };
  let text = "";
  Object.defineProperty(el, "textContent", {
    get: () => text,
    set: (value) => {
      text = value;
      if (id === "summary") globalThis.summaryWrites.push(value);
    },
  });
  return el;
};
globalThis.document = {
  getElementById: (id) => {
    if (!elements.has(id)) elements.set(id, makeElement(id));
    return elements.get(id);
  },
  createElement: () => makeElement("created"),
};
globalThis.window = { open: () => {} };
globalThis.fetch = async () => ({ ok: true, json: async () => ({}) });

const storageListeners = [];
const sessionBacking = {};
const notify = (changes) => { for (const fn of [...storageListeners]) fn(changes, "session"); };
const session = {
  get: async () => ({ ...sessionBacking }),
  set: async (values) => {
    const changes = {};
    for (const [key, value] of Object.entries(values)) {
      changes[key] = { oldValue: sessionBacking[key], newValue: value };
      sessionBacking[key] = value;
    }
    notify(changes);
    return {};
  },
  remove: async (keys) => {
    const changes = {};
    for (const key of [].concat(keys)) {
      changes[key] = { oldValue: sessionBacking[key], newValue: undefined };
      delete sessionBacking[key];
    }
    notify(changes);
    return {};
  },
};
globalThis.chrome = {
  storage: {
    local: { get: async () => ({}), set: async () => ({}), remove: async () => ({}) },
    session,
    onChanged: {
      addListener: (fn) => storageListeners.push(fn),
      removeListener: (fn) => {
        const i = storageListeners.indexOf(fn);
        if (i >= 0) storageListeners.splice(i, 1);
      },
    },
  },
  tabs: { query: async () => [{ id: 7, url: "https://paper.test/article" }] },
  runtime: { sendMessage: () => {} },
  permissions: { contains: async () => true, request: async () => true, remove: async () => true },
};
const runCapture = async () => {
  await import("./popup.js");
  for (let i = 0; i < 20; i += 1) await new Promise((r) => setTimeout(r, 0));
  await elements.get("go").handlers.click[0]();
  for (let i = 0; i < 20; i += 1) await new Promise((r) => setTimeout(r, 0));
};
'''


def test_the_popup_shows_what_the_capture_is_doing(tmp_path: Path) -> None:
    """The four stage writes existed and nothing read them.

    A capture can spend 30 s on one fetch and 15-20 s opening a bot-bypass tab,
    all behind a single static "Capturing…". The stages were already written at
    the right points; the only reader was a poller nothing called.
    """
    result = _run_popup_js_test(
        _PROGRESS_DOM
        + r'''
globalThis.__captureStages = ["extracting", "fetching", "processing", "downloading"];
globalThis.__captureResult = { status: "ok", citekey: "smith2024paper", bib: "main", title: "A Paper" };
await runCapture();
console.log(JSON.stringify({
  writes: globalThis.summaryWrites,
  leftoverStage: sessionBacking["pzi:captureStage"] ?? null,
  listenersLeft: storageListeners.length,
}));
''',
        tmp_path,
    )

    writes = result["writes"]
    assert "Scanning page for metadata…" in writes, writes
    assert "Fetching paper details…" in writes, writes
    assert "Processing metadata…" in writes, writes
    assert "Downloading PDF…" in writes, writes
    # In the order the pipeline runs them.
    order = [w for w in writes if w.endswith("…") and w != "Capturing…"]
    assert order == [
        "Scanning page for metadata…",
        "Fetching paper details…",
        "Processing metadata…",
        "Downloading PDF…",
    ], order

    # The outcome is what the user is left looking at, not a stage.
    assert "smith2024paper" in writes[-1], writes[-1]

    # And the popup tidies up after itself.
    assert result["leftoverStage"] is None
    assert result["listenersLeft"] == 0


def test_a_late_stage_write_cannot_overwrite_the_result(tmp_path: Path) -> None:
    """Unsubscribing has to happen before anything else in the `finally`.

    A stage write landing after the capture resolved would replace the outcome
    the user needs with "Downloading PDF…", which is both wrong and permanent —
    nothing writes the summary again.
    """
    result = _run_popup_js_test(
        _PROGRESS_DOM
        + r'''
globalThis.__captureStages = ["extracting"];
globalThis.__captureResult = { status: "ok", citekey: "smith2024paper", bib: "main", title: "A Paper" };
await runCapture();
// The background service worker outliving the capture, writing one more stage.
await chrome.storage.session.set({ "pzi:captureStage": "downloading" });
for (let i = 0; i < 10; i += 1) await new Promise((r) => setTimeout(r, 0));
console.log(JSON.stringify({ writes: globalThis.summaryWrites }));
''',
        tmp_path,
    )

    assert "smith2024paper" in result["writes"][-1], result["writes"][-1]


def test_popup_stamps_direct_capture_result(tmp_path: Path) -> None:
    result = _run_popup_js_test(
        r'''
const element = () => ({
  value: "", checked: false, disabled: false, textContent: "", innerHTML: "",
  style: {}, appendChild: () => {}, addEventListener: () => {}, querySelectorAll: () => [],
});
globalThis.document = { getElementById: () => element(), createElement: () => element() };
globalThis.chrome = {
  storage: {
    local: { get: async () => ({}), set: async () => ({}), remove: async () => ({}) },
    session: { get: async () => ({}), set: async () => ({}), remove: async () => ({}) },
  },
  tabs: { query: async () => [] },
  runtime: { sendMessage: () => {} },
};
globalThis.window = { open: () => {} };
const mod = await import("./popup.js");
const stamped = mod.stampPopupResult({ status: "error", errors: ["translation server returned no results"] });
console.log(JSON.stringify(stamped));
''',
        tmp_path,
    )

    assert result["status"] == "error"
    # The version the manifest carries, not a literal compiled into two files.
    assert result["popup_build_marker"] == "1.2.3-test"


def test_popup_open_pdf_fetches_with_auth_token_and_opens_blob(tmp_path: Path) -> None:
    result = _run_popup_js_test(
        r'''
const element = () => ({
  value: "", checked: false, disabled: false, textContent: "", innerHTML: "",
  style: {}, appendChild: () => {}, addEventListener: () => {}, querySelectorAll: () => [],
});
globalThis.document = { getElementById: () => element(), createElement: () => element() };
globalThis.chrome = {
  storage: {
    local: { get: async () => ({}), set: async () => ({}), remove: async () => ({}) },
    session: { get: async () => ({}), set: async () => ({}), remove: async () => ({}) },
  },
  tabs: { query: async () => [] },
  runtime: { sendMessage: () => {} },
};
globalThis.__authHeaders = { "X-Pzi-Token": "tok" };
globalThis.events = [];
globalThis.fetch = async (url, options = {}) => {
  globalThis.events.push({ type: "fetch", url, headers: options.headers || {} });
  return { ok: true, blob: async () => new Blob(["%PDF-1.4"], { type: "application/pdf" }) };
};
const NativeURL = URL;
globalThis.URL = class extends NativeURL {
  static createObjectURL(blob) { globalThis.events.push({ type: "createObjectURL", blob_type: blob.type }); return "blob:pzi-pdf"; }
  static revokeObjectURL(url) { globalThis.events.push({ type: "revokeObjectURL", url }); }
};
globalThis.window = { open: (url, target) => { globalThis.events.push({ type: "open", url, target }); } };
const mod = await import("./popup.js");
await mod.openPdf("smith2024paper", "main");
console.log(JSON.stringify({ events: globalThis.events }));
''',
        tmp_path,
    )

    fetch_event = next(e for e in result["events"] if e["type"] == "fetch")
    assert fetch_event["url"] == "http://127.0.0.1:8765/pdf/smith2024paper"
    assert fetch_event["headers"] == {"X-Pzi-Token": "tok"}
    assert {"type": "open", "url": "blob:pzi-pdf", "target": "_blank"} in result["events"]


def test_discover_from_page_function_exists_in_module(tmp_path: Path) -> None:
    result = _run_background_module(
        r'''
globalThis.chrome = {
  runtime: { onInstalled: { addListener: () => {} } },
  webRequest: {
    onHeadersReceived: {
      addListener: () => {},
      removeListener: () => {},
    },
  },
  storage: {
    local: {
      get: async () => ({ endpoint: "http://127.0.0.1:8765/capture", authToken: "" }),
    },
  },
  tabs: {
    create: async () => ({ id: 42 }),
    remove: async () => {},
    onUpdated: { addListener: () => {}, removeListener: () => {} },
  },
  scripting: { executeScript: async () => [] },
};
globalThis.fetch = async () => ({ ok: true, json: async () => ({}), arrayBuffer: async () => new ArrayBuffer(4), headers: new Map() });
globalThis.Headers = Map;
const mod = await import("./background.js");
const exports = Object.keys(mod);
console.log(JSON.stringify({
  module_loaded: true,
  export_count: exports.length,
  has_isBotBypassWhitelisted: exports.includes("isBotBypassWhitelisted"),
  has_collectObservedPdfUrls: exports.includes("collectObservedPdfUrls"),
  has_collectPdfObserverEvents: exports.includes("collectPdfObserverEvents"),
  has_startPdfObserver: exports.includes("startPdfObserver"),
}));
''',
        tmp_path,
    )

    assert result["module_loaded"] is True
    assert result["has_isBotBypassWhitelisted"] is True
    assert result["has_startPdfObserver"] is True


def test_permission_denied_does_not_crash_module_load(tmp_path: Path) -> None:
    """Regression: denied permission + same-origin candidates must not skip all acquisition."""
    result = _run_background_module(
        r'''
let permissionRequestCount = 0;
globalThis.chrome = {
  runtime: { onInstalled: { addListener: () => {} }, onStartup: { addListener: () => {} } },
  webRequest: {
    onHeadersReceived: {
      addListener: () => {},
      removeListener: () => {},
    },
  },
  permissions: {
    contains: async () => false,
    request: async () => { permissionRequestCount++; return false; },
  },
  storage: {
    local: {
      get: async () => ({ endpoint: "http://127.0.0.1:8765/capture", authToken: "" }),
      set: async () => {},
    },
    session: { get: async () => ({}), set: async () => ({}) },
  },
  tabs: {
    create: async () => ({ id: 42 }),
    remove: async () => {},
    onUpdated: { addListener: () => {}, removeListener: () => {} },
    query: async () => [{ id: 1, url: "https://dl.acm.org/doi/10.1145/3442188.3445922" }],
  },
  scripting: { executeScript: async () => [] },
  action: { setBadgeText: async () => {} },
  contextMenus: { create: () => {}, onClicked: { addListener: () => {} } },
  cookies: { getAll: async () => [] },
};
globalThis.fetch = async (url) => {
  if (url && url.includes("/capture")) {
    return {
      ok: true,
      json: async () => ({
        status: "ok", action: "insert", citekey: "test2024", bib: "main",
        pdf_path: null,
        pdf_request: {
          request_id: "rid-1", citekey: "test2024", bib: "main",
          attach: { url: "http://127.0.0.1:8765/attach-pdf-raw?request_id=rid-1&citekey=test2024", token: "tok" },
          candidates: [
            { url: "https://dl.acm.org/doi/pdf/10.1145/3442188.3445922", kind: "pdf_gateway", method: "navigate_monitor", referrer: "https://dl.acm.org/doi/10.1145/3442188.3445922", requires_navigation: true, timeout_ms: 20000 },
            { url: "https://dl.acm.org/doi/10.1145/3442188.3445922", kind: "article_page", method: "discover_from_page", referrer: "https://dl.acm.org/doi/10.1145/3442188.3445922", requires_navigation: false, timeout_ms: 10000 },
          ],
        },
      }),
    };
  }
  // Generic fetch for PDF candidates — should be attempted even when permission denied.
  return { ok: true, arrayBuffer: async () => new ArrayBuffer(4), headers: new Map([["content-type", "application/pdf"]]) };
};
globalThis.Headers = Map;
const mod = await import("./background.js");
console.log(JSON.stringify({
  module_loaded: true,
  permission_request_count: permissionRequestCount,
  export_count: Object.keys(mod).length,
}));
''',
        tmp_path,
    )

    assert result["module_loaded"] is True


def test_dom_scan_runs_in_the_page_world_without_module_scope(tmp_path: Path) -> None:
    """An injected function may not reference the service worker's module scope.

    chrome.scripting.executeScript serialises the function and runs it in the
    page, where a module-level helper does not exist — so `func: () =>
    scanDomForPdfUrls(document)` threw ReferenceError inside the page, the
    rejection was swallowed by the surrounding catch, and DOM-based PDF discovery
    silently never contributed a candidate.

    This stub rebuilds the function from its source via `new Function`, which is
    what makes module scope unreachable, exactly as the browser does.
    """
    result = _run_background_module(
        r'''
globalThis.chrome = {
  storage: {
    local: { get: async () => ({}) },
    session: { get: async () => ({}), set: () => {} },
  },
  runtime: { onInstalled: { addListener: () => {} } },
  tabs: { query: async () => [] },
  scripting: {
    executeScript: async (opts) => {
      globalThis.document = {
        baseURI: "https://journal.example.com/article/1",
        querySelectorAll: (selector) => {
          if (selector.includes("citation_pdf_url")) {
            return [{ getAttribute: () => "https://journal.example.com/article/1.pdf" }];
          }
          return [];
        },
      };
      const rebuilt = new Function("return (" + opts.func.toString() + ")")();
      return [{ result: rebuilt(...(opts.args || [])) }];
    },
  },
};

const { extractPdfUrlCandidates } = await import("./background.mjs");
const candidates = await extractPdfUrlCandidates(11, "https://journal.example.com/article/1");
console.log(JSON.stringify({ candidates }));
''',
        tmp_path,
    )
    assert "https://journal.example.com/article/1.pdf" in result["candidates"]


def test_bot_bypass_allowlist_matches_on_domain_boundaries(tmp_path: Path) -> None:
    """A look-alike domain must not clear the bot-bypass allowlist.

    The check was `hostname.endsWith(domain)`, which has no domain boundary:
    `evil-nature.com` ends with `nature.com`, so an attacker-chosen host could
    reach the hidden-iframe bypass machinery. Only the domain itself and its
    subdomains should match.
    """
    result = _run_background_module(
        r'''
globalThis.chrome = {
  storage: { local: { get: async () => ({}) }, session: { get: async () => ({}), set: () => {} } },
  runtime: { onInstalled: { addListener: () => {} } },
  tabs: { query: async () => [] },
  scripting: { executeScript: async () => [{ result: null }] },
};
const { isBotBypassWhitelisted } = await import("./background.mjs");
console.log(JSON.stringify({
  exact: isBotBypassWhitelisted("https://nature.com/articles/1.pdf"),
  subdomain: isBotBypassWhitelisted("https://www.nature.com/articles/1.pdf"),
  lookalike_prefix: isBotBypassWhitelisted("https://evil-nature.com/x.pdf"),
  lookalike_concat: isBotBypassWhitelisted("https://notsagepub.com/x.pdf"),
  unrelated: isBotBypassWhitelisted("https://example.com/x.pdf"),
}));
''',
        tmp_path,
    )
    assert result["exact"] is True
    assert result["subdomain"] is True
    assert result["lookalike_prefix"] is False
    assert result["lookalike_concat"] is False
    assert result["unrelated"] is False


def test_pdf_candidates_are_capped_to_what_the_server_accepts(tmp_path: Path) -> None:
    """A link-heavy page must not make the whole capture fail.

    The server rejects a capture outright when `pdf_url_candidates` exceeds
    MAX_PDF_URL_CANDIDATES (20), so an uncapped client list turns a page with
    many PDF-ish links into a failed capture rather than a successful one that
    tried the best candidates.
    """
    result = _run_background_module(
        r'''
globalThis.chrome = {
  storage: { local: { get: async () => ({}) }, session: { get: async () => ({}), set: () => {} } },
  runtime: { onInstalled: { addListener: () => {} } },
  tabs: { query: async () => [] },
  scripting: {
    executeScript: async (opts) => {
      globalThis.document = {
        baseURI: "https://journal.example.com/list",
        querySelectorAll: (selector) => {
          if (selector.includes("a[href]")) {
            return Array.from({ length: 60 }, (_v, i) => ({
              href: "https://journal.example.com/paper-" + i + ".pdf",
              getAttribute: () => null,
              textContent: "PDF",
            }));
          }
          return [];
        },
      };
      const rebuilt = new Function("return (" + opts.func.toString() + ")")();
      return [{ result: rebuilt(...(opts.args || [])) }];
    },
  },
};
const { extractPdfUrlCandidates, MAX_PDF_URL_CANDIDATES } = await import("./background.mjs");
const candidates = await extractPdfUrlCandidates(11, "https://journal.example.com/list");
console.log(JSON.stringify({ count: candidates.length, cap: MAX_PDF_URL_CANDIDATES }));
''',
        tmp_path,
    )
    assert result["cap"] == 20, "client cap must match the server's limit"
    assert result["count"] == 20


# ---------------------------------------------------------------------------
# The injected iframe function only sees what `args` hands it
# ---------------------------------------------------------------------------


def test_the_hidden_iframe_receives_its_timeout_as_an_argument(tmp_path: Path) -> None:
    """`func` is serialized into the *page*, where a background-module const
    does not exist — so `BOT_BYPASS_IFRAME_TIMEOUT_MS` was a ReferenceError,
    the injected promise rejected, and the hidden-iframe bypass never ran at
    all. It silently fell through to opening a visible tab every time."""
    result = _run_background_module(
        r'''
let captured = null;
globalThis.chrome = {
  runtime: { onInstalled: { addListener: () => {} } },
  webRequest: {
    onHeadersReceived: { addListener: () => {}, removeListener: () => {} },
  },
  scripting: {
    executeScript: async (options) => {
      captured = options;
      // Run the injected function exactly as the page would: with only the
      // arguments it was given, and with no access to module scope.
      const isolated = new Function("return " + options.func.toString())();
      globalThis.document = {
        createElement: () => ({ style: {} }),
        body: { appendChild: () => {} },
      };
      await isolated(...options.args);
      return [{ result: null }];
    },
  },
  tabs: { create: async () => ({ id: 99 }), remove: async () => {} },
};
const mod = await import("./background.js");
let error = null;
try {
  await mod.botBypassPdfUrl(7, "https://ieeexplore.ieee.org/stamp/stamp.jsp?a=1",
                            { visibleTimeoutMs: 5 });
} catch (e) {
  error = String(e);
}
console.log(JSON.stringify({ argCount: captured?.args?.length ?? 0, error }));
''',
        tmp_path,
    )

    assert result["error"] is None
    # The URL *and* the timeout, not just the URL.
    assert result["argCount"] == 2


# ---------------------------------------------------------------------------
# Failures in the extension's own helpers
# ---------------------------------------------------------------------------


def test_a_body_that_is_not_json_resolves_to_null_instead_of_rejecting(
    tmp_path: Path,
) -> None:
    """`response.json()` returns a *promise* that rejects; the `try` around the
    call never sees it. Every caller doing `await jsonOrNull(r)` therefore threw
    on an empty or truncated body instead of getting the `null` it handles."""
    result = _run_background_module(
        r'''
globalThis.chrome = {
  runtime: { onInstalled: { addListener: () => {} } },
  webRequest: { onHeadersReceived: { addListener: () => {}, removeListener: () => {} } },
};
const { jsonOrNull } = await import("./background/utils.mjs");
const rejecting = { json: () => Promise.reject(new SyntaxError("Unexpected end of JSON input")) };
const throwing = { json: () => { throw new TypeError("no body"); } };
let fromRejecting = "threw";
let fromThrowing = "threw";
try { fromRejecting = await jsonOrNull(rejecting); } catch (_e) {}
try { fromThrowing = await jsonOrNull(throwing); } catch (_e) {}
console.log(JSON.stringify({ fromRejecting, fromThrowing }));
''',
        tmp_path,
    )

    assert result["fromRejecting"] is None
    assert result["fromThrowing"] is None


def test_a_cookie_backend_without_partition_support_still_returns_a_header(
    tmp_path: Path,
) -> None:
    """The fallback `getAll` sat outside any `try`, so a backend that rejects
    both shapes threw straight out of the capture path."""
    result = _run_background_module(
        r'''
globalThis.chrome = {
  runtime: { onInstalled: { addListener: () => {} } },
  webRequest: { onHeadersReceived: { addListener: () => {}, removeListener: () => {} } },
  cookies: {
    getAll: async (query) => {
      if ("partitionKey" in query) throw new TypeError("partitionKey unsupported");
      throw new Error("cookies unavailable");
    },
  },
};
const { cookieHeaderForUrl } = await import("./background/permissions.mjs");
let header = "threw";
try { header = await cookieHeaderForUrl("https://example.com/a"); } catch (_e) {}
console.log(JSON.stringify({ header }));
''',
        tmp_path,
    )

    assert result["header"] == ""


def test_bulk_capture_shows_the_reason_the_server_gave(tmp_path: Path) -> None:
    """The server reports a route failure under `error`, not `message`.

    Every such failure rendered as the literal string "failed", which is exactly
    the information the operator needed and did not get. `responseErrors` in the
    background already reads both keys; this display did not.
    """
    module = tmp_path / "popup_format.mjs"
    module.write_text(
        _rewrite_local_imports(
            (PROJECT_ROOT / "browser-extension" / "popup_format.js").read_text()
        )
    )
    runner = tmp_path / "runner.mjs"
    runner.write_text(
        r'''
import { formatMultiCaptureResult } from "./popup_format.mjs";
const out = formatMultiCaptureResult({
  status: "complete",
  total: 3,
  results: [
    { status: "ok", citekey: "a2024" },
    { status: "error", error: "invalid API token" },
    { status: "error", errors: ["metadata provider unreachable"] },
  ],
});
console.log(JSON.stringify({ out }));
'''
    )
    result = subprocess.run(
        ["node", str(runner)], text=True, capture_output=True, cwd=str(tmp_path)
    )
    assert result.returncode == 0, result.stderr
    text = json.loads(result.stdout)["out"]

    assert "invalid API token" in text
    assert "metadata provider unreachable" in text
    assert "❌ failed" not in text


def test_bulk_capture_does_not_forward_cookies_for_other_domains(tmp_path: Path) -> None:
    """Each search result is a *different* site the user is not on.

    Reading their cookies and forwarding them to the server — which forwards
    them to the publisher — is far beyond "the active tab's session", which is
    what the comment directly above the call already claimed was happening.

    Driven rather than grepped: the real search flow is run to the point of
    clicking "capture all", and the assertion is over the POST bodies that
    leave and over whether the cookie helper was called at all. The assertion
    this replaced named one spelling of one argument.
    """
    result = _run_popup_js_test(
        r'''
// An id-keyed DOM: `document.getElementById` must return the *same* object each
// time, so the click handler popup.js registers on "capture-all" is reachable.
const elements = new Map();
const makeElement = (id) => ({
  id,
  value: "",
  checked: false,
  disabled: false,
  textContent: "",
  innerHTML: "",
  className: "",
  type: "",
  style: { cssText: "" },
  children: [],
  handlers: {},
  appendChild(child) { this.children.push(child); },
  addEventListener(event, handler) { (this.handlers[event] ??= []).push(handler); },
  querySelectorAll: () => [],
});
globalThis.document = {
  getElementById: (id) => {
    if (!elements.has(id)) elements.set(id, makeElement(id));
    return elements.get(id);
  },
  createElement: () => makeElement("created"),
};
globalThis.window = { open: () => {} };
globalThis.chrome = {
  storage: {
    local: { get: async () => ({}), set: async () => ({}), remove: async () => ({}) },
    session: { get: async () => ({}), set: async () => ({}), remove: async () => ({}) },
  },
  tabs: { query: async () => [{ id: 7, url: "https://scholar.google.com/scholar?q=bayesian" }] },
  runtime: { sendMessage: () => {} },
};
// Three results, each on a different publisher the user is not logged into and
// is not currently visiting.
globalThis.__searchResults = {
  detected: true,
  patternName: "Google Scholar",
  items: [
    { index: 0, url: "https://link.springer.com/article/10.1007/s00001", title: "First" },
    { index: 1, url: "https://www.nature.com/articles/s41586-024-00002", title: "Second" },
    { index: 2, url: "https://dl.acm.org/doi/10.1145/3000003", title: "Third" },
  ],
};
// If anything does reach for cookies, hand it something recognisable.
globalThis.__cookieHeader = "session=PZI-PLANTED-COOKIE";
globalThis.posts = [];
globalThis.fetch = async (url, options = {}) => {
  globalThis.posts.push({
    url,
    method: options.method || "GET",
    body: options.body ? JSON.parse(options.body) : null,
  });
  return { ok: true, json: async () => ({ status: "ok", citekey: "k", title: "t", bib: "main" }) };
};

const mod = await import("./popup.js");
// `initSearchDetection()` runs at module load and is not awaited by popup.js.
for (let i = 0; i < 20; i += 1) await new Promise((r) => setTimeout(r, 0));

const captureAll = elements.get("capture-all");
const clicks = (captureAll.handlers.click || []);
if (clicks.length !== 1) throw new Error("expected one capture-all click handler, got " + clicks.length);
await clicks[0]();
for (let i = 0; i < 20; i += 1) await new Promise((r) => setTimeout(r, 0));

console.log(JSON.stringify({
  posts: globalThis.posts,
  cookieCalls: globalThis.__cookieCalls || [],
}));
''',
        tmp_path,
    )

    # The flow really ran: one POST per search result.
    captures = [p for p in result["posts"] if p["method"] == "POST"]
    assert [p["body"]["url"] for p in captures] == [
        "https://link.springer.com/article/10.1007/s00001",
        "https://www.nature.com/articles/s41586-024-00002",
        "https://dl.acm.org/doi/10.1145/3000003",
    ], result["posts"]

    # Nothing asked for cookies, and nothing sent any.
    assert result["cookieCalls"] == [], result["cookieCalls"]
    for post in captures:
        assert "cookies" not in post["body"], post["body"]
    assert "PZI-PLANTED-COOKIE" not in json.dumps(result["posts"])


def test_the_popup_stores_the_api_token_where_it_survives_a_restart(tmp_path: Path) -> None:
    """`storage.session` is cleared when the browser closes, and it *shadows*
    `storage.local` in the merge the background does — so the first capture of
    a new session wrote an empty token over the one onboarding had saved, and
    every request 401'd until it was retyped.

    Only the token moves: capture progress and the recent list are per-session
    state the background writes to `storage.session`, and the popup has to keep
    reading them from there.

    Driven rather than grepped: which storage area a value lands in is a
    runtime fact, and the assertions this replaced pinned three exact source
    lines, so any reformatting broke them without anything changing.
    """
    result = _run_popup_js_test(
        r'''
const elements = new Map();
const makeElement = (id) => ({
  id, value: "", checked: false, disabled: false, textContent: "", innerHTML: "",
  className: "", type: "", style: { cssText: "" }, children: [], handlers: {},
  appendChild(child) { this.children.push(child); },
  addEventListener(event, handler) { (this.handlers[event] ??= []).push(handler); },
  querySelectorAll: () => [],
});
globalThis.document = {
  getElementById: (id) => {
    if (!elements.has(id)) elements.set(id, makeElement(id));
    return elements.get(id);
  },
  createElement: () => makeElement("created"),
};
globalThis.window = { open: () => {} };

globalThis.calls = [];
const area = (name, backing) => ({
  get: async (keys) => {
    globalThis.calls.push({ area: name, op: "get", keys });
    return backing;
  },
  set: async (values) => {
    globalThis.calls.push({ area: name, op: "set", values });
    Object.assign(backing, values);
    return {};
  },
  remove: async () => ({}),
});
// Onboarding already saved a token, to `local`, where a restart cannot clear it.
const localBacking = { authToken: "saved-by-onboarding" };
const sessionBacking = {};
globalThis.chrome = {
  storage: { local: area("local", localBacking), session: area("session", sessionBacking) },
  tabs: { query: async () => [] },
  runtime: { sendMessage: () => {} },
};
globalThis.fetch = async () => ({ ok: true, json: async () => ({ status: "ok" }) });

const mod = await import("./popup.js");
for (let i = 0; i < 20; i += 1) await new Promise((r) => setTimeout(r, 0));

const tokenInput = elements.get("token");
const loadedIntoBox = tokenInput.value;

// A capture with the box left as loaded: the token is stored where it will
// still be there next time the browser starts.
const captureAll = elements.get("capture-all").handlers.click[0];
await captureAll();
for (let i = 0; i < 10; i += 1) await new Promise((r) => setTimeout(r, 0));
const afterTypedCapture = globalThis.calls.slice();

// And a capture with an empty box, which means "I did not type one".
tokenInput.value = "   ";
globalThis.calls = [];
await captureAll();
for (let i = 0; i < 10; i += 1) await new Promise((r) => setTimeout(r, 0));

console.log(JSON.stringify({
  loadedIntoBox,
  afterTypedCapture,
  afterEmptyCapture: globalThis.calls,
  localBacking,
  sessionBacking,
}));
''',
        tmp_path,
    )

    # Read back from the area a restart does not clear.
    assert result["loadedIntoBox"] == "saved-by-onboarding"

    # Written there too, and never to the session area.
    writes = [c for c in result["afterTypedCapture"] if c["op"] == "set"]
    token_writes = [w for w in writes if "authToken" in w["values"]]
    assert token_writes, result["afterTypedCapture"]
    assert all(w["area"] == "local" for w in token_writes), token_writes
    assert result["localBacking"]["authToken"] == "saved-by-onboarding"
    assert "authToken" not in result["sessionBacking"]

    # An empty box is not an instruction to clear the saved token.
    empty_writes = [
        c
        for c in result["afterEmptyCapture"]
        if c["op"] == "set" and "authToken" in c["values"]
    ]
    assert empty_writes == [], empty_writes
    assert result["localBacking"]["authToken"] == "saved-by-onboarding"

    # Nothing in this flow touches the session area any more, and that is the
    # point: the token is configuration and `pzi:recent` is history, so both
    # are durable. The one genuinely session-scoped key, `pzi:captureStage`, is
    # covered by `test_the_popup_shows_what_the_capture_is_doing`.
    assert [c for c in result["afterTypedCapture"] if c["area"] == "session"] == []


def test_every_borrowed_candidate_origin_is_released_not_just_the_winner(
    tmp_path: Path,
) -> None:
    """`requestPdfOriginPermissions` grants upfront; the loop releases per group.

    `background.js:119` asks for every candidate origin before acquisition
    starts, but `maybeStreamPdfBytes` releases only the group it processed and
    returns as soon as one succeeds — so every origin *after* the winning one
    kept a permanent host permission. Same family as the popup's active-tab
    grant, and found by checking whether that one was really the only site.
    """
    result = _run_background_module(
        r'''
const pdfBytes = new Uint8Array([37, 80, 68, 70, 45, 49]).buffer;  // "%PDF-1"
globalThis.events = [];
globalThis.chrome = {
  runtime: { onInstalled: { addListener: () => {} } },
  storage: {
    local: { get: async () => ({ endpoint: "http://127.0.0.1:8765/capture" }) },
    session: { get: async () => ({}), set: async () => ({}) },
  },
  tabs: { query: async () => [{ id: 7, url: "https://paper.test/article" }] },
  webRequest: { onHeadersReceived: { addListener: () => {}, removeListener: () => {} } },
  permissions: {
    contains: async () => false,
    request: async (request) => { globalThis.events.push({ type: "request", request }); return true; },
    remove: async (request) => { globalThis.events.push({ type: "remove", request }); return true; },
  },
  scripting: {
    executeScript: async ({ func, args }) => {
      if (String(func).includes("citation_doi")) {
        return [{ result: { pageTitle: "Paper", sourceUrl: args[0] } }];
      }
      // Two cross-origin candidates: the first works, the second is never tried.
      return [{ result: ["https://first.example/a.pdf", "https://second.example/b.pdf"] }];
    },
  },
};
globalThis.btoa = (value) => Buffer.from(value, "binary").toString("base64");
globalThis.fetch = async (url) => {
  if (url.endsWith(".pdf")) {
    return { ok: true, status: 200, headers: { get: () => "application/pdf" }, arrayBuffer: async () => pdfBytes };
  }
  return { ok: true, status: 200, headers: { get: () => "application/json" }, json: async () => ({ status: "ok", citekey: "smith2024paper" }) };
};
const mod = await import("./background.js");
await mod.captureCurrentTab({ dryRun: false });
console.log(JSON.stringify({ events: globalThis.events }));
''',
        tmp_path,
    )

    requested = [e["request"]["origins"][0] for e in result["events"] if e["type"] == "request"]
    released = [e["request"]["origins"][0] for e in result["events"] if e["type"] == "remove"]

    assert requested, result["events"]
    # Whatever was borrowed is given back, including origins never attempted.
    assert sorted(released) == sorted(requested), {
        "requested": requested,
        "released": released,
    }


# Drives a capture whose later stages each offer candidates, so the appends that
# used to bypass the cap and the safe-URL filter are the ones under test.
def _capture_with_candidate_sources(
    tmp_path: Path,
    *,
    page_urls: list[str],
    observed_urls: list[str],
    embedded_url: str = "https://paper.test/embedded.pdf",
) -> dict:
    return _run_background_module(
        r'''
globalThis.chrome = {
  runtime: { onInstalled: { addListener: () => {} } },
  storage: {
    local: { get: async () => ({ endpoint: "http://127.0.0.1:8765/capture" }) },
    session: { get: async () => ({}), set: async () => ({}) },
  },
  tabs: { query: async () => [{ id: 7, url: "https://paper.test/article" }] },
  webRequest: {
    onHeadersReceived: {
      addListener: (fn) => { globalThis.__observerFn = fn; },
      removeListener: () => {},
    },
  },
  permissions: { contains: async () => true, request: async () => true, remove: async () => true },
  scripting: {
    // Dispatched on call order, which `captureCurrentTab` fixes: metadata
    // extraction first, then the DOM scan, then click discovery. Sniffing the
    // injected function's source instead would be guessing at internals —
    // `scanDomForPdfUrls` shares vocabulary with the click-discovery injection.
    executeScript: async ({ args }) => {
      globalThis.__injections = (globalThis.__injections || 0) + 1;
      if (globalThis.__injections === 1) {
        return [{ result: { pageTitle: "Paper", sourceUrl: args[0], embedded_pdf_url: EMBEDDED } }];
      }
      return [{ result: PAGE_URLS }];
    },
  },
};
globalThis.sent = null;
globalThis.fetch = async (url, options = {}) => {
  if (url.endsWith("/capture")) {
    globalThis.sent = JSON.parse(options.body);
    return { ok: true, status: 200, headers: { get: () => "application/json" }, json: async () => ({ status: "ok" }) };
  }
  return { ok: false, status: 404, headers: { get: () => null }, json: async () => ({}) };
};
const mod = await import("./background.js");
// Feed the always-on observer the URLs it would have seen on the wire.
for (const u of OBSERVED_URLS) {
  mod.startPdfObserver(7);
  if (globalThis.__observerFn) {
    globalThis.__observerFn({ tabId: 7, url: u, responseHeaders: [{ name: "content-type", value: "application/pdf" }] });
  }
}
await mod.captureCurrentTab({ dryRun: false });
console.log(JSON.stringify({ sent: globalThis.sent }));
'''.replace("EMBEDDED", json.dumps(embedded_url))
        .replace("PAGE_URLS", json.dumps(page_urls))
        .replace("OBSERVED_URLS", json.dumps(observed_urls)),
        tmp_path,
    )


def _observe(tmp_path: Path, responses: list[dict]) -> dict:
    """Feed *responses* to the per-capture PDF observer and return what it kept."""
    return _run_background_module(
        r'''
globalThis.chrome = {
  runtime: { onInstalled: { addListener: () => {} } },
  webRequest: {
    onHeadersReceived: {
      addListener: (fn) => { globalThis.__listener = fn; },
      removeListener: () => {},
    },
  },
};
const observer = await import("./background/observer.mjs");
observer.startPdfObserver(7);
for (const response of RESPONSES) {
  globalThis.__listener({
    tabId: response.tab_id,
    url: response.url,
    type: response.type || "sub_frame",
    statusCode: 200,
    responseHeaders: [
      ...(response.content_type ? [{ name: "Content-Type", value: response.content_type }] : []),
      ...(response.disposition ? [{ name: "Content-Disposition", value: response.disposition }] : []),
    ],
  });
}
console.log(JSON.stringify({ observed: observer.collectObservedPdfUrls() }));
'''.replace("RESPONSES", json.dumps(responses)),
        tmp_path,
    )


def test_every_site_with_a_url_pattern_can_reach_its_extractor(tmp_path: Path) -> None:
    """The popup gates detection on a list it kept its own copy of.

    `search.js` defines nine site patterns with a URL pattern; the popup
    hardcoded five, and `initSearchDetection` returns early when its copy
    misses — so ResearchGate, CORE, BASE and SSRN had working extractors that
    could never be reached.

    The expected set is derived from the module rather than restated here, so
    adding a tenth site cannot quietly reintroduce the same gap.
    """
    result = _run_background_module(
        r'''
globalThis.chrome = { runtime: { onInstalled: { addListener: () => {} } } };
const { matchesAnySearchPattern, SEARCH_URL_PATTERNS } = await import("./background/search.mjs");
const probes = {
  "scholar.google.com": "https://scholar.google.com/scholar?q=graph+networks",
  "pubmed": "https://pubmed.ncbi.nlm.nih.gov/?term=graph+networks",
  "semanticscholar": "https://www.semanticscholar.org/search?q=graph",
  "arxiv": "https://arxiv.org/search/?query=graph",
  "dblp": "https://dblp.org/search?q=graph",
  "researchgate": "https://www.researchgate.net/search?q=graph",
  "core": "https://core.ac.uk/search?q=graph",
  "base": "https://www.base-search.net/Search/Results?lookfor=graph",
  "ssrn": "https://papers.ssrn.com/sol3/results.cfm",
};
const matched = {};
for (const [site, url] of Object.entries(probes)) matched[site] = matchesAnySearchPattern(url);
console.log(JSON.stringify({
  matched,
  patternCount: SEARCH_URL_PATTERNS.length,
  ordinaryArticle: matchesAnySearchPattern("https://www.nature.com/articles/s41586-024-00001"),
  nonsense: matchesAnySearchPattern(""),
}));
''',
        tmp_path,
    )

    unreachable = [site for site, hit in result["matched"].items() if not hit]
    assert unreachable == [], unreachable
    # Nine, because the tenth pattern matches by page content and has no URL.
    assert result["patternCount"] == 9, result["patternCount"]

    # The gate still gates: an ordinary article page does not look like a search.
    assert result["ordinaryArticle"] is False
    assert result["nonsense"] is False


def test_opening_the_popup_on_a_researchgate_search_offers_its_results(
    tmp_path: Path,
) -> None:
    """The user-visible half of item 131, and the only test here that could
    fail against the old code for the right reason.

    ResearchGate has a working extractor. The popup's own copy of the pattern
    list did not mention it, so `initSearchDetection` returned before ever
    calling the detector and the popup showed the ordinary single-capture form.
    """
    result = _run_popup_js_test(
        r'''
const elements = new Map();
const makeElement = (id) => ({
  id, value: "", checked: false, disabled: false, textContent: "", innerHTML: "",
  className: "", type: "", style: { cssText: "", display: "unset" }, children: [], handlers: {},
  appendChild(child) { this.children.push(child); },
  addEventListener(event, handler) { (this.handlers[event] ??= []).push(handler); },
  querySelectorAll: () => [],
});
globalThis.document = {
  getElementById: (id) => {
    if (!elements.has(id)) elements.set(id, makeElement(id));
    return elements.get(id);
  },
  createElement: () => makeElement("created"),
};
globalThis.window = { open: () => {} };
globalThis.fetch = async () => ({ ok: true, json: async () => ({}) });
globalThis.chrome = {
  storage: {
    local: { get: async () => ({}), set: async () => ({}), remove: async () => ({}) },
    session: { get: async () => ({}), set: async () => ({}), remove: async () => ({}) },
    onChanged: { addListener: () => {}, removeListener: () => {} },
  },
  tabs: { query: async () => [{ id: 7, url: "https://www.researchgate.net/search?q=graph+networks" }] },
  runtime: { sendMessage: () => {} },
};
globalThis.__searchResults = {
  detected: true,
  patternName: "ResearchGate",
  items: [{ index: 0, url: "https://www.researchgate.net/publication/1", title: "A Paper" }],
};
await import("./popup.js");
for (let i = 0; i < 20; i += 1) await new Promise((r) => setTimeout(r, 0));
console.log(JSON.stringify({
  searchShown: elements.get("search-results").style.display,
  formHidden: elements.get("capture-form").style.display,
  site: elements.get("result-site").textContent,
}));
''',
        tmp_path,
    )

    assert result["searchShown"] == "", result
    assert result["formHidden"] == "none", result
    assert result["site"] == "ResearchGate", result


def test_the_popup_keeps_no_copy_of_the_search_patterns(tmp_path: Path) -> None:
    """A second copy is what made four sites unreachable; one is the fix."""
    text = POPUP_JS.read_text()
    assert "scholar\\\\.google\\\\.com" not in text, "popup has its own pattern list again"
    assert "matchesAnySearchPattern" in text


def test_the_content_only_detector_stays_behind_the_gate(tmp_path: Path) -> None:
    """`generic-doi-list` has no URL pattern and must not be reached this way.

    It detects by counting `doi.org` links and needs ten, so every article page
    carrying a reference list of that size would match — the popup would
    present a paper as a page of search results. Deriving the gate from *all*
    patterns, as item 131 suggested, would have enabled exactly that.
    """
    result = _run_background_module(
        r'''
globalThis.chrome = { runtime: { onInstalled: { addListener: () => {} } } };
const { SEARCH_URL_PATTERNS } = await import("./background/search.mjs");
console.log(JSON.stringify({ patterns: SEARCH_URL_PATTERNS }));
''',
        tmp_path,
    )

    assert all(pattern for pattern in result["patterns"]), result["patterns"]


def test_no_ui_page_loads_the_service_worker(tmp_path: Path) -> None:
    """Importing `background.js` registers its listeners in the importing realm.

    `contextMenus.onClicked` and the always-on `webRequest` observer are
    registered at module level, and `contextMenus.onClicked` is delivered to
    every extension page that has a listener — so with the onboarding tab open,
    one right-click issued two `POST /capture`. The popup and the onboarding
    page must therefore reach their dependencies without loading that module.

    Asserted on the source rather than at runtime because the property is about
    what is *loaded*: once a UI page imports the service worker, the
    registrations have already happened, and a runtime probe would be measuring
    the harness's own module graph rather than the extension's.
    """
    for page in (POPUP_JS, ONBOARDING_JS):
        text = page.read_text()
        assert '"./background.js"' not in text, page.name
        assert "'./background.js'" not in text, page.name

    # And the module they must not import is still the one holding the
    # registrations, so this test keeps meaning what it says.
    service_worker = (PROJECT_ROOT / "browser-extension" / "background.js").read_text()
    assert "chrome.contextMenus.onClicked.addListener" in service_worker
    assert "_registerAlwaysOnPdfObserver" in service_worker


def test_the_capture_pipeline_is_reachable_without_the_service_worker(
    tmp_path: Path,
) -> None:
    """`captureCurrentTab` moved to `background/capture.js` for this reason.

    Importing it must not drag in the listener registrations, which is what
    made splitting it out necessary rather than just tidy.
    """
    result = _run_background_module(
        r'''
globalThis.registrations = [];
globalThis.chrome = {
  runtime: { onInstalled: { addListener: () => { globalThis.registrations.push("onInstalled"); } } },
  contextMenus: {
    create: () => {},
    onClicked: { addListener: () => { globalThis.registrations.push("contextMenus"); } },
  },
  webRequest: {
    onHeadersReceived: {
      addListener: () => { globalThis.registrations.push("webRequest"); },
      removeListener: () => {},
    },
  },
  storage: { local: { get: async () => ({}) }, session: { get: async () => ({}), set: async () => ({}) } },
  tabs: { query: async () => [] },
};
const mod = await import("./background/capture.mjs");
console.log(JSON.stringify({
  hasCapture: typeof mod.captureCurrentTab === "function",
  registrations: globalThis.registrations,
}));
''',
        tmp_path,
    )

    assert result["hasCapture"] is True
    assert result["registrations"] == [], result["registrations"]


def test_a_meta_refresh_resolves_to_the_whole_url(tmp_path: Path) -> None:
    """The capture group was non-greedy and followed by `[^>]*`.

    So the engine satisfied it with one character, and every meta refresh
    resolved to the URL's first letter — `https://host/paper.pdf` became
    `https://host/h`. IEEE's `stamp.jsp` gateway and its kin serve exactly this
    kind of redirect, which is what the fallback exists for, so it had never
    worked for anyone.
    """
    result = _run_background_module(
        r'''
const pdfBytes = new Uint8Array([37, 80, 68, 70, 45, 49]).buffer;
globalThis.fetched = [];
globalThis.chrome = {
  runtime: { onInstalled: { addListener: () => {} } },
  storage: { local: { get: async () => ({ endpoint: "http://127.0.0.1:8765/capture" }) } },
  tabs: { query: async () => [] },
  webRequest: { onHeadersReceived: { addListener: () => {}, removeListener: () => {} } },
  permissions: { contains: async () => true, request: async () => true, remove: async () => true },
};
globalThis.btoa = (v) => Buffer.from(v, "binary").toString("base64");
const HTML = '<html><head><meta http-equiv="refresh" content="0;URL=https://cdn.example/full/paper.pdf"></head></html>';
globalThis.fetch = async (url, options = {}) => {
  if ((options.method || "GET") === "GET") globalThis.fetched.push(url);
  if (url.includes("/attach-pdf")) {
    return { ok: true, status: 200, headers: { get: () => "application/json" }, json: async () => ({ status: "ok" }) };
  }
  if (url.includes("cdn.example")) {
    return { ok: true, status: 200, headers: { get: () => "application/pdf" }, arrayBuffer: async () => pdfBytes };
  }
  return {
    ok: true, status: 200,
    headers: { get: (n) => (n.toLowerCase() === "content-type" ? "text/html" : null) },
    arrayBuffer: async () => new TextEncoder().encode(HTML).buffer,
    text: async () => HTML,
  };
};
const { maybeStreamPdfBytes } = await import("./background/pdf_fetch.mjs");
await maybeStreamPdfBytes({
  endpoint: "http://127.0.0.1:8765/capture",
  citekey: "smith2024paper",
  bib: "main",
  pdfUrlCandidates: [{ url: "https://publisher.test/article.pdf" }],
  pageUrl: "https://publisher.test/article",
});
console.log(JSON.stringify({ fetched: globalThis.fetched }));
''',
        tmp_path,
    )

    assert "https://cdn.example/full/paper.pdf" in result["fetched"], result["fetched"]
    # The truncated form the old capture produced.
    assert not any(url.endswith("/h") for url in result["fetched"]), result["fetched"]


def test_a_pdf_from_a_publisher_redirect_names_the_candidate_it_came_from(
    tmp_path: Path,
) -> None:
    """The attach session pins `allowed_source_urls` to the plan's candidates.

    Discovery routinely leaves the plan — the publisher's article page
    redirects to a PDF on a CDN — so the observed URL is on another host and
    the attach was refused, losing the PDF while the capture reported success.
    The attach now names the planned candidate it began from, and reports the
    observed URL as provenance rather than as the credential.
    """
    result = _run_background_module(
        r'''
const pdfBytes = new Uint8Array([37, 80, 68, 70, 45, 49]).buffer;  // "%PDF-1"
globalThis.posts = [];
globalThis.chrome = {
  runtime: { onInstalled: { addListener: () => {} } },
  storage: { local: { get: async () => ({ endpoint: "http://127.0.0.1:8765/capture" }) } },
  tabs: { query: async () => [] },
  webRequest: { onHeadersReceived: { addListener: () => {}, removeListener: () => {} } },
  permissions: { contains: async () => true, request: async () => true, remove: async () => true },
};
globalThis.btoa = (value) => Buffer.from(value, "binary").toString("base64");
globalThis.fetch = async (url, options = {}) => {
  if (options.method === "POST") globalThis.posts.push({ url, body: options.body });
  if (url.includes("/attach-pdf-raw")) {
    return { ok: true, status: 200, headers: { get: () => "application/json" }, json: async () => ({ status: "ok" }) };
  }
  // The planned candidate serves a meta refresh to a CDN; the CDN serves the PDF.
  if (url.includes("cdn.example")) {
    return { ok: true, status: 200, headers: { get: () => "application/pdf" }, arrayBuffer: async () => pdfBytes };
  }
  return {
    ok: true, status: 200,
    headers: { get: (n) => (n.toLowerCase() === "content-type" ? "text/html" : null) },
    arrayBuffer: async () => new TextEncoder().encode(
      '<html><head><meta http-equiv="refresh" content="0;URL=https://cdn.example/paper.pdf"></head></html>'
    ).buffer,
    text: async () => '<html><head><meta http-equiv="refresh" content="0;URL=https://cdn.example/paper.pdf"></head></html>',
  };
};
const { maybeStreamPdfBytes } = await import("./background/pdf_fetch.mjs");
await maybeStreamPdfBytes({
  endpoint: "http://127.0.0.1:8765/capture",
  citekey: "smith2024paper",
  bib: "main",
  pdfUrlCandidates: [{ url: "https://publisher.test/article.pdf" }],
  pageUrl: "https://publisher.test/article",
  pdfRequest: { request_id: "req-1", attach: { token: "attach-tok" } },
});
const attach = globalThis.posts.find((p) => p.url.includes("/attach-pdf-raw"));
console.log(JSON.stringify({ attachUrl: attach ? attach.url : null }));
''',
        tmp_path,
    )

    attach_url = result["attachUrl"]
    assert attach_url, "no attach was attempted"
    # Provenance: where the bytes really came from.
    assert "cdn.example%2Fpaper.pdf" in attach_url.replace("%3A", ":"), attach_url
    # Authorisation: the planned candidate the fetch began from.
    assert "origin_candidate=" in attach_url, attach_url
    assert "publisher.test" in attach_url, attach_url


def test_the_ieee_mapping_that_actually_runs_is_the_one_tested(tmp_path: Path) -> None:
    """The extractor runs *in the page*, so it cannot call module helpers.

    `metadata.js` carried a module-level IEEE mapper whose only caller was a
    test, beside an inline copy inside the injected function that no test
    touched — the tested one was the dead one. This drives the copy that runs,
    through `chrome.scripting`, the way a capture does.
    """
    result = _run_background_module(
        r'''
globalThis.chrome = {
  runtime: { onInstalled: { addListener: () => {} } },
  scripting: {
    executeScript: async ({ func, args }) => {
      globalThis.document = {
        head: { innerHTML: "<meta charset='utf-8'>" },
        querySelector: () => null,
        querySelectorAll: () => [],
        title: "Fallback Title",
      };
      globalThis.location = { href: args[0], hostname: "ieeexplore.ieee.org", protocol: "https:" };
      globalThis.window = {
        xplGlobal: { document: { metadata: {
          displayDocTitle: "Deep Graph Networks",
          authors: [{ name: "Jane Smith" }, { name: "Ada Lovelace" }],
          publicationYear: "2024",
          publicationTitle: "IEEE Transactions on Parsing",
          abstract: "IEEE abstract text",
          startPage: "1", endPage: "5",
          issn: "1234-5678",
          doi: "10.1109/TEST.2024.1",
          pdfPath: "/stamp/stamp.jsp?tp=&arnumber=9840963",
        } } },
      };
      return [{ result: func(...args) }];
    },
  },
};
const { extractPageMetadata } = await import("./background/metadata.mjs");
const meta = await extractPageMetadata(7, "https://ieeexplore.ieee.org/document/9840963");
console.log(JSON.stringify({
  title: meta.pageTitle,
  authors: meta.embedded_authors,
  year: meta.embedded_year,
  venue: meta.embedded_venue,
  pages: meta.embedded_pages,
  doi: meta.doi,
  pdf: meta.embedded_pdf_url,
  trusted: meta.trusted_fields,
}));
''',
        tmp_path,
    )

    assert result["title"] == "Deep Graph Networks", result
    assert result["authors"] == ["Jane Smith", "Ada Lovelace"], result
    assert result["year"] == "2024", result
    assert result["venue"] == "IEEE Transactions on Parsing", result
    assert result["pages"] == "1--5", result
    assert result["doi"] == "10.1109/TEST.2024.1", result
    # Relative in the page's metadata; absolute by the time it leaves.
    assert result["pdf"].startswith("https://ieeexplore.ieee.org/stamp/"), result
    # The claim the server reads.
    assert "authors" in result["trusted"] and "doi" in result["trusted"], result


def test_the_recent_list_survives_the_browser_closing(tmp_path: Path) -> None:
    """It answers "what did I just save?", and was blank every session start.

    `pzi:recent` lived in `storage.session`, which the browser clears on close,
    so the list was empty at exactly the moment it is asked.
    """
    result = _run_popup_js_test(
        r'''
const elements = new Map();
const makeElement = (id) => ({
  id, value: "", checked: false, disabled: false, textContent: "", innerHTML: "",
  className: "", type: "", style: { cssText: "", display: "" }, children: [], handlers: {},
  appendChild(child) { this.children.push(child); },
  addEventListener(event, handler) { (this.handlers[event] ??= []).push(handler); },
  querySelectorAll: () => [],
});
globalThis.document = {
  getElementById: (id) => {
    if (!elements.has(id)) elements.set(id, makeElement(id));
    return elements.get(id);
  },
  createElement: () => makeElement("created"),
};
globalThis.window = { open: () => {} };
globalThis.fetch = async () => ({ ok: true, json: async () => ({}) });
const localBacking = {};
const sessionBacking = {};
const area = (backing) => ({
  get: async (keys) => {
    const out = {};
    for (const key of [].concat(keys)) if (key in backing) out[key] = backing[key];
    return out;
  },
  set: async (values) => { Object.assign(backing, values); return {}; },
  remove: async (keys) => { for (const k of [].concat(keys)) delete backing[k]; return {}; },
});
globalThis.chrome = {
  storage: {
    local: area(localBacking), session: area(sessionBacking),
    onChanged: { addListener: () => {}, removeListener: () => {} },
  },
  tabs: { query: async () => [{ id: 7, url: "https://paper.test/article" }] },
  runtime: { sendMessage: () => {} },
  permissions: { contains: async () => true, request: async () => true, remove: async () => true },
};
await import("./popup.js");
for (let i = 0; i < 20; i += 1) await new Promise((r) => setTimeout(r, 0));
// A capture completes and is remembered.
globalThis.__captureResult = { status: "ok", citekey: "smith2024paper", bib: "main", title: "A Paper" };
await elements.get("go").handlers.click[0]();
for (let i = 0; i < 20; i += 1) await new Promise((r) => setTimeout(r, 0));
console.log(JSON.stringify({
  inDurableStore: Object.keys(localBacking),
  inSessionStore: Object.keys(sessionBacking),
}));
''',
        tmp_path,
    )

    # The browser clears the session area on close; the recent list must not be there.
    assert "pzi:recent" in result["inDurableStore"], result
    assert "pzi:recent" not in result["inSessionStore"], result


def test_configuration_is_not_shadowed_by_session_storage(tmp_path: Path) -> None:
    """`getStoredConfig` merged `storage.session` *over* `storage.local`.

    That shadowing is how an empty token box wrote an empty token over the
    saved one. Nothing writes configuration to the session area — the only
    session key is `pzi:captureStage` — so reading it there bought nothing and
    cost that bug.
    """
    result = _run_background_module(
        r'''
globalThis.chrome = {
  runtime: { onInstalled: { addListener: () => {} } },
  storage: {
    local: { get: async (k) => ({ [k]: "from-local" }) },
    // A stale session value, of the kind that used to win.
    session: { get: async (k) => ({ [k]: "from-session" }) },
  },
};
const { getStoredConfig } = await import("./background/config.mjs");
console.log(JSON.stringify({ authToken: (await getStoredConfig("authToken")).authToken }));
''',
        tmp_path,
    )

    assert result["authToken"] == "from-local", result


def test_the_head_html_a_capture_sends_is_bounded(tmp_path: Path) -> None:
    """Publisher pages inline JSON-LD, analytics and CSS in `<head>`.

    The whole thing went out with every capture and the server keeps it in a
    page artifact, so a routine capture shipped hundreds of kilobytes. Nothing
    capped it on either side.
    """
    result = _run_background_module(
        r'''
globalThis.captured = null;
globalThis.chrome = {
  runtime: { onInstalled: { addListener: () => {} } },
  scripting: {
    executeScript: async ({ func, args }) => {
      // Run the injected extractor against a stub `document` with an enormous
      // <head>, which is what a publisher page actually presents.
      globalThis.document = {
        head: { innerHTML: "<meta charset='utf-8'>" + "x".repeat(500000) },
        querySelector: () => null,
        querySelectorAll: () => [],
        title: "A Paper",
      };
      // The extractor reads `location.href` and `location.hostname`. Without
      // them the injection throws and `extractPageMetadata` quietly returns its
      // empty record — a silent pass, since the assertion would then be on a
      // record with no head at all.
      globalThis.location = { href: args[0], hostname: "paper.test", protocol: "https:" };
      const out = func(...args);
      globalThis.captured = out;
      return [{ result: out }];
    },
  },
};
const { extractPageMetadata } = await import("./background/metadata.mjs");
const meta = await extractPageMetadata(7, "https://paper.test/article");
console.log(JSON.stringify({ headLength: (meta.headHtml || "").length }));
''',
        tmp_path,
    )

    assert 0 < result["headLength"] <= 64 * 1024, result


def test_hidden_iframe_probes_are_bounded_by_us_not_by_the_page(tmp_path: Path) -> None:
    """Each probe is a real navigation carrying the user's session.

    `clickPdfDiscovery` injected one hidden iframe per discovered URL, in
    parallel, and the match set is whatever the page offers — a publisher page
    with a "PDF" link beside every reference produced dozens of simultaneous
    navigations.
    """
    result = _run_background_module(
        r'''
globalThis.injected = [];
globalThis.chrome = {
  runtime: { onInstalled: { addListener: () => {} } },
  webRequest: { onHeadersReceived: { addListener: () => {}, removeListener: () => {} } },
  scripting: {
    executeScript: async ({ args }) => {
      // First call is the DOM scan; later ones are the iframe injections.
      if (!args || args.length === 0) {
        return [{ result: Array.from({ length: 40 }, (_, i) => `https://paper.test/ref-${i}.pdf`) }];
      }
      globalThis.injected.push(args[0]);
      return [{ result: null }];
    },
  },
};
const mod = await import("./background/pdf_discovery.mjs");
await mod.clickPdfDiscovery(7, "https://paper.test/article");
console.log(JSON.stringify({
  injected: globalThis.injected.length,
  offered: 40,
  cap: mod.MAX_HIDDEN_IFRAME_PROBES ?? null,
}));
''',
        tmp_path,
    )

    # Behavioural: fewer probes than the page offered. Stated this way so the
    # test fails against the unbounded version rather than on a missing export.
    assert result["injected"] < result["offered"], result
    assert result["cap"] and result["injected"] <= result["cap"], result


def test_the_bypass_budget_is_per_capture_not_per_tab(tmp_path: Path) -> None:
    """Three captures in one tab used to exhaust the bypass permanently.

    The budget reset only when the tab *changed*, so a second capture of a
    second paper in the same tab inherited the first one's spent attempts —
    and after three, the bypass stayed off until the user switched tabs and
    back. The cap was always described as being per capture.
    """
    result = _run_background_module(
        r'''
let created = 0;
globalThis.chrome = {
  runtime: { onInstalled: { addListener: () => {} } },
  webRequest: { onHeadersReceived: { addListener: () => {}, removeListener: () => {} } },
  scripting: { executeScript: async () => [{ result: null }] },
  tabs: {
    create: async () => { created += 1; return { id: 99 }; },
    update: async () => {},
    remove: async () => {},
    onUpdated: { addListener: () => {}, removeListener: () => {} },
  },
};
const mod = await import("./background/pdf_fetch.mjs");
const { botBypassPdfUrl } = mod;
// Optional: absent on the version that reset only when the tab changed, so
// this test measures behaviour there rather than failing to import.
const resetBotBypassBudget = mod.resetBotBypassBudget ?? (() => {});
const spend = async () => {
  for (let i = 0; i < 5; i += 1) {
    await botBypassPdfUrl(7, "https://www.nature.com/articles/x" + i + ".pdf", { visibleTimeoutMs: 1 });
  }
};
resetBotBypassBudget(7);
await spend();
const afterFirstCapture = created;
// A second capture, same tab.
resetBotBypassBudget(7);
await spend();
console.log(JSON.stringify({ afterFirstCapture, afterSecondCapture: created }));
''',
        tmp_path,
    )

    # The first capture spends its budget and stops.
    assert result["afterFirstCapture"] > 0
    # The second gets its own, rather than inheriting an exhausted one.
    assert result["afterSecondCapture"] > result["afterFirstCapture"], result


def test_the_bypass_helper_tab_does_not_steal_focus(tmp_path: Path) -> None:
    """It opens mid-capture and closes seconds later. Focused, it took over the
    window the user was reading. A background tab still runs the page's JS,
    which is all the observer needs."""
    result = _run_background_module(
        r'''
globalThis.created = [];
globalThis.chrome = {
  runtime: { onInstalled: { addListener: () => {} } },
  webRequest: { onHeadersReceived: { addListener: () => {}, removeListener: () => {} } },
  // Succeeds and observes nothing, which is what sends the bypass on to the
  // visible-tab fallback. A throw here returns from `botBypassPdfUrl` first.
  scripting: { executeScript: async () => [{ result: null }] },
  tabs: {
    create: async (options) => { globalThis.created.push(options); return { id: 99 }; },
    update: async () => {},
    remove: async () => {},
    onUpdated: { addListener: () => {}, removeListener: () => {} },
  },
};
const mod = await import("./background/pdf_fetch.mjs");
const { botBypassPdfUrl } = mod;
(mod.resetBotBypassBudget ?? (() => {}))(7);
await botBypassPdfUrl(7, "https://www.nature.com/articles/x.pdf", { visibleTimeoutMs: 1 });
console.log(JSON.stringify({ created: globalThis.created }));
''',
        tmp_path,
    )

    assert result["created"], "the visible-tab fallback did not run"
    assert all(opened.get("active") is False for opened in result["created"]), result["created"]


def test_an_attachment_that_is_not_a_pdf_is_not_a_pdf_candidate(tmp_path: Path) -> None:
    """The predicate accepted any `Content-Disposition` naming a filename.

    A page that serves a CSV export, a zip, or a BibTeX file — which is most
    publisher article pages — produced candidates the server then had to
    reject, and each one costs a fetch before it is found not to be a PDF.
    """
    result = _observe(
        tmp_path,
        [
            {"tab_id": 7, "url": "https://paper.test/export", "content_type": "text/csv",
             "disposition": 'attachment; filename="data.csv"'},
            {"tab_id": 7, "url": "https://paper.test/cite", "content_type": "text/plain",
             "disposition": 'attachment; filename="paper.bib"'},
            {"tab_id": 7, "url": "https://paper.test/bundle", "content_type": "application/zip",
             "disposition": 'attachment; filename="supplement.zip"'},
        ],
    )

    assert result["observed"] == [], result["observed"]


def test_a_pdf_named_only_in_the_query_string_is_not_a_candidate(tmp_path: Path) -> None:
    """`.pdf` was matched anywhere in the URL, including the query, and in the
    path of something that is plainly not a PDF."""
    result = _observe(
        tmp_path,
        [
            {"tab_id": 7, "url": "https://paper.test/viewer?file=paper.pdf", "content_type": "text/html"},
            {"tab_id": 7, "url": "https://paper.test/notes.pdf.html", "content_type": "text/html"},
        ],
    )

    assert result["observed"] == [], result["observed"]


def test_the_extensions_own_background_fetch_is_not_a_discovery(tmp_path: Path) -> None:
    """`tabId === -1` is a request belonging to no tab — the extension's own.

    Accepting it fed the extension its own candidate downloads back as fresh
    discoveries, so a URL it had already tried and failed on reappeared as
    something newly found.
    """
    result = _observe(
        tmp_path,
        [
            {"tab_id": -1, "url": "https://paper.test/already-tried.pdf",
             "content_type": "application/pdf", "type": "xmlhttprequest"},
        ],
    )

    assert result["observed"] == [], result["observed"]


def test_the_observer_still_finds_actual_pdfs(tmp_path: Path) -> None:
    """The tightening must not turn the observer off."""
    result = _observe(
        tmp_path,
        [
            {"tab_id": 7, "url": "https://paper.test/a", "content_type": "application/pdf"},
            {"tab_id": 7, "url": "https://paper.test/b.pdf", "content_type": "application/octet-stream"},
            {"tab_id": 7, "url": "https://paper.test/c", "content_type": "application/octet-stream",
             "disposition": 'attachment; filename="paper.pdf"'},
            {"tab_id": 7, "url": "https://ieeexplore.ieee.org/stampPDF/getPDF.jsp?tp=",
             "content_type": "application/octet-stream"},
        ],
    )

    assert result["observed"] == [
        "https://paper.test/a",
        "https://paper.test/b.pdf",
        "https://paper.test/c",
        "https://ieeexplore.ieee.org/stampPDF/getPDF.jsp?tp=",
    ], result["observed"]


def _exhausted_capture(
    tmp_path: Path,
    *,
    permissions_js: str,
    response_js: str,
    candidates: list[str] | None = None,
) -> dict:
    """Drive `maybeStreamPdfBytes` to failure and return what it reported."""
    return _run_background_module(
        r'''
globalThis.chrome = {
  runtime: { onInstalled: { addListener: () => {} } },
  storage: { local: { get: async () => ({ endpoint: "http://127.0.0.1:8765/capture" }) } },
  tabs: { query: async () => [] },
  webRequest: { onHeadersReceived: { addListener: () => {}, removeListener: () => {} } },
  PERMISSIONS
};
globalThis.fetch = async () => (RESPONSE);
const { maybeStreamPdfBytes } = await import("./background/pdf_fetch.mjs");
const outcome = await maybeStreamPdfBytes({
  endpoint: "http://127.0.0.1:8765/capture",
  citekey: "smith2024paper",
  bib: "main",
  pdfUrlCandidates: CANDIDATES.map((url) => ({ url })),
  pageUrl: "https://paper.test/article",
});
console.log(JSON.stringify({ outcome }));
'''.replace("PERMISSIONS", permissions_js)
        .replace("RESPONSE", response_js)
        .replace("CANDIDATES", json.dumps(candidates or ["https://cdn.paper.test/paper.pdf"])),
        tmp_path,
    )


_LOGIN_PAGE_RESPONSE = r'''{
  ok: true,
  status: 200,
  headers: { get: (name) => (name.toLowerCase() === "content-type" ? "text/html" : null) },
  arrayBuffer: async () => new TextEncoder().encode("<html><body>Please sign in to continue</body></html>").buffer,
  text: async () => "<html><body>Please sign in to continue</body></html>",
}'''


def test_a_login_wall_is_reported_as_a_login_wall(tmp_path: Path) -> None:
    """Nothing was denied, so nothing may be blamed on permissions.

    With the permissions API present and granting, the run reaches the page and
    finds a login wall. The message that names that was unreachable, because
    the permission branch above it returned on any status other than
    "granted" — and after a successful grant the *last* status recorded is
    still not necessarily "granted".
    """
    result = _exhausted_capture(
        tmp_path,
        permissions_js='permissions: { contains: async () => true, request: async () => true, remove: async () => true },',
        response_js=_LOGIN_PAGE_RESPONSE,
    )

    assert result["outcome"]["message"].startswith("PDF requires authentication")


def test_a_login_wall_wins_over_a_later_group_that_was_never_granted(
    tmp_path: Path,
) -> None:
    """Item 139's headline: the verdict came from whichever group ran *last*.

    Here the same-origin candidate is fetched and hits a login wall, and a
    cross-origin candidate is then refused. The old code reported only the last
    permission status, so the actionable finding — you need to log in — was
    replaced by a permission complaint about a different host entirely.
    """
    result = _exhausted_capture(
        tmp_path,
        permissions_js='permissions: { contains: async () => false, request: async () => false, remove: async () => true },',
        response_js=_LOGIN_PAGE_RESPONSE,
        candidates=["https://paper.test/paper.pdf", "https://cdn.other.test/paper.pdf"],
    )

    outcome = result["outcome"]
    assert outcome["message"].startswith("PDF requires authentication"), outcome["message"]
    # The refusal is not lost, it just does not get to be the headline.
    assert outcome["pdf_attach_denied_origins"] == ["https://cdn.other.test"], outcome


def test_no_permissions_api_is_not_a_denial(tmp_path: Path) -> None:
    """`"unavailable"` means the browser has no permissions API at all.

    Telling the user their permission was denied, when nothing ever asked them
    for one, sends them looking for a setting that does not exist. The honest
    answer here is the generic one: with no permissions API the cross-origin
    group is skipped without being fetched, so the run learned nothing more
    specific — which is exactly why it must not invent a cause.
    """
    result = _exhausted_capture(
        tmp_path,
        permissions_js="",  # no chrome.permissions at all
        response_js=_LOGIN_PAGE_RESPONSE,
    )

    outcome = result["outcome"]
    assert "permission denied" not in outcome["message"], outcome["message"]
    assert outcome["message"] == "browser PDF fetch failed — all candidates exhausted"
    assert outcome["pdf_attach_attempts"] == [], outcome
    # The status is still carried for diagnostics; it just no longer sets the message.
    assert outcome["pdf_attach_permission"]["status"] == "unavailable", outcome


def test_a_real_denial_still_names_the_origin_that_was_denied(tmp_path: Path) -> None:
    """The guard must not become a way to swallow genuine denials."""
    result = _exhausted_capture(
        tmp_path,
        permissions_js='permissions: { contains: async () => false, request: async () => false, remove: async () => true },',
        response_js='{ ok: false, status: 403, headers: { get: () => null }, text: async () => "" }',
    )

    outcome = result["outcome"]
    assert "permission denied" in outcome["message"], outcome["message"]
    # Named, not just counted — the user has to know which site to allow.
    assert "https://cdn.paper.test" in outcome["message"], outcome["message"]
    assert outcome["pdf_attach_denied_origins"] == ["https://cdn.paper.test"], outcome


def test_a_capture_without_an_attach_session_skips_the_route_that_must_refuse_it(
    tmp_path: Path,
) -> None:
    """`/attach-pdf-raw` refuses any request with no `request_id`.

    That refusal is deliberate — the attach session carries the TTL, byte-limit,
    source-URL and citekey checks, so a caller that could decline to name one
    would be declining every control. But the extension sent the full PDF there
    anyway on captures with no session, then sent the identical bytes to the
    fallback: twice the upload, and a rejected security check logged each time.
    """
    result = _run_background_module(
        r'''
const pdfBytes = new Uint8Array([37, 80, 68, 70, 45, 49]).buffer;  // "%PDF-1"
globalThis.uploads = [];
globalThis.chrome = {
  runtime: { onInstalled: { addListener: () => {} } },
  storage: { local: { get: async () => ({ endpoint: "http://127.0.0.1:8765/capture" }) } },
  tabs: { query: async () => [] },
  webRequest: { onHeadersReceived: { addListener: () => {}, removeListener: () => {} } },
  permissions: { contains: async () => true, request: async () => true, remove: async () => true },
};
globalThis.btoa = (value) => Buffer.from(value, "binary").toString("base64");
globalThis.fetch = async (url, options = {}) => {
  if (options.method === "POST") globalThis.uploads.push(url);
  if (url.includes("/attach-pdf-raw")) {
    // What the server really does without a request_id.
    return { ok: false, status: 403, headers: { get: () => "application/json" },
             json: async () => ({ error: "request_id required: attach must reference a capture" }) };
  }
  if (url.includes("/attach-pdf-bytes")) {
    return { ok: true, status: 200, headers: { get: () => "application/json" }, json: async () => ({ status: "ok" }) };
  }
  return { ok: true, status: 200, headers: { get: () => "application/pdf" }, arrayBuffer: async () => pdfBytes };
};
const { maybeStreamPdfBytes } = await import("./background/pdf_fetch.mjs");
const outcome = await maybeStreamPdfBytes({
  endpoint: "http://127.0.0.1:8765/capture",
  citekey: "smith2024paper",
  bib: "main",
  pdfUrlCandidates: [{ url: "https://paper.test/paper.pdf" }],
  pageUrl: "https://paper.test/article",
  // No `pdfRequest`: this capture has no attach session.
});
console.log(JSON.stringify({ status: outcome.status, uploads: globalThis.uploads }));
''',
        tmp_path,
    )

    assert result["status"] == "ok", result
    raw_uploads = [u for u in result["uploads"] if "/attach-pdf-raw" in u]
    assert raw_uploads == [], raw_uploads
    # And the PDF still gets attached, by the route that can actually accept it.
    assert any("/attach-pdf-bytes" in u for u in result["uploads"]), result["uploads"]


def test_the_candidate_cap_holds_against_every_source(tmp_path: Path) -> None:
    """The server rejects the whole capture for an over-long list.

    `extractPdfUrlCandidates` capped what it collected, then three later
    appends in `background.js` pushed onto the same array without checking —
    so a page offering many PDF-ish links sent more than the server accepts
    and lost its metadata along with the PDF.
    """
    result = _capture_with_candidate_sources(
        tmp_path,
        page_urls=[f"https://paper.test/dom-{i}.pdf" for i in range(30)],
        observed_urls=[f"https://paper.test/seen-{i}.pdf" for i in range(15)],
    )

    candidates = result["sent"]["pdf_url_candidates"]
    assert len(candidates) <= 20, len(candidates)


def test_a_loopback_url_from_the_page_never_reaches_the_server(tmp_path: Path) -> None:
    """`isSafePublicHttpUrl` guards the collector and was skipped by the appends.

    The extension holds `http://127.0.0.1/*` and the page supplies
    `embedded_pdf_url`, so a loopback or private URL genuinely arrives — and the
    server 400s the *entire* capture on one, losing the metadata too.
    """
    result = _capture_with_candidate_sources(
        tmp_path,
        page_urls=["https://paper.test/real.pdf"],
        observed_urls=["https://paper.test/seen.pdf"],
        # Arrives through `embedded_pdf_url`, one of the three appends that
        # bypassed the filter. Routing it through the collector instead would
        # test the guard that already worked.
        embedded_url="http://127.0.0.1:8765/pdf/other",
    )

    candidates = result["sent"]["pdf_url_candidates"]
    assert not any("127.0.0.1" in url for url in candidates), candidates
    # The public ones still got through, so this is a filter and not a blanket.
    assert "https://paper.test/real.pdf" in candidates, candidates


def test_a_capture_that_the_server_rejects_still_releases_its_origins(
    tmp_path: Path,
) -> None:
    """`captureCurrentTab` returns early on a non-ok response and on a body it
    cannot parse, both *after* the origin permissions have been granted. A
    release placed at the end of the happy path would skip exactly the cases
    where the user gets nothing in return for the grant.
    """
    result = _run_background_module(
        r'''
globalThis.events = [];
globalThis.chrome = {
  runtime: { onInstalled: { addListener: () => {} } },
  storage: {
    local: { get: async () => ({ endpoint: "http://127.0.0.1:8765/capture" }) },
    session: { get: async () => ({}), set: async () => ({}) },
  },
  tabs: { query: async () => [{ id: 7, url: "https://paper.test/article" }] },
  webRequest: { onHeadersReceived: { addListener: () => {}, removeListener: () => {} } },
  permissions: {
    contains: async () => false,
    request: async (request) => { globalThis.events.push({ type: "request", request }); return true; },
    remove: async (request) => { globalThis.events.push({ type: "remove", request }); return true; },
  },
  scripting: {
    executeScript: async ({ func, args }) => {
      if (String(func).includes("citation_doi")) {
        return [{ result: { pageTitle: "Paper", sourceUrl: args[0] } }];
      }
      return [{ result: ["https://first.example/a.pdf"] }];
    },
  },
};
globalThis.fetch = async () => ({
  ok: false,
  status: 502,
  statusText: "Bad Gateway",
  json: async () => { throw new SyntaxError("Unexpected token '<'"); },
});
const mod = await import("./background.js");
const outcome = await mod.captureCurrentTab({ dryRun: false });
console.log(JSON.stringify({ status: outcome.status, events: globalThis.events }));
''',
        tmp_path,
    )

    assert result["status"] == "error"
    requested = [e["request"]["origins"][0] for e in result["events"] if e["type"] == "request"]
    released = [e["request"]["origins"][0] for e in result["events"] if e["type"] == "remove"]
    assert requested, result["events"]
    assert sorted(released) == sorted(requested), {
        "requested": requested,
        "released": released,
    }


def test_a_non_loopback_endpoint_is_not_used(tmp_path: Path) -> None:
    """Everything a capture holds goes to this URL: the page HTML, the user's
    cookies for that site, the downloaded PDF, and the API token. It was taken
    from storage unchecked, so anything that could write extension storage —
    or one mistyped character in the options box — redirected the lot to a
    remote host."""
    result = _run_background_module(
        r'''
const store = { endpoint: "https://evil.example.com/capture" };
globalThis.chrome = {
  runtime: { onInstalled: { addListener: () => {} } },
  webRequest: { onHeadersReceived: { addListener: () => {}, removeListener: () => {} } },
  storage: { local: { get: async (k) => ({ [k]: store[k] }) } },
};
const { getEndpoint, DEFAULT_ENDPOINT } = await import("./background/config.mjs");
const remote = await getEndpoint();
store.endpoint = "http://127.0.0.1:9999/capture";
const loopbackIp = await getEndpoint();
store.endpoint = "http://localhost:8765/capture";
const loopbackName = await getEndpoint();
console.log(JSON.stringify({ remote, loopbackIp, loopbackName, DEFAULT_ENDPOINT }));
''',
        tmp_path,
    )

    assert result["remote"] == result["DEFAULT_ENDPOINT"]
    # A loopback endpoint on any port is the normal case and stays honoured.
    assert result["loopbackIp"] == "http://127.0.0.1:9999/capture"
    assert result["loopbackName"] == "http://localhost:8765/capture"



def test_a_temporary_origin_permission_is_released_on_every_path(tmp_path: Path) -> None:
    """The release sat after the call it protects, so a throw out of
    `tryPdfCandidates` left the user holding a host permission they had granted
    for one PDF fetch — silently, and indefinitely.

    Driven, contrary to the note this replaced: no network stack is needed, only
    a `chrome` stub that throws where the code does not guard. A candidate on
    the bot-bypass allowlist reaches `waitForTabLoad`, whose
    `chrome.tabs.onUpdated.addListener` call sits in a promise executor with no
    `try` around it, so a failure there rejects straight through
    `tryPdfCandidates` — the exact path that used to skip the release.
    """
    result = _run_background_module(
        r'''
globalThis.events = [];
globalThis.chrome = {
  runtime: { onInstalled: { addListener: () => {} } },
  storage: { local: { get: async () => ({ endpoint: "http://127.0.0.1:8765/capture" }) } },
  tabs: {
    query: async () => [],
    create: async ({ url }) => { globalThis.events.push({ type: "tab", url }); return { id: 91 }; },
    update: async () => {},
    remove: async () => {},
    onUpdated: {
      // The failure the release has to survive.
      addListener: () => { throw new Error("tab listener registration failed"); },
      removeListener: () => {},
    },
  },
  webRequest: { onHeadersReceived: { addListener: () => {}, removeListener: () => {} } },
  permissions: {
    // Not already held, so the grant is temporary and must be given back.
    contains: async () => false,
    request: async (request) => { globalThis.events.push({ type: "granted", request }); return true; },
    remove: async (request) => { globalThis.events.push({ type: "released", request }); return true; },
  },
  scripting: { executeScript: async () => [{ result: null }] },
};
globalThis.fetch = async () => ({ ok: false, status: 403, headers: { get: () => null }, text: async () => "" });
const { maybeStreamPdfBytes } = await import("./background/pdf_fetch.mjs");
let threw = null;
try {
  await maybeStreamPdfBytes({
    endpoint: "http://127.0.0.1:8765/capture",
    citekey: "smith2024paper",
    bib: "main",
    pdfUrlCandidates: [{ url: "https://www.nature.com/articles/s41586-024-00001.pdf", method: "discover_from_page", timeout_ms: 5 }],
    pageUrl: "https://www.nature.com/articles/s41586-024-00001",
  });
} catch (error) {
  threw = error.message;
}
console.log(JSON.stringify({ threw, events: globalThis.events }));
''',
        tmp_path,
    )

    # The run really did fail on the unguarded path.
    assert result["threw"] == "tab listener registration failed", result

    kinds = [event["type"] for event in result["events"]]
    assert "granted" in kinds, result["events"]
    # And the permission was handed back anyway.
    assert "released" in kinds, result["events"]
    released = next(e for e in result["events"] if e["type"] == "released")
    assert released["request"] == {"origins": ["https://www.nature.com/*"]}


def test_bot_bypass_is_capped_per_capture(tmp_path: Path) -> None:
    """Each attempt opens a hidden iframe and can fall through to a *visible*
    tab, up to 20s apiece. A page offering many allowlisted candidates turned
    one capture into a minutes-long sequence of tabs opening in the user's face.
    """
    result = _run_background_module(
        r'''
let created = 0;
globalThis.chrome = {
  runtime: { onInstalled: { addListener: () => {} } },
  webRequest: { onHeadersReceived: { addListener: () => {}, removeListener: () => {} } },
  scripting: { executeScript: async () => [{ result: null }] },
  tabs: {
    create: async () => { created += 1; return { id: 90 + created }; },
    update: async () => {},
    remove: async () => {},
    onUpdated: { addListener: () => {}, removeListener: () => {} },
  },
};
const mod = await import("./background.js");
const results = [];
for (let i = 0; i < 8; i += 1) {
  results.push(await mod.botBypassPdfUrl(
    7, "https://ieeexplore.ieee.org/stamp/stamp.jsp?arnumber=" + i,
    { visibleTimeoutMs: 5 },
  ));
}
console.log(JSON.stringify({ created, attempts: results.length }));
''',
        tmp_path,
    )

    assert result["attempts"] == 8
    # Far fewer helper tabs than candidates: the budget stopped it.
    assert result["created"] <= 3, result


def test_permission_prompts_are_capped_per_capture(tmp_path: Path) -> None:
    """The candidate list comes from the page, so the prompt count must not.

    One capture measured ten consecutive `chrome.permissions.request` dialogs.
    Prompt fatigue is the only thing between a hostile page and a granted host
    permission for an origin the user never meant to trust.
    """
    result = _run_background_module(
        r'''
let requested = 0;
globalThis.chrome = {
  runtime: { onInstalled: { addListener: () => {} } },
  webRequest: { onHeadersReceived: { addListener: () => {}, removeListener: () => {} } },
  contextMenus: { create: () => {}, onClicked: { addListener: () => {} } },
  permissions: {
    contains: async () => false,
    request: async () => { requested += 1; return false; },
  },
};
const { requestPdfOriginPermissions } = await import("./background/permissions.mjs");
const candidates = [];
for (let i = 0; i < 8; i++) candidates.push({ url: `https://cdn${i}.example.com/p.pdf` });
const permissions = await requestPdfOriginPermissions(candidates, "https://page.example.org/a");
console.log(JSON.stringify({ requested, origins: permissions.size }));
''',
        tmp_path,
    )

    assert result["origins"] == 8, "every origin should still get a recorded outcome"
    assert result["requested"] <= 2, f"{result['requested']} dialogs raised by one capture"


def test_a_non_loopback_attach_url_is_not_trusted(tmp_path: Path) -> None:
    """`new URL(absolute, base)` discards the base.

    `attach.url` is server-supplied and derives from the user-editable `api_url`
    config key, so an absolute value sent the API token and the PDF bytes to
    whatever host it named. `isLoopbackEndpoint` exists for exactly this.

    Driven rather than grepped: `rawAttachUrl` is module-local but reachable
    through `maybeStreamPdfBytes`, and what matters is where the bytes and the
    token are actually sent. Both a remote `attach.url` and an accepted
    loopback one are exercised, so the guard is shown to discriminate.
    """
    result = _run_background_module(
        r'''
const pdfBytes = new Uint8Array([37, 80, 68, 70, 45, 49]).buffer;  // "%PDF-1"
globalThis.chrome = {
  runtime: { onInstalled: { addListener: () => {} } },
  storage: { local: { get: async () => ({ endpoint: "http://127.0.0.1:8765/capture", authToken: "tok" }) } },
  tabs: { query: async () => [] },
  webRequest: { onHeadersReceived: { addListener: () => {}, removeListener: () => {} } },
  permissions: { contains: async () => true, request: async () => true, remove: async () => true },
};
globalThis.btoa = (value) => Buffer.from(value, "binary").toString("base64");
const { maybeStreamPdfBytes } = await import("./background/pdf_fetch.mjs");

const attachTo = async (plannedUrl) => {
  const sent = [];
  globalThis.fetch = async (url, options = {}) => {
    sent.push({ url, headers: options.headers || {}, hasBody: Boolean(options.body) });
    if (url.endsWith(".pdf")) {
      return { ok: true, status: 200, headers: { get: () => "application/pdf" }, arrayBuffer: async () => pdfBytes };
    }
    return { ok: true, status: 200, headers: { get: () => "application/json" }, json: async () => ({ status: "ok" }) };
  };
  await maybeStreamPdfBytes({
    endpoint: "http://127.0.0.1:8765/capture",
    citekey: "smith2024paper",
    bib: "main",
    pdfUrlCandidates: [{ url: "https://paper.test/paper.pdf" }],
    pageUrl: "https://paper.test/article",
    pdfRequest: { request_id: "req-1", attach: { url: plannedUrl, token: "attach-tok" } },
  });
  return sent;
};

console.log(JSON.stringify({
  remote: await attachTo("https://evil.example.com/attach-pdf-raw?citekey=smith2024paper"),
  loopback: await attachTo("http://127.0.0.1:8765/attach-pdf-raw?request_id=req-1&citekey=smith2024paper"),
}));
''',
        tmp_path,
    )

    # Nothing at all reached the host the server named.
    assert not any("evil.example.com" in call["url"] for call in result["remote"]), result["remote"]
    # The bytes and the attach token went to the configured endpoint instead.
    remote_attach = [c for c in result["remote"] if "/attach-pdf-raw" in c["url"]]
    assert remote_attach, result["remote"]
    assert remote_attach[0]["url"].startswith("http://127.0.0.1:8765/attach-pdf-raw")
    assert remote_attach[0]["headers"]["X-Pzi-Attach-Token"] == "attach-tok"

    # And a loopback plan is still honoured, so the guard is not a blanket refusal.
    loopback_attach = [c for c in result["loopback"] if "/attach-pdf-raw" in c["url"]]
    assert loopback_attach, result["loopback"]
    assert "request_id=req-1" in loopback_attach[0]["url"]


def test_bot_bypass_requires_the_candidate_itself_to_be_allowlisted(tmp_path: Path) -> None:
    """`||` let an allowlisted *page* authorise a navigation to any candidate.

    The bypass opens a real, cookie-carrying tab, so the allowlist has to apply
    to the URL being opened.

    Driven rather than grepped, and driven both ways: the observable is whether
    `chrome.tabs.create` runs. Asserting only the `&&` spelling let a mutant
    keep the literal and short-circuit it to true.
    """
    result = _run_background_module(
        r'''
globalThis.created = [];
globalThis.chrome = {
  runtime: { onInstalled: { addListener: () => {} } },
  storage: { local: { get: async () => ({ endpoint: "http://127.0.0.1:8765/capture" }) } },
  webRequest: { onHeadersReceived: { addListener: () => {}, removeListener: () => {} } },
  permissions: { contains: async () => true, request: async () => true, remove: async () => true },
  tabs: {
    create: async ({ url }) => { globalThis.created.push(url); return { id: 40 + globalThis.created.length }; },
    update: async () => {},
    remove: async () => {},
    query: async () => [],
    onUpdated: { addListener: () => {}, removeListener: () => {} },
  },
  scripting: { executeScript: async () => [{ result: null }] },
};
// Nothing downloadable anywhere, so the run always reaches the end of the chain.
globalThis.fetch = async () => ({
  ok: false,
  status: 403,
  headers: { get: () => null },
});
const { maybeStreamPdfBytes } = await import("./background/pdf_fetch.mjs");

// An allowlisted *page* offering a candidate on some other host. The page is
// nature.com; the candidate is not, so no tab may be opened for it.
const foreign = await maybeStreamPdfBytes({
  endpoint: "http://127.0.0.1:8765/capture",
  citekey: "smith2024paper",
  bib: "main",
  pdfUrlCandidates: [{ url: "https://cdn.unlisted.example/paper.pdf", method: "discover_from_page", timeout_ms: 5 }],
  pageUrl: "https://www.nature.com/articles/s41586-024-00001",
});
const createdAfterForeign = globalThis.created.slice();

// The converse: candidate and page both allowlisted, so the bypass is allowed
// to open its helper tab. Without this the test would pass on a guard that
// refuses everything.
const allowed = await maybeStreamPdfBytes({
  endpoint: "http://127.0.0.1:8765/capture",
  citekey: "smith2024paper",
  bib: "main",
  pdfUrlCandidates: [{ url: "https://www.nature.com/articles/s41586-024-00001.pdf", method: "discover_from_page", timeout_ms: 5 }],
  pageUrl: "https://www.nature.com/articles/s41586-024-00001",
});
console.log(JSON.stringify({
  foreign,
  allowed,
  createdAfterForeign,
  created: globalThis.created,
}));
''',
        tmp_path,
    )

    # No tab was opened at the unlisted candidate.
    assert result["createdAfterForeign"] == [], result["createdAfterForeign"]
    skipped = [
        a
        for a in result["foreign"]["pdf_attach_attempts"]
        if a.get("mode") == "discover_from_page" and a.get("status") == "skipped"
    ]
    assert skipped, result["foreign"]["pdf_attach_attempts"]
    assert skipped[0]["reason"] == "domain not bot-bypass allowlisted"

    # And the guard is discriminating, not blanket: an allowlisted candidate on
    # the same allowlisted page does get its helper tab.
    assert result["created"] == [
        "https://www.nature.com/articles/s41586-024-00001.pdf"
    ], result["created"]


def test_no_optional_request_for_a_required_cookies_permission(tmp_path: Path) -> None:
    """`cookies` is required in the manifest and absent from optional_permissions.

    `chrome.permissions.request({permissions:["cookies"]})` is rejected on
    Firefox for a non-optional permission; the catch returned "denied" and the
    cookie-header retry never ran.

    The manifest half below is a real data assertion and stays. The code half
    used to be a substring sweep over every `.js`, which any other spelling of
    the same request would walk past; it is now driven with a `permissions`
    stub that rejects the way Firefox does, so the consequence — a cookie retry
    that never happens — is what fails.
    """
    ext = PROJECT_ROOT / "browser-extension"
    manifest = json.loads((ext / "manifest.base.json").read_text())
    assert "cookies" in manifest["permissions"]
    assert "cookies" not in manifest.get("optional_permissions", [])
    required = set(manifest["permissions"])

    result = _run_background_module(
        r'''
globalThis.permissionRequests = [];
globalThis.fetchCalls = [];
const REQUIRED = new Set(''' + json.dumps(sorted(required)) + r''');
globalThis.chrome = {
  runtime: { onInstalled: { addListener: () => {} } },
  storage: { local: { get: async () => ({ endpoint: "http://127.0.0.1:8765/capture" }) } },
  tabs: { query: async () => [] },
  webRequest: { onHeadersReceived: { addListener: () => {}, removeListener: () => {} } },
  cookies: { getAll: async () => [{ name: "session", value: "s3cret" }] },
  permissions: {
    contains: async () => true,
    remove: async () => true,
    // Firefox rejects a request for a permission that is required rather than
    // optional. Asking for one is the bug, so the stub makes asking fatal.
    request: async (request) => {
      globalThis.permissionRequests.push(request);
      for (const name of request.permissions || []) {
        if (REQUIRED.has(name)) {
          throw new Error("permissions.request may not be used for a required permission: " + name);
        }
      }
      return true;
    },
  },
};
globalThis.fetch = async (url, options = {}) => {
  globalThis.fetchCalls.push({ url, headers: options.headers || null });
  return { ok: false, status: 403, headers: { get: () => null }, text: async () => "" };
};
const { maybeStreamPdfBytes } = await import("./background/pdf_fetch.mjs");
const outcome = await maybeStreamPdfBytes({
  endpoint: "http://127.0.0.1:8765/capture",
  citekey: "smith2024paper",
  bib: "main",
  pdfUrlCandidates: [{ url: "https://paper.test/paper.pdf" }],
  pageUrl: "https://paper.test/article",
});
console.log(JSON.stringify({
  outcome,
  permissionRequests: globalThis.permissionRequests,
  fetchCalls: globalThis.fetchCalls,
}));
''',
        tmp_path,
    )

    # Origin permissions may be requested; the required ones may never be.
    for request in result["permissionRequests"]:
        assert not (set(request.get("permissions", [])) & required), request

    # And the retry the bug suppressed actually ran, carrying the cookie header.
    attempts = result["outcome"]["pdf_attach_attempts"]
    header = [a for a in attempts if a.get("status") == "cookie_header"]
    assert header and header[0]["has_header"] is True, attempts
    assert any(a.get("mode") == "browser_fetch_cookies" for a in attempts), attempts
    cookie_calls = [
        call for call in result["fetchCalls"] if (call["headers"] or {}).get("Cookie")
    ]
    assert cookie_calls, result["fetchCalls"]
    assert cookie_calls[0]["headers"]["Cookie"] == "session=s3cret"


def test_an_authenticated_page_body_is_not_retained_for_display(tmp_path: Path) -> None:
    """The snippet was the body of a page fetched with the user's cookies.

    It is rendered in the popup's raw-response pane, so a login form's CSRF
    token or a prefilled username could be shown back. The classification beside
    it is what the diagnostics actually need.

    Driven rather than grepped: the property is "nothing from the body reaches
    the caller", so the check is that two planted secrets are absent from the
    whole returned structure. The assertion this replaced named one spelling of
    one slice length, and a mutant using any other survived it.
    """
    result = _run_background_module(
        r'''
const LOGIN_HTML = [
  "<html><body><h1>Sign in</h1>",
  '<input name="csrf" value="PZI-PLANTED-CSRF-a1b2c3">',
  '<input name="user" value="planted.user@example.org">',
  "</body></html>",
].join("");
globalThis.chrome = {
  runtime: { onInstalled: { addListener: () => {} } },
  storage: { local: { get: async () => ({ endpoint: "http://127.0.0.1:8765/capture" }) } },
  tabs: { query: async () => [] },
  webRequest: { onHeadersReceived: { addListener: () => {}, removeListener: () => {} } },
  // Granted, so the outcome reports the login page rather than short-circuiting
  // on a permission verdict.
  permissions: { contains: async () => true, request: async () => true, remove: async () => true },
};
globalThis.fetch = async (url) => ({
  ok: true,
  status: 200,
  headers: { get: (name) => (name.toLowerCase() === "content-type" ? "text/html; charset=utf-8" : null) },
  arrayBuffer: async () => new TextEncoder().encode(LOGIN_HTML).buffer,
  text: async () => LOGIN_HTML,
});
const { maybeStreamPdfBytes } = await import("./background/pdf_fetch.mjs");
const outcome = await maybeStreamPdfBytes({
  endpoint: "http://127.0.0.1:8765/capture",
  citekey: "smith2024paper",
  bib: "main",
  pdfUrlCandidates: [{ url: "https://paper.test/paper.pdf" }],
  pageUrl: "https://paper.test/article",
});
console.log(JSON.stringify({ outcome }));
''',
        tmp_path,
    )

    outcome = result["outcome"]
    attempts = outcome["pdf_attach_attempts"]

    # The classification is kept — that is what the diagnostics need.
    login = [a for a in attempts if a.get("status") == "html_login"]
    assert login, attempts
    assert login[0]["content_type"] == "text/html; charset=utf-8"
    assert login[0]["byte_count"] > 0
    assert outcome["message"].startswith("PDF requires authentication")

    # The body is not, by any route.
    assert all(a.get("text_snippet") is None for a in attempts), attempts
    serialised = json.dumps(outcome)
    assert "PZI-PLANTED-CSRF-a1b2c3" not in serialised
    assert "planted.user@example.org" not in serialised
    assert "Sign in" not in serialised


def test_a_non_json_response_does_not_crash_before_its_own_guard(tmp_path: Path) -> None:
    """`jsonOrNull` returns null by design, so the assignment had to come after.

    Setting `extension_version` on the result first threw `Cannot set properties
    of null` on exactly the case the two branches below were written for — a
    proxy's HTML 502, a truncated body — and made the `!result` guard
    unreachable. The user saw an internal JS error instead of the HTTP status.

    Driven rather than grepped: the ordering the assertions this replaced
    checked is a means, and the end is that both bodies produce a reported
    status instead of a thrown TypeError.
    """
    result = _run_background_module(
        r'''
const stub = () => ({
  runtime: { onInstalled: { addListener: () => {} } },
  storage: {
    local: { get: async () => ({ endpoint: "http://127.0.0.1:8765/capture" }) },
    session: { get: async () => ({}), set: async () => ({}) },
  },
  tabs: { query: async () => [{ id: 7, url: "https://paper.test/article" }] },
  webRequest: { onHeadersReceived: { addListener: () => {}, removeListener: () => {} } },
  scripting: {
    executeScript: async ({ func, args }) => {
      if (String(func).includes("citation_doi")) return [{ result: { pageTitle: "Paper", sourceUrl: args[0] } }];
      return [{ result: [] }];
    },
  },
});
globalThis.chrome = stub();
const mod = await import("./background.js");

const capture = async (respond) => {
  globalThis.fetch = async () => respond();
  try {
    return { returned: await mod.captureCurrentTab({ dryRun: true }) };
  } catch (error) {
    return { threw: String(error && error.message || error) };
  }
};

// A proxy's HTML 502: not ok, and the body does not parse.
const proxyError = await capture(() => ({
  ok: false,
  status: 502,
  statusText: "Bad Gateway",
  json: async () => { throw new SyntaxError("Unexpected token '<'"); },
}));

// A truncated body behind a 200: ok, but still not JSON.
const truncated = await capture(() => ({
  ok: true,
  status: 200,
  statusText: "OK",
  json: async () => { throw new SyntaxError("Unexpected end of JSON input"); },
}));

console.log(JSON.stringify({ proxyError, truncated }));
''',
        tmp_path,
    )

    # Neither body may escape as a thrown error.
    assert "threw" not in result["proxyError"], result["proxyError"]
    assert "threw" not in result["truncated"], result["truncated"]

    # The HTTP status the user needed is what comes back.
    proxy = result["proxyError"]["returned"]
    assert proxy["status"] == "error"
    assert any("502" in error for error in proxy["errors"]), proxy

    # And the `!result` guard, which the assignment used to make unreachable.
    truncated = result["truncated"]["returned"]
    assert truncated["status"] == "error"
    assert truncated["errors"] == ["capture request failed: invalid JSON response"]


def test_a_route_rejection_is_named_not_reduced_to_capture_failed(tmp_path: Path) -> None:
    """`captureFailureReason` reads all three channels; `message` alone reads one.

    A route-level rejection — bad token, rate limit, refused host, 500 — carries
    a singular `error`, so every one of them rendered as the literal "capture
    failed", which is the one thing the user already knew.

    Driven rather than grepped: what matters is that the reason reaches the
    rendered text, whichever channel carried it.
    """
    result = _run_popup_format_module(
        r'''
const mod = await import("./popup_format.js");
const rendered = {
  // A route rejection: singular `error`, no `message`.
  route: mod.formatCaptureResult({ status: "error", error: "invalid API token" }),
  // A service result: plural `errors`.
  service: mod.formatCaptureResult({ status: "error", errors: ["translation server returned no results"] }),
  // And the plain `message` channel, which always worked.
  message: mod.formatCaptureResult({ status: "error", message: "bad request" }),
  // Nothing at all is the only case allowed to fall back.
  empty: mod.formatCaptureResult({ status: "error" }),
};
console.log(JSON.stringify(rendered));
''',
        tmp_path,
    )

    assert "invalid API token" in result["route"]
    assert "translation server returned no results" in result["service"]
    assert "bad request" in result["message"]
    # Only the channel-less case may render the bare fallback.
    assert result["empty"] == "❌ Capture failed: failed"


def test_a_down_server_is_reported_rather_than_shown_as_no_bibs(tmp_path: Path) -> None:
    """Returning `[]` on failure made an unreachable server look like an empty
    library, so the popup showed an empty dropdown and said nothing.

    Both halves are driven. The assertions this replaced looked for `return [];`
    in one function and the bare word `catch` in another — the second of which
    says nothing about whether anything is shown to the user.
    """
    raised = _run_background_module(
        r'''
globalThis.chrome = {
  runtime: { onInstalled: { addListener: () => {} } },
  storage: { local: { get: async () => ({ endpoint: "http://127.0.0.1:8765/capture", authToken: "tok" }) } },
};
const { fetchBibs } = await import("./background/config.mjs");
const call = async (respond) => {
  globalThis.fetch = respond;
  try {
    return { returned: await fetchBibs() };
  } catch (error) {
    return { message: error.message };
  }
};
console.log(JSON.stringify({
  down: await call(async () => { throw new TypeError("Failed to fetch"); }),
  rejected: await call(async () => ({ ok: false, status: 401 })),
  broken: await call(async () => ({ ok: false, status: 500 })),
  nonsense: await call(async () => ({ ok: true, json: async () => ({ status: "ok" }) })),
  working: await call(async () => ({ ok: true, json: async () => ({ status: "ok", bibs: [{ name: "ml" }] }) })),
}));
''',
        tmp_path,
    )

    # No failure is reported as an empty library.
    for case in ("down", "rejected", "broken", "nonsense"):
        assert "returned" not in raised[case], (case, raised[case])
    assert "re-pair the extension" in raised["rejected"]["message"]
    assert "HTTP 500" in raised["broken"]["message"]
    assert raised["working"]["returned"] == [{"name": "ml"}]

    # And the popup shows the reason rather than an empty dropdown.
    shown = _run_popup_js_test(
        r'''
const elements = new Map();
const makeElement = (id) => ({
  id, value: "", checked: false, disabled: false, textContent: "", innerHTML: "",
  className: "", type: "", style: { cssText: "" }, children: [], handlers: {},
  appendChild(child) { this.children.push(child); },
  addEventListener(event, handler) { (this.handlers[event] ??= []).push(handler); },
  querySelectorAll: () => [],
});
globalThis.document = {
  getElementById: (id) => {
    if (!elements.has(id)) elements.set(id, makeElement(id));
    return elements.get(id);
  },
  createElement: () => makeElement("created"),
};
globalThis.window = { open: () => {} };
globalThis.chrome = {
  storage: {
    local: { get: async () => ({}), set: async () => ({}), remove: async () => ({}) },
    session: { get: async () => ({}), set: async () => ({}), remove: async () => ({}) },
  },
  tabs: { query: async () => [] },
  runtime: { sendMessage: () => {} },
};
globalThis.__fetchBibsError = "pzi server is not reachable";
let unhandled = null;
process.on("unhandledRejection", (reason) => { unhandled = String(reason); });
await import("./popup.js");
for (let i = 0; i < 20; i += 1) await new Promise((r) => setTimeout(r, 0));
console.log(JSON.stringify({
  summary: elements.get("summary").textContent,
  bibOptions: elements.get("bib").children.length,
  unhandled,
}));
''',
        tmp_path,
    )

    assert "pzi server is not reachable" in shown["summary"]
    assert shown["bibOptions"] == 0
    assert shown["unhandled"] is None, shown["unhandled"]


def test_opening_a_pdf_never_falls_back_to_the_unauthenticated_url(tmp_path: Path) -> None:
    """`window.open(url)` on failure is the same URL without the token, so the
    user got a tab containing `{"error":"invalid API token"}`.

    Driven rather than grepped: the property is "no failure path opens a tab",
    which no substring can express. The assertion this replaced looked for
    `window.open(url, "_blank")` — a string the bug does not contain — so it
    stayed green with the fallback restored verbatim.
    """
    result = _run_popup_js_test(
        r'''
const element = () => ({
  value: "", checked: false, disabled: false, textContent: "", innerHTML: "",
  style: {}, appendChild: () => {}, addEventListener: () => {}, querySelectorAll: () => [],
});
globalThis.document = { getElementById: () => element(), createElement: () => element() };
globalThis.chrome = {
  storage: {
    local: { get: async () => ({}), set: async () => ({}), remove: async () => ({}) },
    session: { get: async () => ({}), set: async () => ({}), remove: async () => ({}) },
  },
  tabs: { query: async () => [] },
  runtime: { sendMessage: () => {} },
};
globalThis.__authHeaders = { "X-Pzi-Token": "tok" };
globalThis.opens = [];
globalThis.window = { open: (url, target) => { globalThis.opens.push({ url, target }); } };
const NativeURL = URL;
globalThis.URL = class extends NativeURL {
  static createObjectURL() { return "blob:pzi-pdf"; }
  static revokeObjectURL() {}
};
const mod = await import("./popup.js");

// Every way the server can refuse. None of them may end in an open tab.
const fetched = [];
const respondWith = (make) => {
  globalThis.fetch = async (url, options = {}) => {
    fetched.push({ url, headers: options.headers || {} });
    return make();
  };
};

respondWith(() => ({ ok: false, status: 401 }));
const unauthorized = await mod.openPdf("smith2024paper", "main");

respondWith(() => ({ ok: false, status: 404 }));
const missing = await mod.openPdf("smith2024paper", "main");

respondWith(() => { throw new TypeError("Failed to fetch"); });
const down = await mod.openPdf("smith2024paper", "main");

console.log(JSON.stringify({ unauthorized, missing, down, opens: globalThis.opens, fetched }));
''',
        tmp_path,
    )

    # The whole property, independent of how a fallback might be spelled.
    assert result["opens"] == [], f"a failure path opened a tab: {result['opens']}"

    # The URL a fallback would have leaked: same path, no token header.
    assert [call["url"] for call in result["fetched"]] == [
        "http://127.0.0.1:8765/pdf/smith2024paper"
    ] * 3
    assert all(call["headers"] == {"X-Pzi-Token": "tok"} for call in result["fetched"])

    # And each refusal is reported to the caller instead.
    assert result["unauthorized"] == {
        "ok": False,
        "message": "could not open the PDF (HTTP 401)",
    }
    assert result["missing"] == {"ok": False, "message": "no PDF stored for smith2024paper"}
    assert result["down"]["ok"] is False
    assert "not reachable" in result["down"]["message"]


# The onboarding page with a settings store that remembers, so a save can be
# checked against what was already there.
_ONBOARDING_DOM = r'''
const elements = new Map();
const makeElement = (id) => ({
  id, value: "", textContent: "", innerHTML: "", disabled: false,
  style: {}, children: [], handlers: {},
  appendChild(child) { this.children.push(child); },
  addEventListener(event, handler) { (this.handlers[event] ??= []).push(handler); },
});
globalThis.document = {
  getElementById: (id) => {
    if (!elements.has(id)) elements.set(id, makeElement(id));
    return elements.get(id);
  },
  createElement: () => makeElement("created"),
};
globalThis.stored = { endpoint: "http://127.0.0.1:8765/capture", authToken: "saved-by-a-previous-run" };
const local = {
  get: async (keys) => {
    const out = {};
    for (const key of [].concat(keys)) if (key in globalThis.stored) out[key] = globalThis.stored[key];
    return out;
  },
  set: async (values) => { Object.assign(globalThis.stored, values); return {}; },
  remove: async () => ({}),
};
globalThis.chrome = {
  storage: { local, session: local, onChanged: { addListener: () => {}, removeListener: () => {} } },
  runtime: { sendMessage: () => {} },
};
globalThis.fetch = async () => ({ ok: true, status: 200, json: async () => ({ status: "ok", bibs: [] }) });
const openOnboarding = async () => {
  await import("./onboarding.js");
  for (let i = 0; i < 20; i += 1) await new Promise((r) => setTimeout(r, 0));
};
const clickSave = async () => {
  await elements.get("save").handlers.click[0]();
  for (let i = 0; i < 20; i += 1) await new Promise((r) => setTimeout(r, 0));
};
'''


def test_onboarding_does_not_erase_a_token_it_was_not_given(tmp_path: Path) -> None:
    """Saving with an empty token box means "I did not type one".

    The popup carries exactly this rule and says so; onboarding wrote whatever
    the box held, so changing only the endpoint silently unpaired the
    extension, and every later request 401'd until the token was retyped.
    """
    result = _run_onboarding_module(
        _ONBOARDING_DOM
        + r'''
await openOnboarding();
// The user clears the token box and edits only the endpoint.
elements.get("token").value = "";
elements.get("endpoint").value = "http://127.0.0.1:9999/capture";
await clickSave();
console.log(JSON.stringify({ stored: globalThis.stored, status: elements.get("status").textContent }));
''',
        tmp_path,
    )

    assert result["stored"]["authToken"] == "saved-by-a-previous-run", result["stored"]
    assert result["stored"]["endpoint"] == "http://127.0.0.1:9999/capture", result["stored"]


def test_onboarding_still_saves_a_token_the_user_typed(tmp_path: Path) -> None:
    """The guard must not become a way to refuse a new token."""
    result = _run_onboarding_module(
        _ONBOARDING_DOM
        + r'''
await openOnboarding();
elements.get("token").value = "  a-freshly-pasted-token  ";
await clickSave();
console.log(JSON.stringify({ stored: globalThis.stored }));
''',
        tmp_path,
    )

    assert result["stored"]["authToken"] == "a-freshly-pasted-token", result["stored"]


def test_onboarding_refuses_an_endpoint_the_extension_would_discard(
    tmp_path: Path,
) -> None:
    """`getEndpoint` accepts only a loopback URL and silently falls back.

    So a remote endpoint was stored, reported as "Settings saved", and then
    "Test connection" — which resolves through `getEndpoint` — tested the
    *default*, succeeded, and said the server was reachable. The user was told
    twice that a setting had taken effect after it was discarded.
    """
    result = _run_onboarding_module(
        _ONBOARDING_DOM
        + r'''
await openOnboarding();
elements.get("endpoint").value = "https://pzi.example.com/capture";
await clickSave();
const status = elements.get("status");
console.log(JSON.stringify({
  stored: globalThis.stored,
  status: status.textContent,
  background: status.style.background,
}));
''',
        tmp_path,
    )

    # Not stored, because storing it would be a setting that does nothing.
    assert result["stored"]["endpoint"] == "http://127.0.0.1:8765/capture", result["stored"]
    # And said so, rather than claiming success.
    assert "not saved" in result["status"].lower(), result["status"]
    assert "loopback" in result["status"].lower(), result["status"]
    assert result["background"] == "#ffebee", result["background"]
    # The token survives a rejected endpoint too.
    assert result["stored"]["authToken"] == "saved-by-a-previous-run", result["stored"]


def test_onboarding_reports_an_unreachable_server(tmp_path: Path) -> None:
    """`fetchBibs` throws now; the popup got the catch and onboarding did not.

    A first run against a stopped server showed an empty dropdown, an empty
    status box, and an unhandled rejection — on the one page whose job is to
    tell a new user what is wrong.

    Driven rather than grepped: the assertion this replaced was the bare word
    `catch` appearing somewhere in the function, which says nothing about
    whether the user is told anything.
    """
    result = _run_onboarding_module(
        r'''
const elements = new Map();
const makeElement = (id) => ({
  id, value: "", textContent: "", innerHTML: "", disabled: false,
  style: {}, children: [], handlers: {},
  appendChild(child) { this.children.push(child); },
  addEventListener(event, handler) { (this.handlers[event] ??= []).push(handler); },
});
globalThis.document = {
  getElementById: (id) => {
    if (!elements.has(id)) elements.set(id, makeElement(id));
    return elements.get(id);
  },
  createElement: () => makeElement("created"),
};
globalThis.chrome = {
  storage: {
    local: { get: async () => ({}), set: async () => ({}) },
    session: { get: async () => ({}), set: async () => ({}) },
  },
  runtime: { sendMessage: () => {} },
};
globalThis.__fetchBibsError = "pzi server is not reachable — is `pzi server` running?";
let unhandled = null;
process.on("unhandledRejection", (reason) => { unhandled = String(reason); });
await import("./onboarding.js");
for (let i = 0; i < 20; i += 1) await new Promise((r) => setTimeout(r, 0));
const status = elements.get("status");
console.log(JSON.stringify({
  status: status.textContent,
  background: status.style.background,
  bibOptions: elements.get("bib").children.length,
  unhandled,
}));
''',
        tmp_path,
    )

    assert "pzi server is not reachable" in result["status"]
    # Shown as a failure, not a neutral note.
    assert result["background"] == "#ffebee"
    assert result["bibOptions"] == 0
    assert result["unhandled"] is None, result["unhandled"]


def test_a_page_cannot_forge_publisher_trust_by_hostname(tmp_path: Path) -> None:
    """The gate is decided from the passed URL, in the extension's own realm.

    `evil-ieeexplore.ieee.org` passes `host.endsWith("ieeexplore.ieee.org")`,
    and in the MAIN world the page can replace `endsWith` outright.
    """
    result = _run_background_module(
        r'''
const mod = await import("./background/metadata.mjs");
console.log(JSON.stringify({
  real: mod.isTrustedPublisherUrl("https://ieeexplore.ieee.org/document/1"),
  subdomain: mod.isTrustedPublisherUrl("https://www.ieeexplore.ieee.org/document/1"),
  lookalike: mod.isTrustedPublisherUrl("https://evil-ieeexplore.ieee.org/x"),
  suffix_trick: mod.isTrustedPublisherUrl("https://attacker.test/ieeexplore.ieee.org"),
  garbage: mod.isTrustedPublisherUrl("not a url"),
}));
''',
        tmp_path,
    )

    assert result["real"] is True
    assert result["subdomain"] is True
    assert result["lookalike"] is False
    assert result["suffix_trick"] is False
    assert result["garbage"] is False


def test_context_menu_capture_only_forwards_same_origin_cookies(tmp_path: Path) -> None:
    r"""A right-clicked link to another origin carries no session.

    The URL was gated only by `/^https?:\/\//i`, so a link to
    `http://127.0.0.1:9999/x` had this machine's loopback cookies read and
    transmitted before the server rejected the capture.
    """
    result = _run_background_module(
        r'''
const bodies = [];
globalThis.chrome = {
  storage: {
    local: { get: async () => ({ endpoint: "http://127.0.0.1:8765/capture" }) },
    session: { get: async () => ({}), set: () => {} },
  },
  runtime: { onInstalled: { addListener: () => {} } },
  action: {
    setBadgeText: () => Promise.resolve(),
    setBadgeBackgroundColor: () => Promise.resolve(),
  },
  cookies: { getAll: async () => [{ name: "session", value: "SECRET" }] },
  contextMenus: { create: () => {}, onClicked: { addListener: () => {} } },
};
globalThis.fetch = async (_url, options) => {
  bodies.push(JSON.parse(options.body));
  return { ok: true, json: async () => ({ status: "ok", citekey: "k" }) };
};
const mod = await import("./background.js");
const tab = { id: 3, url: "https://publisher.test/article" };
await mod._handleContextMenuCapture({ linkUrl: "https://publisher.test/paper.pdf" }, tab);
await mod._handleContextMenuCapture({ linkUrl: "https://elsewhere.test/paper.pdf" }, tab);
await mod._handleContextMenuCapture({ linkUrl: "http://127.0.0.1:9999/x" }, tab);
console.log(JSON.stringify({ bodies }));
''',
        tmp_path,
    )

    bodies = result["bodies"]
    # The loopback link was refused outright, so only two requests were made.
    assert len(bodies) == 2, bodies
    # Same origin as the tab: the session the capture is entitled to reuse.
    assert bodies[0]["cookies"]
    # A different domain the user is not on: none.
    assert not bodies[1]["cookies"]
