// pzi browser extension — PDF network observers and observed-URL cache.
//
// Holds the per-capture observer (startPdfObserver/stopPdfObserver), which
// records application/pdf responses during a single capture window.
//
// There is deliberately no always-on cache of PDFs seen earlier. The service
// worker could fill one, but the popup runs `captureCurrentTab` in its own
// realm with its own module instances (Firefox grants an optional host
// permission only inside the click's gesture), so it could never read it.
//
// Requires the optional "webRequest" permission + host permissions.

// ── Per-capture network PDF observer ──────────────────────────────────────
// Listens for PDF content-type responses during a capture window.
let _pdfObserverUrls = [];
let _pdfObserverEvents = [];
let _pdfObserverListener = null;

export function startPdfObserver(tabId) {
  stopPdfObserver();
  _pdfObserverUrls = [];
  _pdfObserverEvents = [];
  if (typeof chrome === "undefined") {
    _pdfObserverEvents.push({ note: "chrome_undefined" });
    return;
  }
  if (!chrome.webRequest) {
    _pdfObserverEvents.push({ note: "webRequest_unavailable" });
    return;
  }
  if (!chrome.webRequest.onHeadersReceived) {
    _pdfObserverEvents.push({ note: "onHeadersReceived_unavailable" });
    return;
  }

  _pdfObserverListener = (details) => {
    // Only the tab being captured. `tabId === -1` is a request belonging to no
    // tab, which for this extension means its *own* background fetches — so
    // accepting it fed candidate downloads back as fresh discoveries, and a URL
    // that had just failed reappeared as something newly found.
    if (details.tabId !== tabId) return;
    const headers = details.responseHeaders || [];
    const ct = headers.find((h) => h.name.toLowerCase() === "content-type");
    const cd = headers.find((h) => h.name.toLowerCase() === "content-disposition");
    const event = {
      url: details.url,
      tab_id: details.tabId,
      type: details.type || null,
      status_code: details.statusCode || null,
      content_type: ct?.value || null,
      content_disposition: cd?.value || null,
    };
    _pdfObserverEvents.push(event);
    if (_observerEventLooksLikePdf(event)) {
      _pdfObserverUrls.push(details.url);
    }
  };

  try {
    chrome.webRequest.onHeadersReceived.addListener(
      _pdfObserverListener,
      {
        urls: ["https://*/*", "http://*/*"],
      },
      ["responseHeaders"],
    );
  } catch (_e) {
    /* webRequest unavailable or lacks host permissions */
  }
}

export function stopPdfObserver() {
  if (_pdfObserverListener && typeof chrome !== "undefined" && chrome.webRequest) {
    try {
      chrome.webRequest.onHeadersReceived.removeListener(_pdfObserverListener);
    } catch (_e) {
      /* ignore */
    }
  }
  _pdfObserverListener = null;
}

export function collectObservedPdfUrls() {
  return [...new Set(_pdfObserverUrls)];
}

export function collectPdfObserverEvents() {
  return _pdfObserverEvents.slice(-20);
}

function _observerEventLooksLikePdf(event) {
  // The content type is the only authoritative signal; the rest are heuristics
  // for servers that send `application/octet-stream`, and each one costs a
  // wasted fetch when it guesses wrong.
  const contentType = String(event.content_type || "").toLowerCase();
  if (contentType.includes("pdf")) return true;

  // `.pdf` in the disposition's filename, not the mere presence of a `filename`
  // parameter. That looser test made every attachment a publisher page offers —
  // a CSV export, a BibTeX citation, a supplement zip — into a PDF candidate.
  const disposition = String(event.content_disposition || "").toLowerCase();
  if (disposition.includes(".pdf")) return true;

  // The URL *path*, not the whole URL: `?file=paper.pdf` names a viewer, not a
  // PDF, and `/notes.pdf.html` is a web page.
  let path;
  try {
    path = new URL(String(event.url || "")).pathname.toLowerCase();
  } catch (_error) {
    return false;
  }
  return path.endsWith(".pdf") || path.includes("/stamppdf/");
}
