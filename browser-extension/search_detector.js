// ── Search result detection patterns (Tier 4) ───────────────────────────
// Each pattern is a pure data descriptor — no functions, safe to serialize
// into injected scripts via chrome.scripting.executeScript or postMessage.
//
// A generic extractor (*) uses these fields to scrape result items:
//   containerSelector  — CSS selector for each result container
//   titleSelector      — CSS selector(s) for the item title
//   urlAttr            — attribute to read for the item URL (from titleSelector)
//   urlLinkSelector    — alternate anchor inside container for URL
//   authorsSelector    — CSS selector for author text
//   snippetSelector    — CSS selector for abstract/snippet text
//   yearSelector       — CSS selector for year text
//   urlPattern         — regex source string for URL-based detection (fast path)
//   minResults         — minimum containers needed to consider it a result page

export const SEARCH_PATTERNS = [
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

  // Generic fallback: pages with many DOI links in a list-like structure.
  // Higher threshold to reduce false positives on article reference sections.
  {
    name: "generic-doi-list",
    urlPattern: null,
    containerSelector: 'a[href*="doi.org/"]',
    titleSelector: null,
    urlAttr: "href",
    minResults: 10,
  },
];


// ── Detection — injected into the page via executeScript ────────────────


/**
 * Checks whether the current page matches any search pattern.
 * Runs in the page context. Returns { detected, patternName, count }.
 */
export function pageSearchDetection(patterns, currentUrl) {
  if (!currentUrl) return { detected: false };

  for (const pat of patterns) {
    // Fast path: URL pattern match (only if pattern has a urlPattern).
    if (pat.urlPattern) {
      const re = new RegExp(pat.urlPattern, "i");
      if (!re.test(currentUrl)) continue;
    }

    // DOM check: count how many container elements exist.
    let containers;
    try {
      containers = document.querySelectorAll(pat.containerSelector);
    } catch (_e) {
      continue;
    }
    if (!containers || containers.length < (pat.minResults || 1)) continue;

    return {
      detected: true,
      patternName: pat.name,
      count: containers.length,
    };
  }

  return { detected: false };
}


/**
 * Extracts result items from the page for a matched pattern.
 * Runs in the page context. Returns an array of { title, url, authors, snippet, year }.
 */
export function pageSearchExtraction(patterns, patternName) {
  const pat = patterns.find((p) => p.name === patternName);
  if (!pat) return [];

  let containers;
  try {
    containers = document.querySelectorAll(pat.containerSelector);
  } catch (_e) {
    return [];
  }
  if (!containers) return [];

  const items = [];
  let index = 0;

  for (const container of containers) {
    if (index >= 20) break;  // cap display at 20 items

    // Title
    let titleEl = null;
    if (pat.titleSelector) {
      try { titleEl = container.querySelector(pat.titleSelector); } catch (_e) {}
    }
    const title = titleEl ? (titleEl.textContent || "").trim() : "";

    // URL — prefer urlLinkSelector, then titleEl, then container itself
    let urlEl = null;
    if (pat.urlLinkSelector) {
      try { urlEl = container.querySelector(pat.urlLinkSelector); } catch (_e) {}
    }
    if (!urlEl) urlEl = titleEl;
    if (!urlEl && pat.containerSelector && container.tagName !== "A") {
      try { urlEl = container.querySelector("a"); } catch (_e) {}
    }
    // For generic-doi-list, container IS the anchor element
    if (!urlEl && container.tagName === "A") urlEl = container;

    const urlAttr = pat.urlAttr || "href";
    let url = null;
    if (urlEl) {
      url = (urlEl.getAttribute(urlAttr) || "").trim() || null;
      // Resolve relative URLs
      if (url && !/^https?:\\/\\//i.test(url) && urlEl.href) url = urlEl.href;
    }

    // Authors
    let authorsText = null;
    if (pat.authorsSelector) {
      try {
        const el = container.querySelector(pat.authorsSelector);
        if (el) authorsText = (el.textContent || "").trim();
      } catch (_e) {}
    }

    // Snippet / abstract
    let snippet = null;
    if (pat.snippetSelector) {
      try {
        const el = container.querySelector(pat.snippetSelector);
        if (el) snippet = (el.textContent || "").trim();
      } catch (_e) {}
    }

    // Year
    let year = null;
    if (pat.yearSelector) {
      try {
        const el = container.querySelector(pat.yearSelector);
        if (el) year = (el.textContent || "").trim();
      } catch (_e) {}
    }

    if (title || url) {
      items.push({ title, url, authors: authorsText, snippet, year, index });
      index++;
    }
  }

  return items;
}
