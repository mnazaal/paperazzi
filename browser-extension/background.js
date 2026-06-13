// ---------- Imports from split modules ----------
import {
  EXTENSION_VERSION,
  setCaptureTabId,
  getEndpoint,
  getAuthHeaders,
  getStoredConfig,
  fetchBibs,
  detectBrowser,
  endpointFor,
} from "./background/config.js";

import {
  originOf,
  sameOrigin,
  candidateUrl,
  normalizeMetadataUrl,
  normalizeDoi,
  jsonOrNull,
  responseErrors,
  isSafePublicHttpUrl,
  filterStalePdfWarnings,
  doiFromKnownPreprintUrl,
} from "./background/utils.js";

import {
  cookieHeaderForUrl,
  requestPdfOriginPermissions,
} from "./background/permissions.js";

import {
  extractPageMetadata,
  extractIeeeXploreMetadata,
} from "./background/metadata.js";

import {
  startPdfObserver,
  stopPdfObserver,
  collectObservedPdfUrls,
  collectPdfObserverEvents,
  addObserverEntry,
} from "./background/observer.js";

import {
  scanDomForPdfUrls,
  extractPdfUrlCandidates,
  clickPdfDiscovery,
  buildPdfCandidates,
  isBotBypassWhitelisted,
} from "./background/pdf_discovery.js";

import {
  maybeStreamPdfBytes,
  botBypassPdfUrl,
} from "./background/pdf_fetch.js";

// Re-export for external consumers (tests, popup)
export { getEndpoint, getAuthHeaders, fetchBibs, detectBrowser, endpointFor };
export { isSafePublicHttpUrl };
export { cookieHeaderForUrl };
export { extractIeeeXploreMetadata };
export { detectAndExtractSearchResults, captureSearchResults } from "./background/search.js";
export { startPdfObserver, collectPdfObserverEvents };
export { scanDomForPdfUrls, clickPdfDiscovery, isBotBypassWhitelisted };
export { botBypassPdfUrl };

export async function captureCurrentTab({ tags = [], bib = null, dryRun = false, tabId = null, tabUrl = null, forceNew = false } = {}) {
  let tab;
  if (tabId != null && tabUrl != null) {
    tab = { id: tabId, url: tabUrl };
  } else {
    [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  }
  if (!tab || !tab.url) {
    return { status: "error", errors: ["no active tab"] };
  }
  setCaptureTabId(tab.id);

  // Progress: scanning page for metadata and PDF links.
  chrome.storage.session?.set?.({ "pzi:captureStage": "extracting" });

  // Start network observer before extraction to catch PDF redirects/dynamic loads.
  startPdfObserver(tab.id);

  const pageMetadata = await extractPageMetadata(tab.id, tab.url);
  pageMetadata.canonicalUrl = normalizeMetadataUrl(pageMetadata.canonicalUrl, tab.url) || tab.url;
  pageMetadata.sourceUrl = normalizeMetadataUrl(pageMetadata.sourceUrl, tab.url) || tab.url;
  pageMetadata.abstractUrl = normalizeMetadataUrl(pageMetadata.abstractUrl, tab.url) || pageMetadata.canonicalUrl || tab.url;
  pageMetadata.doi = normalizeDoi(pageMetadata.doi) || doiFromKnownPreprintUrl(tab.url);
  const pdfUrlCandidates = await extractPdfUrlCandidates(tab.id, tab.url);
  if (typeof pageMetadata.embedded_pdf_url === "string" && pageMetadata.embedded_pdf_url.trim()) {
    const embeddedPdfUrl = normalizeMetadataUrl(pageMetadata.embedded_pdf_url, tab.url);
    if (embeddedPdfUrl && !pdfUrlCandidates.includes(embeddedPdfUrl)) {
      pdfUrlCandidates.push(embeddedPdfUrl);
    }
  }

  // Tier 3: click-based PDF discovery — try clicking "PDF" / "Download PDF" buttons.
  const clickPdfUrls = await clickPdfDiscovery(tab.id, tab.url);
  for (const u of clickPdfUrls) {
    if (!pdfUrlCandidates.includes(u)) pdfUrlCandidates.push(u);
  }

  // Collect network-observed PDF URLs and stop observer.
  const observedUrls = collectObservedPdfUrls();
  stopPdfObserver();
  for (const u of observedUrls) {
    if (!pdfUrlCandidates.includes(u)) pdfUrlCandidates.push(u);
  }

  const pdfCandidates = buildPdfCandidates(pdfUrlCandidates, tab.url, observedUrls);
  const pdfOriginPermissions = dryRun ? new Map() : await requestPdfOriginPermissions(pdfCandidates, tab.url);
  const endpoint = await getEndpoint();
  const authHeaders = await getAuthHeaders();
  // Extract browser cookies for the page domain (Tier 1 cookie bridge).
  // Progress: sending capture request to pzi server.
  chrome.storage.session?.set?.({ "pzi:captureStage": "fetching" });

  const cookieHeader = await cookieHeaderForUrl(tab.url);
  const captureBody = {
    url: tab.url,
    browser: detectBrowser(),
    extension_version: EXTENSION_VERSION,
    tags,
    bib,
    dry_run: dryRun,
    verbose: true,
    force_new: forceNew,
    cookies: cookieHeader != null ? "<redacted>" : null,
    pdf_url_candidates: pdfUrlCandidates,
    pdf_candidates: pdfCandidates.map(c => typeof c === "string" ? c : { url: candidateUrl(c), source: c.source, same_origin: c.same_origin, requires_cookies: c.requires_cookies }),
    page_title: pageMetadata.pageTitle,
    canonical_url: pageMetadata.canonicalUrl,
    source_url: pageMetadata.sourceUrl,
    abstract_url: pageMetadata.abstractUrl,
    doi: pageMetadata.doi,
    head_html: pageMetadata.headHtml || null,
    // Rich embedded metadata
    embedded_authors: pageMetadata.embedded_authors,
    embedded_year: pageMetadata.embedded_year,
    embedded_venue: pageMetadata.embedded_venue,
    embedded_abstract: pageMetadata.embedded_abstract,
    embedded_volume: pageMetadata.embedded_volume,
    embedded_issue: pageMetadata.embedded_issue,
    embedded_pages: pageMetadata.embedded_pages,
    embedded_issn: pageMetadata.embedded_issn,
    embedded_isbn: pageMetadata.embedded_isbn,
    embedded_pdf_url: pageMetadata.embedded_pdf_url,
    embedded_jsonld_authors: pageMetadata.embedded_jsonld_authors,
    embedded_jsonld_title: pageMetadata.embedded_jsonld_title,
    embedded_jsonld_year: pageMetadata.embedded_jsonld_year,
    embedded_og_title: pageMetadata.embedded_og_title,
    metadata_source: pageMetadata.metadata_source,
    trusted_fields: pageMetadata.trusted_fields,
  };
  const response = await fetch(endpoint, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders },
    body: JSON.stringify({...captureBody, cookies: cookieHeader, pdf_candidates: pdfCandidates}),
  });
    const result = await jsonOrNull(response);
    result.extension_version = EXTENSION_VERSION;
    if (!response.ok) {
    return {
      status: "error",
      extension_version: EXTENSION_VERSION,
      capture_body: captureBody,
      errors: responseErrors(result, `capture request failed: HTTP ${response.status} ${response.statusText || ""}`.trim()),
    };
  }
  if (!result) {
    return { status: "error", extension_version: EXTENSION_VERSION, capture_body: captureBody, errors: ["capture request failed: invalid JSON response"] };
  }
  if (!dryRun && result && result.status === "ok" && result.citekey && !result.pdf_path) {
    result.pdf_candidates_debug = pdfCandidates;
    // Progress: server returned metadata; checking for PDF.
    chrome.storage.session?.set?.({ "pzi:captureStage": "processing" });

    // Feed server-discovered pdf_url into browser fetch candidates (Fix 3).
    if (result.pdf_url && typeof result.pdf_url === "string") {
      const serverPdfUrl = result.pdf_url;
      if (!pdfCandidates.some(c => candidateUrl(c) === serverPdfUrl)) {
        pdfCandidates.unshift({
          url: serverPdfUrl,
          source: "server_discovery",
          origin: originOf(serverPdfUrl),
          same_origin: sameOrigin(serverPdfUrl, tab.url),
          requires_permission: !sameOrigin(serverPdfUrl, tab.url),
          requires_cookies: false,
          confidence: 50,
        });
      }
    }
    // Progress: downloading PDF bytes with browser session.
    chrome.storage.session?.set?.({ "pzi:captureStage": "downloading" });

    const pdfAttach = result.pdf_request
      ? await maybeStreamPdfBytes({
          endpoint,
          citekey: result.citekey,
          bib,
          pdfUrlCandidates: result.pdf_request.candidates || [],
          pageUrl: tab.url,
          originPermissions: pdfOriginPermissions,
          pdfRequest: result.pdf_request,
        })
      : await maybeStreamPdfBytes({
          endpoint,
          citekey: result.citekey,
          bib,
          pdfUrlCandidates: pdfCandidates,
          pageUrl: tab.url,
          originPermissions: pdfOriginPermissions,
        });
    // Always surface attempt details, including failed attach attempts.
    if (pdfAttach.pdf_attach_attempts) {
      result.pdf_attach_attempts = pdfAttach.pdf_attach_attempts;
    }
    if (pdfAttach.pdf_attach_permission) {
      result.pdf_attach_permission = pdfAttach.pdf_attach_permission;
    }
    if (pdfAttach.pdf_attach_cookie_permission) {
      result.pdf_attach_cookie_permission = pdfAttach.pdf_attach_cookie_permission;
    }
    if (pdfAttach.status === "ok") {
      result.pdf_attach = pdfAttach;
      delete pdfAttach.pdf_attach_attempts;
      result.warnings = filterStalePdfWarnings(result.warnings, {
        attachedUrl: pdfAttach.source_url,
        staleError: result.pdf_error,
        attemptedUrls: [tab.url, ...pdfUrlCandidates],
      });
    }
  }
  result.capture_body = captureBody;
  return result;
}

chrome.runtime.onInstalled.addListener(async (details) => {
  console.log("pzi capture extension installed");

  // ── Context menu: right-click a link → "Save to pzi" ─────────────────
  if (typeof chrome !== "undefined" && chrome.contextMenus) {
    chrome.contextMenus.create({
      id: "pzi-capture-link",
      title: "Save to pzi",
      contexts: ["link"],
    });
  }

  // ── First-run onboarding ─────────────────────────────────────────────
  if (details.reason === "install") {
    const stored = await getStoredConfig("authToken");
    if (!stored.authToken) {
      try {
        chrome.tabs.create({ url: chrome.runtime.getURL("onboarding.html") });
      } catch (_e) { /* ignore */ }
    }
  }
});

// ── Context menu click handler ──────────────────────────────────────────
if (typeof chrome !== "undefined" && chrome.contextMenus) {
  chrome.contextMenus.onClicked.addListener((info, tab) => {
    if (info.menuItemId === "pzi-capture-link") {
      _handleContextMenuCapture(info, tab);
    }
  });
}

async function _handleContextMenuCapture(info, tab) {
  const url = info.linkUrl;
  if (!url || !/^https?:\/\//i.test(url)) return;

  _setBadge("…", "#FFA500");

  try {
    const endpoint = await getEndpoint();
    const authHeaders = await getAuthHeaders();
    const cookieHeader = await cookieHeaderForUrl(url);

    const response = await fetch(endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...authHeaders },
      body: JSON.stringify({
        url,
        browser: detectBrowser(),
        extension_version: EXTENSION_VERSION,
        cookies: cookieHeader,
        page_url: tab?.url || null,
      }),
    });

    const result = await jsonOrNull(response);

    if (result && result.status === "ok") {
      _setBadge("✓", "#4CAF50");
    } else {
      _setBadge("✗", "#F44336");
    }

    setTimeout(_clearBadge, 5000);
  } catch (_error) {
    _setBadge("✗", "#F44336");
    setTimeout(_clearBadge, 5000);
  }
}

// ── Always-on PDF observer ──────────────────────────────────────────────
// Passively records application/pdf responses from all tabs.
// Observes main_frame, sub_frame, and xmlhttprequest.
// Cache is domain-scoped with 3-minute TTL.
(function _registerAlwaysOnPdfObserver() {
  if (typeof chrome === "undefined" || !chrome.webRequest || !chrome.webRequest.onHeadersReceived) return;

  try {
    chrome.webRequest.onHeadersReceived.addListener(
      (details) => {
        const headers = details.responseHeaders || [];
        for (const h of headers) {
          if (h.name.toLowerCase() === "content-type" && h.value && h.value.toLowerCase().includes("application/pdf")) {
            addObserverEntry(details.url, details.tabId);
            break;
          }
        }
      },
      { urls: ["https://*/*", "http://*/*"] },
      ["responseHeaders"],
    );
  } catch (_e) {
    /* webRequest unavailable or lacks permissions */
  }
})();

// ── Badge helpers ───────────────────────────────────────────────────────
function _setBadge(text, color) {
  if (typeof chrome === "undefined" || !chrome.action) return;
  chrome.action.setBadgeText({ text }).catch(() => {});
  chrome.action.setBadgeBackgroundColor({ color }).catch(() => {});
}

function _clearBadge() {
  if (typeof chrome === "undefined" || !chrome.action) return;
  chrome.action.setBadgeText({ text: "" }).catch(() => {});
}

// ── Popup-to-background bridge ──────────────────────────────────────────
// Popup sends a "pzi:capture" message with {tags,bib,dryRun,tabId,tabUrl}.
// Background runs the full capture pipeline, stores result in session
// storage, and manages the badge.  This keeps capture alive after popup
// close (MV3 service-worker persistence).
if (typeof chrome !== "undefined" && chrome.runtime && chrome.runtime.onMessage) {
  chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
    if (message && message.type === "pzi:capture") {
      _handlePziCapture(message, sendResponse);
      return true; // async — keep channel open
    }
    return false;
  });
}

async function _handlePziCapture(message, sendResponse) {
  const { tags, bib, dryRun, tabId, tabUrl, forceNew } = message;

  _setBadge("…", "#FFA500");

  try {
    await chrome.storage.session.set({ "pzi:captureInProgress": true });

    const result = await captureCurrentTab({ tags, bib, dryRun, tabId, tabUrl, forceNew });

    await chrome.storage.session.set({
      "pzi:lastCapture": result,
      "pzi:captureInProgress": false,
      "pzi:captureStage": null,
    });

    if (result && result.status === "ok") {
      _setBadge("✓", "#4CAF50");
    } else {
      _setBadge("✗", "#F44336");
    }

    sendResponse({ status: "received" });
  } catch (err) {
    await chrome.storage.session.set({
      "pzi:lastCapture": { status: "error", message: err?.message || String(err) },
      "pzi:captureInProgress": false,
      "pzi:captureStage": null,
    });
    _setBadge("✗", "#F44336");
    sendResponse({ status: "error", message: err?.message || String(err) });
  }
}
