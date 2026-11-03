// pzi browser extension — cookie and origin-permission helpers.
//
// Thin wrappers over chrome.permissions / chrome.cookies used by the PDF
// fetch pipeline. Pure URL reasoning is delegated to utils.js.

import { PDF_BODY_IDLE_TIMEOUT_MS, PDF_BODY_TOTAL_TIMEOUT_MS } from "./config.js";
import { PRIVATE_DESTINATION_PATTERNS, candidateUrl, originOf, sameOrigin } from "./utils.js";

export { PRIVATE_DESTINATION_PATTERNS };

//: How many distinct cross-origin permission dialogs one capture may raise.
//: The candidate list comes from the page, so without a cap a hostile page can
//: queue as many prompts as it likes and wait for one to be accepted.
const MAX_ORIGIN_PROMPTS_PER_CAPTURE = 2;

// Prompts already spent, keyed by the per-capture permissions map. The map is
// what `requestPdfOriginPermissions` hands to `maybeStreamPdfBytes`, so both
// stages of one capture draw on a single budget. It used to be a local counter
// in the first stage, and the second stage asked for every origin the server
// named — which the page, through the server's discovery, can also influence.
const _promptsUsed = new WeakMap();

/**
 * Spend one prompt from this capture's budget. Returns false when it is spent.
 */
export function takeOriginPrompt(permissions) {
  const used = _promptsUsed.get(permissions) || 0;
  if (used >= MAX_ORIGIN_PROMPTS_PER_CAPTURE) return false;
  _promptsUsed.set(permissions, used + 1);
  return true;
}

export async function cookieHeaderForUrl(url) {
  if (!chrome.cookies?.getAll) return "";
  let cookies = [];
  try {
    cookies = await chrome.cookies.getAll({ url, partitionKey: {} });
  } catch (_error) {
    // The fallback needs its own guard: it sat bare in the `catch`, so a
    // backend that rejects both shapes — or has the permission revoked
    // mid-capture — threw straight out of the capture path. No cookies is a
    // degraded capture, not a failed one.
    try {
      cookies = await chrome.cookies.getAll({ url });
    } catch (_fallbackError) {
      return "";
    }
  }
  return (cookies || []).map((cookie) => `${cookie.name}=${cookie.value}`).join("; ");
}

export function groupPdfCandidates(candidates, pageUrl) {
  const same = [];
  const cross = [];
  for (const candidate of candidates || []) {
    const url = candidateUrl(candidate);
    if (!url) continue;
    const bucket = sameOrigin(url, pageUrl) ? same : cross;
    if (!bucket.some((item) => candidateUrl(item) === url)) bucket.push(candidate);
  }
  return { sameOrigin: same, crossOrigin: cross };
}

export function groupCandidatesByOrigin(candidates) {
  const groups = [];
  const indexByOrigin = new Map();
  for (const candidate of candidates || []) {
    const origin = originOf(candidateUrl(candidate));
    if (!origin) continue;
    if (!indexByOrigin.has(origin)) {
      indexByOrigin.set(origin, groups.length);
      groups.push([]);
    }
    groups[indexByOrigin.get(origin)].push(candidate);
  }
  return groups;
}

export async function requestPdfOriginPermissions(candidates, pageUrl) {
  const permissions = new Map();
  const grouped = groupPdfCandidates(candidates || [], pageUrl);
  for (const group of groupCandidatesByOrigin(grouped.sameOrigin)) {
    const origin = originOf(candidateUrl(group[0]));
    if (!origin || permissions.has(origin)) continue;
    permissions.set(origin, { status: "unavailable", origin, removed: false });
  }
  const originGroups = groupCandidatesByOrigin(grouped.crossOrigin);
  for (const group of originGroups) {
    const origin = originOf(candidateUrl(group[0]));
    if (!origin || permissions.has(origin)) continue;
    // Capped, because the list is page-controlled. One capture measured ten
    // consecutive permission dialogs, and prompt fatigue is the only thing
    // between a hostile page and a granted host permission for an origin the
    // user never meant to trust. Candidates are ordered by confidence, so the
    // first few are the ones worth asking about.
    if (!takeOriginPrompt(permissions)) {
      permissions.set(origin, { status: "not_requested", origin, removed: false });
      continue;
    }
    permissions.set(origin, await requestTemporaryOriginPermission(group[0]));
  }
  return permissions;
}

export async function releasePdfOriginPermissions(permissions) {
  // Whoever borrows, returns. `requestPdfOriginPermissions` grants every
  // candidate origin *before* acquisition starts, but `maybeStreamPdfBytes`
  // releases only the origin group it processed and returns as soon as one
  // succeeds — so every origin after the winning one kept a permanent host
  // permission, silently widening what the always-on `webRequest` observer
  // sees. Entries already handed back carry `removed`, and one the user
  // granted themselves is not ours to revoke (`removeTemporaryOriginPermission`
  // draws that line).
  if (!permissions || typeof permissions.values !== "function") return permissions;
  for (const permission of permissions.values()) {
    if (!permission || permission.removed) continue;
    await removeTemporaryOriginPermission(permission.origin, permission);
  }
  return permissions;
}

export function permissionForCandidate(permissions, candidate) {
  if (!permissions || typeof permissions.get !== "function") return null;
  const origin = originOf(candidateUrl(candidate));
  if (!origin) return null;
  return permissions.get(origin) || null;
}

// ── State a capture borrows, and how it is handed back if the popup dies ──
// `captureCurrentTab` runs in the popup realm, and a popup closes on blur: a
// `finally` that never runs cannot hand anything back. Two things are borrowed
// for a capture. Temporary host permissions persist in the browser profile until
// removed, so each new grant is journaled in `chrome.storage.local` before it
// is requested and struck from the journal when handed back. Session rules die
// with the browser but not with the popup, and their ids are fixed, so they need
// no journal. A Web Lock held for the life of a capture says whether anyone is
// still using either (the browser drops a lock when its realm dies), and
// `sweepAbandonedCaptureState` clears whatever no live lock covers. Without
// `navigator.locks` nothing is swept, because a live capture cannot be told
// from a dead one.
const BORROWED_GRANTS_KEY = "pzi:borrowedGrants";
const CAPTURE_LOCK = "pzi-capture-active";
const GUARD_LOCK = "pzi-guard-active";
let _journalQueue = Promise.resolve();

function updateGrantJournal(change) {
  const next = _journalQueue.then(async () => {
    const storage = chrome.storage?.local;
    if (!storage?.get || !storage?.set) return;
    const stored = (await storage.get(BORROWED_GRANTS_KEY))?.[BORROWED_GRANTS_KEY];
    const current = Array.isArray(stored) ? stored.filter((item) => typeof item === "string") : [];
    await storage.set({ [BORROWED_GRANTS_KEY]: change(current) });
  });
  _journalQueue = next.catch(() => {});
  return next.catch(() => {});
}

/** Record that this capture is about to borrow `pattern` (a `https://host/*` origin pattern). */
export function noteBorrowedGrant(pattern) {
  return updateGrantJournal((list) => (list.includes(pattern) ? list : [...list, pattern]));
}

/** Strike `pattern` from the journal: it was handed back, or was never ours. */
export function forgetBorrowedGrant(pattern) {
  return updateGrantJournal((list) => list.filter((item) => item !== pattern));
}

/** Hold the "a capture is in progress" lock; the returned function lets go. */
export async function holdCaptureLock() {
  return holdLock(CAPTURE_LOCK);
}

function holdLock(name) {
  const locks = globalThis.navigator?.locks;
  if (!locks?.request) return Promise.resolve(() => {});
  // The returned function resolves once the lock is really gone: a caller that
  // goes on to ask for it exclusively would otherwise find it still held.
  return new Promise((resolve) => {
    const finished = locks.request(name, { mode: "shared" }, () => new Promise((release) => {
      resolve(async () => { release(); await finished; });
    }));
  });
}

async function whenNobodyHolds(name, work, { withoutLocks }) {
  const locks = globalThis.navigator?.locks;
  if (!locks?.request) {
    if (withoutLocks) await work();
    return;
  }
  await locks.request(name, { mode: "exclusive", ifAvailable: true }, async (lock) => {
    if (lock) await work();
  });
}

/**
 * Hand back what a capture that never finished still holds: journaled host
 * permissions and the session guard rules. Runs at popup open and worker
 * start; a capture still running in some realm holds a lock and is left alone.
 */
export async function sweepAbandonedCaptureState() {
  try {
    await whenNobodyHolds(CAPTURE_LOCK, async () => {
      const stored = (await chrome.storage?.local?.get?.(BORROWED_GRANTS_KEY))?.[BORROWED_GRANTS_KEY];
      for (const pattern of Array.isArray(stored) ? stored : []) {
        if (typeof pattern !== "string") continue;
        try {
          await chrome.permissions.remove({ origins: [pattern] });
        } catch (_error) {
          continue; // stays journaled for the next sweep
        }
        await forgetBorrowedGrant(pattern);
      }
    }, { withoutLocks: false });
    await whenNobodyHolds(GUARD_LOCK, removeGuardRules, { withoutLocks: false });
  } catch (_error) {
    /* a sweep is best effort; the next one tries again */
  }
}

export function originPatternForUrl(url) {
  try {
    return `${new URL(candidateUrl(url)).origin}/*`;
  } catch (_error) {
    return null;
  }
}

export async function requestTemporaryOriginPermission(url) {
  const pattern = originPatternForUrl(url);
  if (!pattern || !chrome.permissions) {
    return { status: "unavailable", origin: originOf(candidateUrl(url)), removed: false };
  }
  const request = { origins: [pattern] };
  let alreadyGranted = false;
  try {
    alreadyGranted = Boolean(await chrome.permissions.contains(request));
  } catch (_error) {
    alreadyGranted = false;
  }
  if (alreadyGranted) {
    return { status: "granted", origin: originOf(candidateUrl(url)), removed: false, already_granted: true };
  }
  let granted = false;
  // Journaled first: a popup that closes while the dialog is open still leaves
  // a record, and a grant the user then declines is struck again below.
  await noteBorrowedGrant(pattern);
  try {
    granted = Boolean(await chrome.permissions.request(request));
  } catch (_error) {
    granted = false;
  }
  if (!granted) await forgetBorrowedGrant(pattern);
  return { status: granted ? "granted" : "denied", origin: originOf(candidateUrl(url)), removed: false };
}

export async function removeTemporaryOriginPermission(url, permission) {
  if (!permission || permission.status !== "granted" || permission.already_granted) return permission;
  const pattern = originPatternForUrl(url);
  if (!pattern || !chrome.permissions?.remove) return permission;
  try {
    permission.removed = Boolean(await chrome.permissions.remove({ origins: [pattern] }));
  } catch (_error) {
    permission.removed = false;
  }
  if (permission.removed) await forgetBorrowedGrant(pattern);
  return permission;
}

export async function fetchWithTimeout(url, options, timeoutMs) {
  // Bounds the wait for *headers* only: the timer is cleared once `fetch`
  // resolves. A caller that goes on to read the body must use
  // `readBodyBounded`, which owns the body's byte and time limits.
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await fetch(url, { ...options, signal: controller.signal });
  } finally {
    clearTimeout(timer);
  }
}

export class BodyLimitError extends Error {
  constructor(reason, byteCount) {
    super(reason === "too_large" ? "response body exceeds the byte limit" : "response body stalled or ran past its time limit");
    this.name = "BodyLimitError";
    this.reason = reason; // "too_large" | "timeout"
    this.byteCount = byteCount;
  }
}

/**
 * Read a response body without letting it outgrow *maxBytes* or outlast its
 * time budget, and cancel the transfer when either is hit.
 *
 * `response.arrayBuffer()` buffers whatever the server sends for as long as it
 * keeps sending: a chunked body has no Content-Length to check first, and the
 * header timer in `fetchWithTimeout` is gone by then, so an endless or stalled
 * stream held the capture (and the host permissions it had borrowed) open and
 * grew memory without limit.
 *
 * `truncate` returns the first *maxBytes* instead of throwing, for callers that
 * only need a prefix (a meta-refresh sits in the page head).
 */
export async function readBodyBounded(
  response,
  {
    maxBytes,
    idleTimeoutMs = PDF_BODY_IDLE_TIMEOUT_MS,
    totalTimeoutMs = PDF_BODY_TOTAL_TIMEOUT_MS,
    truncate = false,
  },
) {
  const reader = response.body?.getReader?.();
  if (!reader) {
    // A response with no stream (a test double). Real responses always have one.
    const whole = await response.arrayBuffer();
    if (whole.byteLength <= maxBytes) return whole;
    if (truncate) return whole.slice(0, maxBytes);
    throw new BodyLimitError("too_large", whole.byteLength);
  }
  const chunks = [];
  let total = 0;
  let totalTimer;
  const overall = new Promise((resolve) => {
    totalTimer = setTimeout(() => resolve("timeout"), totalTimeoutMs);
  });
  try {
    for (;;) {
      let idleTimer;
      const idle = new Promise((resolve) => {
        idleTimer = setTimeout(() => resolve("timeout"), idleTimeoutMs);
      });
      const pending = reader.read();
      pending.catch(() => {}); // a read abandoned by a timeout must not surface later
      let step;
      try {
        step = await Promise.race([pending, idle, overall]);
      } finally {
        clearTimeout(idleTimer);
      }
      if (step === "timeout") throw new BodyLimitError("timeout", total);
      if (step.done) break;
      chunks.push(step.value);
      total += step.value.byteLength;
      if (total > maxBytes) {
        if (truncate) break;
        throw new BodyLimitError("too_large", total);
      }
    }
  } finally {
    clearTimeout(totalTimer);
    // Stops the transfer: on an early exit this is what closes the connection.
    reader.cancel().catch(() => {});
  }
  const out = new Uint8Array(Math.min(total, maxBytes));
  let offset = 0;
  for (const chunk of chunks) {
    const room = out.byteLength - offset;
    if (room <= 0) break;
    out.set(chunk.byteLength > room ? chunk.subarray(0, room) : chunk, offset);
    offset += Math.min(chunk.byteLength, room);
  }
  return out.buffer;
}

// ── Private-network guard for extension-initiated fetches ───────────────
// A background `fetch` follows redirects on its own, and `redirect: "manual"`
// would hide the destination (an opaque response). Once the user has granted a
// publisher's origin, the browser issues the redirected GET to wherever the
// publisher points — including 127.0.0.1 or a LAN host — before any code here
// sees `response.url`. The only point that can refuse the *request* is the
// network layer, so a session declarativeNetRequest rule blocks private
// destinations for requests this extension initiates, with an allow rule for
// the pzi endpoint it has to keep talking to.
//
// The pattern list lives in utils.js: `isSafePublicHttpUrl` is built from the
// same list, so the rule table and the predicate cannot drift.
//: Every fetch the extension makes to fetch a PDF is an XHR-type request.
export const GUARD_RESOURCE_TYPES = ["xmlhttprequest"];
const GUARD_ALLOW_RULE_ID = 91001;
const GUARD_RULE_IDS = [
  GUARD_ALLOW_RULE_ID,
  ...PRIVATE_DESTINATION_PATTERNS.map((_pattern, index) => GUARD_ALLOW_RULE_ID + 1 + index),
];
let _guardHolders = 0;
let _guardInstalled = null; // the in-flight or finished install shared by every holder
let _guardLockRelease = null;

/**
 * Block private destinations for this extension's fetches until the returned
 * function is called. Resolves to `null` when the browser has no
 * declarativeNetRequest (a test realm); rejects when the rules cannot be
 * installed, so the caller can refuse to fetch rather than fetch unguarded.
 *
 * The holder count moves before the first await: counted after it, two
 * concurrent installs each saw zero holders and the pair counted as one, so the
 * first release dropped the rules under the second. The guard lock carries the
 * same fact across realms.
 */
export async function installPrivateNetworkGuard(endpoint) {
  const dnr = chrome.declarativeNetRequest;
  if (!dnr?.updateSessionRules || !chrome.runtime?.getURL) return null;
  _guardHolders += 1;
  try {
    if (!_guardInstalled) {
      _guardInstalled = (async () => {
        _guardLockRelease = await holdLock(GUARD_LOCK);
        const initiator = new URL(chrome.runtime.getURL("/")).hostname;
        const condition = { initiatorDomains: [initiator], resourceTypes: GUARD_RESOURCE_TYPES };
        const addRules = [];
        const endpointOrigin = originPatternForUrl(endpoint)?.replace(/\/\*$/, "");
        if (endpointOrigin) {
          addRules.push({
            id: GUARD_ALLOW_RULE_ID,
            priority: 2,
            action: { type: "allow" },
            condition: { ...condition, urlFilter: `|${endpointOrigin}/` },
          });
        }
        PRIVATE_DESTINATION_PATTERNS.forEach((regexFilter, index) => {
          addRules.push({
            id: GUARD_ALLOW_RULE_ID + 1 + index,
            priority: 1,
            action: { type: "block" },
            condition: { ...condition, regexFilter },
          });
        });
        await dnr.updateSessionRules({ removeRuleIds: GUARD_RULE_IDS, addRules });
      })();
    }
    await _guardInstalled;
  } catch (error) {
    await releaseGuardCount()?.();
    throw error;
  }
  return releasePrivateNetworkGuard;
}

// Returns the function that lets go of this realm's guard lock when this was the
// last holder here, else null.
function releaseGuardCount() {
  _guardHolders = Math.max(0, _guardHolders - 1);
  if (_guardHolders > 0) return null;
  _guardInstalled = null;
  const release = _guardLockRelease || (async () => {});
  _guardLockRelease = null;
  return release;
}

async function releasePrivateNetworkGuard() {
  const letGo = releaseGuardCount();
  if (!letGo) return;
  await letGo();
  await whenNobodyHolds(GUARD_LOCK, removeGuardRules, { withoutLocks: true });
}

async function removeGuardRules() {
  try {
    await chrome.declarativeNetRequest.updateSessionRules({ removeRuleIds: GUARD_RULE_IDS });
  } catch (_error) {
    /* session rules do not outlive the browser; nothing more to do */
  }
}
