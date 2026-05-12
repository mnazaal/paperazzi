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
  const pdfUrlCandidates = await extractPdfUrlCandidates(tab.id);
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
  if (!dryRun && result && result.status === "ok" && result.citekey) {
    await maybeStreamPdfBytes({
      endpoint,
      citekey: result.citekey,
      bib,
      pdfUrlCandidates,
    });
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

async function extractPdfUrlCandidates(tabId) {
  if (!tabId) {
    return [];
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
          if (!/^https?:\/\//i.test(trimmed)) return;
          if (!out.includes(trimmed)) out.push(trimmed);
        };

        document
          .querySelectorAll('meta[name="citation_pdf_url"], meta[name="wkhealth_pdf_url"]')
          .forEach((node) => add(node.getAttribute("content")));

        document.querySelectorAll("a[href], link[href]").forEach((node) => {
          const href = node.href || node.getAttribute("href");
          const text = (node.textContent || node.getAttribute("title") || "").toLowerCase();
          if (/\.pdf([?#].*)?$/i.test(href || "") || text.includes("pdf")) {
            add(href);
          }
        });
        return out;
      },
    });
    return Array.isArray(result) ? result : [];
  } catch (_error) {
    return [];
  }
}

async function maybeStreamPdfBytes({ endpoint, citekey, bib, pdfUrlCandidates }) {
  for (const candidate of pdfUrlCandidates || []) {
    try {
      const response = await fetch(candidate, { credentials: "include" });
      const contentType = response.headers.get("content-type") || "";
      if (!response.ok || !contentType.toLowerCase().includes("pdf")) {
        continue;
      }
      const bytes = await response.arrayBuffer();
      const base64 = arrayBufferToBase64(bytes);
      await fetch(endpoint.replace(/\/capture$/, "/attach-pdf-bytes"), {
        method: "POST",
        headers: { "Content-Type": "application/json", ...(await getAuthHeaders()) },
        body: JSON.stringify({
          citekey,
          bib,
          source_url: candidate,
          pdf_base64: base64,
        }),
      });
      return;
    } catch (_error) {
      continue;
    }
  }
}

function arrayBufferToBase64(buffer) {
  const bytes = new Uint8Array(buffer);
  let binary = "";
  for (const byte of bytes) {
    binary += String.fromCharCode(byte);
  }
  return btoa(binary);
}

chrome.runtime.onInstalled.addListener(() => {
  console.log("pzi capture extension installed");
});
