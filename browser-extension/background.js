// ---------- Imports from split modules ----------
import {
  setCaptureTabId,
  getEndpoint,
  getAuthHeaders,
  getStoredConfig,
  fetchBibs,
  detectBrowser,
  endpointFor,
} from "./background/config.js";

import {
  originOf,
  sameOrigin,
  candidateUrl,
  normalizeMetadataUrl,
  normalizeDoi,
  jsonOrNull,
  responseErrors,
  isSafePublicHttpUrl,
  filterStalePdfWarnings,
  doiFromKnownPreprintUrl,
} from "./background/utils.js";

import {
  cookieHeaderForUrl,
  releasePdfOriginPermissions,
  requestPdfOriginPermissions,
} from "./background/permissions.js";

import { extractPageMetadata } from "./background/metadata.js";

import {
  startPdfObserver,
  stopPdfObserver,
  collectObservedPdfUrls,
  collectPdfObserverEvents,
  addObserverEntry,
} from "./background/observer.js";

import {
  scanDomForPdfUrls,
  extractPdfUrlCandidates,
  addPdfUrlCandidate,
  MAX_PDF_URL_CANDIDATES,
  clickPdfDiscovery,
  buildPdfCandidates,
  isBotBypassWhitelisted,
} from "./background/pdf_discovery.js";

import {
  maybeStreamPdfBytes,
  botBypassPdfUrl,
} from "./background/pdf_fetch.js";

// Re-export for external consumers (tests, popup)
export { getEndpoint, getAuthHeaders, fetchBibs, detectBrowser, endpointFor };
export { isSafePublicHttpUrl };
// Onboarding validates the endpoint the user types with the same predicate
// `getEndpoint` applies, so the two cannot disagree about what is storable.
export { isLoopbackEndpoint } from "./background/config.js";
export { cookieHeaderForUrl };
export { detectAndExtractSearchResults } from "./background/search.js";
export { startPdfObserver, collectPdfObserverEvents };
export {
  scanDomForPdfUrls,
  extractPdfUrlCandidates,
  MAX_PDF_URL_CANDIDATES,
  clickPdfDiscovery,
  isBotBypassWhitelisted,
};
export { botBypassPdfUrl };

// `captureCurrentTab` lives in `background/capture.js` so the UI can reach it
// without loading this module, whose listener registrations below must run in
// the service worker only.
export { captureCurrentTab } from "./background/capture.js";

// Exported for the test harness: the context-menu path decides what session a
// captured link may reuse, which is worth asserting directly rather than
// through a synthesized `contextMenus.onClicked` event.
export { _handleContextMenuCapture };


chrome.runtime.onInstalled.addListener(async (details) => {
  console.log("paperazzi capture extension installed");

  // ── Context menu: right-click a link → "Save to paperazzi" ────────────
  if (typeof chrome !== "undefined" && chrome.contextMenus) {
    chrome.contextMenus.create({
      id: "pzi-capture-link",
      title: "Save to paperazzi",
      contexts: ["link"],
    });
  }

  // ── First-run onboarding ─────────────────────────────────────────────
  if (details.reason === "install") {
    const stored = await getStoredConfig("authToken");
    if (!stored.authToken) {
      try {
        chrome.tabs.create({ url: chrome.runtime.getURL("onboarding.html") });
      } catch (_e) { /* ignore */ }
    }
  }
});

// ── Context menu click handler ──────────────────────────────────────────
if (typeof chrome !== "undefined" && chrome.contextMenus) {
  chrome.contextMenus.onClicked.addListener((info, tab) => {
    if (info.menuItemId === "pzi-capture-link") {
      _handleContextMenuCapture(info, tab);
    }
  });
}

/** Same scheme+host+port, for deciding whether a link may reuse the tab's session. */
function _sameOrigin(a, b) {
  try {
    return new URL(a).origin === new URL(b).origin;
  } catch (_) {
    return false;
  }
}

async function _handleContextMenuCapture(info, tab) {
  const url = info.linkUrl;
  // `isSafePublicHttpUrl`, not a bare scheme test. The scheme regex accepted
  // `http://127.0.0.1:9999/x`, so right-clicking such a link read this
  // machine's loopback cookies and transmitted them before the server rejected
  // the capture.
  if (!url || !isSafePublicHttpUrl(url)) return;

  _setBadge("…", "#FFA500");

  try {
    const endpoint = await getEndpoint();
    const authHeaders = await getAuthHeaders();
    // Cookies only when the link is on the origin the user is actually reading.
    // The bulk path refuses cookies outright because "each result is a
    // *different* domain the user is not on" (popup.js) — true there, and not
    // true of a link on the page in front of them, where reusing the session is
    // the point. A link pointing elsewhere carries none.
    const pageUrl = tab && tab.url;
    const cookieHeader = pageUrl && _sameOrigin(url, pageUrl)
      ? await cookieHeaderForUrl(url)
      : null;

    const response = await fetch(endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...authHeaders },
      body: JSON.stringify({
        url,
        browser: detectBrowser(),
        // No `extension_version` or `page_url`: `/capture` reads neither. The
        // only `page_url` the server reads belongs to `/browser/discover`.
        cookies: cookieHeader,
      }),
    });

    const result = await jsonOrNull(response);

    if (result && result.status === "ok") {
      _setBadge("✓", "#4CAF50");
    } else {
      _setBadge("✗", "#F44336");
    }

    setTimeout(_clearBadge, 5000);
  } catch (_error) {
    _setBadge("✗", "#F44336");
    setTimeout(_clearBadge, 5000);
  }
}

// ── Always-on PDF observer ──────────────────────────────────────────────
// Passively records application/pdf responses from all tabs.
// Observes main_frame, sub_frame, and xmlhttprequest.
// Cache is domain-scoped with 3-minute TTL.
(function _registerAlwaysOnPdfObserver() {
  if (typeof chrome === "undefined" || !chrome.webRequest || !chrome.webRequest.onHeadersReceived) return;

  try {
    chrome.webRequest.onHeadersReceived.addListener(
      (details) => {
        const headers = details.responseHeaders || [];
        for (const h of headers) {
          if (h.name.toLowerCase() === "content-type" && h.value && h.value.toLowerCase().includes("application/pdf")) {
            addObserverEntry(details.url, details.tabId);
            break;
          }
        }
      },
      { urls: ["https://*/*", "http://*/*"] },
      ["responseHeaders"],
    );
  } catch (_e) {
    /* webRequest unavailable or lacks permissions */
  }
})();

// ── Badge helpers ───────────────────────────────────────────────────────
function _setBadge(text, color) {
  if (typeof chrome === "undefined" || !chrome.action) return;
  chrome.action.setBadgeText({ text }).catch(() => {});
  chrome.action.setBadgeBackgroundColor({ color }).catch(() => {});
}

function _clearBadge() {
  if (typeof chrome === "undefined" || !chrome.action) return;
  chrome.action.setBadgeText({ text: "" }).catch(() => {});
}

// ── Why there is no popup-to-background capture bridge ──────────────────
// There used to be a `chrome.runtime.onMessage` listener here that ran the
// capture pipeline in the service worker, so a capture would survive the popup
// closing. Nothing ever sent that message: `doSingleCapture` calls
// `captureCurrentTab` directly, deliberately, because Firefox only grants an
// optional host permission while a user gesture is in scope — moving the work
// to the background loses the click and the permission request fails.
//
// The listener was therefore unreachable code asserting a property the
// extension does not have. Removed rather than wired.
//
// The context-menu path does still run in the background, and reports through
// the toolbar badge (`✓` / `✗`) in `_handleContextMenuCapture` — not through
// `pzi:lastCapture`, as this comment used to claim. Nothing ever wrote that
// key, so the popup reader it named displayed nothing on every run since it
// was written; both halves are gone now. Anything richer than the badge for a
// context-menu capture would be a new feature, not a repair.
