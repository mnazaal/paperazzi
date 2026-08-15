// pzi browser extension — configuration, endpoint, and auth helpers.
//
// Pure configuration module with no DOM/chrome.runtime dependencies
// (chrome.storage is the only browser API used here).

export const DEFAULT_HOST = "http://127.0.0.1:8765";
export const DEFAULT_ENDPOINT = `${DEFAULT_HOST}/capture`;
export const PDF_FETCH_TIMEOUT_MS = 30000;
export const MAX_ATTACH_PDF_BYTES = 47 * 1024 * 1024;
// Read from the manifest the build stamps, not hardcoded. The literal here was
// `"2025-06-12-phases-012"`, repeated verbatim in `popup.js`, and had been
// wrong for every release since — while `build_extension.py` was already
// computing the real version into `version_name`. The fallback covers a test
// realm with no `chrome.runtime`.
export const EXTENSION_VERSION = (() => {
  try {
    const manifest = chrome.runtime.getManifest();
    return manifest.version_name || manifest.version || "unknown";
  } catch (_error) {
    return "unknown";
  }
})();

// Pure, and takes both versions rather than reading `EXTENSION_VERSION` itself,
// so it is testable without a `chrome.runtime` realm.
//
// Exact-string comparison, not semver ordering: the extension and the server
// ship from one tag, so "different" is the only question worth asking, and
// deciding which side is *older* would be inventing a policy nothing needs.
//
// Returns null for the three non-mismatches, and the middle one matters most:
// a server too old to report a version must not produce a permanent warning
// that nothing can clear.
export function describeVersionMismatch(extensionVersion, serverVersion) {
  if (!serverVersion || typeof serverVersion !== "string") return null;
  if (!extensionVersion || extensionVersion === "unknown") return null;
  if (extensionVersion === serverVersion) return null;
  return (
    `extension ${extensionVersion} vs pzi server ${serverVersion} — ` +
    "these ship together; capture still works, but rebuild and reload the " +
    "extension to be sure (see README, “Extension and server versions”)"
  );
}

// The ID of the tab currently being captured (set by captureCurrentTab).
export let _captureTabId = null;
export function setCaptureTabId(val) {
  _captureTabId = val;
}

// ── Endpoint & auth ────────────────────────────────────────────────────

// pzi's API runs on this machine, by construction — `pzi server` refuses a
// wildcard bind and the Host guard rejects anything else. So an endpoint that
// is not loopback cannot be a pzi server, and everything a capture carries
// goes to this URL: the page HTML, the user's cookies for that site, the
// downloaded PDF bytes, and the API token. Taken from storage unchecked, one
// mistyped character in the options box — or anything able to write extension
// storage — redirected all of it to a remote host.
export function isLoopbackEndpoint(rawEndpoint) {
  try {
    const { protocol, hostname } = new URL(rawEndpoint);
    if (protocol !== "http:" && protocol !== "https:") return false;
    const host = hostname.replace(/^\[|\]$/g, "").toLowerCase();
    return (
      host === "localhost"
      || host === "127.0.0.1"
      || host === "::1"
      || /^127\.\d{1,3}\.\d{1,3}\.\d{1,3}$/.test(host)
    );
  } catch (_e) {
    return false;
  }
}

export async function getEndpoint() {
  const stored = await getStoredConfig("endpoint");
  if (stored.endpoint && isLoopbackEndpoint(stored.endpoint)) return stored.endpoint;
  return DEFAULT_ENDPOINT;
}

export async function getAuthHeaders() {
  const stored = await getStoredConfig("authToken");
  return stored.authToken ? { "X-Pzi-Token": stored.authToken } : {};
}

// Local storage only. This used to merge `storage.session` *over* local, so a
// session value shadowed the configured one — which is how an empty token box
// wrote an empty token over the saved one and 401'd every request until it was
// retyped. Nothing writes configuration to session storage: the only session
// keys are `pzi:captureStage` and, historically, the recent list. Reading it
// here bought nothing and cost that bug.
export async function getStoredConfig(key) {
  return chrome.storage.local ? await chrome.storage.local.get(key) : {};
}

export async function fetchBibs() {
  // Throws rather than returning `[]` on failure. Collapsing both outcomes
  // meant a server that was not running, or a wrong token, presented as a
  // library with no bibs — so the popup showed an empty dropdown and said
  // nothing, and the README documented the symptom as a troubleshooting entry.
  const endpoint = await getEndpoint();
  const bibsUrl = endpointFor(endpoint, "/bibs");
  const response = await fetch(bibsUrl, { headers: await getAuthHeaders() });
  if (!response.ok) {
    throw new Error(
      response.status === 401
        ? "pzi server rejected the API token — re-pair the extension"
        : `pzi server returned HTTP ${response.status} for /bibs`,
    );
  }
  const data = await response.json();
  if (data.status !== "ok" || !Array.isArray(data.bibs)) {
    throw new Error("pzi server sent an unexpected response for /bibs");
  }
  return data.bibs;
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
