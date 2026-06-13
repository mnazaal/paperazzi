// pzi browser extension — pure utility functions.
//
// Zero chrome.* API dependencies. All functions are pure or use only
// standard browser globals (URL, TextDecoder, etc.).

import { MAX_ATTACH_PDF_BYTES } from "./config.js";

// ── Type coercion helpers ──────────────────────────────────────────────

export function firstString(value) {
  if (typeof value === "string" && value.trim()) return value.trim();
  if (Array.isArray(value)) {
    for (const item of value) {
      const found = firstString(item?.value ?? item);
      if (found) return found;
    }
  }
  return null;
}

// ── URL helpers ─────────────────────────────────────────────────────────

export function tryHostname(url) {
  try {
    return new URL(url).hostname;
  } catch (_e) {
    return null;
  }
}

export function originOf(url) {
  if (!url) return "";
  try {
    const parsed = new URL(url);
    return `${parsed.protocol}//${parsed.hostname}${parsed.port ? ":" + parsed.port : ""}`;
  } catch (_) {
    return "";
  }
}

export function sameOrigin(a, b) {
  return typeof a === "string" && typeof b === "string" && !!a && !!b && originOf(a) === originOf(b);
}

export function candidateUrl(candidate) {
  if (typeof candidate === "string") return candidate;
  if (candidate && typeof candidate.url === "string") return candidate.url;
  return null;
}

// ── Normalization ───────────────────────────────────────────────────────

export function normalizeMetadataUrl(raw, pageUrl) {
  switch (typeof raw) {
    case "string": {
      if (!raw) return null;
      try {
        return new URL(raw, pageUrl).href;
      } catch (_) {
        return raw;
      }
    }
    case "object": {
      if (Array.isArray(raw) && raw.length > 0) return normalizeMetadataUrl(raw[0], pageUrl);
      return null;
    }
    default: return null;
  }
}

export function normalizeDoi(raw) {
  if (typeof raw !== "string" || !raw.trim()) return null;
  let doi = raw.trim().replace(/^https?:\/\/doi\.org\//i, "");
  return doi || null;
}

// ── HTTP/fetch utilities ────────────────────────────────────────────────

export function jsonOrNull(response) {
  try {
    return response.json();
  } catch (_error) {
    return Promise.resolve(null);
  }
}

export function responseErrors(data, defaultMessage) {
  if (!data) return [defaultMessage];
  const errors = Array.isArray(data.errors) ? data.errors : [];
  return errors.length > 0 ? errors : [data.message || defaultMessage];
}

export function arrayBufferToBase64(buffer) {
  let binary = "";
  const bytes = new Uint8Array(buffer);
  for (let i = 0; i < bytes.byteLength; i++) {
    binary += String.fromCharCode(bytes[i]);
  }
  return btoa(binary);
}

// ── Content validation ──────────────────────────────────────────────────

export function contentLengthExceedsAttachLimit(headers) {
  const raw = headers.get("content-length");
  return raw ? Number.parseInt(raw, 10) > MAX_ATTACH_PDF_BYTES : false;
}

export function looksLikePdfBytes(bytes) {
  if (!bytes || bytes.byteLength < 5) return false;
  const header = new Uint8Array(bytes.slice(0, 5));
  return header[0] === 0x25 && header[1] === 0x50 && header[2] === 0x44 && header[3] === 0x46; // %PDF
}

// ── URL safety ──────────────────────────────────────────────────────────

export function isSafePublicHttpUrl(value) {
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

// ── Timing helpers ──────────────────────────────────────────────────────

export function sleep(ms) {
  return new Promise((resolve) => { setTimeout(resolve, ms); });
}

// ── PDF warning filtering ───────────────────────────────────────────────

export function filterStalePdfWarnings(warnings, { attachedUrl = null, staleError = null, attemptedUrls = [] } = {}) {
  if (!Array.isArray(warnings)) return warnings;
  return warnings.filter((warning) => !isSupersededPdfWarning(warning, { attachedUrl, staleError, attemptedUrls }));
}

function isSupersededPdfWarning(warning, { attachedUrl, staleError, attemptedUrls }) {
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

// ── DOI from known preprint URL ─────────────────────────────────────────

export function doiFromKnownPreprintUrl(pageUrl) {
  if (typeof pageUrl !== "string") return null;
  let parsed;
  try { parsed = new URL(pageUrl); } catch (_error) { return null; }
  const host = parsed.hostname.toLowerCase();
  if (!host.endsWith("biorxiv.org") && !host.endsWith("medrxiv.org")) return null;
  const match = parsed.pathname.match(/\/content\/(10\.\d{4,9}\/[^/?#]+)/i);
  if (!match) return null;
  return decodeURIComponent(match[1]).replace(/v\d+$/i, "");
}
