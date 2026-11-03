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
      // null when it will not parse, never the raw string: the server rejects
      // the whole capture for a malformed URL (`http://[`), and every caller
      // already has a valid tab URL to fall back to. A parseable URL that is
      // not http(s) (`javascript:`, `data:`) is as unusable as a page URL.
      try {
        const parsed = new URL(raw, pageUrl);
        return parsed.protocol === "http:" || parsed.protocol === "https:" ? parsed.href : null;
      } catch (_) {
        return null;
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
  // `response.json()` returns a promise that *rejects* on a body that is not
  // JSON — a truncated response, an empty 204, an HTML error page from
  // something in front of the server. A `try` around the call cannot see that,
  // so every `await jsonOrNull(r)` threw instead of receiving the `null` its
  // callers are written to handle. The `try` still covers a response object
  // with no `json` method at all.
  try {
    return Promise.resolve(response.json()).catch(() => null);
  } catch (_error) {
    return Promise.resolve(null);
  }
}

export function responseErrors(data, defaultMessage) {
  if (!data) return [defaultMessage];
  const errors = Array.isArray(data.errors) ? data.errors : [];
  if (errors.length > 0) return errors;
  // The server has two error channels. Service results carry `errors[]`, but
  // every pre-service rejection — invalid API token, origin not allowed, rate
  // limit exceeded, "bib must name a library configured in config.toml" —
  // answers with the singular `error`. Reading only `errors` and `message`
  // turned all of those into a bare "HTTP 401" or "failed" in the popup.
  if (typeof data.error === "string" && data.error) return [data.error];
  return [data.message || defaultMessage];
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

// One list answers "is this destination private?" for both the
// declarativeNetRequest block rules (permissions.js) and `isSafePublicHttpUrl`
// below, which tests the same patterns against the parsed `href`. The browser
// matches rules against its canonical URL form (lowercased host, numeric hosts
// rewritten to dotted decimal), and `new URL(...).href` is the same form, so
// `0x7f.1`, `2130706433` and `%6cocalhost` arrive here already normalised.
// What the canonical form keeps is trailing dots (`localhost.`), hence the `\.*`.
// The extension has no resolver: a name that merely resolves to a private
// address is outside what this list can see (docs/security.md, Design calls).
//
// Chrome refuses a single regexFilter that compiles past 2 KB, so this is a
// table of small patterns rather than one expression.
const _END = String.raw`\.*([:/?#]|$)`;
const _OCTET = String.raw`[0-9]+`;
export const PRIVATE_DESTINATION_PATTERNS = [
  String.raw`^https?://[^/?#]*@`, // userinfo: the real host is hard to read past it
  String.raw`^https?://\[`, // IPv6 literal (also keeps fc../fd.. host names out of any IPv6 test)
  String.raw`^https?://[^/?#:.@]+` + _END, // single-label host (localhost, intranet names)
  String.raw`^https?://[^/?#:@]+\.(localhost|local|internal|lan|home)` + _END,
  String.raw`^https?://localhost\.localdomain` + _END,
  // this-network 0/8, loopback, private 10/8, multicast and reserved 224/3
  String.raw`^https?://(0|10|127|22[4-9]|23[0-9]|24[0-9]|25[0-5])\.${_OCTET}\.${_OCTET}\.${_OCTET}` + _END,
  String.raw`^https?://192\.168\.${_OCTET}\.${_OCTET}` + _END,
  String.raw`^https?://172\.(1[6-9]|2[0-9]|3[01])\.${_OCTET}\.${_OCTET}` + _END,
  String.raw`^https?://169\.254\.${_OCTET}\.${_OCTET}` + _END,
  // carrier-grade NAT 100.64/10 (Tailscale and friends)
  String.raw`^https?://100\.(6[4-9]|[7-9][0-9]|1[01][0-9]|12[0-7])\.${_OCTET}\.${_OCTET}` + _END,
  // benchmarking 198.18/15 and TEST-NET-2 198.51.100/24
  String.raw`^https?://198\.(1[89]\.${_OCTET}|51\.100)\.${_OCTET}` + _END,
  // IETF protocol assignments 192.0.0/24 and TEST-NET-1 192.0.2/24
  String.raw`^https?://192\.0\.[02]\.${_OCTET}` + _END,
  // TEST-NET-3
  String.raw`^https?://203\.0\.113\.${_OCTET}` + _END,
];
const _PRIVATE_DESTINATION_REGEXES = PRIVATE_DESTINATION_PATTERNS.map((pattern) => new RegExp(pattern, "i"));

export function isSafePublicHttpUrl(value) {
  let parsed;
  try {
    parsed = new URL(value);
  } catch (_error) {
    return false;
  }
  if (parsed.protocol !== "http:" && parsed.protocol !== "https:") return false;
  if (!parsed.hostname) return false;
  return !_PRIVATE_DESTINATION_REGEXES.some((re) => re.test(parsed.href));
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
