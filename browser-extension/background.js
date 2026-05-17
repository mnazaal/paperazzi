const DEFAULT_HOST = "http://127.0.0.1:8765";
const DEFAULT_ENDPOINT = `${DEFAULT_HOST}/capture`;

export async function getEndpoint() {
  const stored = await chrome.storage.local.get("endpoint");
  return stored.endpoint || DEFAULT_ENDPOINT;
}

export async function getAuthHeaders() {
  const stored = await chrome.storage.local.get("authToken");
  return stored.authToken ? { "X-Pzi-Token": stored.authToken } : {};
}

export async function fetchBibs() {
  const endpoint = await getEndpoint();
  const bibsUrl = endpoint.replace(/\/capture$/, "/bibs");
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

export async function captureCurrentTab({ tags = [], bib = null, dryRun = false } = {}) {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab || !tab.url) {
    return { status: "error", errors: ["no active tab"] };
  }
  const pageMetadata = await extractPageMetadata(tab.id, tab.url);
  const pdfUrlCandidates = await extractPdfUrlCandidates(tab.id, tab.url);
  const endpoint = await getEndpoint();
  const authHeaders = await getAuthHeaders();
  const response = await fetch(endpoint, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders },
    body: JSON.stringify({
      url: tab.url,
      tags,
      bib,
      dry_run: dryRun,
      pdf_url_candidates: pdfUrlCandidates,
      page_title: pageMetadata.pageTitle,
      canonical_url: pageMetadata.canonicalUrl,
      source_url: pageMetadata.sourceUrl,
      abstract_url: pageMetadata.abstractUrl,
      doi: pageMetadata.doi,
    }),
  });
  const result = await response.json();
  if (!dryRun && result && result.status === "ok" && result.citekey && !result.pdf_path) {
    const pdfAttach = await maybeStreamPdfBytes({
      endpoint,
      citekey: result.citekey,
      bib,
      pdfUrlCandidates,
    });
    if (pdfAttach) {
      result.pdf_attach = pdfAttach;
    }
  }
  return result;
}

async function extractPageMetadata(tabId, pageUrl) {
  if (!tabId) {
    return { pageTitle: null, canonicalUrl: pageUrl, sourceUrl: pageUrl, abstractUrl: pageUrl, doi: null };
  }
  try {
    const [{ result }] = await chrome.scripting.executeScript({
      target: { tabId },
      func: (currentUrl) => {
        const contentOf = (selector) => document.querySelector(selector)?.getAttribute("content") || null;
        const hrefOf = (selector) => document.querySelector(selector)?.getAttribute("href") || null;
        const doiMeta =
          contentOf('meta[name="citation_doi"]') ||
          contentOf('meta[name="dc.Identifier"]') ||
          contentOf('meta[name="DC.Identifier"]');
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
        };
      },
      args: [pageUrl],
    });
    return result || { pageTitle: null, canonicalUrl: pageUrl, sourceUrl: pageUrl, abstractUrl: pageUrl, doi: null };
  } catch (_error) {
    return { pageTitle: null, canonicalUrl: pageUrl, sourceUrl: pageUrl, abstractUrl: pageUrl, doi: null };
  }
}

async function extractPdfUrlCandidates(tabId, pageUrl) {
  const candidates = [];
  const addCandidate = (value) => {
    if (typeof value !== "string") return;
    const trimmed = value.trim();
    if (!trimmed || !isSafePublicHttpUrl(trimmed)) return;
    if (!candidates.includes(trimmed)) candidates.push(trimmed);
  };
  if (looksLikePdfUrl(pageUrl)) {
    addCandidate(pageUrl);
  }
  if (!tabId) {
    return candidates;
  }
  try {
    const [{ result }] = await chrome.scripting.executeScript({
      target: { tabId },
      func: () => {
        const out = [];
        const add = (value) => {
          if (typeof value !== "string") return;
          const trimmed = value.trim();
          if (!trimmed) return;
          let absolute;
          try {
            absolute = new URL(trimmed, document.baseURI).href;
          } catch (_error) {
            return;
          }
          if (!/^https?:\/\//i.test(absolute)) return;
          if (!out.includes(absolute)) out.push(absolute);
        };

        document
          .querySelectorAll(
            'meta[name="citation_pdf_url"], meta[name="wkhealth_pdf_url"], meta[name="eprints.document_url"]'
          )
          .forEach((node) => add(node.getAttribute("content")));

        document.querySelectorAll('link[rel="alternate"][type="application/pdf"], a[href], link[href]').forEach((node) => {
          const href = node.href || node.getAttribute("href");
          const text = (node.textContent || node.getAttribute("title") || node.getAttribute("aria-label") || "").toLowerCase();
          const type = (node.getAttribute("type") || "").toLowerCase();
          if (/\.pdf([?#].*)?$/i.test(href || "") || type.includes("pdf") || text.includes("pdf")) {
            add(href);
          }
        });
        return out;
      },
    });
    if (Array.isArray(result)) {
      for (const candidate of result) addCandidate(candidate);
    }
    return candidates;
  } catch (_error) {
    return candidates;
  }
}

function looksLikePdfUrl(value) {
  return typeof value === "string" && /\.pdf([?#].*)?$/i.test(value.trim());
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

async function maybeStreamPdfBytes({ endpoint, citekey, bib, pdfUrlCandidates }) {
  for (const candidate of pdfUrlCandidates || []) {
    try {
      const response = await fetch(candidate, { credentials: "include" });
      const contentType = response.headers.get("content-type") || "";
      if (!response.ok) {
        continue;
      }
      const bytes = await response.arrayBuffer();
      if (!looksLikePdfBytes(bytes) && !contentType.toLowerCase().includes("pdf")) {
        continue;
      }
      const base64 = arrayBufferToBase64(bytes);
      const attachResponse = await fetch(endpoint.replace(/\/capture$/, "/attach-pdf-bytes"), {
        method: "POST",
        headers: { "Content-Type": "application/json", ...(await getAuthHeaders()) },
        body: JSON.stringify({
          citekey,
          bib,
          source_url: candidate,
          pdf_base64: base64,
        }),
      });
      const attachResult = await attachResponse.json();
      return attachResult;
    } catch (_error) {
      continue;
    }
  }
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

chrome.runtime.onInstalled.addListener(() => {
  console.log("pzi capture extension installed");
});
