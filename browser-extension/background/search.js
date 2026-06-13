// pzi browser extension — search-result detection and bulk capture (Tier 4).
//
// Detects academic search-result pages (Google Scholar, PubMed, arXiv, …) and
// extracts per-result metadata for the popup's multi-capture flow.

import { getAuthHeaders, getEndpoint } from "./config.js";
import { jsonOrNull } from "./utils.js";

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
