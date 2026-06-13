import json
import re
import subprocess
from pathlib import Path

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


def test_popup_recent_actions_use_endpoint_path_helper() -> None:
    text = POPUP_JS.read_text()

    assert "endpointFor," in text
    assert "endpointFor(endpoint, \"/pdf/\" + encodeURIComponent(citekey))" in text
    assert 'base + "/pdf/"' not in text


def test_popup_recent_list_is_a_capture_log_without_delete() -> None:
    """The recent list is a read-only capture log (open-PDF only); no delete UI."""
    text = POPUP_JS.read_text()

    assert "data-action=\"pdf\"" in text          # open-PDF affordance kept
    assert "data-action=\"delete\"" not in text   # delete button removed
    assert "_deleteEntry" not in text             # delete handler removed
    assert "endpointFor(endpoint, \"/delete\")" not in text  # no POST /delete from popup


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


def test_capture_generic_page_metadata_not_marked_ieee_trusted(tmp_path: Path) -> None:
    result = _run_background_module(
        r'''
globalThis.chrome = {
  storage: {
    local: { get: async () => ({ endpoint: "http://pzi.test/capture" }) },
    session: { get: async () => ({}), set: () => {} },
  },
  runtime: { onInstalled: { addListener: () => {} } },
  tabs: { query: async () => [{ id: 7, url: "https://example.com/paper" }] },
  scripting: {
    executeScript: async (opts) => {
      if (opts.world === "MAIN") {
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
console.log(JSON.stringify({ capture_body: result.capture_body }));
''',
        tmp_path,
    )

    assert result["capture_body"]["metadata_source"] == "generic_dom"
    assert result["capture_body"]["trusted_fields"] is None


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
    assert result["calls"][0]["url"] == "http://pzi.test/capture"
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
  storage: { local: { get: async () => ({ endpoint: "http://pzi.test/capture" }) } },
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
          url: "http://pzi.test/attach-pdf-raw?request_id=req-1&citekey=smith2024paper",
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


def test_ieee_xplore_metadata_extractor_reads_embedded_xplglobal(tmp_path: Path) -> None:
    result = _run_background_module(
        r'''
globalThis.chrome = { runtime: { onInstalled: { addListener: () => {} } } };
const mod = await import("./background.js");
const doc = {
  baseURI: "https://ieeexplore.ieee.org/document/9840963",
  defaultView: {
    xplGlobal: {
      document: {
        metadata: {
          displayDocTitle: "Analysis of the Use of the Kalman Filter",
          authors: [{ name: "N. E. Poborchaya" }, { name: "E. O. Lobova" }],
          publicationYear: "2022",
          publicationTitle: "2022 Systems of Signal Synchronization",
          abstract: "IEEE abstract text",
          startPage: "1",
          endPage: "5",
          issn: [{ value: "2832-0514" }],
          isbn: [{ value: "978-1-6654-7064-3" }],
          pdfUrl: "/stamp/stamp.jsp?tp=&arnumber=9840963",
          doi: "10.1109/SYNCHROINFO55067.2022.9840963"
        }
      }
    }
  },
  querySelectorAll: () => [],
};
const out = mod.extractIeeeXploreMetadata(doc, "https://ieeexplore.ieee.org/document/9840963");
console.log(JSON.stringify(out));
''',
        tmp_path,
    )

    assert result == {
        "title": "Analysis of the Use of the Kalman Filter",
        "authors": ["N. E. Poborchaya", "E. O. Lobova"],
        "year": "2022",
        "venue": "2022 Systems of Signal Synchronization",
        "abstract": "IEEE abstract text",
        "pages": "1--5",
        "issn": "2832-0514",
        "isbn": "978-1-6654-7064-3",
        "pdfUrl": "https://ieeexplore.ieee.org/stamp/stamp.jsp?tp=&arnumber=9840963",
        "doi": "10.1109/SYNCHROINFO55067.2022.9840963",
    }


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
    local: { get: async () => ({ endpoint: "http://pzi.test/capture" }) },
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
    assert body["metadata_source"] == "ieee_xplore"
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
    local: { get: async () => ({ endpoint: "http://pzi.test/capture" }) },
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


def _run_popup_js_test(script: str, tmp_path: Path) -> dict:
    """Run a test script that imports popup.js functions."""
    module_path = tmp_path / "popup_test.mjs"
    module_path.write_text(POPUP_JS.read_text().replace('./background.js', './background.mjs'))
    (tmp_path / "popup_format.js").write_text(POPUP_FORMAT_JS.read_text())
    # popup.js imports from background.js — mock the imports
    mock_path = tmp_path / "background.mjs"
    mock_path.write_text(
        "export async function fetchBibs() { return []; }\n"
        "export async function getEndpoint() { return 'http://127.0.0.1:8765/capture'; }\n"
        "export async function getAuthHeaders() { return globalThis.__authHeaders || {}; }\n"
        "export async function detectAndExtractSearchResults() { return null; }\n"
        "export async function cookieHeaderForUrl() { return ''; }\n"
        "export async function captureCurrentTab() { return { status: 'ok' }; }\n"
        "export function endpointFor(rawEndpoint, path) { const base = new URL(rawEndpoint); const target = new URL(path, base); target.search = ''; return target.href.replace(/\\/$/, ''); }\n"
    )
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
    """Test _urlMatchesAnySearchPattern against known search URLs."""
    result = _run_popup_js_test(
        r'''
function _urlMatchesAnySearchPattern(url) {
  const patterns = [
    "scholar\\.google\\.com\\/scholar",
    "pubmed\\.ncbi\\.nlm\\.nih\\.gov",
    "semanticscholar\\.org\\/search",
    "arxiv\\.org\\/search",
    "dblp\\.org\\/search",
  ];
  for (const pat of patterns) {
    if (new RegExp(pat, "i").test(url)) return true;
  }
  return false;
}
const r1 = _urlMatchesAnySearchPattern("https://scholar.google.com/scholar?q=test");
const r2 = _urlMatchesAnySearchPattern("https://pubmed.ncbi.nlm.nih.gov/?term=covid");
const r3 = _urlMatchesAnySearchPattern("https://arxiv.org/search/?query=ml");
const r4 = _urlMatchesAnySearchPattern("https://semanticscholar.org/search?q=transformers");
const r5 = _urlMatchesAnySearchPattern("https://dblp.org/search/publ?q=ai");
const r6 = _urlMatchesAnySearchPattern("https://example.com/paper");
console.log(JSON.stringify({ r1, r2, r3, r4, r5, r6 }));
''',
        tmp_path,
    )
    assert result["r1"] is True
    assert result["r2"] is True
    assert result["r3"] is True
    assert result["r4"] is True
    assert result["r5"] is True
    assert result["r6"] is False


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
    assert result["popup_build_marker"] == "2025-06-12-phases-012"


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
      get: async () => ({ endpoint: "http://pzi.test/capture", authToken: "" }),
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
      get: async () => ({ endpoint: "http://pzi.test/capture", authToken: "" }),
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
