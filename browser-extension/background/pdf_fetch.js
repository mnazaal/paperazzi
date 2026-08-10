// pzi browser extension — PDF byte acquisition and attach pipeline.
//
// Given ranked PDF candidates, attempts to obtain PDF bytes through several
// strategies (background fetch, page-context fetch, navigate/monitor, bot
// bypass, discover-from-page) and streams them to the local pzi API.

import {
  MAX_ATTACH_PDF_BYTES,
  PDF_FETCH_TIMEOUT_MS,
  _captureTabId,
  endpointFor,
  getAuthHeaders,
  isLoopbackEndpoint,
} from "./config.js";
import {
  arrayBufferToBase64,
  candidateUrl,
  contentLengthExceedsAttachLimit,
  isSafePublicHttpUrl,
  jsonOrNull,
  looksLikePdfBytes,
  sameOrigin,
  sleep,
} from "./utils.js";
import {
  cookieHeaderForUrl,
  fetchWithTimeout,
  groupCandidatesByOrigin,
  groupPdfCandidates,
  permissionForCandidate,
  removeTemporaryOriginPermission,
  requestTemporaryOriginPermission,
} from "./permissions.js";
import {
  collectObservedPdfUrls,
  collectPdfObserverEvents,
  startPdfObserver,
  stopPdfObserver,
} from "./observer.js";
import { isBotBypassWhitelisted } from "./pdf_discovery.js";

// ── Bot/JS bypass via hidden iframe ──────────────────────────────────────
// For allowlisted domains, inject a hidden iframe to the failed URL,
// letting the page's JS redirect to the real PDF. The network observer
// catches the resulting PDF response.

const BOT_BYPASS_IFRAME_TIMEOUT_MS = 5000;
const BOT_BYPASS_VISIBLE_TIMEOUT_MS = 15000;

// How many bypass attempts one capture may spend. Each costs a hidden iframe
// and can fall through to opening a *visible* tab, up to 20s apiece — so a page
// offering many allowlisted candidates turned one capture into a minutes-long
// sequence of tabs opening in the user's face. The attempts were already
// serialized (each is awaited in turn); what was missing was a bound.
const MAX_BOT_BYPASS_ATTEMPTS_PER_CAPTURE = 3;
let _bypassBudgetTabId = null;
let _bypassAttemptsUsed = 0;

/**
 * Start a fresh bypass budget. Called once per capture.
 *
 * The budget used to reset only when the *tab* changed, so a second capture in
 * the same tab inherited the first one's spent attempts — three captures on one
 * tab and the bypass was off for good, until the user switched away and back.
 * "Per capture" is what the cap was always described as.
 */
export function resetBotBypassBudget(tabId) {
  _bypassBudgetTabId = tabId;
  _bypassAttemptsUsed = 0;
}

function takeBotBypassBudget(tabId) {
  if (_bypassBudgetTabId !== tabId) {
    resetBotBypassBudget(tabId);
  }
  if (_bypassAttemptsUsed >= MAX_BOT_BYPASS_ATTEMPTS_PER_CAPTURE) return false;
  _bypassAttemptsUsed += 1;
  return true;
}

export async function botBypassPdfUrl(tabId, candidateUrl, options = {}) {
  if (!tabId || !candidateUrl) return null;
  if (!isBotBypassWhitelisted(candidateUrl)) return null;
  if (!takeBotBypassBudget(tabId)) return null;
  const visibleTimeoutMs = options.visibleTimeoutMs || BOT_BYPASS_VISIBLE_TIMEOUT_MS;

  startPdfObserver(tabId);

  try {
    // Inject hidden iframe that navigates to the intermediate/blocked URL.
    // The page's JavaScript may serve a different PDF URL that our
    // observer will catch.
    await chrome.scripting.executeScript({
      target: { tabId },
      // Every value this function needs must arrive through `args`: it is
      // serialized and evaluated in the *page*, where this module's scope does
      // not exist. Reading BOT_BYPASS_IFRAME_TIMEOUT_MS from the closure threw
      // a ReferenceError inside the injected promise, so the whole hidden-iframe
      // bypass rejected and silently fell through to opening a visible tab.
      func: (url, timeoutMs) => {
        return new Promise((resolve) => {
          const iframe = document.createElement("iframe");
          iframe.style.display = "none";
          iframe.src = url;
          const cleanup = () => {
            setTimeout(() => {
              if (iframe.parentNode) iframe.parentNode.removeChild(iframe);
            }, 500);
          };
          iframe.onload = () => {};
          iframe.onerror = () => {};
          document.body.appendChild(iframe);
          // Wait for page JS to potentially trigger PDF loads.
          setTimeout(() => {
            cleanup();
            resolve();
          }, timeoutMs);
        });
      },
      args: [candidateUrl, BOT_BYPASS_IFRAME_TIMEOUT_MS],
    });

    // Give observer a chance to collect any PDF URLs that the iframe triggered.
    await new Promise((r) => setTimeout(r, 500));
    const observed = collectObservedPdfUrls();
    // Return first observed URL that differs from the injected one
    // and looks like a valid http(s) URL.
    const hiddenObserved = observed.find((u) => /^https?:\/\//i.test(u));
    if (hiddenObserved) return hiddenObserved;
    return await botBypassViaVisibleTab(candidateUrl, { timeoutMs: visibleTimeoutMs });
  } catch (_e) {
    return null;
  } finally {
    stopPdfObserver();
  }
}

async function botBypassViaVisibleTab(candidateUrl, { timeoutMs }) {
  if (!chrome.tabs?.create) return null;
  let helperTab = null;
  try {
    // `active: false`: this opens mid-capture, navigates to a publisher URL and
    // is closed again seconds later. Focused, it took over the user's window
    // while they were reading something else. A background tab still loads and
    // runs the page's JS, which is all the observer needs.
    helperTab = await chrome.tabs.create({ url: "about:blank", active: false });
    if (!helperTab || helperTab.id == null) return null;
    startPdfObserver(helperTab.id);
    if (chrome.tabs?.update) {
      await chrome.tabs.update(helperTab.id, { url: candidateUrl });
    } else {
      await chrome.tabs.create({ url: candidateUrl, active: false });
    }
    // Wait for tab navigation to complete, then give JS redirects time to fire.
    await new Promise((resolve) => {
      const done = () => {
        try { chrome.tabs.onUpdated.removeListener(onUpdated); } catch (_e) {}
        clearTimeout(timer);
        resolve();
      };
      const timer = setTimeout(done, timeoutMs);
      const onUpdated = (tabId, changeInfo) => {
        if (tabId === helperTab.id && changeInfo.status === "complete") {
          // Tab loaded — give PDF viewer / JS redirects an extra window.
          clearTimeout(timer);
          setTimeout(done, 3000);
        }
      };
      try {
        chrome.tabs.onUpdated.addListener(onUpdated);
      } catch (_e) {
        // Fall back to blind timeout if onUpdated unavailable.
        setTimeout(resolve, timeoutMs);
        return;
      }
    });
    const observed = collectObservedPdfUrls();
    return observed.find((u) => /^https?:\/\//i.test(u)) || null;
  } catch (_e) {
    return null;
  } finally {
    if (helperTab && helperTab.id != null && chrome.tabs?.remove) {
      try {
        await chrome.tabs.remove(helperTab.id);
      } catch (_e) {
        /* user may have closed helper tab */
      }
    }
  }
}

export async function maybeStreamPdfBytes({ endpoint, citekey, bib, pdfUrlCandidates, pageUrl, originPermissions = null, pdfRequest = null }) {
  const attempts = [];
  const grouped = groupPdfCandidates(pdfUrlCandidates || [], pageUrl);
  let lastPermission = null;
  const deniedPermissions = [];
  const originGroups = [
    ...groupCandidatesByOrigin(grouped.sameOrigin).map((candidates) => ({ candidates, allowWithoutPermissionApi: true })),
    ...groupCandidatesByOrigin(grouped.crossOrigin).map((candidates) => ({ candidates, allowWithoutPermissionApi: false })),
  ];
  for (const { candidates, allowWithoutPermissionApi } of originGroups) {
    const permission = permissionForCandidate(originPermissions, candidates[0])
      || await requestTemporaryOriginPermission(candidates[0]);
    lastPermission = permission;
    if (permission.status !== "granted" && !(allowWithoutPermissionApi && permission.status === "unavailable")) {
      // Allow same-origin candidates even when permission is "denied" —
      // generic background fetch + cookies works without host permissions.
      if (!(allowWithoutPermissionApi && permission.status === "denied")) {
        // Recorded here, not at the top of the loop: this is the point where a
        // refusal actually costs the user a candidate. A same-origin group is
        // tried anyway despite a denial, so counting that one would name an
        // origin that was never in fact blocked.
        if (permission.status === "denied") deniedPermissions.push(permission);
        continue;
      }
      // Fall through: tryPdfCandidates will skip navigate/discover methods
      // but still attempt generic fetch and page-context fetch.
    }
    // The release goes in a `finally`: it sat after the call, so a throw out of
    // `tryPdfCandidates` left the user holding a host permission they had
    // granted for one PDF fetch — silently, and until they revoked it by hand.
    let attach = null;
    try {
      attach = await tryPdfCandidates({
        endpoint,
        citekey,
        bib,
        candidates,
        permission,
        pageUrl,
        attempts,
        pdfRequest,
      });
    } finally {
      await removeTemporaryOriginPermission(candidates[0], permission);
    }
    if (attach) {
      attach.pdf_attach_attempts = attempts;
      return attach;
    }
  }
  // What the run actually learned comes first. This block used to sit *after* a
  // permission check that returned on any status other than "granted" —
  // including "unavailable" (no permissions API at all) and "not_requested"
  // (the per-capture prompt cap was reached), neither of which is a denial. So
  // a paper behind a login wall reported "PDF permission denied", sending the
  // user to grant something nobody had asked them for, and the message that
  // named the real obstacle was unreachable.
  let message = "browser PDF fetch failed — all candidates exhausted";
  const authStatuses = attempts.filter(a => a.status === "html_login" || a.status === "html_access_denied");
  if (authStatuses.length > 0) {
    message = authStatuses[0].status === "html_login"
      ? "PDF requires authentication — log in to the site in your browser first, then retry capture"
      : "PDF access denied — you may need institutional access or a subscription";
  } else if (deniedPermissions.length > 0) {
    // Every origin the user turned down, not just whichever group happened to
    // run last.
    const origins = deniedPermissions.map((p) => p.origin).filter(Boolean);
    message = origins.length
      ? `PDF permission denied for ${origins.join(", ")}`
      : "PDF permission denied";
  }
  const failure = {
    status: "error",
    message,
    pdf_attach_attempts: attempts,
  };
  if (deniedPermissions.length > 0) {
    failure.pdf_attach_permission = deniedPermissions[0];
    failure.pdf_attach_denied_origins = deniedPermissions.map((p) => p.origin).filter(Boolean);
  } else if (lastPermission && lastPermission.status !== "granted") {
    // Still surfaced for diagnostics — it explains why a candidate was skipped
    // — but it no longer decides the message.
    failure.pdf_attach_permission = lastPermission;
  }
  return failure;
}

async function tryPdfCandidates({ endpoint, citekey, bib, candidates, permission = null, pageUrl = null, attempts = [], pdfRequest = null }) {
  const permissionDenied = permission && permission.status === "denied";
  for (const candidate of candidates) {
    const url = candidateUrl(candidate);
    if (!url) continue;

    // navigate_monitor and discover_from_page need host permissions
    // (tabs.create to arbitrary domain). Skip if permission denied.
    if (!permissionDenied && candidate && candidate.method === "navigate_monitor") {
      // Try lightweight background fetch first — works for OA papers without
      // opening any tabs.  Only fall back to visible-tab navigation if the
      // response is HTML (paywalled gateway), not PDF.
      const referrer = candidate.referrer || pageUrl || undefined;
      const lightFetched = await fetchCandidateBytes(url, {
        credentials: "include",
        referrer,
      }, "browser_fetch", attempts);
      if (lightFetched) {
        const attachResult = await attachPdfToServer({ endpoint, citekey, bib, sourceUrl: url, bytes: lightFetched.bytes, pdfRequest });
        if (attachResult && attachResult.status === "ok") {
          markLastAttemptSaved(attempts, url);
          if (permission) attachResult.pdf_attach_permission = permission;
          return attachResult;
        }
      }

      // Same-origin page-context fetch carries real browser session.
      // Try before heavy navigation — avoids opening tabs if it works.
      if (_captureTabId && url !== pageUrl && sameOrigin(url, pageUrl)) {
        const pageBytes = await fetchPdfViaPageContext(_captureTabId, url, endpoint, citekey, bib, attempts, pdfRequest);
        if (pageBytes) {
          const attachResult = await attachPdfToServer({ endpoint, citekey, bib, sourceUrl: url, bytes: pageBytes, pdfRequest });
          if (attachResult && attachResult.status === "ok") {
            markLastAttemptSaved(attempts, url);
            if (permission) attachResult.pdf_attach_permission = permission;
            return attachResult;
          }
        }
      }

      // Lightweight methods exhausted — fall back to heavy visible-tab navigation.
      const monitored = await fetchPdfViaNavigateMonitor({
        candidate,
        endpoint,
        citekey,
        bib,
        pageUrl,
        attempts,
        pdfRequest,
      });
      if (monitored) {
        if (permission) monitored.pdf_attach_permission = permission;
        return monitored;
      }
      // All methods for this candidate exhausted — skip generic fallthrough
      // (page-context fetch + fetchPdfCandidate) to avoid duplicate heavy ops.
      continue;
    }

    if (!permissionDenied && candidate && candidate.method === "discover_from_page") {
      const discovered = await fetchPdfViaDiscoverFromPage({
        candidate,
        endpoint,
        citekey,
        bib,
        pageUrl,
        attempts,
        pdfRequest,
      });
      if (discovered) {
        if (permission) discovered.pdf_attach_permission = permission;
        return discovered;
      }
    }

    // Phase 3: try page-context fetch for same-origin stamp candidates.
    // IEEE anti-bot blocks background fetch() — injecting a content script
    // that fetches from within the page context carries the real browser session.
    if (_captureTabId && url !== pageUrl && sameOrigin(url, pageUrl)) {
      const pageBytes = await fetchPdfViaPageContext(_captureTabId, url, endpoint, citekey, bib, attempts, pdfRequest);
      if (pageBytes) {
        const attachResult = await attachPdfToServer({ endpoint, citekey, bib, sourceUrl: url, bytes: pageBytes, pdfRequest });
        if (attachResult && attachResult.status === "ok") {
          markLastAttemptSaved(attempts, url);
          if (permission) attachResult.pdf_attach_permission = permission;
          return attachResult;
        }
      }
    }

    try {
      const fetched = await fetchPdfCandidate(url, { pageUrl, attempts });
      if (!fetched) continue;
      const attachResult = await attachPdfToServer({ endpoint, citekey, bib, sourceUrl: url, bytes: fetched.bytes, pdfRequest });
      if (attachResult && attachResult.status === "ok") {
        markLastAttemptSaved(attempts, url);
        if (permission) attachResult.pdf_attach_permission = permission;
        return attachResult;
      }
      continue;
    } catch (_error) {
      continue;
    }
  }
}

async function fetchPdfViaNavigateMonitor({ candidate, endpoint, citekey, bib, pageUrl, attempts, pdfRequest = null }) {
  const url = candidateUrl(candidate);
  if (!_captureTabId || !url) return null;
  attempts.push({ url, mode: "navigate_monitor", status: "started", referrer: candidate.referrer || pageUrl || null });
  const observedUrl = await botBypassPdfUrl(_captureTabId, url, {
    visibleTimeoutMs: candidate.timeout_ms || BOT_BYPASS_VISIBLE_TIMEOUT_MS,
  });
  attempts.push({
    url,
    mode: "navigate_monitor",
    status: "observed",
    observed_url: observedUrl,
    observer_events: collectPdfObserverEvents(),
  });
  if (!observedUrl) return null;
  const fetched = await fetchCandidateBytes(observedUrl, {
    credentials: "include",
    referrer: candidate.referrer || pageUrl || undefined,
  }, "navigate_monitor_fetch", attempts);
  if (!fetched) return null;
  const attachResult = await attachPdfToServer({ endpoint, citekey, bib, sourceUrl: observedUrl, bytes: fetched.bytes, pdfRequest });
  if (attachResult && attachResult.status === "ok") {
    markLastAttemptSaved(attempts, observedUrl);
    return attachResult;
  }
  return null;
}

// ── discover_from_page acquisition ─────────────────────────────────────
// For article page candidates: open visible helper tab, click PDF links,
// observe resulting PDF download, fetch and attach bytes.

const BOT_BYPASS_PAGE_CLICK_TIMEOUT_MS = 20000;

async function fetchPdfViaDiscoverFromPage({ candidate, endpoint, citekey, bib, pageUrl, attempts, pdfRequest = null }) {
  const url = candidateUrl(candidate);
  if (!url) return null;
  attempts.push({ url, mode: "discover_from_page", status: "started", referrer: candidate.referrer || pageUrl || null });

  // `&&`, not `||`: the bypass opens a real, cookie-carrying navigation via
  // `chrome.tabs.create`, so the *candidate* must be allowlisted. With `||` an
  // allowlisted page authorised a navigation to any URL it happened to offer.
  const useBotBypass = isBotBypassWhitelisted(url) && isBotBypassWhitelisted(pageUrl);
  if (!useBotBypass) {
    attempts.push({ url, mode: "discover_from_page", status: "skipped", reason: "domain not bot-bypass allowlisted" });
    return null;
  }

  const pageTimeout = candidate.timeout_ms || BOT_BYPASS_PAGE_CLICK_TIMEOUT_MS;

  // Open visible helper tab to the article page.
  let helperTab = null;
  try { helperTab = await chrome.tabs.create({ url, active: false }); } catch (_e) { /* noop */ }
  if (!helperTab || !helperTab.id) {
    attempts.push({ url, mode: "discover_from_page", status: "error", reason: "helper tab creation failed" });
    return null;
  }

  // Arm observer on helper tab.
  startPdfObserver(helperTab.id);

  // Wait for page to load.
  const loaded = await waitForTabLoad(helperTab.id, pageTimeout);
  if (!loaded) {
    stopPdfObserver();
    try { await chrome.tabs.remove(helperTab.id); } catch (_e) { /* noop */ }
    attempts.push({ url, mode: "discover_from_page", status: "timeout", reason: "page load timeout" });
    return null;
  }

  // Give the page a moment to render fully.
  await sleep(2000);

  // Try clicking PDF links on the page.
  const clicked = await clickPdfElementsOnPage(helperTab.id);
  attempts.push({ url, mode: "discover_from_page", status: "clicked", clicked: clicked || "none" });

  // Wait for PDF to appear via observer.
  await sleep(3000);

  // Collect observed PDF URLs.
  const observedUrls = collectObservedPdfUrls();
  const events = collectPdfObserverEvents();
  attempts.push({ url, mode: "discover_from_page", status: "observed", observed_urls: observedUrls, observer_events: events });

  stopPdfObserver();
  try { await chrome.tabs.remove(helperTab.id); } catch (_e) { /* noop */ }

  if (observedUrls.length === 0) return null;

  // Try each observed URL.
  for (const observedUrl of observedUrls) {
    const fetched = await fetchCandidateBytes(observedUrl, {
      credentials: "include",
      referrer: candidate.referrer || pageUrl || undefined,
    }, "discover_from_page_fetch", attempts);
    if (!fetched) continue;
    const attachResult = await attachPdfToServer({ endpoint, citekey, bib, sourceUrl: observedUrl, bytes: fetched.bytes, pdfRequest });
    if (attachResult && attachResult.status === "ok") {
      markLastAttemptSaved(attempts, observedUrl);
      return attachResult;
    }
  }

  return null;
}

// Click any visible PDF/download links on the page.
async function clickPdfElementsOnPage(tabId) {
  try {
    const results = await chrome.scripting.executeScript({
      target: { tabId },
      func: () => {
        const clicked = [];
        // Common PDF link/button selectors used by publishers.
        const selectors = [
          'a[href*="/pdf/"]', 'a[href*="/epdf/"]', 'a[href*="/pdfdirect/"]',
          'a[href*="/pdfft"]', 'a[href*=".pdf"]', 'a[href*="download"]',
          '[data-testid="pdf-download"]', '[aria-label*="PDF"]',
          '.pdf-link', '.download-pdf', '.article-pdf',
          'a[title*="PDF"]', 'a[title*="Download"]',
        ];
        for (const sel of selectors) {
          try {
            const el = document.querySelector(sel);
            if (el && el.offsetParent !== null) { // visible
              el.click();
              clicked.push(sel);
              break; // One click is enough — let observer catch the result.
            }
          } catch (_e) { /* skip broken selector */ }
        }
        return clicked.length > 0 ? clicked[0] : null;
      },
    });
    if (results && results[0] && results[0].result) return results[0].result;
  } catch (_e) { /* page context may reject script */ }
  return null;
}

// Wait for a tab to reach "complete" loading state.
function waitForTabLoad(tabId, timeoutMs) {
  return new Promise((resolve) => {
    const timer = setTimeout(() => {
      chrome.tabs.onUpdated.removeListener(listener);
      resolve(false);
    }, timeoutMs);
    const listener = (updatedTabId, changeInfo) => {
      if (updatedTabId === tabId && changeInfo.status === "complete") {
        clearTimeout(timer);
        chrome.tabs.onUpdated.removeListener(listener);
        resolve(true);
      }
    };
    chrome.tabs.onUpdated.addListener(listener);
  });
}

async function attachPdfToServer({ endpoint, citekey, bib, sourceUrl, bytes, pdfRequest = null }) {
  // The raw route requires a `request_id` naming an attach session, and refuses
  // without one — deliberately, since the session carries the TTL, byte-limit,
  // source-URL and citekey checks, and a control the caller can decline is no
  // control at all. A capture with no `pdfRequest` has no session to name, so
  // this used to upload the entire PDF to a route certain to 403 and then
  // upload the identical bytes again to the fallback: double the bandwidth, and
  // a rejected security check in the server log for every such capture.
  const hasAttachSession = Boolean(pdfRequest?.request_id);
  if (hasAttachSession) {
    const rawResponse = await fetch(rawAttachUrl(endpoint, { citekey, bib, sourceUrl, pdfRequest }), {
      method: "POST",
      headers: { "Content-Type": "application/pdf", ...attachTokenHeader(pdfRequest), ...(await getAuthHeaders()) },
      body: bytes,
    });
    const rawResult = await jsonOrNull(rawResponse);
    if (rawResponse.ok && rawResult && rawResult.status === "ok") return rawResult;
  }

  const base64 = arrayBufferToBase64(bytes);
  // Inherit the bib the capture actually wrote to. The raw URL above carries
  // the plan's concrete library name, while this fallback used to send the
  // popup's `bib` — which is null whenever the user left the selector on
  // "default" — so the request was rejected and an already-downloaded PDF was
  // discarded.
  const effectiveBib = bib || pdfRequest?.bib || bibFromAttachUrl(pdfRequest) || undefined;
  const attachResponse = await fetch(endpointFor(endpoint, "/attach-pdf-bytes"), {
    method: "POST",
    headers: { "Content-Type": "application/json", ...(await getAuthHeaders()) },
    body: JSON.stringify({
      citekey,
      ...(effectiveBib ? { bib: effectiveBib } : {}),
      source_url: sourceUrl,
      pdf_base64: base64,
      ...attachSessionPayload(pdfRequest),
    }),
  });
  return await jsonOrNull(attachResponse);
}

function rawAttachUrl(endpoint, { citekey, bib, sourceUrl, pdfRequest = null }) {
  // The planned URL comes from the server, which derives it from the
  // user-editable `api_url` config key. `new URL(absolute, base)` discards the
  // base, so an absolute value here sent the API token and the PDF bytes to
  // whatever host it named. `isLoopbackEndpoint` exists for exactly this and
  // was not applied on this path.
  const planned = pdfRequest?.attach?.url;
  const plannedUrl = planned && isLoopbackEndpoint(planned) ? planned : null;
  const base = plannedUrl || `${endpointFor(endpoint, "/attach-pdf-raw")}?${new URLSearchParams({ citekey }).toString()}`;
  const url = new URL(base, endpointFor(endpoint, "/"));
  if (!url.searchParams.get("citekey")) url.searchParams.set("citekey", citekey);
  if (bib && !url.searchParams.get("bib")) url.searchParams.set("bib", bib);
  url.searchParams.set("source_url", sourceUrl);
  return url.toString();
}


function bibFromAttachUrl(pdfRequest) {
  const planned = pdfRequest?.attach?.url;
  if (!planned) return null;
  try {
    return new URL(planned, "http://127.0.0.1").searchParams.get("bib");
  } catch (_error) {
    return null;
  }
}

function attachTokenHeader(pdfRequest) {
  const token = pdfRequest?.attach?.token;
  return token ? { "X-Pzi-Attach-Token": token } : {};
}

function attachSessionPayload(pdfRequest) {
  const requestId = pdfRequest?.request_id;
  const token = pdfRequest?.attach?.token;
  if (!requestId || !token) return {};
  return { request_id: requestId, attach_token: token };
}

async function fetchPdfViaPageContext(tabId, url, _endpoint, citekey, bib, attempts, pdfRequest = null) {
  if (!tabId || !url) return null;

  let result;
  try {
    const results = await chrome.scripting.executeScript({
      target: { tabId },
      world: "MAIN",
      func: async (pdfUrl) => {
        try {
          const response = await fetch(pdfUrl, { credentials: "include" });
          if (!response.ok) return { error: `fetch HTTP ${response.status}` };
          const contentType = response.headers.get("content-type") || "";
          if (!contentType.toLowerCase().includes("pdf")) {
            return { error: "not_pdf", contentType };
          }
          const buf = await response.arrayBuffer();
          if (buf.byteLength === 0) return { error: "empty" };
          // Return as base64 to background for secure API attach.
          const bytes = new Uint8Array(buf);
          let binary = "";
          for (let i = 0; i < bytes.byteLength; i++) {
            binary += String.fromCharCode(bytes[i]);
          }
          return { base64: btoa(binary) };
        } catch (e) {
          return { error: e.message };
        }
      },
      args: [url],
    });
    result = results?.[0]?.result;
  } catch (_e) {
    attempts.push({ url, mode: "page_context_fetch", status: "fetch_error", error: _e?.message || String(_e) });
    return null;
  }

  if (result && result.base64) {
    attempts.push({ url, mode: "page_context_fetch", status: "fetched" });
    try {
      const binary = atob(result.base64);
      const bytes = new Uint8Array(binary.length);
      for (let i = 0; i < binary.length; i++) {
        bytes[i] = binary.charCodeAt(i);
      }
      return bytes.buffer;
    } catch (_e) {
      attempts.push({ url, mode: "page_context_fetch", status: "base64_decode_error", error: _e?.message || String(_e) });
      return null;
    }
  }
  if (result && result.error) {
    attempts.push({ url, mode: "page_context_fetch", status: result.error, content_type: result.contentType });
  }
  return null;
}

async function fetchPdfCandidate(candidate, { pageUrl = null, attempts = [] } = {}) {
  const url = candidateUrl(candidate);
  if (!url) return null;
  const firstOpts = { credentials: "include" };
  // Always send Referer — many publishers (IEEE, ACM) require it to serve
  // PDF download gateway pages instead of anti-leech HTML forms.
  if (pageUrl) firstOpts.referrer = pageUrl;
  const first = await fetchCandidateBytes(url, firstOpts, "browser_fetch", attempts);
  if (first) return first;

  // Try extracting a meta-refresh redirect URL from the HTML response.
  // IEEE stamp.jsp and similar gateways serve HTML with <meta http-equiv="refresh">
  // rather than HTTP 3xx.  fetch() does not follow these, so we must parse explicitly.
  const redirectedUrl = await extractMetaRedirectUrl(url, firstOpts);
  if (redirectedUrl) {
    const redirected = await fetchCandidateBytes(redirectedUrl, firstOpts, "browser_fetch_meta", attempts);
    if (redirected) return redirected;
  }

  const cookieRetry = shouldRetryWithCookies(url, pageUrl);
  attempts.push({ url, mode: "diag", status: "cookie_retry_decision", cookie_retry: cookieRetry, candidate_is_page_url: url === pageUrl });
  if (cookieRetry) {
    // No `chrome.permissions.request` here: `cookies` is a *required*
    // permission in the manifest and absent from `optional_permissions`, so
    // requesting it is rejected on Firefox — the catch returned "denied" and
    // this whole retry never ran. It is granted at install; just read.
    const cookieHeader = await cookieHeaderForUrl(url);
    {
      attempts.push({ url, mode: "diag", status: "cookie_header", has_header: !!cookieHeader });
      if (cookieHeader) {
        const second = await fetchCandidateBytes(url, {
          credentials: "include",
          headers: { Cookie: cookieHeader },
          referrer: pageUrl || undefined,
        }, "browser_fetch_cookies", attempts);
        if (second) {
          return second;
        }
      }
    }
  }

  // If still no PDF, try bot bypass for allowlisted domains.
  const bypassAllowed = url && isBotBypassWhitelisted(url);
  attempts.push({ url, mode: "diag", status: "bot_bypass_decision", bypass_allowed: bypassAllowed, tab_id: _captureTabId });
  if (bypassAllowed) {
    const bypassedUrl = await botBypassPdfUrl(_captureTabId, url);
    attempts.push({ url, mode: "diag", status: "bot_bypass_result", bypassed_url: bypassedUrl });
    if (bypassedUrl) {
      const bypassed = await fetchCandidateBytes(bypassedUrl, { credentials: "include" }, "bot_bypass", attempts);
      if (bypassed) return bypassed;
    }
  }

  return null;
}

function shouldRetryWithCookies(candidate, pageUrl) {
  if (candidate === pageUrl) return false;
  try {
    const parsed = new URL(candidate);
    return /\.pdf([?#].*)?$/i.test(parsed.pathname)
      || /\/stamp\//i.test(parsed.pathname)
      || /[?&](pdf|download)=/i.test(parsed.search);
  } catch (_error) {
    return false;
  }
}

async function fetchCandidateBytes(candidate, options, mode, attempts) {
  const url = candidateUrl(candidate);
  if (!url) return null;
  let response;
  try {
    response = await fetchWithTimeout(url, options, PDF_FETCH_TIMEOUT_MS);
  } catch (error) {
    attempts.push({ url, mode, status: "fetch_error", error: error?.message || String(error), byte_count: 0 });
    return null;
  }
  const contentType = response.headers.get("content-type") || "";
  if (!response.ok) {
    attempts.push({ url, mode, status: "http_error", http_status: response.status || null, content_type: contentType, byte_count: 0 });
    return null;
  }
  if (contentLengthExceedsAttachLimit(response.headers)) {
    attempts.push({ url, mode, status: "too_large", http_status: response.status || null, content_type: contentType, byte_count: Number.parseInt(response.headers.get("content-length") || "0", 10) || 0 });
    return null;
  }
  const bytes = await response.arrayBuffer();
  if (bytes.byteLength > MAX_ATTACH_PDF_BYTES) {
    attempts.push({ url, mode, status: "too_large", http_status: response.status || null, content_type: contentType, byte_count: bytes.byteLength });
    return null;
  }
  if (!looksLikePdfBytes(bytes) && !contentType.toLowerCase().includes("pdf")) {
    let htmlStatus = "not_pdf";
    let textSnippet = null;
    if (contentType.toLowerCase().includes("html") || /\btext\//i.test(contentType)) {
      try {
        const decoder = new TextDecoder();
        const text = decoder.decode(bytes);
        const lower = text.toLowerCase();
        if (/sign[\s-]*in|login|log[\s-]*in|authentication\s+required/i.test(lower)) {
          htmlStatus = "html_login";
        } else if (/access\s+denied|forbidden|not\s+authorized|subscription\s+required|payment\s+required/i.test(lower)) {
          htmlStatus = "html_access_denied";
        }
        // Deliberately not retained. This is the body of a page fetched with
        // the user's cookies, so a login form's CSRF token or a prefilled
        // username could land in the popup's raw-response pane. The
        // classification below is what the diagnostics actually need.
        textSnippet = null;
      } catch (_e) { /* ignore decode errors */ }
    }
    attempts.push({ url, mode, status: htmlStatus, http_status: response.status || null, content_type: contentType, byte_count: bytes.byteLength, text_snippet: textSnippet });
    return null;
  }
  attempts.push({ url, mode, status: "fetched", http_status: response.status || null, content_type: contentType, byte_count: bytes.byteLength });
  return { bytes };
}

/**
 * Fetch the URL as text and extract a meta-refresh redirect URL.
 * Returns the resolved URL or null.
 *
 * Handles patterns like:
 *   <meta http-equiv="refresh" content="0;URL=https://example.com/paper.pdf">
 *   <meta http-equiv="Refresh" content="0; url=https://example.com/paper.pdf">
 */
async function extractMetaRedirectUrl(candidate, options) {
  try {
    const url = typeof candidate === "string" ? candidate : candidate?.url;
    if (!url) return null;
    const response = await fetchWithTimeout(url, options, PDF_FETCH_TIMEOUT_MS);
    if (!response.ok) return null;
    const text = await response.text();
    // Match meta refresh patterns with flexible spacing/case/quoting
    const re = /<meta\s[^>]*http-equiv\s*=\s*["']?refresh["']?[^>]*content\s*=\s*["']?(\d+)\s*;\s*(?:url\s*=\s*)?(\S+?)["']?[^>]*\/?>/i;
    const match = re.exec(text);
    if (!match) return null;
    const redirectUrl = match[2].replace(/['"\s]/g, "");
    // Resolve relative URLs against the base URL
    try {
      const resolved = new URL(redirectUrl, url).href;
      // Revalidate: prevent meta-refresh from redirecting to local/private URLs.
      if (!isSafePublicHttpUrl(resolved)) return null;
      return resolved;
    } catch (_e) {
      return null;
    }
  } catch (_error) {
    return null;
  }
}

function markLastAttemptSaved(attempts, url) {
  for (let index = attempts.length - 1; index >= 0; index -= 1) {
    if (attempts[index].url === url && attempts[index].status === "fetched") {
      attempts[index].status = "saved";
      return;
    }
  }
}
