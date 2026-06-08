import {
  captureCurrentTab,
  fetchBibs,
  getEndpoint,
  getAuthHeaders,
  detectAndExtractSearchResults,
  cookieHeaderForUrl,
} from "./background.js";
import { formatCaptureResult, formatMultiCaptureResult } from "./popup_format.js";

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

// ── Search result detection (Tier 4, P1-fixed: URL fast path first) ──────

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

// ── Multi-item capture (P2-fixed: cookies + progress) ────────────────────

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


// ── Single-item capture (existing) ──────────────────────────────────────

async function doSingleCapture() {
  const tags = document.getElementById("tags").value.split(",").map((s) => s.trim()).filter(Boolean);
  const bib = bibSelect.value || null;
  const dryRun = document.getElementById("dry").checked;
  await getStorage().set({ authToken: tokenInput.value.trim() });

  summary.textContent = "Capturing…";
  raw.textContent = "";
  button.disabled = true;
  try {
    const result = await captureCurrentTab({ tags, bib, dryRun });
    summary.textContent = formatCaptureResult(result);
    raw.textContent = JSON.stringify(result, null, 2);
  } catch (err) {
    summary.textContent = "❌ Capture failed: " + (err && err.message ? err.message : String(err));
  } finally {
    button.disabled = false;
  }
}


// ── Event listeners ─────────────────────────────────────────────────────

button.addEventListener("click", doSingleCapture);
captureSelectedBtn.addEventListener("click", () => doMultiCapture(false));
captureAllBtn.addEventListener("click", () => doMultiCapture(true));
cancelSearchBtn.addEventListener("click", cancelSearch);

// Auto-detect search results on popup open.
initSearchDetection();
