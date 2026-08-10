// Imported from the split modules, never from `background.js`: that module
// registers a `contextMenus.onClicked` listener and an always-on `webRequest`
// observer at module level, so every extension page importing it added another
// copy — and one right-click issued a `POST /capture` per open page.
import {
  fetchBibs,
  getEndpoint,
  getAuthHeaders,
  endpointFor,
} from "./background/config.js";
import { detectAndExtractSearchResults, matchesAnySearchPattern } from "./background/search.js";
import { captureCurrentTab } from "./background/capture.js";
import { formatCaptureResult, formatMultiCaptureResult } from "./popup_format.js";

const POPUP_BUILD_MARKER = "2025-06-12-phases-012";

// ── DOM refs ────────────────────────────────────────────────────────────
const summary = document.getElementById("summary");
const raw = document.getElementById("raw");
const button = document.getElementById("go");
const captureForm = document.getElementById("capture-form");
const bibSelect = document.getElementById("bib");
const tokenInput = document.getElementById("token");
const searchSection = document.getElementById("search-results");
const resultCount = document.getElementById("result-count");
const resultSite = document.getElementById("result-site");
const resultList = document.getElementById("result-list");
const captureSelectedBtn = document.getElementById("capture-selected");
const captureAllBtn = document.getElementById("capture-all");
const cancelSearchBtn = document.getElementById("cancel-search");
const searchProgress = document.getElementById("search-progress");
const searchProgressText = document.getElementById("search-progress-text");
const recentList = document.getElementById("recent-list");

let _searchItems = [];

// ── Init ─────────────────────────────────────────────────────────────────
// Capture progress and the recent list are per-session state the *background*
// writes to `storage.session`, so the popup has to read them from the same
// place.
function getStorage() {
  return (chrome.storage.session) ? chrome.storage.session : chrome.storage.local;
}

// The API token is not session state. It lived in `storage.session`, which the
// browser clears on close *and* which shadows `storage.local` in the merge
// `getStoredConfig` does — so the first capture of a new session wrote the
// empty token box over the token onboarding had saved to local, and every
// request 401'd until the user retyped it.
function getTokenStorage() {
  return chrome.storage.local;
}

getTokenStorage().get("authToken").then((stored) => {
  tokenInput.value = stored.authToken || "";
});

// An empty box means "I did not type one", never "clear the saved token".
async function storeAuthToken() {
  const token = tokenInput.value.trim();
  if (!token) return;
  await getTokenStorage().set({ authToken: token });
}

async function populateBibs() {
  let bibs;
  try {
    bibs = await fetchBibs();
  } catch (error) {
    // Say so rather than presenting an empty dropdown. `fetchBibs` used to
    // swallow this, so a server that was not running looked like a library
    // with no bibs — and the call site did not await, so even a throw would
    // have been an unhandled rejection.
    summary.textContent = `⚠️ ${error.message}`;
    return;
  }
  for (const bib of bibs) {
    const option = document.createElement("option");
    option.value = bib.name;
    option.textContent = bib.name + (bib.default ? " (default)" : "");
    bibSelect.appendChild(option);
  }
}

populateBibs();

// ── Recent captures ──────────────────────────────────────────────────────

const MAX_RECENT = 20;

async function _storeRecent(citekey, title, bib) {
  const stored = await getStorage().get("pzi:recent");
  let items = (stored && stored["pzi:recent"]) || [];
  // Remove duplicates of same citekey
  items = items.filter((r) => r.citekey !== citekey);
  items.unshift({ citekey, title: (title || "").slice(0, 80), bib: bib || "main", ts: Date.now() });
  if (items.length > MAX_RECENT) items = items.slice(0, MAX_RECENT);
  await getStorage().set({ "pzi:recent": items });
}

async function _loadRecent() {
  const stored = await getStorage().get("pzi:recent");
  return (stored && stored["pzi:recent"]) || [];
}

function _renderRecent(items) {
  if (!items.length) {
    recentList.innerHTML = '<span style="color:#888;">(none yet)</span>';
    return;
  }
  let html = "";
  for (const item of items) {
    const short = item.citekey + (item.title ? " — " + item.title : "");
    html += '<div style="display:flex; align-items:center; margin:3px 0; gap:4px;">'
      + '<span style="flex:1; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;">' + escHtml(short) + '</span>'
      + '<button data-action="pdf" data-citekey="' + escAttr(item.citekey) + '" data-bib="' + escAttr(item.bib) + '" style="width:auto; padding:1px 5px; margin:0; font-size:10px;">PDF</button>'
      + '</div>';
  }
  recentList.innerHTML = html;
  // Wire buttons
  recentList.querySelectorAll("button[data-action='pdf']").forEach(btn => {
    btn.addEventListener("click", () => {
      openPdf(btn.dataset.citekey, btn.dataset.bib).then((outcome) => {
        if (outcome && !outcome.ok) summary.textContent = `⚠️ ${outcome.message}`;
      });
    });
  });
}

function escHtml(s) {
  return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}
function escAttr(s) {
  return String(s).replace(/&/g, "&amp;").replace(/"/g, "&quot;");
}

export async function openPdf(citekey, bib) {
  const endpoint = await getEndpoint();
  const url = endpointFor(endpoint, "/pdf/" + encodeURIComponent(citekey)) + (bib && bib !== "main" ? "?bib=" + encodeURIComponent(bib) : "");
  const authHeaders = await getAuthHeaders();
  // Never `window.open(url)` on failure: that is the same URL without the
  // token, so the user got a browser tab containing `{"error":"invalid API
  // token"}`. And an unhandled rejection here — the server being down — left
  // the popup showing nothing at all, since the click handler has no catch.
  let response;
  try {
    response = await fetch(url, { headers: authHeaders });
  } catch (_error) {
    return { ok: false, message: "pzi server is not reachable — is `pzi server` running?" };
  }
  if (!response.ok) {
    return {
      ok: false,
      message: response.status === 404
        ? `no PDF stored for ${citekey}`
        : `could not open the PDF (HTTP ${response.status})`,
    };
  }
  const blob = await response.blob();
  const objectUrl = URL.createObjectURL(blob);
  window.open(objectUrl, "_blank");
  return { ok: true };
}

// ── Populate recent on load ───────────────────────────────────────────────
async function _initRecent() {
  const items = await _loadRecent();
  _renderRecent(items);
}
_initRecent();

async function initSearchDetection() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab || !tab.id || !tab.url) return;

  // Fast path: skip executeScript if URL doesn't match any known search pattern.
  // Gated on `search.js`'s own patterns. The popup used to keep a copy of
  // five of the nine, so four sites had extractors it could never reach.
  if (!matchesAnySearchPattern(tab.url)) return;

  const result = await detectAndExtractSearchResults(tab.id, tab.url);
  if (!result || !result.detected || !result.items || result.items.length === 0) return;

  _searchItems = result.items;

  // Show search UI, hide normal capture form.
  captureForm.style.display = "none";
  searchSection.style.display = "";

  resultCount.textContent = result.items.length;
  resultSite.textContent = result.patternName || "this page";

  // Build item list.
  resultList.innerHTML = "";
  for (const item of result.items) {
    const row = document.createElement("label");
    row.className = "row";
    row.style.cssText = "margin:4px 0; font-size:11px; align-items:flex-start;";

    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.value = String(item.index);
    checkbox.addEventListener("change", () => updateSelectedButton());
    row.appendChild(checkbox);

    const info = document.createElement("div");
    info.style.cssText = "flex:1;";
    let desc = item.title || "(no title)";
    if (item.authors) {
      const short = item.authors.split(",")[0] || item.authors.split(";")[0] || item.authors.slice(0, 30);
      desc += " — " + short.trim();
    }
    if (item.year) desc += " (" + item.year + ")";
    info.textContent = desc;
    row.appendChild(info);

    resultList.appendChild(row);
  }

  updateSelectedButton();
}

function updateSelectedButton() {
  const checked = resultList.querySelectorAll('input[type="checkbox"]:checked');
  captureSelectedBtn.disabled = checked.length === 0;
  captureSelectedBtn.textContent = checked.length > 0
    ? "Capture " + checked.length : "Capture selected";
}

// ── Multi-item capture ────────────────────────────────────────────────────

async function doMultiCapture(all) {
  const tags = document.getElementById("tags").value.split(",").map((s) => s.trim()).filter(Boolean);
  const bib = bibSelect.value || null;
  const dryRun = document.getElementById("dry").checked;
  await storeAuthToken();

  let selectedItems;
  if (all) {
    selectedItems = _searchItems.slice(0, 20);
  } else {
    const checked = resultList.querySelectorAll('input[type="checkbox"]:checked');
    const indices = new Set(Array.from(checked).map((cb) => parseInt(cb.value, 10)));
    selectedItems = _searchItems.filter((item) => indices.has(item.index));
  }

  if (selectedItems.length === 0) return;

  // Show progress.
  searchSection.style.display = "none";
  searchProgress.style.display = "";
  const total = selectedItems.length;
  searchProgressText.textContent = "Capturing 0/" + total + "…";

  // Per-item capture with progress updates.
  const endpoint = await getEndpoint();
  const authHeaders = await getAuthHeaders();
  const results = [];

  for (let i = 0; i < selectedItems.length; i++) {
    const item = selectedItems[i];
    searchProgressText.textContent = "Capturing " + (i + 1) + "/" + total + "…";

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
          verbose: true,
          // Deliberately no `cookies`: each result is a *different* domain the
          // user is not on. Reading their cookies and forwarding them to the
          // server — which forwards them to the publisher — is far beyond the
          // active tab's session, which is the only thing a capture is entitled
          // to reuse. The comment said so while the code did the opposite.
        }),
      });
      const data = await response.json().catch(() => null);
      results.push(data || { status: "error", message: "invalid JSON" });
      if (data && data.citekey) {
        _storeRecent(data.citekey, data.title || "", data.bib || bib || "");
      }
    } catch (err) {
      results.push({ status: "error", message: err?.message || String(err), item_url: item.url });
    }
  }

  const outcome = { status: "complete", total, results };

  // Show results.
  searchProgress.style.display = "none";
  document.getElementById("status").style.display = "";
  summary.textContent = formatMultiCaptureResult(outcome);
  try { raw.textContent = JSON.stringify(outcome, null, 2); } catch (_e) {}

  // Restore UI.
  _resetSearchUI();
  _searchItems = [];
  _initRecent();
}

function _resetSearchUI() {
  captureForm.style.display = "";
  searchSection.style.display = "none";
  // Proper reset: clear checkboxes by rebuilding from empty items.
  resultList.innerHTML = "";
  captureSelectedBtn.disabled = true;
  captureSelectedBtn.textContent = "Capture selected";
}


function cancelSearch() {
  _resetSearchUI();
  searchProgress.style.display = "none";
  _searchItems = [];
}


// ── Single-item capture (background-bridged) ────────────────────────────

export async function requestActiveTabOriginPermission(tabUrl) {
  let origin;
  try {
    origin = new URL(tabUrl).origin;
  } catch (_error) {
    return { status: "invalid_url", origin: null };
  }
  if (!chrome.permissions) return { status: "unavailable", origin };
  const request = { origins: [`${origin}/*`] };
  try {
    if (await chrome.permissions.contains(request)) {
      return { status: "granted", origin, already_granted: true };
    }
  } catch (_error) {
    // continue to request; popup click still holds user gesture in Firefox.
  }
  try {
    const granted = Boolean(await chrome.permissions.request(request));
    return { status: granted ? "granted" : "denied", origin };
  } catch (_error) {
    return { status: "denied", origin };
  }
}

export async function releaseActiveTabOriginPermission(tabUrl, permission) {
  // The twin of `requestActiveTabOriginPermission`, drawing the same line
  // `removeTemporaryOriginPermission` draws for PDF-candidate origins in the
  // background: a grant the user made deliberately is theirs and must survive,
  // but one borrowed for a single capture has to be handed back.
  //
  // Nothing released this before, and the caller discarded the permission
  // object entirely, so it was not even reachable to release. The extension
  // accumulated a permanent host permission for every site ever captured from,
  // which also widens what the always-on `webRequest` observer can see.
  if (!permission || permission.status !== "granted" || permission.already_granted) return false;
  if (!chrome.permissions?.remove) return false;
  let origin;
  try {
    origin = new URL(tabUrl).origin;
  } catch (_error) {
    return false;
  }
  try {
    return Boolean(await chrome.permissions.remove({ origins: [`${origin}/*`] }));
  } catch (_error) {
    return false;
  }
}

// ── Capture progress ────────────────────────────────────────────────────

// `background.js` writes one of these four stages as a capture proceeds. They
// were written and never read: the only reader was a poller nothing called, so
// the popup showed a static "Capturing…" across a pipeline that can spend 30 s
// on a single fetch and 15-20 s on a bot bypass.
const CAPTURE_STAGE_LABELS = {
  extracting: "Scanning page for metadata…",
  fetching: "Fetching paper details…",
  processing: "Processing metadata…",
  downloading: "Downloading PDF…",
};

export function captureStageLabel(stage) {
  return CAPTURE_STAGE_LABELS[stage] || null;
}

/**
 * Report capture progress to *onLabel* until the returned function is called.
 *
 * Subscribes rather than polls: the capture runs in this same realm, so its
 * `storage.session` writes raise a change event here directly. A browser
 * without `storage.onChanged` gets a no-op unsubscriber, so the missing API
 * costs the progress display and nothing else.
 */
export function watchCaptureStage(onLabel) {
  const onChanged = chrome.storage?.onChanged;
  if (!onChanged || typeof onChanged.addListener !== "function") return () => {};
  const listener = (changes) => {
    const change = changes && changes["pzi:captureStage"];
    if (!change) return;
    const label = captureStageLabel(change.newValue);
    if (label) onLabel(label);
  };
  onChanged.addListener(listener);
  return () => {
    try {
      onChanged.removeListener(listener);
    } catch (_error) {
      /* listener already gone */
    }
  };
}

export function stampPopupResult(result) {
  const out = (result && typeof result === "object") ? { ...result } : { status: "error", errors: ["invalid capture result"] };
  out.popup_build_marker = POPUP_BUILD_MARKER;
  return out;
}

async function doSingleCapture() {
  const tags = document.getElementById("tags").value.split(",").map((s) => s.trim()).filter(Boolean);
  const bib = bibSelect.value || null;
  const dryRun = document.getElementById("dry").checked;
  const forceNew = document.getElementById("force").checked;
  await storeAuthToken();

  summary.textContent = "Capturing…";
  raw.textContent = "";
  button.disabled = true;

  let tabId = null;
  let tabUrl = null;
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (tab && tab.id && tab.url) {
      tabId = tab.id;
      tabUrl = tab.url;
    }
  } catch (_err) {
    summary.textContent = "❌ Cannot access active tab";
    button.disabled = false;
    return;
  }
  if (!tabId) {
    summary.textContent = "❌ No active tab found";
    button.disabled = false;
    return;
  }

  // Kept, not discarded: the release below needs to know whether this capture
  // is the thing that granted it, or whether the user already had it.
  let originPermission = null;
  if (!dryRun) {
    originPermission = await requestActiveTabOriginPermission(tabUrl);
  }

  // Run capture in popup context so Firefox keeps optional permission request
  // tied to the user's click and avoids stale background service workers.
  await getStorage().remove(["pzi:captureStage"]);
  const stopProgress = watchCaptureStage((label) => { summary.textContent = label; });
  try {
    const result = stampPopupResult(await captureCurrentTab({ tags, bib, dryRun, forceNew, tabId, tabUrl }));
    summary.textContent = formatCaptureResult(result);
    raw.textContent = JSON.stringify(result, null, 2);
    if (result.citekey) {
      _storeRecent(result.citekey, result.title || "", result.bib || "").then(() => _initRecent());
    }
  } catch (err) {
    const result = stampPopupResult({ status: "error", errors: [err?.message || String(err)] });
    summary.textContent = formatCaptureResult(result);
    raw.textContent = JSON.stringify(result, null, 2);
  } finally {
    // First in the `finally`, so a stage write arriving late cannot overwrite
    // the outcome the `try` just displayed with "Downloading PDF…".
    stopProgress();
    await getStorage().remove(["pzi:captureStage"]);
    button.disabled = false;
    _clearBadge();
    // In the `finally`, not after the `try`: a throw out of the capture must
    // not leave the user holding a host permission they granted for one fetch.
    // `maybeStreamPdfBytes` shipped exactly that bug for PDF-candidate origins.
    await releaseActiveTabOriginPermission(tabUrl, originPermission);
  }
}


function _clearBadge() {
  if (typeof chrome !== "undefined" && chrome.action) {
    chrome.action.setBadgeText({ text: "" }).catch(() => {});
  }
}


// ── Event listeners ─────────────────────────────────────────────────────

button.addEventListener("click", doSingleCapture);
captureSelectedBtn.addEventListener("click", () => doMultiCapture(false));
captureAllBtn.addEventListener("click", () => doMultiCapture(true));
cancelSearchBtn.addEventListener("click", cancelSearch);

// Auto-detect search results and check for stored capture on popup open.
initSearchDetection();
