const DEFAULT_HOST = "http://127.0.0.1:8765";
const DEFAULT_ENDPOINT = `${DEFAULT_HOST}/capture`;
const PDF_FETCH_TIMEOUT_MS = 30000;
const MAX_ATTACH_PDF_BYTES = 47 * 1024 * 1024;
let _captureTabId = null;

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

export async function captureCurrentTab({ tags = [], bib = null, dryRun = false } = {}) {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab || !tab.url) {
    return { status: "error", errors: ["no active tab"] };
  }
  _captureTabId = tab.id;

  // Start network observer before extraction to catch PDF redirects/dynamic loads.
  startPdfObserver(tab.id);

  const pageMetadata = await extractPageMetadata(tab.id, tab.url);
  pageMetadata.canonicalUrl = normalizeMetadataUrl(pageMetadata.canonicalUrl, tab.url) || tab.url;
  pageMetadata.sourceUrl = normalizeMetadataUrl(pageMetadata.sourceUrl, tab.url) || tab.url;
  pageMetadata.abstractUrl = normalizeMetadataUrl(pageMetadata.abstractUrl, tab.url) || pageMetadata.canonicalUrl || tab.url;
  pageMetadata.doi = normalizeDoi(pageMetadata.doi) || doiFromKnownPreprintUrl(tab.url);
  const pdfUrlCandidates = await extractPdfUrlCandidates(tab.id, tab.url);

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
  const endpoint = await getEndpoint();
  const authHeaders = await getAuthHeaders();
  // Extract browser cookies for the page domain (Tier 1 cookie bridge).
  const cookieHeader = await cookieHeaderForUrl(tab.url);
  const response = await fetch(endpoint, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders },
    body: JSON.stringify({
      url: tab.url,
      browser: detectBrowser(),
      tags,
      bib,
      dry_run: dryRun,
      verbose: true,
      cookies: cookieHeader,
      pdf_url_candidates: pdfUrlCandidates,
      pdf_candidates: pdfCandidates,
      page_title: pageMetadata.pageTitle,
      canonical_url: pageMetadata.canonicalUrl,
      source_url: pageMetadata.sourceUrl,
      abstract_url: pageMetadata.abstractUrl,
      doi: pageMetadata.doi,
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
    }),
  });
  const result = await jsonOrNull(response);
  if (!response.ok) {
    return {
      status: "error",
      errors: responseErrors(result, `capture request failed: HTTP ${response.status} ${response.statusText || ""}`.trim()),
    };
  }
  if (!result) {
    return { status: "error", errors: ["capture request failed: invalid JSON response"] };
  }
  if (!dryRun && result && result.status === "ok" && result.citekey && !result.pdf_path) {
    const pdfAttach = await maybeStreamPdfBytes({
      endpoint,
      citekey: result.citekey,
      bib,
      pdfUrlCandidates: pdfCandidates,
      pageUrl: tab.url,
    });
    if (pdfAttach) {
      result.pdf_attach = pdfAttach;
      if (pdfAttach.pdf_attach_attempts) {
        result.pdf_attach_attempts = pdfAttach.pdf_attach_attempts;
        delete pdfAttach.pdf_attach_attempts;
      }
      if (pdfAttach.pdf_attach_permission) {
        result.pdf_attach_permission = pdfAttach.pdf_attach_permission;
      }
      if (pdfAttach.pdf_attach_cookie_permission) {
        result.pdf_attach_cookie_permission = pdfAttach.pdf_attach_cookie_permission;
      }
      if (pdfAttach.status === "ok") {
        result.warnings = filterStalePdfWarnings(result.warnings, {
          attachedUrl: pdfAttach.source_url,
          staleError: result.pdf_error,
          attemptedUrls: [tab.url, ...pdfUrlCandidates],
        });
      }
    }
  }
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
          pageTitle: document.title || null,
          canonicalUrl: hrefOf('link[rel="canonical"]') || currentUrl || null,
          sourceUrl: currentUrl || null,
          abstractUrl:
            contentOf('meta[name="citation_abstract_html_url"]') ||
            hrefOf('link[rel="canonical"]') ||
            currentUrl ||
            null,
          doi: doiMeta,
          // Rich fields
          embedded_authors: authors,
          embedded_year: yearFromMeta ? String(yearFromMeta).slice(0, 4) : null,
          embedded_venue: venue,
          embedded_abstract: abstract,
          embedded_volume: volume,
          embedded_issue: issue,
          embedded_pages: pages,
          embedded_issn: issn,
          embedded_isbn: isbn,
          embedded_pdf_url: pdfUrl,
          // JSON-LD / OG fallbacks (used when citation_* is absent)
          embedded_jsonld_authors: jsonldAuthors,
          embedded_jsonld_title: jsonldTitle,
          embedded_jsonld_year: jsonldYear,
          embedded_og_title: ogTitle,
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
export function extractRichMetadataFromDoc(doc, currentUrl) {
  const contentOf = (selector) => {
    const el = doc.querySelector ? doc.querySelector(selector) : null;
    if (!el) return null;
    return (el.getAttribute("content") || "").trim() || null;
  };
  const hrefOf = (selector) => {
    const el = doc.querySelector ? doc.querySelector(selector) : null;
    if (!el) return null;
    return (el.getAttribute("href") || "").trim() || null;
  };
  const allContentOf = (selector) => {
    const nodes = doc.querySelectorAll ? doc.querySelectorAll(selector) : [];
    const vals = [];
    Array.from(nodes).forEach((n) => {
      const v = (n.getAttribute("content") || "").trim();
      if (v) vals.push(v);
    });
    return vals.length ? vals : null;
  };

  const doiMeta =
    contentOf('meta[name="citation_doi"]') ||
    contentOf('meta[name="dc.Identifier"]') ||
    contentOf('meta[name="DC.Identifier"]');

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

  // JSON-LD
  let jsonldAuthors = null, jsonldTitle = null, jsonldYear = null;
  try {
    const scripts = doc.querySelectorAll
      ? doc.querySelectorAll('script[type="application/ld+json"]')
      : [];
    for (const script of Array.from(scripts)) {
      const data = JSON.parse((script.textContent || ""));
      const type = data["@type"] || "";
      if (!/Article/i.test(String(type))) continue;
      const jldA = (data.author || []).map((a) =>
        typeof a === "string" ? a : (a && a.name ? a.name : "")
      ).filter(Boolean);
      if (jldA.length) jsonldAuthors = jldA;
      if (data.name || data.headline) jsonldTitle = data.name || data.headline;
      if (data.datePublished) jsonldYear = String(data.datePublished).slice(0, 4);
      break;
    }
  } catch (_) { /* ignore */ }

  // OpenGraph
  const ogTitle =
    contentOf('meta[property="og:title"]') ||
    contentOf('meta[name="twitter:title"]');

  return {
    pageTitle: (doc.title || null),
    canonicalUrl: hrefOf('link[rel="canonical"]') || currentUrl || null,
    sourceUrl: currentUrl || null,
    abstractUrl:
      contentOf('meta[name="citation_abstract_html_url"]') ||
      hrefOf('link[rel="canonical"]') ||
      currentUrl ||
      null,
    doi: doiMeta,
    embedded_authors: authors,
    embedded_year: yearFromMeta ? String(yearFromMeta).slice(0, 4) : null,
    embedded_venue: venue,
    embedded_abstract: abstract,
    embedded_volume: volume,
    embedded_issue: issue,
    embedded_pages: pages,
    embedded_issn: issn,
    embedded_isbn: isbn,
    embedded_pdf_url: pdfUrl,
    embedded_jsonld_authors: jsonldAuthors,
    embedded_jsonld_title: jsonldTitle,
    embedded_jsonld_year: jsonldYear,
    embedded_og_title: ogTitle,
  };
}

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

// ── Click-based PDF discovery (Tier 3, exported for testing) ──────────
// Ported from browser_pdf_hook.py::_click_downloadish_links.
export async function clickPdfDiscovery(tabId, pageUrl) {
  if (!tabId) return [];
  try {
    const [{ result }] = await chrome.scripting.executeScript({
      target: { tabId },
      func: () => {
        // Text patterns to match in element textContent (lowercase).
        const TEXT_PATTERNS = ["pdf", "download pdf", "full text pdf", "view pdf"];
        // Href substrings for anchor tags.
        const HREF_PATTERNS = [".pdf", "/pdf/"];

        for (const tag of ["a", "button"]) {
          const elements = document.querySelectorAll(tag);
          for (const el of elements) {
            const text = (el.textContent || "").toLowerCase();
            const href = (el.getAttribute("href") || "").toLowerCase();
            // Skip elements that smell like destructive actions.
            if (/delete|remove|archive|flag|report/i.test(text)) continue;
            // Match text content patterns.
            for (const pat of TEXT_PATTERNS) {
              if (text.includes(pat)) {
                el.click();
                return { clicked: true, selector: `${tag}:has-text("${pat}")` };
              }
            }
            // Match href patterns (only for anchors).
            if (tag === "a") {
              for (const pat of HREF_PATTERNS) {
                if (href.includes(pat)) {
                  el.click();
                  return { clicked: true, selector: `${tag}[href*="${pat}"]` };
                }
              }
            }
          }
        }
        return { clicked: false };
      },
    });

    // Wait for PDF observer to catch any downloads triggered by the click.
    await new Promise((resolve) => setTimeout(resolve, 2500));
    return collectObservedPdfUrls();
  } catch (_error) {
    return [];
  }
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
let _pdfObserverListener = null;

function startPdfObserver(tabId) {
  stopPdfObserver();
  _pdfObserverUrls = [];
  if (typeof chrome === "undefined" || !chrome.webRequest || !chrome.webRequest.onHeadersReceived) return;

  _pdfObserverListener = (details) => {
    if (details.tabId !== tabId && details.tabId !== -1) return;
    const ct = (details.responseHeaders || []).find(
      (h) => h.name.toLowerCase() === "content-type",
    );
    if (ct && ct.value && ct.value.toLowerCase().includes("application/pdf")) {
      _pdfObserverUrls.push(details.url);
    }
  };

  try {
    chrome.webRequest.onHeadersReceived.addListener(
      _pdfObserverListener,
      {
        urls: ["https://*/*", "http://*/*"],
        types: ["main_frame", "sub_frame", "xmlhttprequest"],
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

// ── Bot/JS bypass via hidden iframe ──────────────────────────────────────
// For allowlisted domains, inject a hidden iframe to the failed URL,
// letting the page's JS redirect to the real PDF. The network observer
// catches the resulting PDF response.

const BOT_BYPASS_IFRAME_TIMEOUT_MS = 5000;

export async function botBypassPdfUrl(tabId, candidateUrl) {
  if (!tabId || !candidateUrl) return null;
  if (!isBotBypassWhitelisted(candidateUrl)) return null;

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
    return observed.find((u) => u !== candidateUrl && /^https?:\/\//i.test(u)) || null;
  } catch (_e) {
    return null;
  } finally {
    stopPdfObserver();
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

async function maybeStreamPdfBytes({ endpoint, citekey, bib, pdfUrlCandidates, pageUrl }) {
  const attempts = [];
  const grouped = groupPdfCandidates(pdfUrlCandidates || [], pageUrl);
  const sameOriginAttach = await tryPdfCandidates({
    endpoint,
    citekey,
    bib,
    candidates: grouped.sameOrigin,
    pageUrl,
    attempts,
  });
  if (sameOriginAttach) {
    sameOriginAttach.pdf_attach_attempts = attempts;
    return sameOriginAttach;
  }

  let lastPermission = null;
  for (const candidates of groupCandidatesByOrigin(grouped.crossOrigin)) {
    const permission = await requestTemporaryOriginPermission(candidates[0]);
    lastPermission = permission;
    if (permission.status !== "granted") {
      continue;
    }
    const attach = await tryPdfCandidates({
      endpoint,
      citekey,
      bib,
      candidates,
      permission,
      pageUrl,
      attempts,
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
}

async function tryPdfCandidates({ endpoint, citekey, bib, candidates, permission = null, pageUrl = null, attempts = [] }) {
  for (const candidate of candidates) {
    const url = candidateUrl(candidate);
    if (!url) continue;
    try {
      const fetched = await fetchPdfCandidate(url, { pageUrl, attempts });
      if (!fetched) continue;
      const attachResult = await attachPdfToServer({ endpoint, citekey, bib, sourceUrl: url, bytes: fetched.bytes });
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

async function attachPdfToServer({ endpoint, citekey, bib, sourceUrl, bytes }) {
  const rawResponse = await fetch(rawAttachUrl(endpoint, { citekey, bib, sourceUrl }), {
    method: "POST",
    headers: { "Content-Type": "application/pdf", ...(await getAuthHeaders()) },
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

function rawAttachUrl(endpoint, { citekey, bib, sourceUrl }) {
  const params = new URLSearchParams({ citekey });
  if (bib) params.set("bib", bib);
  params.set("source_url", sourceUrl);
  return `${endpointFor(endpoint, "/attach-pdf-raw")}?${params.toString()}`;
}

async function fetchPdfCandidate(candidate, { pageUrl = null, attempts = [] } = {}) {
  const url = candidateUrl(candidate);
  const first = await fetchCandidateBytes(candidate, { credentials: "include" }, "browser_fetch", attempts);
  if (first) return first;

  if (shouldRetryWithCookies(candidate, pageUrl)) {
    const cookiePermission = await requestCookiePermission();
    if (cookiePermission.status === "granted") {
      const cookieHeader = await cookieHeaderForUrl(url);
      if (cookieHeader) {
        const second = await fetchCandidateBytes(candidate, {
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
  if (url && isBotBypassWhitelisted(url)) {
    const bypassedUrl = await botBypassPdfUrl(_captureTabId, url);
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
    return /\.pdf([?#].*)?$/i.test(parsed.pathname) || /[?&](pdf|download)=/i.test(parsed.search);
  } catch (_error) {
    return false;
  }
}

async function fetchCandidateBytes(candidate, options, mode, attempts) {
  const response = await fetchWithTimeout(candidate, options, PDF_FETCH_TIMEOUT_MS);
  const contentType = response.headers.get("content-type") || "";
  if (!response.ok) {
    attempts.push({ url: candidate, mode, status: "http_error", http_status: response.status || null, content_type: contentType, byte_count: 0 });
    return null;
  }
  if (contentLengthExceedsAttachLimit(response.headers)) {
    attempts.push({ url: candidate, mode, status: "too_large", http_status: response.status || null, content_type: contentType, byte_count: Number.parseInt(response.headers.get("content-length") || "0", 10) || 0 });
    return null;
  }
  const bytes = await response.arrayBuffer();
  if (bytes.byteLength > MAX_ATTACH_PDF_BYTES) {
    attempts.push({ url: candidate, mode, status: "too_large", http_status: response.status || null, content_type: contentType, byte_count: bytes.byteLength });
    return null;
  }
  if (!looksLikePdfBytes(bytes) && !contentType.toLowerCase().includes("pdf")) {
    attempts.push({ url: candidate, mode, status: "not_pdf", http_status: response.status || null, content_type: contentType, byte_count: bytes.byteLength });
    return null;
  }
  attempts.push({ url: candidate, mode, status: "fetched", http_status: response.status || null, content_type: contentType, byte_count: bytes.byteLength });
  return { bytes };
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
    return { status: "denied", origin: originOf(candidateUrl(url)), removed: false };
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


chrome.runtime.onInstalled.addListener(() => {
  console.log("pzi capture extension installed");
});
