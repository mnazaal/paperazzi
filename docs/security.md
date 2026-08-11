# paperazzi Security Model

paperazzi is local-first. Every data path keeps user content local, rejects
non-local/private network traffic, and exposes the absolute minimum surface
needed for a browser-extension-assisted paper capture workflow.

## Architecture overview

```text
[Browser (extension)]                    [paperazzi CLI / server]
        |                                          |
        |  POST /capture              (JSON)       |  pzi add <doi/url/pdf>
        |  POST /attach-pdf-bytes     (JSON)       |
        |  POST /attach-pdf-raw       (binary)     |
        |  POST /tags/add, /tags/remove            |
        |  POST /update, /promote, /delete         |
        |  POST /inbox/drain                       |
        |  POST /browser/discover, /browser/download
        |  GET  /health, /bibs, /search, /entries, /tags, /export
        |  GET  /pdf/<citekey>, /export/raw        |
        |                                          |
        +----- http://127.0.0.1:8765 --------------+
                        |
              [translation-server :1969]
              (internal, no external exposure)
```

**Nineteen routes, not three.** An earlier version of this diagram showed only
the three capture routes, which understated what a caller holding the API token
can do. Two deserve naming explicitly:

- `POST /browser/download` is a "fetch this URL through a headless browser
  carrying my real profile" primitive. It is SSRF-guarded (`url_safety`), but a
  caller with the token can drive an authenticated browser session.
- `POST /delete` and `POST /update` mutate the library.

Everything on this surface is gated by the same token; there is no per-route
authorization. Treat the token as equivalent to shell access to your library.

- pzi server binds `127.0.0.1` by default.
- All HTTP traffic stays on the loopback interface.
- Translation-server is a local process; paperazzi auto-starts and auto-stops it.
- PDF bytes and BibTeX metadata never leave the local machine through paperazzi.

## Extension permissions — why each exists

The browser extension is the most privileged component. Every permission is
explained below.

| Permission | Why needed | What data it accesses |
|---|---|---|
| `activeTab` | Extract page HTML, metadata, PDF candidate URLs, and observed PDF responses from the tab the user clicked on. Access ends when the tab is closed. | Current tab URL, HTML `<head>`, `<meta>` tags, visible PDF links. |
| `storage` | Persist API endpoint, auth token, recent captures, and user preferences (bib selection, dry-run toggle) across sessions. | Small JSON values stored in browser local/session storage. |
| `scripting` | Inject content scripts to extract embedded metadata (JSON-LD, OpenGraph, highwire tags) and to execute page-context PDF fetches that carry the real browser session. | HTML source, page variables. |
| `cookies` | Forward cookies from the active tab domain to the translation-server so it can resolve authenticated metadata (e.g., paywalled publisher pages that your institution provides). Used for metadata lookup, not for arbitrary sites. | Cookie header for the active tab's domain only. |
| `contextMenus` | Register "Save to paperazzi" right-click menu on links so users can capture a paper URL without navigating to it. | Target link URL (no page access). |
| `webRequest` | Observe network responses to detect PDF files as they load (the PDF observer cache) so the extension can capture PDF candidates that appear via JavaScript redirects or dynamic loads. | Response headers (Content-Type only) and URL. |

### Host permissions explained

**Permanent host permissions** (`host_permissions` in manifest):

| Host | Why |
|---|---|
| `http://127.0.0.1/*`, `http://localhost/*` | Communicate with local pzi server. Required for every capture/attach call. |
| `https://ieeexplore.ieee.org/*` | IEEE Xplore article pages and PDF stamp gateways. IEEE serves PDFs on the same domain; the extension needs to inject content scripts to fetch them with the user's institutional session. |
| `https://dl.acm.org/*` | ACM Digital Library PDF gateways (`/doi/pdf/`). Same-origin PDF fetch requires content script access. |
| `https://www.sciencedirect.com/*`, `https://pdf.sciencedirectassets.com/*` | ScienceDirect articles may redirect PDFs to a CDN subdomain; both are needed for authenticated PDF capture. |
| `https://onlinelibrary.wiley.com/*` | Wiley ePDF/PDF gateways. |
| `https://www.tandfonline.com/*` | Taylor & Francis PDF gateways. |
| `https://journals.sagepub.com/*` | SAGE PDF gateways. |
| `https://academic.oup.com/*` | Oxford Academic PDF gateways. |
| `https://www.nature.com/*` | Nature publisher PDF gateways. |
| `https://www.ncbi.nlm.nih.gov/*`, `https://pmc.ncbi.nlm.nih.gov/*` | PubMed Central full-text and article pages. |

**Optional host permissions** (`optional_host_permissions`):

- `https://*/*`, `http://*/*` — requested in two situations, and in both the
  grant is temporary:
  - **Cross-origin PDF candidate** (e.g. article on `publisher.com` with the PDF
    on `cdn.publisher.com`). The extension asks for a narrow host permission
    (`https://cdn.publisher.com/*`) after the user clicks capture, uses it for
    that fetch, and removes it afterwards. Candidate origins are granted before
    acquisition begins, so a run that succeeds on its first candidate still
    releases the origins it never reached.
  - **The active tab's own origin**, once per non-dry-run capture, so the
    capture can re-fetch the page with the user's session. Released as soon as
    that capture finishes, including when it fails or throws.

A permission the user had already granted for an origin is never revoked by
this: only grants the capture itself obtained are handed back. The practical
guarantee is that the permission set the browser lists for paperazzi does not
grow as the extension is used.

If the user denies the optional permission prompt, metadata capture still
completes. Only the cross-origin PDF fetch is skipped.

### Why not fewer permissions?

- The publisher host list exists because many major publishers serve PDFs on
  the same domain as the article page. Without those hosts in the manifest,
  the extension cannot inject content scripts to fetch authenticated PDFs.
- `cookies` is needed for the translation-server to resolve metadata on
  paywalled pages. Without it, metadata capture for institutional-access
  papers would fail.
- `webRequest` enables the PDF observer cache, which catches PDF responses
  triggered by JavaScript redirects or dynamic page loads — important for
  modern publisher sites.

## What data leaves the browser

| Data | Sent to | Purpose | Encrypted? |
|---|---|---|---|
| Page URL | local pzi server | Metadata lookup | Loopback (no network) |
| Page HTML `<head>` | local pzi server | Fallback metadata extraction | Loopback |
| Page cookies (active domain) | local pzi server, then translation-server (local), **then the publisher that set them** | Authenticated metadata resolution | Loopback, then that publisher |
| PDF candidates (URL list) | local pzi server | PDF discovery | Loopback |
| PDF bytes (captured) | local pzi server | Save to `papers/` directory | Loopback |

**PDFs and page HTML never leave the local machine**, and the external
metadata APIs (Crossref, OpenAlex, Semantic Scholar, Unpaywall, DOAJ, Europe
PMC) receive only DOIs, titles, or author names — never PDFs, cookies, or HTML.

**Cookies are the exception, and they are the point.** The cookie header for the
active tab's domain is forwarded to the local translation-server, which
**replays it to that same publisher** when it fetches the page for metadata.
That is what the cookie bridge is for: without it an authenticated or paywalled
page resolves as a login wall. The cookies go to the site that issued them and
to nowhere else — not to any other publisher, and not to any metadata API.

## API security (pzi server)

| Control | Default | How to strengthen |
|---|---|---|
| Bind address | `127.0.0.1` only | Keep loopback. `0.0.0.0` is **refused outright** — the Host check that guards against DNS rebinding has no bind address to match, so every request would be rejected anyway. Bind a specific LAN address if you must share, and only over a trusted network. |
| Auth token | Generated by `pzi init` into `<data-home>/api_token` (0600) and auto-read at runtime | Kept. To source it from a password manager instead, set `api_auth_token_cmd`. Copy the token into the extension popup. |
| Origin check | Allows `chrome-extension://`, `moz-extension://`, `http://localhost`, `http://127.0.0.1` | Restrict via the `api_allowed_origins` config list. An explicit empty list now means *no* cross-origin request is allowed; omit the key to keep the defaults. |
| Host check (DNS rebinding) | Loopback bind accepts only a loopback `Host` header; an explicit non-loopback `api_listen_host` accepts only that host | Kept — derived from `api_listen_host`, no separate config key. Blocks a page that points its own domain's DNS at 127.0.0.1 and sends a plain GET (no Origin header) with `Host: <attacker-domain>`. |
| Body size cap | 64 MiB (`api_max_body_bytes`) | Lower if you only capture papers (most PDFs are <20 MiB). |
| Rate limiting | **None** | Removed. It was keyed on the peer address — so on loopback every local process shared one bucket — and ran *after* the auth gate, so it never metered a failed token. It slowed your own tools down and did not slow an attacker. The API token is the control. |
| Attach session tokens | Random 32-byte URL-safe token, TTL 10 minutes, one-shot consume | Tokens generated per capture request, validated on raw PDF upload. |
| Content-Length validation | Bodies over `api_max_body_bytes` rejected before reading | Kept. |
| Recursive DNS safety | `safe_public_http_url` resolves hostnames with 250ms budget, rejects private/local IPs | Kept. One scoped exception: a configured `ezproxy_host` (see below). |
| Local capture paths | `/capture` accepts a local filesystem path only if it resolves inside `capture_source_dirs`; that list is **empty by default**, so local-file capture is refused over HTTP until you opt in | Leave unset unless you script local ingests. Paths are resolved (symlinks and `..` collapsed) before the containment test. |
| Inbox draining | `POST /inbox/drain` drains only the configured `inbox_path` and refuses any other file; unset closes the route. Client-supplied `delay` is bounded and defaults to the CLI's value | Leave `inbox_path` unset unless you drive the inbox over HTTP. |

### API token considerations

- The token lives in `<data-home>/api_token` (0600), not in `config.toml`, so
  the config file stays secret-free and committable. `api_auth_token` in the
  config is still supported as a plaintext fallback.
- It is sent over plain HTTP on the loopback interface.
- This is safe when `api_listen_host` is `127.0.0.1` (default).
- Binding to a LAN address exposes the token to the local network. `0.0.0.0`
  specifically is refused at startup (see the bind-address row above), so this
  is about a deliberate `api_listen_host = "192.168.x.y"`, not the wildcard.
- **Recommendation**: always use loopback bind + the auto-provisioned
  `<data-home>/api_token` together. `api_auth_token` in `config.toml` is a
  plaintext fallback, not the recommended form.

### What an attacker on the local machine could do

If an attacker has code execution on the same machine:

- They can read `config.toml` (same as any local config file).
- They can read `.bib` and `papers/` files (same as any local file).
- They can call the loopback API if no auth token is set.
- They cannot access browser cookies or extension storage without browser-level compromise.

paperazzi's threat model assumes the local machine is trusted. If the machine is
compromised, the attacker can access any local file regardless of paperazzi.

**One qualification.** That reasoning covers a local *actor*, but two routes used
to let a request name a local path directly: `/capture` would read and copy any
readable PDF into `papers_dir` (from where `GET /pdf/<citekey>` serves it), and
`/inbox/drain` would rewrite any writable file in place. Those turn a
loopback-reachable request — including one originating from a web page through
the extension — into arbitrary local file access, which is a different boundary
from "the user at a shell". Both are now confined to explicitly configured
directories and are closed by default.

## Browser extension security

### Cross-origin PDF fetch flow

1. User clicks "Capture current page" on a publisher article.
2. Extension extracts metadata + PDF candidates from the page.
3. Extension sends `/capture` to local pzi server.
4. Pzi server returns metadata result + optional `pdf_request` plan.
5. For same-origin candidates: extension fetches with `credentials: "include"`.
6. For cross-origin candidates: extension requests optional host permission via
   browser prompt. User must approve.
7. Extension fetches PDF bytes, validates they start with `%PDF-`.
8. Extension uploads bytes to `/attach-pdf-raw` with attach-token.
9. Pzi server validates token, citekey, size, source URL, then saves PDF.
10. Extension removes temporary host permission.

`/attach-pdf-raw` **requires** a `request_id` naming a live attach session, so
the checks in step 9 cannot be skipped by omitting it.

`/attach-pdf-bytes` (the JSON fallback, used when the capture produced no attach
plan) accepts an attach session and enforces the same checks when one is given,
but also accepts a sessionless upload. That upload is governed by the API-token
auth gate alone — it is not covered by the TTL, size or source-URL checks above.
Treat the API token as the boundary for it.

### Cookie handling

- Cookies are read **only** for the active tab's domain.
- The cookie header is sent in the `/capture` JSON body to the local pzi
  server over loopback. pzi forwards it to the local translation-server, which
  **sends it onward to the publisher whose domain it came from** — that outbound
  replay is what lets an authenticated page resolve at all. pzi installs this
  bridge itself, by patching translation-server's `webSession.js` /
  `webEndpoint.js` on install (`ts_backend._apply_cookie_patch`).
- Cookies go only to the domain that issued them. They are **never** sent to
  another publisher, and **never** to a metadata API (Crossref, OpenAlex,
  Semantic Scholar, Unpaywall, DOAJ, Europe PMC).
- Debug/status payloads redact cookie values before display or logging.
- Bulk capture from a search-results page sends **no** cookies: those results are
  other people's domains, and the user is not on them.

### Content security

- Popup uses `chrome.storage.session` (preferred; cleared when browser closes)
  with `chrome.storage.local` fallback.
- Auth token stored in extension storage, sent as `X-Pzi-Token` header.
- PDF bytes are validated (`%PDF-` magic) before upload.
- Attach sessions have TTL (10 min), max byte limit, and allowlisted source URLs.
- Extension version marker in every capture body for debugging.

## Recommendations

1. **Always use loopback** (`api_listen_host = "127.0.0.1"`). `0.0.0.0` is
   refused at startup; a specific LAN address is accepted but puts the whole
   API — including `POST /browser/download` and `POST /delete` — behind one
   bearer token on an untrusted network.
2. **Keep the auth token** (`pzi init` writes `<data-home>/api_token`) for
   defense-in-depth even on loopback. Prefer `api_auth_token_cmd` over the
   plaintext `api_auth_token` if you use a password manager.
3. **Keep extension up to date** — reload after `pzi` updates.
4. **Review publisher host permissions** periodically — remove hosts you don't
   use.
5. **Prefer `_cmd` secret variants** (`contact_email_cmd`,
   `unpaywall_email_cmd`, `semantic_scholar_api_key_cmd`) so secrets are never
   written to `config.toml`.
6. **Deny the optional host permission** if you don't need cross-origin PDF
   capture — metadata capture still works.

## Known limitations

- Metadata APIs (Crossref, OpenAlex, S2) see your IP address. If you use a
  VPN, those services see the VPN exit IP.
- The Semantic Scholar API key is sent **only** to `api.semanticscholar.org`.
  (Before 0.1.0b5 the shared metadata fetcher attached it to every provider, so
  Crossref, OpenAlex, DBLP and OpenReview also received it.)
- FlareSolverr (optional, opt-in) routes publisher page requests through a
  third-party service. paperazzi warns when it is configured.
- Translation-server (Zotero) makes outbound HTTP requests to publisher sites
  for metadata resolution. These carry your machine's IP address **and, for the
  domain you captured from, the cookies your browser holds for it** — pzi
  patches translation-server on install to forward them, so an authenticated
  page resolves as your session sees it rather than as a login wall. A
  publisher therefore sees a request attributable to your account. No other
  domain receives them.
- The `browser_pdf_cmd` (Playwright headless hook) opens a headless browser
  that carries your browser profile. It is a local process but may make
  outbound requests to publisher sites. Your institutional access applies if
  you point it at your real browser profile.
- **EZProxy SSRF exception.** paperazzi normally pins each connection to a public IP
  and re-validates every redirect, rejecting private/loopback/link-local
  targets (SSRF defense). When you configure `ezproxy_host`, PDF fetches
  rewritten through *that exact host* are permitted to resolve to a
  private/campus IP, since institutional proxies often live on internal
  networks. This is the only place the guard is relaxed, it is opt-in, and it
  applies solely to the explicitly-configured proxy host — every other URL and
  redirect target keeps full private-IP rejection.
