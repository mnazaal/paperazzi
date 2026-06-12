const DEFAULT_HOST = "http://127.0.0.1:8765";
const DEFAULT_ENDPOINT = `${DEFAULT_HOST}/capture`;
const PDF_FETCH_TIMEOUT_MS = 30000;
const MAX_ATTACH_PDF_BYTES = 47 * 1024 * 1024;
const EXTENSION_VERSION = "2025-06-12-phases-012"; // bump to verify extension is current
let _captureTabId = null;

// ── Always-on PDF observer cache ────────────────────────────────────────
// Continuously records application/pdf responses.  Used as primary
// PDF candidate source during capture (cross-tab, domain-scoped).
const OBSERVER_CACHE_TTL_MS = 3 * 60 * 1000;
const MAX_OBSERVER_ENTRIES = 50;
const _observerCache = [];

function _pruneObserverCache() {
  const now = Date.now();
  while (_observerCache.length > 0 && (now - _observerCache[0].timestamp) > OBSERVER_CACHE_TTL_MS) {
    _observerCache.shift();
  }
  while (_observerCache.length > MAX_OBSERVER_ENTRIES) {
    _observerCache.shift();
  }
}

function _getObserverUrlsForDomain(domain) {
  if (typeof domain !== "string" || !domain) return [];
  const now = Date.now();
  const urls = [];
  for (const entry of _observerCache) {
    if (entry.domain === domain && (now - entry.timestamp) < OBSERVER_CACHE_TTL_MS) {
      urls.push(entry.url);
    }
  }
  return urls;
}

function _addObserverEntry(url, tabId) {
  if (typeof url !== "string" || !url) return;
  let hostname;
  try { hostname = new URL(url).hostname; } catch (_e) { return; }
  _observerCache.push({ url, domain: hostname, tabId: tabId ?? -1, timestamp: Date.now() });
  _pruneObserverCache();
}

export async function getEndpoint() {
  const stored = await getStoredConfig("endpoint");
  return stored.endpoint || DEFAULT_ENDPOINT;
}

export async function getAuthHeaders() {
  const stored = await getStoredConfig("authToken");
  return stored.authToken ? { "X-Pzi-Token": stored.authToken } : {};
}

// Prefer session storage for popup/runtime choices, but still honor local
// storage for documented manual configuration on Chrome.
async function getStoredConfig(key) {
  const localStored = chrome.storage.local ? await chrome.storage.local.get(key) : {};
  if (!chrome.storage.session) return localStored;
  const sessionStored = await chrome.storage.session.get(key);
  return { ...localStored, ...sessionStored };
}

export async function fetchBibs() {
  const endpoint = await getEndpoint();
  const bibsUrl = endpointFor(endpoint, "/bibs");
  try {
    const response = await fetch(bibsUrl, { headers: await getAuthHeaders() });
    if (!response.ok) return [];
    const data = await response.json();
    if (data.status !== "ok" || !Array.isArray(data.bibs)) return [];
    return data.bibs;
  } catch (_err) {
    return [];
  }
}

function detectBrowser() {
  if (typeof browser !== 'undefined' && browser.runtime?.getBrowserInfo) {
    return 'firefox';
  }
  return 'chrome';
}

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
  _captureTabId = tab.id;

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
    head_html: pageMetadata.headHtml ? `${(pageMetadata.headHtml || "").length} bytes` : null,
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
    // Always surface attempt details even on failure (Fix 1).
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

function doiFromKnownPreprintUrl(pageUrl) {
  if (typeof pageUrl !== "string") return null;
  let parsed;
  try {
    parsed = new URL(pageUrl);
  } catch (_error) {
    return null;
  }
  const host = parsed.hostname.toLowerCase();
  if (!host.endsWith("biorxiv.org") && !host.endsWith("medrxiv.org")) return null;
  const match = parsed.pathname.match(/\/content\/(10\.\d{4,9}\/[^/?#]+)/i);
  if (!match) return null;
  return decodeURIComponent(match[1]).replace(/v\d+$/i, "");
}

function filterStalePdfWarnings(warnings, { attachedUrl = null, staleError = null, attemptedUrls = [] } = {}) {
  if (!Array.isArray(warnings)) return warnings;
  return warnings.filter((warning) => !isSupersededPdfWarning(warning, { attachedUrl, staleError, attemptedUrls }));
}

function isSupersededPdfWarning(warning, { attachedUrl = null, staleError = null, attemptedUrls = [] } = {}) {
  if (typeof warning !== "string") return false;
  if (!isPdfDownloadWarning(warning)) return false;
  if (staleError && warning === staleError) return true;
  if (attachedUrl && warning.includes(attachedUrl)) return true;
  if (attemptedUrls.some((url) => typeof url === "string" && url && warning.includes(url))) return true;
  return false;
}

function isPdfDownloadWarning(warning) {
  return (
    warning.includes("all download methods failed") ||
    warning.includes("PDF download blocked") ||
    warning.includes("downloaded content from")
  );
}

const _EMPTY_METADATA = { pageTitle: null, canonicalUrl: null, sourceUrl: null, abstractUrl: null, doi: null };

function firstString(value) {
  if (typeof value === "string" && value.trim()) return value.trim();
  if (Array.isArray(value)) {
    for (const item of value) {
      const found = firstString(item?.value ?? item);
      if (found) return found;
    }
  }
  return null;
}

function normalizeIeeeAuthors(metadata) {
  if (Array.isArray(metadata?.authors)) {
    const authors = [];
    for (const author of metadata.authors) {
      const name = firstString(author?.name ?? author);
      if (name && !authors.includes(name)) authors.push(name);
    }
    if (authors.length) return authors;
  }
  const authorNames = firstString(metadata?.authorNames);
  if (!authorNames) return null;
  const authors = authorNames.split(";").map((s) => s.trim()).filter(Boolean);
  return authors.length ? [...new Set(authors)] : null;
}

function mapIeeeXploreMetadata(metadata, pageUrl) {
  if (!metadata || typeof metadata !== "object") return null;
  const startPage = firstString(metadata.startPage);
  const endPage = firstString(metadata.endPage);
  const pdfUrl = firstString(metadata.pdfUrl || metadata.pdfPath);
  let absolutePdfUrl = null;
  if (pdfUrl) {
    try { absolutePdfUrl = new URL(pdfUrl, pageUrl).href; } catch (_e) { absolutePdfUrl = pdfUrl; }
  }
  return {
    title: firstString(metadata.displayDocTitle) || firstString(metadata.title) || firstString(metadata.formulaStrippedArticleTitle),
    authors: normalizeIeeeAuthors(metadata),
    year: firstString(metadata.publicationYear),
    venue: firstString(metadata.publicationTitle) || firstString(metadata.displayPublicationTitle),
    abstract: firstString(metadata.abstract),
    pages: startPage && endPage ? `${startPage}--${endPage}` : startPage,
    issn: firstString(metadata.issn),
    isbn: firstString(metadata.isbn),
    pdfUrl: absolutePdfUrl,
    doi: firstString(metadata.doi) || firstString(metadata.doiLink)?.replace(/^https?:\/\/doi\.org\//i, ""),
  };
}

export function extractIeeeXploreMetadata(doc, pageUrl) {
  const host = tryHostname(pageUrl);
  if (!host || !host.endsWith("ieeexplore.ieee.org")) return null;
  const direct = doc?.defaultView?.xplGlobal?.document?.metadata;
  const mapped = mapIeeeXploreMetadata(direct, pageUrl);
  if (mapped && (mapped.title || mapped.doi)) return mapped;
  return null;
}

async function extractPageMetadata(tabId, pageUrl) {
  if (!tabId) {
    return {
      ..._EMPTY_METADATA,
      canonicalUrl: pageUrl, sourceUrl: pageUrl, abstractUrl: pageUrl,
    };
  }
  try {
    const [{ result }] = await chrome.scripting.executeScript({
      target: { tabId },
      world: "MAIN",
      func: (currentUrl) => {
        const contentOf = (selector) => document.querySelector(selector)?.getAttribute("content")?.trim() || null;
        const hrefOf = (selector) => document.querySelector(selector)?.getAttribute("href")?.trim() || null;
        const allContentOf = (selector) => {
          const nodes = document.querySelectorAll(selector);
          const vals = [];
          nodes.forEach((n) => {
            const v = (n.getAttribute("content") || "").trim();
            if (v) vals.push(v);
          });
          return vals.length ? vals : null;
        };
        const textOf = (selector) => document.querySelector(selector)?.textContent?.trim() || null;

        const doiMeta =
          contentOf('meta[name="citation_doi"]') ||
          contentOf('meta[name="dc.Identifier"]') ||
          contentOf('meta[name="DC.Identifier"]');

        // --- Rich embedded metadata from citation_* meta tags ---
        const authors = allContentOf('meta[name="citation_author"]');
        const yearFromMeta =
          contentOf('meta[name="citation_date"]') ||
          contentOf('meta[name="citation_publication_date"]') ||
          contentOf('meta[name="citation_year"]') ||
          contentOf('meta[name="dc.Date"]');
        const venue =
          contentOf('meta[name="citation_journal_title"]') ||
          contentOf('meta[name="citation_conference_title"]') ||
          contentOf('meta[name="citation_inbook_title"]');
        const abstract =
          contentOf('meta[name="citation_abstract"]') ||
          contentOf('meta[name="og:description"]') ||
          contentOf('meta[name="description"]') ||
          contentOf('meta[name="dc.Description"]');
        const volume = contentOf('meta[name="citation_volume"]');
        const issue = contentOf('meta[name="citation_issue"]');
        const firstPage = contentOf('meta[name="citation_firstpage"]');
        const lastPage = contentOf('meta[name="citation_lastpage"]');
        const pages = firstPage && lastPage ? `${firstPage}--${lastPage}` : (firstPage || null);
        const issn = contentOf('meta[name="citation_issn"]');
        const isbn = contentOf('meta[name="citation_isbn"]');
        const pdfUrl = contentOf('meta[name="citation_pdf_url"]');

        const firstString = (value) => {
          if (typeof value === "string" && value.trim()) return value.trim();
          if (Array.isArray(value)) {
            for (const item of value) {
              const found = firstString((item && item.value) || item);
              if (found) return found;
            }
          }
          return null;
        };
        const ieee = (() => {
          const host = location.hostname.toLowerCase();
          if (!host.endsWith("ieeexplore.ieee.org")) return {};
          const meta = window.xplGlobal && window.xplGlobal.document && window.xplGlobal.document.metadata;
          if (!meta || typeof meta !== "object") return {};
          const authors = Array.isArray(meta.authors)
            ? meta.authors.map((a) => firstString((a && a.name) || a)).filter(Boolean)
            : firstString(meta.authorNames)?.split(";").map((s) => s.trim()).filter(Boolean) || null;
          const startPage = firstString(meta.startPage);
          const endPage = firstString(meta.endPage);
          const rawPdfUrl = firstString(meta.pdfUrl || meta.pdfPath);
          let absolutePdfUrl = null;
          if (rawPdfUrl) {
            try { absolutePdfUrl = new URL(rawPdfUrl, currentUrl).href; } catch (_) { absolutePdfUrl = rawPdfUrl; }
          }
          return {
            title: firstString(meta.displayDocTitle) || firstString(meta.title) || firstString(meta.formulaStrippedArticleTitle),
            authors: authors && authors.length ? [...new Set(authors)] : null,
            year: firstString(meta.publicationYear),
            venue: firstString(meta.publicationTitle) || firstString(meta.displayPublicationTitle),
            abstract: firstString(meta.abstract),
            pages: startPage && endPage ? `${startPage}--${endPage}` : startPage,
            issn: firstString(meta.issn),
            isbn: firstString(meta.isbn),
            pdfUrl: absolutePdfUrl,
            doi: firstString(meta.doi) || firstString(meta.doiLink)?.replace(/^https?:\/\/doi\.org\//i, ""),
          };
        })();

        // --- Schema.org JSON-LD -------------------------------------------------
        let jsonldAuthors = null;
        let jsonldTitle = null;
        let jsonldYear = null;
        try {
          const scripts = document.querySelectorAll('script[type="application/ld+json"]');
          for (const script of scripts) {
            const data = JSON.parse(script.textContent || "");
            const type = data["@type"] || "";
            if (!/Article/i.test(String(type))) continue;
            const jldAuthors = (data.author || []).map((a) =>
              typeof a === "string" ? a : (a && a.name ? a.name : "")
            ).filter(Boolean);
            if (jldAuthors.length) jsonldAuthors = jldAuthors;
            if (data.name || data.headline) jsonldTitle = data.name || data.headline;
            if (data.datePublished) jsonldYear = String(data.datePublished).slice(0, 4);
            break;  // use first matching Article block
          }
        } catch (_) { /* ignore broken JSON-LD */ }

        // --- OpenGraph ---------------------------------------------------------
        const ogTitle =
          contentOf('meta[property="og:title"]') ||
          contentOf('meta[name="twitter:title"]');

        return {
          pageTitle: document.title || ieee.title || null,
          headHtml: document.head ? document.head.innerHTML : null,
          canonicalUrl: hrefOf('link[rel="canonical"]') || currentUrl || null,
          sourceUrl: currentUrl || null,
          abstractUrl:
            contentOf('meta[name="citation_abstract_html_url"]') ||
            hrefOf('link[rel="canonical"]') ||
            currentUrl ||
            null,
          doi: doiMeta || ieee.doi || null,
          // Rich fields
          embedded_authors: authors || ieee.authors || null,
          embedded_year: yearFromMeta ? String(yearFromMeta).slice(0, 4) : ieee.year,
          embedded_venue: venue || ieee.venue || null,
          embedded_abstract: abstract || ieee.abstract || null,
          embedded_volume: volume,
          embedded_issue: issue,
          embedded_pages: pages || ieee.pages || null,
          embedded_issn: issn || ieee.issn || null,
          embedded_isbn: isbn || ieee.isbn || null,
          embedded_pdf_url: pdfUrl || ieee.pdfUrl || null,
          // JSON-LD / OG fallbacks (used when citation_* is absent)
          embedded_jsonld_authors: jsonldAuthors,
          embedded_jsonld_title: jsonldTitle,
          embedded_jsonld_year: jsonldYear,
          embedded_og_title: ogTitle,
          metadata_source: ieee != null ? "ieee_xplore" : "generic_dom",
          trusted_fields: ieee != null ? ["doi", "authors", "year", "title", "venue", "abstract", "pages", "issn", "isbn"] : null,
        };
      },
      args: [pageUrl],
    });
    return result || {
      ..._EMPTY_METADATA,
      canonicalUrl: pageUrl, sourceUrl: pageUrl, abstractUrl: pageUrl,
    };
  } catch (_error) {
    return {
      ..._EMPTY_METADATA,
      canonicalUrl: pageUrl, sourceUrl: pageUrl, abstractUrl: pageUrl,
    };
  }
}

// ── Rich embedded metadata (exported for testing) ──────────────────────
// Keep in sync with the injected func in extractPageMetadata above.
// ── DOM PDF scanner (exported for testing) ──────────────────────────────
export function scanDomForPdfUrls(doc) {
  const out = [];
  const add = (value) => {
    if (typeof value !== "string") return;
    const trimmed = value.trim();
    if (!trimmed) return;
    let absolute;
    try {
      absolute = new URL(trimmed, doc.baseURI).href;
    } catch (_error) {
      return;
    }
    if (!/^https?:\/\//i.test(absolute)) return;
    if (!out.includes(absolute)) out.push(absolute);
  };

  doc
    .querySelectorAll(
      'meta[name="citation_pdf_url"], meta[name="wkhealth_pdf_url"], meta[name="eprints.document_url"], meta[name="dc.identifier"], meta[name="DC.Identifier"]'
    )
    .forEach((node) => add(node.getAttribute("content")));

  doc.querySelectorAll('link[rel="alternate"][type="application/pdf"], a[href], link[href]').forEach((node) => {
    const href = node.href || node.getAttribute("href");
    const text = (node.textContent || node.getAttribute("title") || node.getAttribute("aria-label") || "").toLowerCase();
    const type = (node.getAttribute("type") || "").toLowerCase();
    if (/\.pdf([?#].*)?$/i.test(href || "") || type.includes("pdf") || text.includes("pdf")) {
      add(href);
    }
  });
  doc.querySelectorAll('iframe[src], embed[src], object[data]').forEach((node) => {
    const value = node.getAttribute("src") || node.getAttribute("data") || node.src || node.data;
    add(value);
  });
  return out;
}

async function extractPdfUrlCandidates(tabId, pageUrl) {
  const candidates = [];
  const addCandidate = (value) => {
    if (typeof value !== "string") return;
    const trimmed = value.trim();
    if (!trimmed || !isSafePublicHttpUrl(trimmed)) return;
    if (!candidates.includes(trimmed)) candidates.push(trimmed);
  };
  // Always-on observer: prepend PDF URLs recently seen for this domain.
  let pageDomain;
  try { pageDomain = new URL(pageUrl).hostname; } catch (_e) { pageDomain = null; }
  if (pageDomain) {
    const observerUrls = _getObserverUrlsForDomain(pageDomain);
    for (const u of observerUrls) addCandidate(u);
  }

  // Include current tab URL even without a .pdf suffix. Many publisher PDF
  // viewers use opaque download URLs; browser-session fetch validates bytes
  // before attaching, so HTML pages are safely skipped.
  addCandidate(pageUrl);

  // Run URL-based site extractors before DOM scan.
  const siteUrls = runSitePdfExtractors(pageUrl);
  for (const u of siteUrls) addCandidate(u);

  if (!tabId) {
    return candidates;
  }
  try {
    const [{ result }] = await chrome.scripting.executeScript({
      target: { tabId },
      func: () => scanDomForPdfUrls(document),
    });
    if (Array.isArray(result)) {
      for (const candidate of result) addCandidate(candidate);
    }
    return candidates;
  } catch (_error) {
    return candidates;
  }
}

// ── Hidden-iframe PDF discovery (Tier 3, exported for testing) ─────────
// Scans the page for PDF-related elements, extracts their URLs without
// clicking, loads each candidate in a hidden iframe, and collects
// application/pdf responses via the always-on observer.  No tab navigation.
// Replaces the old click-based approach that disrupted the user's tab.
const HIDDEN_IFRAME_DISCOVERY_TIMEOUT_MS = 3000;

async function _injectHiddenIframe(tabId, url) {
  try {
    await chrome.scripting.executeScript({
      target: { tabId },
      func: (iframeUrl, timeout) => {
        return new Promise((done) => {
          const iframe = document.createElement("iframe");
          iframe.style.cssText = "display:none;width:0;height:0;border:0;";
          iframe.src = iframeUrl;
          const cleanup = () => {
            if (iframe.parentNode) {
              try { iframe.parentNode.removeChild(iframe); } catch (_e) {}
            }
            done();
          };
          iframe.addEventListener("load", () => {});
          iframe.addEventListener("error", () => {});
          document.body.appendChild(iframe);
          setTimeout(cleanup, timeout);
        });
      },
      args: [url, HIDDEN_IFRAME_DISCOVERY_TIMEOUT_MS],
    });
  } catch (_e) {
    /* Silently ignore injection failures. */
  }
}

function _collectObserverUrlsAfterIframe(beforeUrls) {
  const after = _observerCache
    .filter((e) => (Date.now() - e.timestamp) < OBSERVER_CACHE_TTL_MS)
    .map((e) => e.url);
  const beforeSet = new Set(beforeUrls || []);
  return after.filter((u) => !beforeSet.has(u) && isSafePublicHttpUrl(u));
}

export async function clickPdfDiscovery(tabId, pageUrl) {
  if (!tabId) return [];

  // 1. Scan page for PDF-related elements, extract URLs (no clicks).
  let discoveredUrls = [];
  try {
    const [{ result }] = await chrome.scripting.executeScript({
      target: { tabId },
      func: () => {
        const urls = [];
        const addUrl = (value) => {
          if (typeof value !== "string") return;
          let absolute;
          try { absolute = new URL(value.trim(), document.baseURI).href; } catch (_e) { return; }
          if (!/^https?:\/\//i.test(absolute)) return;
          if (!urls.includes(absolute)) urls.push(absolute);
        };

        // Text patterns to match (lowercase).
        const TEXT_PATTERNS = ["pdf", "download pdf", "full text pdf", "view pdf", "pdf (", "open pdf"];
        // Href substrings.
        const HREF_PATTERNS = [".pdf", "/pdf/", "/stamp/"];

        // Collect URLs from inline event handlers (relative + absolute).
        const extractFromOnclick = (el) => {
          const attr = el.getAttribute("onclick");
          if (!attr) return;
          // window.open('...'), location.href='...', location='...',
          // openPdf('...'), pdf_download('...'), etc.
          for (const m of attr.matchAll(/['"]([^'"]+)['"]/g)) {
            addUrl(m[1]);
          }
        };

        // Check if element text/aria/title matches PDF patterns.
        const elementMatchesPdf = (el) => {
          const text = (el.textContent || "").toLowerCase();
          const aria = (el.getAttribute("aria-label") || "").toLowerCase();
          const title = (el.getAttribute("title") || "").toLowerCase();
          const combined = text + " " + aria + " " + title;
          for (const pat of TEXT_PATTERNS) {
            if (combined.includes(pat)) return true;
          }
          return false;
        };

        for (const tag of ["a", "button", "input", "span", "div", "li"]) {
          for (const el of document.querySelectorAll(tag)) {
            const text = (el.textContent || "").toLowerCase();
            const href = (el.getAttribute("href") || "").toLowerCase();
            // Skip destructive actions.
            if (/delete|remove|archive|flag|report/i.test(text)) continue;

            let matched = false;
            // Match by visible text / aria-label / title.
            if (elementMatchesPdf(el)) matched = true;
            // Match by href patterns.
            if (!matched && tag === "a") {
              for (const pat of HREF_PATTERNS) {
                if (href.includes(pat)) { matched = true; break; }
              }
            }
            // Match by publisher-specific attributes.
            if (!matched) {
              if (el.getAttribute("xplpdf") || el.getAttribute("data-pdf-url")
                  || el.getAttribute("data-download-url") || el.getAttribute("data-fulltext-url")) {
                matched = true;
              }
            }
            if (!matched) continue;

            // Extract URL.
            if (el.href && typeof el.href === "string" && /^https?:\/\//i.test(el.href)) {
              addUrl(el.href);
            }
            // Publisher-specific attributes.
            addUrl(el.getAttribute("xplpdf")
              || el.getAttribute("data-pdf-url")
              || el.getAttribute("data-download-url")
              || el.getAttribute("data-fulltext-url")
              || el.getAttribute("data-url")
              || el.getAttribute("data-href")
              || el.getAttribute("data-pdf"));
            if (tag === "a" && el.getAttribute("href")) addUrl(el.getAttribute("href"));
            extractFromOnclick(el);
            if (tag === "input" && el.form && el.form.action) addUrl(el.form.action);
          }
        }

        return urls;
      },
    });
    if (Array.isArray(result)) discoveredUrls = result;
  } catch (_error) {
    return [];
  }

  // Fall back to site extractors when DOM scan finds nothing (e.g., IEEE JS-based links).
  if (!discoveredUrls.length) {
    const siteUrls = runSitePdfExtractors(pageUrl);
    for (const u of siteUrls) {
      if (u && !discoveredUrls.includes(u)) discoveredUrls.push(u);
    }
  }

  if (!discoveredUrls.length) return [];

  // 2. Snapshot current observer cache.
  const beforeUrls = _observerCache.map((e) => e.url);

  // 3. Load each discovered URL in a hidden iframe (parallel).
  const iframePromises = discoveredUrls.map((url) => _injectHiddenIframe(tabId, url));
  await Promise.all(iframePromises);

  // 4. Small wait for observer to process responses.
  await new Promise((r) => setTimeout(r, 500));

  // 5. Return new observer-caught entries.
  return _collectObserverUrlsAfterIframe(beforeUrls);
}

function buildPdfCandidates(urls, pageUrl, observedUrls = []) {
  const out = [];
  const seen = new Set();
  const siteUrls = new Set(runSitePdfExtractors(pageUrl));
  const observedSet = new Set(observedUrls);
  for (const url of urls || []) {
    if (typeof url !== "string" || !url || seen.has(url)) continue;
    seen.add(url);
    const origin = originOf(url);
    const same = sameOrigin(url, pageUrl);
    const activeTab = url === pageUrl;
    const fromSiteModule = siteUrls.has(url);
    const fromObserver = observedSet.has(url);
    let source;
    let confidence;
    if (fromSiteModule) {
      source = "site_module";
      confidence = 95;
    } else if (fromObserver) {
      source = "network_observed";
      confidence = 70;
    } else if (activeTab) {
      source = "active_tab";
      confidence = 60;
    } else {
      source = "dom";
      confidence = 80;
    }
    out.push({
      url,
      source,
      origin,
      same_origin: same,
      requires_permission: !same,
      requires_cookies: false,
      confidence,
    });
  }
  return out;
}

// ── Site-specific PDF extractors ──────────────────────────────────────────
// URL-based extractors run in background (no DOM needed).
const SITE_PDF_EXTRACTORS = [
  {
    hostname: /arxiv\.org$/,
    extract: (pageUrl) => {
      const m = pageUrl.match(/[/]abs[/]([^/?#]+)/);
      if (!m) return [];
      const id = m[1].replace(/(\.pdf)?$/i, "");
      return [new URL(`/pdf/${id}.pdf`, pageUrl).href];
    },
  },
  {
    hostname: /ieeexplore\.ieee\.org$/,
    extract: (pageUrl) => {
      const m = pageUrl.match(/\/document\/(\d+)/);
      if (!m) return [];
      const ref = typeof btoa === "function" ? btoa(pageUrl) : "";
      return [`https://ieeexplore.ieee.org/stamp/stamp.jsp?tp=&arnumber=${m[1]}&ref=${ref}`];
    },
  },
  {
    hostname: /dl\.acm\.org$/,
    extract: (pageUrl) => {
      // /doi/10.1145/... or /doi/abs/10.1145/... → /doi/pdf/10.1145/...?download=true
      const m = pageUrl.match(/[/]doi[/](?:[^/?#]+[/])?(10\.[^?#]+)/);
      if (!m) return [];
      const doi = m[1];
      return [`https://dl.acm.org/doi/pdf/${doi}?download=true`];
    },
  },
  {
    hostname: /nature\.com$/,
    extract: (pageUrl) => {
      // /articles/s41586-… → /articles/s41586-….pdf
      const m = pageUrl.match(/(\/articles\/[^/?#]+)(?:\.html)?(?=[?#]|$)/);
      if (m) return [new URL(m[1] + ".pdf", pageUrl).href];
      // /full/nchem….html → /pdf/nchem….pdf
      const m2 = pageUrl.match(/(\/full\/[^/?#]+)\.html(?=[?#]|$)/);
      if (m2) return [new URL(m2[1].replace("/full/", "/pdf/") + ".pdf", pageUrl).href];
      return [];
    },
  },
  {
    hostname: /sciencedirect\.com$/,
    extract: (pageUrl) => {
      // /science/article/pii/S1234 → /science/article/pii/S1234/pdfft?download=true
      // Also handles /science/article/abs/pii/S1234 (strip /abs/)
      const m = pageUrl.match(/\/science\/article\/(?:abs\/)?(pii\/[^/?#]+)/i);
      if (!m) return [];
      // Build redirect URL: works like Zotero's canonical + /pdfft fallback
      return [new URL(`/science/article/${m[1]}/pdfft?download=true`, pageUrl).href];
    },
  },
  {
    hostname: /(?:ncbi\.nlm\.nih\.gov|pmc\.ncbi\.nlm\.nih\.gov)$/,
    extract: (pageUrl) => {
      // /pmc/articles/PMC123456/ or /articles/PMC123456/ → append /pdf/
      const m = pageUrl.match(/\/(?:pmc\/)?articles\/(PMC\d+)\//i);
      if (!m) return [];
      const prefix = pageUrl.includes("/pmc/articles/") ? "/pmc" : "";
      return [new URL(`${prefix}/articles/${m[1]}/pdf/`, pageUrl).href];
    },
  },
  {
    hostname: /onlinelibrary\.wiley\.com$/,
    extract: (pageUrl) => {
      // /doi/10.1002/... or /doi/abs/10.1002/... → /doi/epdf/10.1002/...
      const m = pageUrl.match(/\/doi\/(?:abs\/)?(10\.[^/?#]+\/[^?#]+)/);
      if (!m) return [];
      return [`https://onlinelibrary.wiley.com/doi/epdf/${m[1]}`];
    },
  },
  {
    hostname: /tandfonline\.com$/,
    extract: (pageUrl) => {
      // /doi/full/10.1080/... or /doi/abs/10.1080/... → /doi/pdf/10.1080/...
      const m = pageUrl.match(/\/doi\/(?:full|abs)\/(10\.[^/?#]+\/[^?#]+)/);
      if (!m) return [];
      return [`https://www.tandfonline.com/doi/pdf/${m[1]}`];
    },
  },
  {
    hostname: /sagepub\.com$/,
    extract: (pageUrl) => {
      // /doi/10.1177/... or /doi/abs/10.1177/... → /doi/pdf/10.1177/...
      const m = pageUrl.match(/\/doi\/(?:abs\/)?(10\.[^/?#]+\/[^?#]+)/);
      if (!m) return [];
      const host = tryHostname(pageUrl) ? `https://${tryHostname(pageUrl)}` : "https://journals.sagepub.com";
      return [`${host}/doi/pdf/${m[1]}`];
    },
  },
  {
    hostname: /academic\.oup\.com$/,
    extract: (pageUrl) => {
      // /journal/article/vol/iss/id or /journal/article-abstract/... → /article-pdf/...
      // Oxford Academic uses /article-pdf/ as the gateway.
      const m = pageUrl.match(/\/([a-z-]+)\/article(?:-abstract)?\/(.+)/);
      if (!m) return [];
      return [`https://academic.oup.com/${m[1]}/article-pdf/${m[2]}`];
    },
  },
];

function runSitePdfExtractors(pageUrl) {
  const urls = [];
  const hostname = tryHostname(pageUrl);
  if (!hostname) return urls;
  for (const ext of SITE_PDF_EXTRACTORS) {
    if (ext.hostname.test(hostname)) {
      try {
        const extracted = ext.extract(pageUrl);
        if (Array.isArray(extracted)) urls.push(...extracted);
      } catch (_e) {
        /* skip broken extractor */
      }
    }
  }
  return urls;
}

function tryHostname(url) {
  try {
    return new URL(url).hostname;
  } catch (_e) {
    return null;
  }
}

// ── Bot bypass allowlist (mirrors Zotero BOT_BYPASS_WHITELISTED_DOMAINS) ──
const BOT_BYPASS_WHITELISTED_DOMAINS = [
  "sciencedirect.com",
  "pdf.sciencedirectassets.com",
  "ncbi.nlm.nih.gov",
  "ieeexplore.ieee.org",
  "dl.acm.org",
  "onlinelibrary.wiley.com",
  "tandfonline.com",
  "sagepub.com",
  "journals.sagepub.com",
  "academic.oup.com",
  "nature.com",
];

export function isBotBypassWhitelisted(url) {
  const hostname = tryHostname(url);
  if (!hostname) return false;
  return BOT_BYPASS_WHITELISTED_DOMAINS.some((d) => hostname.endsWith(d));
}

// ── Network PDF observer ──────────────────────────────────────────────────
// Listens for PDF content-type responses during a capture window.
// Requires optional "webRequest" permission + host permissions.
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
    if (details.tabId !== tabId && details.tabId !== -1) return;
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

function stopPdfObserver() {
  if (_pdfObserverListener && typeof chrome !== "undefined" && chrome.webRequest) {
    try {
      chrome.webRequest.onHeadersReceived.removeListener(_pdfObserverListener);
    } catch (_e) {
      /* ignore */
    }
  }
  _pdfObserverListener = null;
}

function collectObservedPdfUrls() {
  return [...new Set(_pdfObserverUrls)];
}

export function collectPdfObserverEvents() {
  return _pdfObserverEvents.slice(-20);
}

function _observerEventLooksLikePdf(event) {
  const contentType = String(event.content_type || "").toLowerCase();
  const disposition = String(event.content_disposition || "").toLowerCase();
  const url = String(event.url || "").toLowerCase();
  return contentType.includes("pdf")
    || disposition.includes(".pdf")
    || disposition.includes("filename")
    || url.includes("/stamppdf/")
    || url.includes(".pdf");
}

// ── Bot/JS bypass via hidden iframe ──────────────────────────────────────
// For allowlisted domains, inject a hidden iframe to the failed URL,
// letting the page's JS redirect to the real PDF. The network observer
// catches the resulting PDF response.

const BOT_BYPASS_IFRAME_TIMEOUT_MS = 5000;
const BOT_BYPASS_VISIBLE_TIMEOUT_MS = 15000;

export async function botBypassPdfUrl(tabId, candidateUrl, options = {}) {
  if (!tabId || !candidateUrl) return null;
  if (!isBotBypassWhitelisted(candidateUrl)) return null;
  const visibleTimeoutMs = options.visibleTimeoutMs || BOT_BYPASS_VISIBLE_TIMEOUT_MS;

  startPdfObserver(tabId);

  try {
    // Inject hidden iframe that navigates to the intermediate/blocked URL.
    // The page's JavaScript may serve a different PDF URL that our
    // observer will catch.
    await chrome.scripting.executeScript({
      target: { tabId },
      func: (url) => {
        return new Promise((resolve) => {
          const iframe = document.createElement("iframe");
          iframe.style.display = "none";
          iframe.src = url;
          const cleanup = () => {
            setTimeout(() => {
              if (iframe.parentNode) iframe.parentNode.removeChild(iframe);
            }, 500);
          };
          iframe.onload = () => {};
          iframe.onerror = () => {};
          document.body.appendChild(iframe);
          // Wait for page JS to potentially trigger PDF loads.
          setTimeout(() => {
            cleanup();
            resolve();
          }, BOT_BYPASS_IFRAME_TIMEOUT_MS);
        });
      },
      args: [candidateUrl],
    });

    // Give observer a chance to collect any PDF URLs that the iframe triggered.
    await new Promise((r) => setTimeout(r, 500));
    const observed = collectObservedPdfUrls();
    // Return first observed URL that differs from the injected one
    // and looks like a valid http(s) URL.
    const hiddenObserved = observed.find((u) => /^https?:\/\//i.test(u));
    if (hiddenObserved) return hiddenObserved;
    return await botBypassViaVisibleTab(candidateUrl, { timeoutMs: visibleTimeoutMs });
  } catch (_e) {
    return null;
  } finally {
    stopPdfObserver();
  }
}

async function botBypassViaVisibleTab(candidateUrl, { timeoutMs }) {
  if (!chrome.tabs?.create) return null;
  let helperTab = null;
  try {
    helperTab = await chrome.tabs.create({ url: "about:blank", active: true });
    if (!helperTab || helperTab.id == null) return null;
    startPdfObserver(helperTab.id);
    if (chrome.tabs?.update) {
      await chrome.tabs.update(helperTab.id, { url: candidateUrl });
    } else {
      await chrome.tabs.create({ url: candidateUrl, active: true });
    }
    // Wait for tab navigation to complete, then give JS redirects time to fire.
    await new Promise((resolve) => {
      const done = () => {
        try { chrome.tabs.onUpdated.removeListener(onUpdated); } catch (_e) {}
        clearTimeout(timer);
        resolve();
      };
      const timer = setTimeout(done, timeoutMs);
      const onUpdated = (tabId, changeInfo) => {
        if (tabId === helperTab.id && changeInfo.status === "complete") {
          // Tab loaded — give PDF viewer / JS redirects an extra window.
          clearTimeout(timer);
          setTimeout(done, 3000);
        }
      };
      try {
        chrome.tabs.onUpdated.addListener(onUpdated);
      } catch (_e) {
        // Fall back to blind timeout if onUpdated unavailable.
        setTimeout(resolve, timeoutMs);
        return;
      }
    });
    const observed = collectObservedPdfUrls();
    return observed.find((u) => /^https?:\/\//i.test(u)) || null;
  } catch (_e) {
    return null;
  } finally {
    if (helperTab && helperTab.id != null && chrome.tabs?.remove) {
      try {
        await chrome.tabs.remove(helperTab.id);
      } catch (_e) {
        /* user may have closed helper tab */
      }
    }
  }
}

function isSafePublicHttpUrl(value) {
  let parsed;
  try {
    parsed = new URL(value);
  } catch (_error) {
    return false;
  }
  if (parsed.protocol !== "http:" && parsed.protocol !== "https:") return false;
  const host = parsed.hostname.toLowerCase();
  if (!host || host === "localhost" || host === "localhost.localdomain") return false;
  if (!host.includes(".") || host.endsWith(".localhost") || host.endsWith(".local")) return false;
  if (host.endsWith(".internal") || host.endsWith(".lan") || host.endsWith(".home")) return false;
  if (/^(127|10)\./.test(host)) return false;
  if (/^192\.168\./.test(host)) return false;
  if (/^172\.(1[6-9]|2\d|3[0-1])\./.test(host)) return false;
  if (/^(0|169\.254|224|255)\./.test(host)) return false;
  if (host === "::1" || host.startsWith("fc") || host.startsWith("fd") || host.startsWith("fe80")) return false;
  return true;
}

async function maybeStreamPdfBytes({ endpoint, citekey, bib, pdfUrlCandidates, pageUrl, originPermissions = null, pdfRequest = null }) {
  const attempts = [];
  const grouped = groupPdfCandidates(pdfUrlCandidates || [], pageUrl);
  let lastPermission = null;
  const originGroups = [
    ...groupCandidatesByOrigin(grouped.sameOrigin).map((candidates) => ({ candidates, allowWithoutPermissionApi: true })),
    ...groupCandidatesByOrigin(grouped.crossOrigin).map((candidates) => ({ candidates, allowWithoutPermissionApi: false })),
  ];
  for (const { candidates, allowWithoutPermissionApi } of originGroups) {
    const permission = permissionForCandidate(originPermissions, candidates[0])
      || await requestTemporaryOriginPermission(candidates[0]);
    lastPermission = permission;
    if (permission.status !== "granted" && !(allowWithoutPermissionApi && permission.status === "unavailable")) {
      // Allow same-origin candidates even when permission is "denied" —
      // generic background fetch + cookies works without host permissions.
      if (!(allowWithoutPermissionApi && permission.status === "denied")) {
        continue;
      }
      // Fall through: tryPdfCandidates will skip navigate/discover methods
      // but still attempt generic fetch and page-context fetch.
    }
    const attach = await tryPdfCandidates({
      endpoint,
      citekey,
      bib,
      candidates,
      permission,
      pageUrl,
      attempts,
      pdfRequest,
    });
    await removeTemporaryOriginPermission(candidates[0], permission);
    if (attach) {
      attach.pdf_attach_attempts = attempts;
      return attach;
    }
  }
  if (lastPermission && lastPermission.status !== "granted") {
    return {
      status: "error",
      message: "PDF permission denied",
      pdf_attach_permission: lastPermission,
      pdf_attach_attempts: attempts,
    };
  }
  // All candidates exhausted — return structured failure so attempts surface.
  let message = "browser PDF fetch failed — all candidates exhausted";
  const authStatuses = attempts.filter(a => a.status === "html_login" || a.status === "html_access_denied");
  if (authStatuses.length > 0) {
    const status = authStatuses[0].status;
    if (status === "html_login") {
      message = "PDF requires authentication — log in to the site in your browser first, then retry capture";
    } else {
      message = "PDF access denied — you may need institutional access or a subscription";
    }
  }
  return {
    status: "error",
    message,
    pdf_attach_attempts: attempts,
  };
}

async function tryPdfCandidates({ endpoint, citekey, bib, candidates, permission = null, pageUrl = null, attempts = [], pdfRequest = null }) {
  const permissionDenied = permission && permission.status === "denied";
  for (const candidate of candidates) {
    const url = candidateUrl(candidate);
    if (!url) continue;

    // navigate_monitor and discover_from_page need host permissions
    // (tabs.create to arbitrary domain). Skip if permission denied.
    if (!permissionDenied && candidate && candidate.method === "navigate_monitor") {
      // Try lightweight background fetch first — works for OA papers without
      // opening any tabs.  Only fall back to visible-tab navigation if the
      // response is HTML (paywalled gateway), not PDF.
      const referrer = candidate.referrer || pageUrl || undefined;
      const lightFetched = await fetchCandidateBytes(url, {
        credentials: "include",
        referrer,
      }, "browser_fetch", attempts);
      if (lightFetched) {
        const attachResult = await attachPdfToServer({ endpoint, citekey, bib, sourceUrl: url, bytes: lightFetched.bytes, pdfRequest });
        if (attachResult && attachResult.status === "ok") {
          markLastAttemptSaved(attempts, url);
          if (permission) attachResult.pdf_attach_permission = permission;
          return attachResult;
        }
      }

      // Same-origin page-context fetch carries real browser session.
      // Try before heavy navigation — avoids opening tabs if it works.
      if (_captureTabId && url !== pageUrl && sameOrigin(url, pageUrl)) {
        const pageBytes = await fetchPdfViaPageContext(_captureTabId, url, endpoint, citekey, bib, attempts, pdfRequest);
        if (pageBytes) {
          const attachResult = await attachPdfToServer({ endpoint, citekey, bib, sourceUrl: url, bytes: pageBytes, pdfRequest });
          if (attachResult && attachResult.status === "ok") {
            markLastAttemptSaved(attempts, url);
            if (permission) attachResult.pdf_attach_permission = permission;
            return attachResult;
          }
        }
      }

      // Lightweight methods exhausted — fall back to heavy visible-tab navigation.
      const monitored = await fetchPdfViaNavigateMonitor({
        candidate,
        endpoint,
        citekey,
        bib,
        pageUrl,
        attempts,
        pdfRequest,
      });
      if (monitored) {
        if (permission) monitored.pdf_attach_permission = permission;
        return monitored;
      }
      // All methods for this candidate exhausted — skip generic fallthrough
      // (page-context fetch + fetchPdfCandidate) to avoid duplicate heavy ops.
      continue;
    }

    if (!permissionDenied && candidate && candidate.method === "discover_from_page") {
      const discovered = await fetchPdfViaDiscoverFromPage({
        candidate,
        endpoint,
        citekey,
        bib,
        pageUrl,
        attempts,
        pdfRequest,
      });
      if (discovered) {
        if (permission) discovered.pdf_attach_permission = permission;
        return discovered;
      }
    }

    // Phase 3: try page-context fetch for same-origin stamp candidates.
    // IEEE anti-bot blocks background fetch() — injecting a content script
    // that fetches from within the page context carries the real browser session.
    if (_captureTabId && url !== pageUrl && sameOrigin(url, pageUrl)) {
      const pageBytes = await fetchPdfViaPageContext(_captureTabId, url, endpoint, citekey, bib, attempts, pdfRequest);
      if (pageBytes) {
        const attachResult = await attachPdfToServer({ endpoint, citekey, bib, sourceUrl: url, bytes: pageBytes, pdfRequest });
        if (attachResult && attachResult.status === "ok") {
          markLastAttemptSaved(attempts, url);
          if (permission) attachResult.pdf_attach_permission = permission;
          return attachResult;
        }
      }
    }

    try {
      const fetched = await fetchPdfCandidate(url, { pageUrl, attempts });
      if (!fetched) continue;
      const attachResult = await attachPdfToServer({ endpoint, citekey, bib, sourceUrl: url, bytes: fetched.bytes, pdfRequest });
      if (attachResult && attachResult.status === "ok") {
        markLastAttemptSaved(attempts, url);
        if (permission) attachResult.pdf_attach_permission = permission;
        if (fetched.cookiePermission) attachResult.pdf_attach_cookie_permission = fetched.cookiePermission;
        return attachResult;
      }
      continue;
    } catch (_error) {
      continue;
    }
  }
}

async function fetchPdfViaNavigateMonitor({ candidate, endpoint, citekey, bib, pageUrl, attempts, pdfRequest = null }) {
  const url = candidateUrl(candidate);
  if (!_captureTabId || !url) return null;
  attempts.push({ url, mode: "navigate_monitor", status: "started", referrer: candidate.referrer || pageUrl || null });
  const observedUrl = await botBypassPdfUrl(_captureTabId, url, {
    visibleTimeoutMs: candidate.timeout_ms || BOT_BYPASS_VISIBLE_TIMEOUT_MS,
  });
  attempts.push({
    url,
    mode: "navigate_monitor",
    status: "observed",
    observed_url: observedUrl,
    observer_events: collectPdfObserverEvents(),
  });
  if (!observedUrl) return null;
  const fetched = await fetchCandidateBytes(observedUrl, {
    credentials: "include",
    referrer: candidate.referrer || pageUrl || undefined,
  }, "navigate_monitor_fetch", attempts);
  if (!fetched) return null;
  const attachResult = await attachPdfToServer({ endpoint, citekey, bib, sourceUrl: observedUrl, bytes: fetched.bytes, pdfRequest });
  if (attachResult && attachResult.status === "ok") {
    markLastAttemptSaved(attempts, observedUrl);
    return attachResult;
  }
  return null;
}

// ── discover_from_page acquisition ─────────────────────────────────────
// For article page candidates: open visible helper tab, click PDF links,
// observe resulting PDF download, fetch and attach bytes.

const BOT_BYPASS_PAGE_CLICK_TIMEOUT_MS = 20000;

async function fetchPdfViaDiscoverFromPage({ candidate, endpoint, citekey, bib, pageUrl, attempts, pdfRequest = null }) {
  const url = candidateUrl(candidate);
  if (!url) return null;
  attempts.push({ url, mode: "discover_from_page", status: "started", referrer: candidate.referrer || pageUrl || null });

  const useBotBypass = isBotBypassWhitelisted(url) || isBotBypassWhitelisted(pageUrl);
  if (!useBotBypass) {
    attempts.push({ url, mode: "discover_from_page", status: "skipped", reason: "domain not bot-bypass allowlisted" });
    return null;
  }

  const pageTimeout = candidate.timeout_ms || BOT_BYPASS_PAGE_CLICK_TIMEOUT_MS;

  // Open visible helper tab to the article page.
  let helperTab = null;
  try { helperTab = await chrome.tabs.create({ url, active: false }); } catch (_e) { /* noop */ }
  if (!helperTab || !helperTab.id) {
    attempts.push({ url, mode: "discover_from_page", status: "error", reason: "helper tab creation failed" });
    return null;
  }

  // Arm observer on helper tab.
  startPdfObserver(helperTab.id);

  // Wait for page to load.
  const loaded = await waitForTabLoad(helperTab.id, pageTimeout);
  if (!loaded) {
    stopPdfObserver();
    try { await chrome.tabs.remove(helperTab.id); } catch (_e) { /* noop */ }
    attempts.push({ url, mode: "discover_from_page", status: "timeout", reason: "page load timeout" });
    return null;
  }

  // Give the page a moment to render fully.
  await sleep(2000);

  // Try clicking PDF links on the page.
  const clicked = await clickPdfElementsOnPage(helperTab.id);
  attempts.push({ url, mode: "discover_from_page", status: "clicked", clicked: clicked || "none" });

  // Wait for PDF to appear via observer.
  await sleep(3000);

  // Collect observed PDF URLs.
  const observedUrls = collectObservedPdfUrls();
  const events = collectPdfObserverEvents();
  attempts.push({ url, mode: "discover_from_page", status: "observed", observed_urls: observedUrls, observer_events: events });

  stopPdfObserver();
  try { await chrome.tabs.remove(helperTab.id); } catch (_e) { /* noop */ }

  if (observedUrls.length === 0) return null;

  // Try each observed URL.
  for (const observedUrl of observedUrls) {
    const fetched = await fetchCandidateBytes(observedUrl, {
      credentials: "include",
      referrer: candidate.referrer || pageUrl || undefined,
    }, "discover_from_page_fetch", attempts);
    if (!fetched) continue;
    const attachResult = await attachPdfToServer({ endpoint, citekey, bib, sourceUrl: observedUrl, bytes: fetched.bytes, pdfRequest });
    if (attachResult && attachResult.status === "ok") {
      markLastAttemptSaved(attempts, observedUrl);
      return attachResult;
    }
  }

  return null;
}

// Click any visible PDF/download links on the page.
async function clickPdfElementsOnPage(tabId) {
  try {
    const results = await chrome.scripting.executeScript({
      target: { tabId },
      func: () => {
        const clicked = [];
        // Common PDF link/button selectors used by publishers.
        const selectors = [
          'a[href*="/pdf/"]', 'a[href*="/epdf/"]', 'a[href*="/pdfdirect/"]',
          'a[href*="/pdfft"]', 'a[href*=".pdf"]', 'a[href*="download"]',
          'a:contains("PDF")', 'button:contains("PDF")',
          '[data-testid="pdf-download"]', '[aria-label*="PDF"]',
          '.pdf-link', '.download-pdf', '.article-pdf',
          'a[title*="PDF"]', 'a[title*="Download"]',
        ];
        for (const sel of selectors) {
          try {
            const el = document.querySelector(sel);
            if (el && el.offsetParent !== null) { // visible
              el.click();
              clicked.push(sel);
              break; // One click is enough — let observer catch the result.
            }
          } catch (_e) { /* skip broken selector */ }
        }
        return clicked.length > 0 ? clicked[0] : null;
      },
    });
    if (results && results[0] && results[0].result) return results[0].result;
  } catch (_e) { /* page context may reject script */ }
  return null;
}

// Wait for a tab to reach "complete" loading state.
function waitForTabLoad(tabId, timeoutMs) {
  return new Promise((resolve) => {
    const timer = setTimeout(() => resolve(false), timeoutMs);
    const listener = (updatedTabId, changeInfo) => {
      if (updatedTabId === tabId && changeInfo.status === "complete") {
        clearTimeout(timer);
        chrome.tabs.onUpdated.removeListener(listener);
        resolve(true);
      }
    };
    chrome.tabs.onUpdated.addListener(listener);
  });
}

function sleep(ms) {
  return new Promise((resolve) => { setTimeout(resolve, ms); });
}

async function attachPdfToServer({ endpoint, citekey, bib, sourceUrl, bytes, pdfRequest = null }) {
  const rawResponse = await fetch(rawAttachUrl(endpoint, { citekey, bib, sourceUrl, pdfRequest }), {
    method: "POST",
    headers: { "Content-Type": "application/pdf", ...attachTokenHeader(pdfRequest), ...(await getAuthHeaders()) },
    body: bytes,
  });
  const rawResult = await jsonOrNull(rawResponse);
  if (rawResponse.ok && rawResult && rawResult.status === "ok") return rawResult;

  const base64 = arrayBufferToBase64(bytes);
  const attachResponse = await fetch(endpointFor(endpoint, "/attach-pdf-bytes"), {
    method: "POST",
    headers: { "Content-Type": "application/json", ...(await getAuthHeaders()) },
    body: JSON.stringify({
      citekey,
      bib,
      source_url: sourceUrl,
      pdf_base64: base64,
    }),
  });
  return await jsonOrNull(attachResponse);
}

function rawAttachUrl(endpoint, { citekey, bib, sourceUrl, pdfRequest = null }) {
  const plannedUrl = pdfRequest?.attach?.url;
  const base = plannedUrl || `${endpointFor(endpoint, "/attach-pdf-raw")}?${new URLSearchParams({ citekey }).toString()}`;
  const url = new URL(base, endpointFor(endpoint, "/"));
  if (!url.searchParams.get("citekey")) url.searchParams.set("citekey", citekey);
  if (bib && !url.searchParams.get("bib")) url.searchParams.set("bib", bib);
  url.searchParams.set("source_url", sourceUrl);
  return url.toString();
}

function attachTokenHeader(pdfRequest) {
  const token = pdfRequest?.attach?.token;
  return token ? { "X-Pzi-Attach-Token": token } : {};
}

async function fetchPdfViaPageContext(tabId, url, endpoint, citekey, bib, attempts, pdfRequest = null) {
  if (!tabId || !url || !endpoint) return null;
  const authHeaders = await getAuthHeaders();
  const rawUrl = rawAttachUrl(endpoint, { citekey, bib, sourceUrl: url, pdfRequest });

  let result;
  try {
    const results = await chrome.scripting.executeScript({
      target: { tabId },
      world: "MAIN",
      func: async (pdfUrl, attachUrl, headers) => {
        try {
          const response = await fetch(pdfUrl, { credentials: "include" });
          if (!response.ok) return { error: `fetch HTTP ${response.status}` };
          const contentType = response.headers.get("content-type") || "";
          if (!contentType.toLowerCase().includes("pdf")) {
            return { error: "not_pdf", contentType };
          }
          const buf = await response.arrayBuffer();
          if (buf.byteLength === 0) return { error: "empty" };
          // POST PDF bytes directly to pzi backend from page context
          const attachResp = await fetch(attachUrl, {
            method: "POST",
            headers: { ...headers, "Content-Type": "application/pdf" },
            body: buf,
          });
          if (!attachResp.ok) return { error: `attach HTTP ${attachResp.status}` };
          return await attachResp.json();
        } catch (e) {
          return { error: e.message };
        }
      },
      args: [url, rawUrl, { ...authHeaders, ...attachTokenHeader(pdfRequest) }],
    });
    result = results?.[0]?.result;
  } catch (_e) {
    attempts.push({ url, mode: "page_context_fetch", status: "fetch_error", error: _e?.message || String(_e) });
    return null;
  }

  if (result && result.status === "ok") {
    attempts.push({ url, mode: "page_context_fetch", status: "saved" });
    return new ArrayBuffer(0); // bytes already posted to backend by injected script
  }
  if (result && result.error) {
    attempts.push({ url, mode: "page_context_fetch", status: result.error, content_type: result.contentType });
  }
  return null;
}

async function fetchPdfCandidate(candidate, { pageUrl = null, attempts = [] } = {}) {
  const url = candidateUrl(candidate);
  if (!url) return null;
  const firstOpts = { credentials: "include" };
  // Always send Referer — many publishers (IEEE, ACM) require it to serve
  // PDF download gateway pages instead of anti-leech HTML forms.
  if (pageUrl) firstOpts.referrer = pageUrl;
  const first = await fetchCandidateBytes(url, firstOpts, "browser_fetch", attempts);
  if (first) return first;

  // Try extracting a meta-refresh redirect URL from the HTML response.
  // IEEE stamp.jsp and similar gateways serve HTML with <meta http-equiv="refresh">
  // rather than HTTP 3xx.  fetch() does not follow these, so we must parse explicitly.
  const redirectedUrl = await extractMetaRedirectUrl(url, firstOpts);
  if (redirectedUrl) {
    const redirected = await fetchCandidateBytes(redirectedUrl, firstOpts, "browser_fetch_meta", attempts);
    if (redirected) return redirected;
  }

  const cookieRetry = shouldRetryWithCookies(url, pageUrl);
  attempts.push({ url, mode: "diag", status: "cookie_retry_decision", cookie_retry: cookieRetry, candidate_is_page_url: url === pageUrl });
  if (cookieRetry) {
    const cookiePermission = await requestCookiePermission();
    attempts.push({ url, mode: "diag", status: "cookie_permission", permission: cookiePermission.status });
    if (cookiePermission.status === "granted") {
      const cookieHeader = await cookieHeaderForUrl(url);
      attempts.push({ url, mode: "diag", status: "cookie_header", has_header: !!cookieHeader });
      if (cookieHeader) {
        const second = await fetchCandidateBytes(url, {
          credentials: "include",
          headers: { Cookie: cookieHeader },
          referrer: pageUrl || undefined,
        }, "browser_fetch_cookies", attempts);
        if (second) {
          second.cookiePermission = cookiePermission;
          return second;
        }
      }
    }
  }

  // If still no PDF, try bot bypass for allowlisted domains.
  const bypassAllowed = url && isBotBypassWhitelisted(url);
  attempts.push({ url, mode: "diag", status: "bot_bypass_decision", bypass_allowed: bypassAllowed, tab_id: _captureTabId });
  if (bypassAllowed) {
    const bypassedUrl = await botBypassPdfUrl(_captureTabId, url);
    attempts.push({ url, mode: "diag", status: "bot_bypass_result", bypassed_url: bypassedUrl });
    if (bypassedUrl) {
      const bypassed = await fetchCandidateBytes(bypassedUrl, { credentials: "include" }, "bot_bypass", attempts);
      if (bypassed) return bypassed;
    }
  }

  return null;
}

function shouldRetryWithCookies(candidate, pageUrl) {
  if (candidate === pageUrl) return false;
  try {
    const parsed = new URL(candidate);
    return /\.pdf([?#].*)?$/i.test(parsed.pathname)
      || /\/stamp\//i.test(parsed.pathname)
      || /[?&](pdf|download)=/i.test(parsed.search);
  } catch (_error) {
    return false;
  }
}

async function fetchCandidateBytes(candidate, options, mode, attempts) {
  const url = candidateUrl(candidate);
  if (!url) return null;
  let response;
  try {
    response = await fetchWithTimeout(url, options, PDF_FETCH_TIMEOUT_MS);
  } catch (error) {
    attempts.push({ url, mode, status: "fetch_error", error: error?.message || String(error), byte_count: 0 });
    return null;
  }
  const contentType = response.headers.get("content-type") || "";
  if (!response.ok) {
    attempts.push({ url, mode, status: "http_error", http_status: response.status || null, content_type: contentType, byte_count: 0 });
    return null;
  }
  if (contentLengthExceedsAttachLimit(response.headers)) {
    attempts.push({ url, mode, status: "too_large", http_status: response.status || null, content_type: contentType, byte_count: Number.parseInt(response.headers.get("content-length") || "0", 10) || 0 });
    return null;
  }
  const bytes = await response.arrayBuffer();
  if (bytes.byteLength > MAX_ATTACH_PDF_BYTES) {
    attempts.push({ url, mode, status: "too_large", http_status: response.status || null, content_type: contentType, byte_count: bytes.byteLength });
    return null;
  }
  if (!looksLikePdfBytes(bytes) && !contentType.toLowerCase().includes("pdf")) {
    let htmlStatus = "not_pdf";
    let textSnippet = null;
    if (contentType.toLowerCase().includes("html") || /\btext\//i.test(contentType)) {
      try {
        const decoder = new TextDecoder();
        const text = decoder.decode(bytes);
        const lower = text.toLowerCase();
        if (/sign[\s-]*in|login|log[\s-]*in|authentication\s+required/i.test(lower)) {
          htmlStatus = "html_login";
        } else if (/access\s+denied|forbidden|not\s+authorized|subscription\s+required|payment\s+required/i.test(lower)) {
          htmlStatus = "html_access_denied";
        }
        textSnippet = text.slice(0, 500);
      } catch (_e) { /* ignore decode errors */ }
    }
    attempts.push({ url, mode, status: htmlStatus, http_status: response.status || null, content_type: contentType, byte_count: bytes.byteLength, text_snippet: textSnippet });
    return null;
  }
  attempts.push({ url, mode, status: "fetched", http_status: response.status || null, content_type: contentType, byte_count: bytes.byteLength });
  return { bytes };
}

/**
 * Fetch the URL as text and extract a meta-refresh redirect URL.
 * Returns the resolved URL or null.
 *
 * Handles patterns like:
 *   <meta http-equiv="refresh" content="0;URL=https://example.com/paper.pdf">
 *   <meta http-equiv="Refresh" content="0; url=https://example.com/paper.pdf">
 */
async function extractMetaRedirectUrl(candidate, options) {
  try {
    const url = typeof candidate === "string" ? candidate : candidate?.url;
    if (!url) return null;
    const response = await fetchWithTimeout(url, options, PDF_FETCH_TIMEOUT_MS);
    if (!response.ok) return null;
    const text = await response.text();
    // Match meta refresh patterns with flexible spacing/case/quoting
    const re = /<meta\s[^>]*http-equiv\s*=\s*["']?refresh["']?[^>]*content\s*=\s*["']?(\d+)\s*;\s*(?:url\s*=\s*)?(\S+?)["']?[^>]*\/?>/i;
    const match = re.exec(text);
    if (!match) return null;
    const redirectUrl = match[2].replace(/['"\s]/g, "");
    // Resolve relative URLs against the base URL
    try {
      return new URL(redirectUrl, url).href;
    } catch (_e) {
      return null;
    }
  } catch (_error) {
    return null;
  }
}

function markLastAttemptSaved(attempts, url) {
  for (let index = attempts.length - 1; index >= 0; index -= 1) {
    if (attempts[index].url === url && attempts[index].status === "fetched") {
      attempts[index].status = "saved";
      return;
    }
  }
}

function candidateUrl(candidate) {
  if (typeof candidate === "string") return candidate;
  if (candidate && typeof candidate.url === "string") return candidate.url;
  return null;
}

async function requestCookiePermission() {
  if (!chrome.permissions?.request) return { status: "denied" };
  try {
    const granted = Boolean(await chrome.permissions.request({ permissions: ["cookies"] }));
    return { status: granted ? "granted" : "denied" };
  } catch (_error) {
    return { status: "denied" };
  }
}

export async function cookieHeaderForUrl(url) {
  if (!chrome.cookies?.getAll) return "";
  let cookies = [];
  try {
    cookies = await chrome.cookies.getAll({ url, partitionKey: {} });
  } catch (_error) {
    cookies = await chrome.cookies.getAll({ url });
  }
  return cookies.map((cookie) => `${cookie.name}=${cookie.value}`).join("; ");
}

function groupPdfCandidates(candidates, pageUrl) {
  const same = [];
  const cross = [];
  for (const candidate of candidates || []) {
    const url = candidateUrl(candidate);
    if (!url) continue;
    const bucket = sameOrigin(url, pageUrl) ? same : cross;
    if (!bucket.some((item) => candidateUrl(item) === url)) bucket.push(candidate);
  }
  return { sameOrigin: same, crossOrigin: cross };
}

function groupCandidatesByOrigin(candidates) {
  const groups = [];
  const indexByOrigin = new Map();
  for (const candidate of candidates || []) {
    const origin = originOf(candidateUrl(candidate));
    if (!origin) continue;
    if (!indexByOrigin.has(origin)) {
      indexByOrigin.set(origin, groups.length);
      groups.push([]);
    }
    groups[indexByOrigin.get(origin)].push(candidate);
  }
  return groups;
}

async function requestPdfOriginPermissions(candidates, pageUrl) {
  const permissions = new Map();
  const grouped = groupPdfCandidates(candidates || [], pageUrl);
  const originGroups = [
    ...groupCandidatesByOrigin(grouped.sameOrigin),
    ...groupCandidatesByOrigin(grouped.crossOrigin),
  ];
  for (const group of originGroups) {
    const origin = originOf(candidateUrl(group[0]));
    if (!origin || permissions.has(origin)) continue;
    permissions.set(origin, await requestTemporaryOriginPermission(group[0]));
  }
  return permissions;
}

function permissionForCandidate(permissions, candidate) {
  if (!permissions || typeof permissions.get !== "function") return null;
  const origin = originOf(candidateUrl(candidate));
  if (!origin) return null;
  return permissions.get(origin) || null;
}

function originPatternForUrl(url) {
  try {
    return `${new URL(candidateUrl(url)).origin}/*`;
  } catch (_error) {
    return null;
  }
}

async function requestTemporaryOriginPermission(url) {
  const pattern = originPatternForUrl(url);
  if (!pattern || !chrome.permissions) {
    return { status: "unavailable", origin: originOf(candidateUrl(url)), removed: false };
  }
  const request = { origins: [pattern] };
  let alreadyGranted = false;
  try {
    alreadyGranted = Boolean(await chrome.permissions.contains(request));
  } catch (_error) {
    alreadyGranted = false;
  }
  if (alreadyGranted) {
    return { status: "granted", origin: originOf(candidateUrl(url)), removed: false, already_granted: true };
  }
  let granted = false;
  try {
    granted = Boolean(await chrome.permissions.request(request));
  } catch (_error) {
    granted = false;
  }
  return { status: granted ? "granted" : "denied", origin: originOf(candidateUrl(url)), removed: false };
}

async function removeTemporaryOriginPermission(url, permission) {
  if (!permission || permission.status !== "granted" || permission.already_granted) return permission;
  const pattern = originPatternForUrl(url);
  if (!pattern || !chrome.permissions?.remove) return permission;
  try {
    permission.removed = Boolean(await chrome.permissions.remove({ origins: [pattern] }));
  } catch (_error) {
    permission.removed = false;
  }
  return permission;
}

function originOf(url) {
  try {
    return new URL(url).origin;
  } catch (_error) {
    return null;
  }
}

function sameOrigin(candidateUrl, pageUrl) {
  try {
    return new URL(candidateUrl).origin === new URL(pageUrl).origin;
  } catch (_error) {
    return false;
  }
}

async function fetchWithTimeout(url, options, timeoutMs) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await fetch(url, { ...options, signal: controller.signal });
  } finally {
    clearTimeout(timer);
  }
}

function contentLengthExceedsAttachLimit(headers) {
  const value = headers?.get?.("content-length");
  if (!value) return false;
  const length = Number.parseInt(value, 10);
  return Number.isFinite(length) && length > MAX_ATTACH_PDF_BYTES;
}

function looksLikePdfBytes(buffer) {
  const bytes = new Uint8Array(buffer, 0, Math.min(5, buffer.byteLength));
  return bytes.length >= 5 && bytes[0] === 0x25 && bytes[1] === 0x50 && bytes[2] === 0x44 && bytes[3] === 0x46 && bytes[4] === 0x2d;
}

function arrayBufferToBase64(buffer) {
  const bytes = new Uint8Array(buffer);
  const chunkSize = 0x8000;
  let binary = "";
  for (let offset = 0; offset < bytes.length; offset += chunkSize) {
    const chunk = bytes.subarray(offset, offset + chunkSize);
    binary += String.fromCharCode(...chunk);
  }
  return btoa(binary);
}

function endpointFor(endpoint, path) {
  if (typeof endpoint !== "string" || !endpoint.trim()) return `${DEFAULT_HOST}${path}`;
  const trimmed = endpoint.trim();
  try {
    const parsed = new URL(trimmed);
    if (/\/capture\/?$/i.test(parsed.pathname)) {
      parsed.pathname = path;
    } else {
      parsed.pathname = path;
    }
    parsed.search = "";
    parsed.hash = "";
    return parsed.href;
  } catch (_error) {
    return trimmed.replace(/\/capture\/?$/i, path);
  }
}

function normalizeMetadataUrl(value, baseUrl) {
  if (typeof value !== "string" || !value.trim()) return null;
  try {
    const normalized = new URL(value.trim(), baseUrl).href;
    return /^https?:\/\//i.test(normalized) ? normalized : null;
  } catch (_error) {
    return null;
  }
}

function normalizeDoi(value) {
  if (typeof value !== "string") return null;
  let doi = value.trim();
  if (!doi) return null;
  doi = doi.replace(/^doi:\s*/i, "");
  doi = doi.replace(/^https?:\/\/(dx\.)?doi\.org\//i, "");
  doi = doi.trim();
  return doi || null;
}

async function jsonOrNull(response) {
  try {
    return await response.json();
  } catch (_error) {
    return null;
  }
}

function responseErrors(data, fallback) {
  if (data && Array.isArray(data.errors) && data.errors.length > 0) return data.errors;
  if (data && typeof data.error === "string" && data.error.trim()) return [data.error.trim()];
  return [fallback];
}

// ── Search result detection (Tier 4, exported for testing + popup) ─────

const SEARCH_PATTERNS = [
  {
    name: "Google Scholar",
    urlPattern: "scholar\\.google\\.com\\/scholar",
    containerSelector: ".gs_ri, .gs_r.gs_or.gs_scl",
    titleSelector: ".gs_rt a, .gs_rt",
    urlAttr: "href",
    authorsSelector: ".gs_a",
    snippetSelector: ".gs_rs",
    minResults: 1,
  },
  {
    name: "PubMed",
    urlPattern: "pubmed\\.ncbi\\.nlm\\.nih\\.gov",
    containerSelector: ".docsum-content",
    titleSelector: ".docsum-title",
    urlLinkSelector: ".docsum-title",
    urlAttr: "href",
    authorsSelector: ".docsum-authors",
    snippetSelector: ".full-journal-citation",
    minResults: 1,
  },
  {
    name: "Semantic Scholar",
    urlPattern: "semanticscholar\\.org\\/search",
    containerSelector: '[data-selenium-selector="paper-search-result"]',
    titleSelector: '[data-selenium-selector="title-link"]',
    urlAttr: "href",
    authorsSelector: '[data-selenium-selector="author-list"]',
    yearSelector: '[data-selenium-selector="paper-year"]',
    minResults: 1,
  },
  {
    name: "arXiv",
    urlPattern: "arxiv\\.org\\/search",
    containerSelector: "li.arxiv-result",
    titleSelector: "p.title",
    urlLinkSelector: "p.list-title a",
    urlAttr: "href",
    authorsSelector: "p.authors",
    snippetSelector: "p.abstract",
    minResults: 1,
  },
  {
    name: "DBLP",
    urlPattern: "dblp\\.org\\/search",
    containerSelector: ".result-list li",
    titleSelector: ".title",
    urlLinkSelector: ".publ ul li a, a",
    urlAttr: "href",
    authorsSelector: "[itemprop='author']",
    yearSelector: "[itemprop='datePublished']",
    minResults: 1,
  },
  {
    name: "ResearchGate",
    urlPattern: "researchgate\\.net\\/search",
    containerSelector: "div.nova-legacy-c-card, div.gsc-result",
    titleSelector: "div.nova-legacy-c-card__body a.nova-legacy-e-link, a.nova-legacy-e-link--theme-solid",
    urlAttr: "href",
    authorsSelector: "div.nova-legacy-v-person-inline-item__fullname, div.nova-legacy-v-publication-item__author-list",
    yearSelector: "div.nova-legacy-v-publication-item__meta-item",
    minResults: 1,
  },
  {
    name: "CORE",
    urlPattern: "core\\.ac\\.uk\\/search",
    containerSelector: "div.search-result, li.search-result",
    titleSelector: "h3.title a, a.title",
    urlAttr: "href",
    authorsSelector: "div.authors, span.author",
    snippetSelector: "div.description, p.description",
    yearSelector: "span.date, time.year",
    minResults: 1,
  },
  {
    name: "BASE",
    urlPattern: "base-search\\.net",
    containerSelector: "div.result-item, .hit",
    titleSelector: "h4 a, a.title, .hit-title a",
    urlAttr: "href",
    authorsSelector: "span.author, .hit-author",
    snippetSelector: "div.snippet, .hit-abstract",
    yearSelector: "span.year, .hit-year",
    minResults: 1,
  },
  {
    name: "SSRN",
    urlPattern: "papers\\.ssrn\\.com\\/sol3\\/results",
    containerSelector: "div.result, div.search-result, tr.result",
    titleSelector: "h3 a, a.title, a.result-title",
    urlAttr: "href",
    authorsSelector: "div.authors, span.author",
    snippetSelector: "div.abstract-text, p.abstract",
    minResults: 1,
  },
  {
    name: "generic-doi-list",
    urlPattern: null,
    containerSelector: 'a[href*="doi.org/"]',
    titleSelector: null,
    urlAttr: "href",
    minResults: 10,
  },
];


export async function detectAndExtractSearchResults(tabId, pageUrl) {
  if (!tabId || !pageUrl) return { detected: false, items: [] };

  try {
    const [{ result }] = await chrome.scripting.executeScript({
      target: { tabId },
      func: (patterns, currentUrl) => {
        // --- Detection ---
        let matchedPattern = null;
        for (const pat of patterns) {
          if (pat.urlPattern) {
            const re = new RegExp(pat.urlPattern, "i");
            if (!re.test(currentUrl)) continue;
          }
          let containers;
          try { containers = document.querySelectorAll(pat.containerSelector); } catch (_e) { continue; }
          if (!containers || containers.length < (pat.minResults || 1)) continue;
          matchedPattern = pat;
          break;
        }
        if (!matchedPattern) return { detected: false, items: [] };

        // --- Extraction ---
        let containers;
        try { containers = document.querySelectorAll(matchedPattern.containerSelector); } catch (_e) { return { detected: true, patternName: matchedPattern.name, count: 0, items: [] }; }

        const items = [];
        let idx = 0;
        for (const container of Array.from(containers)) {
          if (idx >= 20) break;

          let titleEl = null;
          if (matchedPattern.titleSelector) {
            try { titleEl = container.querySelector(matchedPattern.titleSelector); } catch (_e) {}
          }
          const title = titleEl ? (titleEl.textContent || "").trim() : "";

          let urlEl = null;
          if (matchedPattern.urlLinkSelector) {
            try { urlEl = container.querySelector(matchedPattern.urlLinkSelector); } catch (_e) {}
          }
          if (!urlEl) urlEl = titleEl;
          if (!urlEl) {
            try { urlEl = container.querySelector("a"); } catch (_e) {}
          }
          if (!urlEl && container.tagName === "A") urlEl = container;

          const urlAttr = matchedPattern.urlAttr || "href";
          let url = null;
          if (urlEl) {
            url = (urlEl.getAttribute(urlAttr) || "").trim() || null;
            if (url && !/^https?:\/\//i.test(url) && urlEl.href) url = urlEl.href;
          }

          let authorsText = null;
          if (matchedPattern.authorsSelector) {
            try {
              const el = container.querySelector(matchedPattern.authorsSelector);
              if (el) authorsText = (el.textContent || "").trim() || null;
            } catch (_e) {}
          }

          let snippet = null;
          if (matchedPattern.snippetSelector) {
            try {
              const el = container.querySelector(matchedPattern.snippetSelector);
              if (el) snippet = (el.textContent || "").trim() || null;
            } catch (_e) {}
          }

          let year = null;
          if (matchedPattern.yearSelector) {
            try {
              const el = container.querySelector(matchedPattern.yearSelector);
              if (el) year = (el.textContent || "").trim() || null;
            } catch (_e) {}
          }

          if (title || url) {
            items.push({ title, url, authors: authorsText, snippet, year, index: idx });
            idx++;
          }
        }

        return {
          detected: true,
          patternName: matchedPattern.name,
          count: items.length,
          items,
        };
      },
      args: [SEARCH_PATTERNS, pageUrl],
    });

    return result || { detected: false, items: [] };
  } catch (_error) {
    return { detected: false, items: [] };
  }
}


export async function captureSearchResults(selectedItems, tags, bib, dryRun) {
  if (!Array.isArray(selectedItems) || selectedItems.length === 0) {
    return { status: "error", errors: ["no items selected"] };
  }
  const endpoint = await getEndpoint();
  const authHeaders = await getAuthHeaders();
  const results = [];

  for (const item of selectedItems) {
    if (!item.url) {
      results.push({ status: "error", message: "no URL for item", item_title: item.title });
      continue;
    }
    try {
      const response = await fetch(endpoint, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...authHeaders },
        body: JSON.stringify({
          url: item.url,
          tags,
          bib,
          dry_run: dryRun,
        }),
      });
      const data = await jsonOrNull(response);
      results.push(data || { status: "error", message: "invalid JSON" });
    } catch (err) {
      results.push({ status: "error", message: err?.message || String(err), item_url: item.url });
    }
  }

  return {
    status: "complete",
    total: selectedItems.length,
    results,
  };
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
            _addObserverEntry(details.url, details.tabId);
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
