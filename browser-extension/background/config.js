// pzi browser extension — configuration, endpoint, and auth helpers.
//
// Pure configuration module with no DOM/chrome.runtime dependencies
// (chrome.storage is the only browser API used here).

export const DEFAULT_HOST = "http://127.0.0.1:8765";
export const DEFAULT_ENDPOINT = `${DEFAULT_HOST}/capture`;
export const PDF_FETCH_TIMEOUT_MS = 30000;
export const MAX_ATTACH_PDF_BYTES = 47 * 1024 * 1024;
export const EXTENSION_VERSION = "2025-06-12-phases-012";

// The ID of the tab currently being captured (set by captureCurrentTab).
export let _captureTabId = null;
export function setCaptureTabId(val) {
  _captureTabId = val;
}

// ── Endpoint & auth ────────────────────────────────────────────────────

export async function getEndpoint() {
  const stored = await getStoredConfig("endpoint");
  return stored.endpoint || DEFAULT_ENDPOINT;
}

export async function getAuthHeaders() {
  const stored = await getStoredConfig("authToken");
  return stored.authToken ? { "X-Pzi-Token": stored.authToken } : {};
}

// Prefer session storage for popup/runtime choices, but fall back
// to local storage for documented manual configuration on Chrome.
export async function getStoredConfig(key) {
  const localStored = chrome.storage.local
    ? await chrome.storage.local.get(key)
    : {};
  if (!chrome.storage.session) return localStored;
  const sessionStored = await chrome.storage.session.get(key);
  return { ...localStored, ...sessionStored };
}

export async function fetchBibs() {
  const endpoint = await getEndpoint();
  const bibsUrl = endpointFor(endpoint, "/bibs");
  try {
    const response = await fetch(bibsUrl, {
      headers: await getAuthHeaders(),
    });
    if (!response.ok) return [];
    const data = await response.json();
    if (data.status !== "ok" || !Array.isArray(data.bibs)) return [];
    return data.bibs;
  } catch (_err) {
    return [];
  }
}

export function detectBrowser() {
  if (typeof browser !== "undefined" && browser.runtime?.getBrowserInfo) {
    return "firefox";
  }
  return "chrome";
}

// Build a full URL by replacing path on the same origin as `endpoint`.
export function endpointFor(rawEndpoint, path) {
  try {
    const base = new URL(rawEndpoint);
    const target = new URL(path, base);
    target.search = "";
    return target.href.replace(/\/$/, "");
  } catch (_e) {
    return `${rawEndpoint.replace(/\/$/, "")}${path}`;
  }
}
