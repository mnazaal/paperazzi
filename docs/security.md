# pzi Security Model

pzi is local-first. Every data path keeps user content local, rejects
non-local/private network traffic, and exposes the absolute minimum surface
needed for a browser-extension-assisted paper capture workflow.

## Architecture overview

```text
 [Browser (extension)]                        [pzi CLI / server]
        |                                           |
        |  POST /capture (JSON)                     |  pzi add <doi/url/pdf>
        |  POST /attach-pdf-bytes (JSON)             |
        |  POST /attach-pdf-raw (binary + token)     |
        |                                           |
        +-----  http://127.0.0.1:8765  -------------+
                          |
                  [translation-server :1969]  (internal, no external exposure)
```

- pzi server binds `127.0.0.1` by default.
- All HTTP traffic stays on the loopback interface.
- Translation-server is a local process; pzi auto-starts and auto-stops it.
- PDF bytes and BibTeX metadata never leave the local machine through pzi.

## Extension permissions — why each exists

The browser extension is the most privileged component. Every permission is
explained below.

| Permission | Why needed | What data it accesses |
|---|---|---|
| `activeTab` | Extract page HTML, metadata, PDF candidate URLs, and observed PDF responses from the tab the user clicked on. Access ends when the tab is closed. | Current tab URL, HTML `<head>`, `<meta>` tags, visible PDF links. |
| `storage` | Persist API endpoint, auth token, recent captures, and user preferences (bib selection, dry-run toggle) across sessions. | Small JSON values stored in browser local/session storage. |
| `scripting` | Inject content scripts to extract embedded metadata (JSON-LD, OpenGraph, highwire tags) and to execute page-context PDF fetches that carry the real browser session. | HTML source, page variables. |
| `cookies` | Forward cookies from the active tab domain to the translation-server so it can resolve authenticated metadata (e.g., paywalled publisher pages that your institution provides). Used for metadata lookup, not for arbitrary sites. | Cookie header for the active tab's domain only. |
| `contextMenus` | Register "Save to pzi" right-click menu on links so users can capture a paper URL without navigating to it. | Target link URL (no page access). |
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

- `https://*/*`, `http://*/*` — requested only when a cross-origin PDF candidate is
  discovered (e.g., article on `publisher.com` with PDF on `cdn.publisher.com`).
  The extension asks for a narrow host permission (`https://cdn.publisher.com/*`)
  after the user clicks capture. This permission is used only for that fetch
  attempt and is removed immediately afterwards.

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
| Page cookies (active domain) | local pzi server, then translation-server (local) | Authenticated metadata resolution | Loopback |
| PDF candidates (URL list) | local pzi server | PDF discovery | Loopback |
| PDF bytes (captured) | local pzi server | Save to `papers/` directory | Loopback |

**Nothing leaves the local machine.** pzi never sends PDFs, HTML, or cookies
to external services. External metadata APIs (Crossref, OpenAlex, Semantic
Scholar, Unpaywall, DOAJ, Europe PMC) receive only DOIs, titles, or author
names — never PDFs, cookies, or HTML.

## API security (pzi server)

| Control | Default | How to strengthen |
|---|---|---|
| Bind address | `127.0.0.1` only | Keep loopback. Never use `0.0.0.0` without TLS. |
| Auth token | None (optional) | Set `api_auth_token` in config, copy to extension popup. |
| Origin check | Allows `chrome-extension://`, `moz-extension://`, `http://localhost`, `http://127.0.0.1` | Restrict to specific origins via `api_allowed_origins` config list. |
| Body size cap | 64 MiB (`api_max_body_bytes`) | Lower if you only capture papers (most PDFs are <20 MiB). |
| Rate limiting | 60 req/min per client IP | Adjust `rate_limit_rpm` in config. |
| Attach session tokens | Random 32-byte URL-safe token, TTL 5 minutes, one-shot consume | Tokens generated per capture request, validated on raw PDF upload. |
| Content-Length validation | Bodies over `api_max_body_bytes` rejected before reading | Kept. |
| Recursive DNS safety | `safe_public_http_url` resolves hostnames with 250ms budget, rejects private/local IPs | Kept. One scoped exception: a configured `ezproxy_host` (see below). |

### API token considerations

- The token is stored in plain text in `config.toml`.
- It is sent over plain HTTP on the loopback interface.
- This is safe when `api_listen_host` is `127.0.0.1` (default).
- Binding to `0.0.0.0` exposes the token to the local network.
- **Recommendation**: always use loopback bind + `api_auth_token` together.

### What an attacker on the local machine could do

If an attacker has code execution on the same machine:

- They can read `config.toml` (same as any local config file).
- They can read `.bib` and `papers/` files (same as any local file).
- They can call the loopback API if no auth token is set.
- They cannot access browser cookies or extension storage without browser-level compromise.

pzi's threat model assumes the local machine is trusted. If the machine is
compromised, the attacker can access any local file regardless of pzi.

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

### Cookie handling

- Cookies are read **only** for the active tab's domain.
- The cookie header is sent in the `/capture` JSON body to the local pzi
  server over loopback. pzi forwards it only to the local translation-server
  for metadata resolution.
- Debug/status payloads redact cookie values before display or logging.
- Cookies are **never** sent to external metadata APIs.

### Content security

- Popup uses `chrome.storage.session` (preferred; cleared when browser closes)
  with `chrome.storage.local` fallback.
- Auth token stored in extension storage, sent as `X-Pzi-Token` header.
- PDF bytes are validated (`%PDF-` magic) before upload.
- Attach sessions have TTL (5 min), max byte limit, and allowlisted source URLs.
- Extension version marker in every capture body for debugging.

## Recommendations

1. **Always use loopback** (`api_listen_host = "127.0.0.1"`). Never bind to
   `0.0.0.0` without transport encryption.
2. **Set `api_auth_token`** for defense-in-depth even on loopback.
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
- FlareSolverr (optional, opt-in) routes publisher page requests through a
  third-party service. pzi warns when it is configured.
- Translation-server (Zotero) may make outbound HTTP requests to publisher
  sites for metadata resolution. These requests carry your machine's IP
  address, not your browser cookies.
- The `browser_pdf_cmd` (Playwright headless hook) opens a headless browser
  that carries your browser profile. It is a local process but may make
  outbound requests to publisher sites. Your institutional access applies if
  you point it at your real browser profile.
- **EZProxy SSRF exception.** pzi normally pins each connection to a public IP
  and re-validates every redirect, rejecting private/loopback/link-local
  targets (SSRF defense). When you configure `ezproxy_host`, PDF fetches
  rewritten through *that exact host* are permitted to resolve to a
  private/campus IP, since institutional proxies often live on internal
  networks. This is the only place the guard is relaxed, it is opt-in, and it
  applies solely to the explicitly-configured proxy host — every other URL and
  redirect target keeps full private-IP rejection.
