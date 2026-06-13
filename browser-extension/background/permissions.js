// pzi browser extension — cookie and origin-permission helpers.
//
// Thin wrappers over chrome.permissions / chrome.cookies used by the PDF
// fetch pipeline. Pure URL reasoning is delegated to utils.js.

import { candidateUrl, originOf, sameOrigin } from "./utils.js";

export async function requestCookiePermission() {
  if (!chrome.permissions?.request) return { status: "denied" };
  try {
    const granted = Boolean(await chrome.permissions.request({ permissions: ["cookies"] }));
    return { status: granted ? "granted" : "denied" };
  } catch (_error) {
    return { status: "denied" };
  }
}

export async function cookieHeaderForUrl(url) {
  if (!chrome.cookies?.getAll) return "";
  let cookies = [];
  try {
    cookies = await chrome.cookies.getAll({ url, partitionKey: {} });
  } catch (_error) {
    cookies = await chrome.cookies.getAll({ url });
  }
  return cookies.map((cookie) => `${cookie.name}=${cookie.value}`).join("; ");
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
    permissions.set(origin, await requestTemporaryOriginPermission(group[0]));
  }
  return permissions;
}

export function permissionForCandidate(permissions, candidate) {
  if (!permissions || typeof permissions.get !== "function") return null;
  const origin = originOf(candidateUrl(candidate));
  if (!origin) return null;
  return permissions.get(origin) || null;
}

function originPatternForUrl(url) {
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
  try {
    granted = Boolean(await chrome.permissions.request(request));
  } catch (_error) {
    granted = false;
  }
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
  return permission;
}

export async function fetchWithTimeout(url, options, timeoutMs) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await fetch(url, { ...options, signal: controller.signal });
  } finally {
    clearTimeout(timer);
  }
}
