// pzi browser extension — the capture pipeline.
//
// Split out of `background.js` so the popup and the onboarding page can import
// `captureCurrentTab` without importing the service worker itself. That module
// registers a `contextMenus.onClicked` listener and an always-on `webRequest`
// observer at module level, and every extension page that imported it added
// another copy — so with the onboarding tab open, one right-click issued two
// `POST /capture`.

import {
  EXTENSION_VERSION,
  setCaptureTabId,
  getEndpoint,
  getAuthHeaders,
  detectBrowser,
} from "./config.js";
import {
  originOf,
  sameOrigin,
  candidateUrl,
  normalizeMetadataUrl,
  normalizeDoi,
  jsonOrNull,
  responseErrors,
  filterStalePdfWarnings,
  doiFromKnownPreprintUrl,
} from "./utils.js";
import {
  cookieHeaderForUrl,
  releasePdfOriginPermissions,
  requestPdfOriginPermissions,
} from "./permissions.js";
import {
  extractPageMetadata,
} from "./metadata.js";
import {
  startPdfObserver,
  stopPdfObserver,
  collectObservedPdfUrls,
} from "./observer.js";
import {
  extractPdfUrlCandidates,
  addPdfUrlCandidate,
  clickPdfDiscovery,
  buildPdfCandidates,
} from "./pdf_discovery.js";
import {
  maybeStreamPdfBytes,
} from "./pdf_fetch.js";

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
  // Every append below goes through `addPdfUrlCandidate`, which is the single
  // place the 20-candidate cap and the public-URL check live. These three sites
  // used to push onto the array directly, so a page offering many PDF-ish links
  // sent an over-long list, and a loopback URL the observer had seen went out
  // unfiltered — and the server rejects the *whole* capture for either, losing
  // the metadata along with the PDF.
  const pdfUrlCandidates = await extractPdfUrlCandidates(tab.id, tab.url);
  if (typeof pageMetadata.embedded_pdf_url === "string" && pageMetadata.embedded_pdf_url.trim()) {
    addPdfUrlCandidate(pdfUrlCandidates, normalizeMetadataUrl(pageMetadata.embedded_pdf_url, tab.url));
  }

  // Tier 3: click-based PDF discovery — try clicking "PDF" / "Download PDF" buttons.
  const clickPdfUrls = await clickPdfDiscovery(tab.id, tab.url);
  for (const u of clickPdfUrls) addPdfUrlCandidate(pdfUrlCandidates, u);

  // Collect network-observed PDF URLs and stop observer.
  const observedUrls = collectObservedPdfUrls();
  stopPdfObserver();
  for (const u of observedUrls) addPdfUrlCandidate(pdfUrlCandidates, u);

  const pdfCandidates = buildPdfCandidates(pdfUrlCandidates, tab.url, observedUrls);
  const pdfOriginPermissions = dryRun ? new Map() : await requestPdfOriginPermissions(pdfCandidates, tab.url);
  // From here to the end in a `try`: the release below must run on the
  // early returns too (a non-ok capture response, an unparseable body),
  // which a tail call would skip — the exact bug `maybeStreamPdfBytes`
  // shipped for these same permissions.
  try {
    const endpoint = await getEndpoint();
    const authHeaders = await getAuthHeaders();
    // Extract browser cookies for the page domain (Tier 1 cookie bridge).
    // Progress: sending capture request to pzi server.
    chrome.storage.session?.set?.({ "pzi:captureStage": "fetching" });

    const cookieHeader = await cookieHeaderForUrl(tab.url);
    const captureBody = {
      url: tab.url,
      browser: detectBrowser(),
      tags,
      bib,
      dry_run: dryRun,
      // No `extension_version` or `metadata_source`: both went out on every
      // capture and were read by no route. The version is still reported back
      // to the popup on the result object below, where something does read it.
      verbose: true,
      force_new: forceNew,
      cookies: cookieHeader != null ? "<redacted>" : null,
      // `pdf_url_candidates` is the key the server reads. The parallel
      // `pdf_candidates` array (richer per-candidate objects) was computed and
      // sent on every capture and read by nothing on either side.
      pdf_url_candidates: pdfUrlCandidates,
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
      trusted_fields: pageMetadata.trusted_fields,
    };
    const response = await fetch(endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...authHeaders },
      body: JSON.stringify({...captureBody, cookies: cookieHeader}),
    });
      const result = await jsonOrNull(response);
      // Assigned *after* both guards. `jsonOrNull` returns null by design, so
      // doing this first threw `Cannot set properties of null` on exactly the
      // case the two branches below were written for — a proxy's HTML 502, a
      // truncated body — and made the `!result` guard unreachable.
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
    result.extension_version = EXTENSION_VERSION;
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
  } finally {
    // Hand back every origin permission this capture borrowed, including
    // ones granted upfront for candidates never attempted because an
    // earlier one succeeded.
    await releasePdfOriginPermissions(pdfOriginPermissions);
  }
}
