// pzi browser extension — PDF URL discovery.
//
// Finds candidate PDF URLs for a page via several tiers: DOM/meta scanning,
// URL-based site-specific extractors, and hidden-iframe discovery that relies
// on the network observer. Also owns the bot-bypass domain allowlist.

import { isSafePublicHttpUrl, originOf, sameOrigin, tryHostname } from "./utils.js";
import {
  getObserverUrlsForDomain,
  observerCacheUrls,
  recentObserverUrls,
} from "./observer.js";

// ── DOM PDF scanner (exported for testing) ──────────────────────────────
// Keep the meta selectors in sync with the injected func in
// extractPageMetadata (background/metadata.js).
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

export async function extractPdfUrlCandidates(tabId, pageUrl) {
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
    const observerUrls = getObserverUrlsForDomain(pageDomain);
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
  const after = recentObserverUrls();
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
  const beforeUrls = observerCacheUrls();

  // 3. Load each discovered URL in a hidden iframe (parallel).
  const iframePromises = discoveredUrls.map((url) => _injectHiddenIframe(tabId, url));
  await Promise.all(iframePromises);

  // 4. Small wait for observer to process responses.
  await new Promise((r) => setTimeout(r, 500));

  // 5. Return new observer-caught entries.
  return _collectObserverUrlsAfterIframe(beforeUrls);
}

export function buildPdfCandidates(urls, pageUrl, observedUrls = []) {
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
