import {
  fetchBibs,
  getEndpoint,
  getAuthHeaders,
  detectAndExtractSearchResults,
  cookieHeaderForUrl,
  captureCurrentTab,
  endpointFor,
} from "./background.js";
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
function getStorage() {
  return (chrome.storage.session) ? chrome.storage.session : chrome.storage.local;
}

getStorage().get("authToken").then((stored) => {
  tokenInput.value = stored.authToken || "";
});

async function populateBibs() {
  const bibs = await fetchBibs();
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
    btn.addEventListener("click", () => openPdf(btn.dataset.citekey, btn.dataset.bib));
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
  const response = await fetch(url, { headers: authHeaders });
  if (!response.ok) {
    window.open(url, "_blank");
    return;
  }
  const blob = await response.blob();
  const objectUrl = URL.createObjectURL(blob);
  window.open(objectUrl, "_blank");
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
  if (!_urlMatchesAnySearchPattern(tab.url)) return;

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

// URL patterns for known search sites (fast path — avoids executeScript on
// every article page the user opens).
function _urlMatchesAnySearchPattern(url) {
  const patterns = [
    "scholar\\.google\\.com\\/scholar",
    "pubmed\\.ncbi\\.nlm\\.nih\\.gov",
    "semanticscholar\\.org\\/search",
    "arxiv\\.org\\/search",
    "dblp\\.org\\/search",
  ];
  for (const pat of patterns) {
    if (new RegExp(pat, "i").test(url)) return true;
  }
  return false;
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
  await getStorage().set({ authToken: tokenInput.value.trim() });

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
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  const cookies = tab && tab.url ? await cookieHeaderForUrl(tab.url) : "";
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
          cookies,
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
  await getStorage().set({ authToken: tokenInput.value.trim() });

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

  if (!dryRun) {
    await requestActiveTabOriginPermission(tabUrl);
  }

  // Run capture in popup context so Firefox keeps optional permission request
  // tied to the user's click and avoids stale background service workers.
  await getStorage().remove(["pzi:lastCapture", "pzi:captureInProgress", "pzi:captureStage"]);
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
    button.disabled = false;
    _clearBadge();
  }
}

async function _pollCaptureResult(retries) {
  const stored = await getStorage().get(["pzi:lastCapture", "pzi:captureInProgress", "pzi:captureStage"]);
  const result = stored && stored["pzi:lastCapture"];
  const stage = stored && stored["pzi:captureStage"];

  if (result) {
    if (!result.extension_version) result.popup_build_marker = POPUP_BUILD_MARKER;
    summary.textContent = formatCaptureResult(result);
    raw.textContent = JSON.stringify(result, null, 2);
    button.disabled = false;
    // Store in recent captures
    if (result.citekey) {
      _storeRecent(result.citekey, result.title || "", result.bib || "").then(() => _initRecent());
    }
    await getStorage().remove(["pzi:lastCapture", "pzi:captureInProgress", "pzi:captureStage"]);
    _clearBadge();
    return;
  }

  if (stage) {
    const stageLabels = {
      extracting: "Scanning page for metadata…",
      fetching: "Fetching paper details…",
      processing: "Processing metadata…",
      downloading: "Downloading PDF…",
    };
    summary.textContent = stageLabels[stage] || stage;
  }

  if (retries > 0) {
    setTimeout(() => _pollCaptureResult(retries - 1), 500);
  } else {
    summary.textContent += " (still in progress…)";
    // Keep polling while service worker is alive.
    setTimeout(() => _pollCaptureResult(120), 500);
  }
}

function _clearBadge() {
  if (typeof chrome !== "undefined" && chrome.action) {
    chrome.action.setBadgeText({ text: "" }).catch(() => {});
  }
}

// ── Check for stored capture from previous popup close ──────────────────

async function _checkStoredCapture() {
  // Don't interfere if the user already clicked capture.
  if (button.disabled) return;
  const stored = await getStorage().get(["pzi:lastCapture", "pzi:captureInProgress", "pzi:captureStage"]);
  const result = stored && stored["pzi:lastCapture"];
  if (result) {
    if (!result.extension_version) result.popup_build_marker = POPUP_BUILD_MARKER;
    summary.textContent = formatCaptureResult(result);
    raw.textContent = JSON.stringify(result, null, 2);
    await getStorage().remove(["pzi:lastCapture", "pzi:captureInProgress", "pzi:captureStage"]);
    _clearBadge();
  }
}


// ── Event listeners ─────────────────────────────────────────────────────

button.addEventListener("click", doSingleCapture);
captureSelectedBtn.addEventListener("click", () => doMultiCapture(false));
captureAllBtn.addEventListener("click", () => doMultiCapture(true));
cancelSearchBtn.addEventListener("click", cancelSearch);

// Auto-detect search results and check for stored capture on popup open.
initSearchDetection();
_checkStoredCapture();
