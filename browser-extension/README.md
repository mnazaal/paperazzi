# pzi browser extension

Minimal Manifest V3 extension that sends the current tab URL to the local
`pzi server` HTTP API. When pzi can capture metadata but direct CLI-style
PDF download is blocked, the extension can fetch visible PDF candidates with the
active browser session (`credentials: "include"`) and upload the PDF bytes to
the local `/attach-pdf-bytes` endpoint.

Install as an unpacked/temporary extension. On first install, an onboarding page
opens to help set up the API token and test the connection. Once configured, you
can capture by clicking the toolbar icon or by right-clicking any link → **Save
to pzi**.

## Build

Browser-specific manifests are generated from `manifest.base.json`:

```sh
python tools/build_extension.py
```

Outputs:
- `dist/firefox/` — unpacked extension for Firefox
- `dist/chrome/` — unpacked extension for Chrome
- `dist/pzi-capture-firefox.zip` — packaged for Firefox store
- `dist/pzi-capture-chrome.zip` — packaged for Chrome store

## Install (Firefox)

1. Build: `python tools/build_extension.py`
2. Start the service: `pzi server`
3. Go to `about:debugging` → This Firefox → Load Temporary Add-on
4. Select `dist/firefox/manifest.json`
5. The onboarding page opens automatically — set your API token and test the connection
6. Click the extension action, optionally set tags/bib/dry-run, and click **Capture current page**
7. You can also right-click any link on any page → **Save to pzi**

## Install (Chrome)

1. Build: `python tools/build_extension.py`
2. Start the service: `pzi server`
3. Open `chrome://extensions`, enable developer mode, click "Load unpacked"
4. Select `dist/chrome/`
5. The onboarding page opens automatically — set your API token and test the connection
6. Click the extension action and click **Capture current page**
7. You can also right-click any link on any page → **Save to pzi**

## Permissions

The extension requests several permissions to enable browser-session PDF capture
and authenticated metadata lookup. Every permission is explained in
[docs/security.md](../docs/security.md#extension-permissions--why-each-exists).

Quick summary:

- **`activeTab`, `scripting`** — extract page metadata and fetch PDFs with your
  browser session. Only for the tab you click the extension on.
- **`cookies`** — forward the active tab's cookies to the local
  translation-server for authenticated metadata resolution. Cookies never leave
  your machine.
- **`webRequest`** — observe PDF responses from publisher sites so the extension
  can discover PDF URLs that appear via JavaScript redirects.
- **`contextMenus`** — add "Save to pzi" to the right-click menu on links.
- **`storage`** — save your API token, bib preference, and recent captures.
- **Publisher host permissions** — needed so the extension can inject content
  scripts on major publisher sites for authenticated PDF fetch.
- **Optional `https://*/*`** — requested only when a cross-origin PDF candidate
  is found. Used for that one fetch, then removed. Denying it still captures
  metadata.

No PDF data, HTML, or cookies ever leave your machine through pzi.

## Configuration

- The capture endpoint defaults to `http://127.0.0.1:8765/capture`.
- The popup fetches available bibs from `GET /bibs` and populates the bib dropdown automatically.
- The extension requests only local pzi host access by default. Browser-session PDF attach is limited to same-origin PDF candidates from the active tab so authenticated cross-site responses are not fetched broadly.
- For cross-origin PDF candidates (for example an article on `publisher.com` with a PDF on `cdn.publisher.com`), the extension can ask for a narrow optional host permission after you click capture. pzi tries same-origin candidates first, then requests access only for the candidate PDF origin, fetches with browser cookies, uploads validated PDF bytes locally, and removes the temporary permission after the attempt.
- Capture results expose `pdf_status`; `direct_blocked` means metadata was saved but the PDF needs browser capture or `browser_pdf_cmd`.
- Advanced/devtools-only: to change the endpoint persistently, open the extension popup devtools and set a value:
  `chrome.storage.local.set({ endpoint: "http://127.0.0.1:9000/capture" })`.
  Runtime/session values override local values until the browser clears session storage.

## Smoke test

Use this before calling the extension usable.

### 1. Start from a fresh test library

```sh
tmpdir=$(mktemp -d)
pzi init --setup --bib "$tmpdir/main.bib" --config "$tmpdir/config.toml" --force
pzi server --config "$tmpdir/config.toml" --stop-after 30
```

Keep the server terminal visible. It should listen on `http://127.0.0.1:8765`.

### 2. Build and load the extension

```sh
python tools/build_extension.py
```

- Firefox: `about:debugging` → This Firefox → Load Temporary Add-on → `dist/firefox/manifest.json`
- Chrome: `chrome://extensions` → Developer mode → Load unpacked → `dist/chrome/`

### 3. Capture open-access PDF page

1. Open an arXiv abstract page, for example `https://arxiv.org/abs/2301.07041`.
2. Click pzi extension.
3. Optional: add tags like `smoke,arxiv`.
4. Click **Capture current page**.
5. Expect popup summary with `Added` or `Updated` and `PDF saved`.
6. Check the BibTeX file has an entry with a `file = {...}` field and that the PDF exists in `papers/`.

### 4. Capture opaque PDF URL

1. Open a PDF viewer/download URL that does not end in `.pdf`.
2. Click **Capture current page**.
3. Expect `PDF saved` if the browser displays PDF bytes.
4. If only metadata is saved, open **Show raw response** and inspect `pdf_status`, `pdf_error`, and `pdf_attach`.

### 5. Capture authenticated publisher PDF

1. Sign in through your institution in the browser.
2. Open a publisher article where the PDF candidate is same-origin, or open the actual PDF tab.
3. Click **Capture current page**.
4. Expect metadata saved first, then browser-session PDF attach for same-origin PDF candidates.
5. If the PDF is on a different host, approve the browser's optional host permission prompt for that cross-origin PDF host.
6. Success means popup shows `PDF saved`; this is the Zotero-like path.

### 6. Expected failures

- `PDF not saved: publisher blocked direct download`: metadata is saved; try opening the actual PDF page and capture again.
- `invalid API token`: copy the same `api_auth_token` from pzi config into the popup.
- Empty bib dropdown: ensure `pzi server --stop-after 30` is running and `/bibs` is reachable.

## Zotero-like parity checklist

Use this as the extension/backend contract when changing capture behavior.

- Active browser session: capture uses the current tab URL, page cookies, and browser `credentials: "include"` PDF fetches before falling back to backend-only PDF download.
- embedded metadata: extension sends citation meta tags, JSON-LD, OpenGraph title, DOI, canonical/source/abstract URLs, and page-discovered PDF candidates.
- translation-server metadata: backend still resolves with translation-server and public provider fallbacks, then selects the best scored candidate rather than blindly accepting the first result.
- metadata diagnostics: extension requests verbose capture payloads and popup summary can show metadata warnings plus selected/rejected candidate diagnostics.
- lossless BibTeX: backend append/update/tag/delete paths preserve comments, `@string` macros, unrelated entries, and existing source formatting where patchable.
- PDF recovery: if direct fetch fails, Open the actual PDF tab and click pzi again; popup/raw response should make this next step visible.
- Cross-origin PDF: extension requests narrow optional host permission only for the candidate PDF origin and removes temporary permission after the attempt.
