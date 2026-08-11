# paperazzi browser extension

Minimal Manifest V3 extension that sends the current tab URL to the local
`pzi server` HTTP API. When paperazzi can capture metadata but direct CLI-style
PDF download is blocked, the extension can fetch visible PDF candidates with the
active browser session (`credentials: "include"`) and upload the PDF bytes to
the local `/attach-pdf-bytes` endpoint.

Install as an unpacked/temporary extension. On first install, an onboarding page
opens to help set up the API token and test the connection. Once configured, you
can capture by clicking the toolbar icon or by right-clicking any link → **Save
to paperazzi**.

## Build

Browser-specific manifests are generated from `manifest.base.json`:

```sh
python tools/build_extension.py
```

Outputs:
- `dist/firefox/` — unpacked extension for Firefox
- `dist/chrome/` — unpacked extension for Chrome
- `dist/paperazzi-capture-firefox.zip` — packaged for Firefox store
- `dist/paperazzi-capture-chrome.zip` — packaged for Chrome store

## Install (Firefox)

1. Build: `python tools/build_extension.py`
2. Start the service: `pzi server`
3. Go to `about:debugging` → This Firefox → Load Temporary Add-on
4. Select `dist/firefox/manifest.json`
5. The onboarding page opens automatically — set your API token and test the connection
6. Click the extension action, optionally set tags/bib/dry-run, and click **Capture current page**
7. You can also right-click any link on any page → **Save to paperazzi**

## Install (Chrome)

1. Build: `python tools/build_extension.py`
2. Start the service: `pzi server`
3. Open `chrome://extensions`, enable developer mode, click "Load unpacked"
4. Select `dist/chrome/`
5. The onboarding page opens automatically — set your API token and test the connection
6. Click the extension action and click **Capture current page**
7. You can also right-click any link on any page → **Save to paperazzi**

## Permissions

The extension requests several permissions to enable browser-session PDF capture
and authenticated metadata lookup. Every permission is explained in
[docs/security.md](../docs/security.md#extension-permissions--why-each-exists).

Quick summary:

- **`activeTab`, `scripting`** — extract page metadata and fetch PDFs with your
  browser session. Only for the tab you click the extension on.
- **`cookies`** — forward the active tab's cookies to the local pzi server,
  which passes them to the local translation-server. That server **replays them
  to the publisher whose domain issued them** when it fetches the page: without
  that, an authenticated or paywalled page resolves as a login wall. They go to
  that domain and nowhere else — never to another site, never to a metadata API.
  Capturing a whole page of search results sends no cookies at all, since those
  results are other people's domains.
- **`webRequest`** — observe PDF responses from publisher sites so the extension
  can discover PDF URLs that appear via JavaScript redirects.
- **`contextMenus`** — add "Save to paperazzi" to the right-click menu on links.
- **`storage`** — save your API token, bib preference, and recent captures.
- **Publisher host permissions** — granted at *install* time, not on demand, so
  the browser lists them up front. Eleven publisher origins (IEEE Xplore, ACM
  DL, ScienceDirect and its CDN, Wiley, Taylor & Francis, SAGE, Oxford
  Academic, Nature, NCBI and PMC), plus `http://127.0.0.1/*` and
  `http://localhost/*` for the local pzi server — thirteen in total. On the
  publisher origins they let the extension fetch PDFs with your session; the
  two local ones are how it reaches your own pzi server. Content-script
  injection is separate: that comes from the `scripting` permission and the
  active tab, not from these.
- **Optional host permission** — requested at capture time when a PDF candidate
  lives on an origin not in that list. Used for that one fetch and released
  afterwards, on every path including failure. Denying it still captures the
  metadata. Candidate origins are granted *before* acquisition starts, so ones
  the run never reaches — because an earlier candidate already produced the PDF
  — are released too, not just the one that worked.
- **Active-tab origin permission** — requested once per non-dry-run capture, for
  the origin of the page you are on, and released as soon as that capture
  finishes. This is what lets the capture re-fetch the page with your session.
  A permission you had already granted for that origin is left alone, since it
  is yours rather than the capture's to hand back.

Nothing requested on demand is kept: a capture borrows the host permissions it
needs and returns them, so the set the browser lists for paperazzi does not grow
as you use it. Earlier versions kept the active-tab grant permanently, which
also widened what the always-on `webRequest` observer could see.

**What leaves your machine.** PDF bytes and page HTML do not: they go to the
local pzi server over loopback and stop there. Two things do leave, both to the
site you are capturing from and to nowhere else:

- the **cookies** for that domain, replayed by the local translation-server as
  described above;
- the page's **`<head>` HTML**, uploaded to the local server as `head_html` for
  fallback metadata extraction — local only, but worth knowing it is collected.

External metadata APIs (Crossref, OpenAlex, Semantic Scholar, Unpaywall, DOAJ,
Europe PMC) receive only DOIs, titles and author names.

## Configuration

- The capture endpoint defaults to `http://127.0.0.1:8765/capture`.
- The popup fetches available bibs from `GET /bibs` and populates the bib dropdown automatically.
- Browser-session PDF attach is limited to same-origin PDF candidates from the active tab, so authenticated cross-site responses are not fetched broadly. (This bullet used to open with "the extension requests only local pzi host access by default", which contradicted both the permissions list above and `manifest.base.json`: thirteen host permissions are granted at install time.)
- For cross-origin PDF candidates (for example an article on `publisher.com` with a PDF on `cdn.publisher.com`), the extension can ask for a narrow optional host permission after you click capture. paperazzi tries same-origin candidates first, then requests access only for the candidate PDF origin, fetches with browser cookies, uploads validated PDF bytes locally, and removes the temporary permission after the attempt.
- Capture results expose `pdf_status`; `direct_blocked` means metadata was saved but the PDF needs browser capture or `browser_pdf_cmd`.
- Advanced/devtools-only: to change the endpoint persistently, open the extension popup devtools and set a value:
  `chrome.storage.local.set({ endpoint: "http://127.0.0.1:9000/capture" })`.
  Runtime/session values override local values until the browser clears session storage.

## Smoke test

Use this before calling the extension usable.

### 1. Start from a fresh test library

```sh
tmpdir=$(mktemp -d)
# `pzi init` reuses the API token already on disk, so this does not un-pair your
# extension. (It used to mint a fresh one on every run — following this very
# smoke test broke your own setup. Pass `--rotate-token` only when you mean it.)
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
2. Click paperazzi extension.
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
- `invalid API token`: copy the token from `<pzi-data-home>/api_token` (written by `pzi init`, mode 0600) into the popup. It is not in `config.toml` — that is what makes the config safe to commit.
- Empty bib dropdown: ensure `pzi server --stop-after 30` is running and `/bibs` is reachable.

## Zotero-like parity checklist

Use this as the extension/backend contract when changing capture behavior.

- Active browser session: capture uses the current tab URL, page cookies, and browser `credentials: "include"` PDF fetches before falling back to backend-only PDF download.
- embedded metadata: extension sends citation meta tags, JSON-LD, OpenGraph title, DOI, canonical/source/abstract URLs, and page-discovered PDF candidates.
- translation-server metadata: backend still resolves with translation-server and public provider fallbacks, then selects the best scored candidate rather than blindly accepting the first result.
- metadata diagnostics: extension requests verbose capture payloads and popup summary can show metadata warnings plus selected/rejected candidate diagnostics.
- lossless BibTeX: backend append/update/tag/delete paths preserve comments, `@string` macros, unrelated entries, and existing source formatting where patchable.
- PDF recovery: if direct fetch fails, Open the actual PDF tab and click paperazzi again; popup/raw response should make this next step visible.
- Cross-origin PDF: extension requests narrow optional host permission only for the candidate PDF origin and removes temporary permission after the attempt.
