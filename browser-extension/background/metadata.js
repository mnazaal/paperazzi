// pzi browser extension — page and IEEE Xplore metadata extraction.
//
// Reads citation_*/JSON-LD/OpenGraph metadata from the active tab via
// chrome.scripting, plus IEEE Xplore's embedded xplGlobal metadata.

import { firstString, tryHostname } from "./utils.js";

//: How much of the page's `<head>` travels with a capture. It was sent whole:
//: publisher pages inline JSON-LD, analytics and stylesheets there, so a
//: routine capture shipped hundreds of kilobytes that the server keeps in a
//: page artifact. What is actually read out of it — `<meta>` tags, JSON-LD,
//: OpenGraph — sits at the top, so a prefix keeps the useful part.
const MAX_HEAD_HTML_CHARS = 64 * 1024;

const _EMPTY_METADATA = { pageTitle: null, canonicalUrl: null, sourceUrl: null, abstractUrl: null, doi: null };

// Publisher pages whose page-realm globals pzi reads and marks trusted. Matched
// on the *host*, exactly or as a subdomain — `endsWith` on the raw string also
// accepts `evil-ieeexplore.ieee.org`.
const _TRUSTED_PUBLISHER_HOSTS = ["ieeexplore.ieee.org"];

export function isTrustedPublisherUrl(pageUrl) {
  let host;
  try {
    host = new URL(pageUrl).hostname.toLowerCase();
  } catch (_) {
    return false;
  }
  return _TRUSTED_PUBLISHER_HOSTS.some(
    (trusted) => host === trusted || host.endsWith(`.${trusted}`)
  );
}

export async function extractPageMetadata(tabId, pageUrl) {
  if (!tabId) {
    return {
      ..._EMPTY_METADATA,
      canonicalUrl: pageUrl, sourceUrl: pageUrl, abstractUrl: pageUrl,
    };
  }
  // Decided here, in the extension's own realm, from the URL the caller passed.
  // The publisher check used to run *inside* the page as
  // `location.hostname.endsWith(...)`, in the MAIN world where a page can
  // replace `String.prototype.endsWith` — so any page could pass the gate,
  // populate `window.xplGlobal.document.metadata`, and have its nine
  // `trusted_fields` promoted by the server into authoritative overrides that
  // beat the real Crossref lookup.
  const trustedPublisher = isTrustedPublisherUrl(pageUrl);
  try {
    const [{ result }] = await chrome.scripting.executeScript({
      target: { tabId },
      // MAIN only for a publisher whose page-realm globals we deliberately
      // read; every other page is read from ISOLATED, where the page cannot
      // reach the reader at all.
      world: trustedPublisher ? "MAIN" : "ISOLATED",
      func: (currentUrl, maxHeadHtml, isTrustedPublisher) => {
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
          if (!isTrustedPublisher) return null;
          const meta = window.xplGlobal && window.xplGlobal.document && window.xplGlobal.document.metadata;
          if (!meta || typeof meta !== "object") return null;
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
          pageTitle: ieee?.title || document.title || null,
          headHtml: document.head ? document.head.innerHTML.slice(0, maxHeadHtml) : null,
          canonicalUrl: hrefOf('link[rel="canonical"]') || currentUrl || null,
          sourceUrl: currentUrl || null,
          abstractUrl:
            contentOf('meta[name="citation_abstract_html_url"]') ||
            hrefOf('link[rel="canonical"]') ||
            currentUrl ||
            null,
          doi: doiMeta || ieee?.doi || null,
          // Rich fields
          embedded_authors: authors || ieee?.authors || null,
          embedded_year: yearFromMeta ? String(yearFromMeta).slice(0, 4) : ieee?.year,
          embedded_venue: venue || ieee?.venue || null,
          embedded_abstract: abstract || ieee?.abstract || null,
          embedded_volume: volume,
          embedded_issue: issue,
          embedded_pages: pages || ieee?.pages || null,
          embedded_issn: issn || ieee?.issn || null,
          embedded_isbn: isbn || ieee?.isbn || null,
          embedded_pdf_url: pdfUrl || ieee?.pdfUrl || null,
          // JSON-LD / OG fallbacks (used when citation_* is absent)
          embedded_jsonld_authors: jsonldAuthors,
          embedded_jsonld_title: jsonldTitle,
          embedded_jsonld_year: jsonldYear,
          embedded_og_title: ogTitle,
          metadata_source: ieee ? "ieee_xplore" : "generic_dom",
          trusted_fields: ieee ? ["doi", "authors", "year", "title", "venue", "abstract", "pages", "issn", "isbn"] : null,
        };
      },
      args: [pageUrl, MAX_HEAD_HTML_CHARS, trustedPublisher],
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
