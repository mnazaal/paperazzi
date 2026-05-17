# pzi

pzi is a local-first paper capture tool that makes a Zotero-style capture workflow easy to use with plain BibTeX. Give it a DOI, URL, or PDF; it writes a BibTeX entry and, when possible, saves the PDF next to your library.

One source of truth: your `.bib` file + a sibling `papers/` dir. No database.

## Quickstart

### Requirements

- Python 3.11+
- `uv` or `pipx` for installation
- Podman if you want `pzi services up` to manage Zotero translation-server
- A repo checkout if you want to build the browser extension

### 1. Install

```sh
uv tool install 'pzi[browser] @ git+https://github.com/mnazaal/pzi.git'
# or:
pipx install 'pzi[browser] @ git+https://github.com/mnazaal/pzi.git'
```

### 2. Create config and start metadata lookup

```sh
pzi init --setup --bib ~/bibs/main.bib
pzi services up
```

This creates `~/.config/pzi/config.toml`, helper service files, and a BibTeX library at `~/bibs/main.bib`.

### 3. Capture a paper from the CLI

```sh
pzi add https://arxiv.org/abs/2301.07041
pzi add 10.1145/1327452.1327492 --tags systems,classic
pzi add ~/Downloads/paper.pdf
```

Expected result:

- BibTeX entries are written to `~/bibs/main.bib`
- PDFs are saved to `~/bibs/papers/` when pzi can find or fetch them

### 4. Optional: capture from the browser

Use this for authenticated publisher pages, pages with PDF links visible only in the browser, or quick one-click capture.

```sh
# keep this running while using the extension
pzi serve

# from the repo checkout
python tools/build_extension.py
```

**Firefox**: go to `about:debugging` → This Firefox → Load Temporary Add-on → select `dist/firefox/manifest.json`

**Chrome**: open `chrome://extensions`, enable developer mode, click "Load unpacked", select `dist/chrome/`

Then open a paper page, click the pzi icon, optionally choose a bib/tags/dry-run, and hit **capture**. The extension sends page hints to the local API. If a PDF is visible to your browser, the extension can fetch it with your browser session cookies and attach it to the BibTeX entry.

### 5. Optional: advanced PDF fallback

Prefer the browser extension or `browser_pdf_cmd` for authenticated access. If you need a Cloudflare fallback, opt into FlareSolverr explicitly:

```sh
pzi init --setup --with-flaresolverr --bib ~/bibs/main.bib --force
pzi services up
```

FlareSolverr may violate publisher terms of service; pzi warns when it is used.

---

## Reference

### Config

`pzi init --setup --bib ~/bibs/main.bib` creates `~/.config/pzi/config.toml` and writes managed service files beside it.

Generated config looks like:

```toml
translation_server_url = "http://127.0.0.1:1969"
api_listen_host = "127.0.0.1"
api_listen_port = 8765
# api_auth_token = "change-me-long-random-token"  # optional but recommended
# api_allowed_origins = ["http://127.0.0.1", "http://localhost", "chrome-extension://", "moz-extension://"]
# api_max_body_bytes = 67108864  # allows browser extension PDF byte uploads
unpaywall_email = "your@email.com"        # optional - enables open-access PDF lookup
# unpaywall_email_cmd = "pass show unpaywall-email"  # or resolve via command

[[bibs]]
name = "ml"
path = "~/bibs/ml.bib"
default = true

[[bibs]]
name = "sys"
path = "~/bibs/sys.bib"
```

`papers_dir` defaults to `<bib-dir>/papers/` when omitted.

`unpaywall_email` enables [Unpaywall](https://unpaywall.org) lookups - the same service Zotero uses for "Find Available PDF". When set, pzi will try to find an open-access PDF by DOI for any paper where the translation server returns no attachment.

### CLI reference

```sh
pzi init [--force]                        # create config from template
pzi init --setup --bib ~/bibs/main.bib    # config + services + browser fallback
pzi init --setup --with-flaresolverr      # explicit Cloudflare fallback opt-in
pzi services up|down|status               # manage local helper services
pzi browser install [chromium|firefox|chrome]  # install Playwright browser binary
pzi add <doi-or-url-or-pdf> [--tags t1,t2] [--bib NAME] [--dry-run]
pzi pdf retry <citekey> [--bib NAME]
pzi pdf attach <citekey> <url-or-path> [--bib NAME]
pzi tag add|remove <citekey> <tag...> [--bib NAME]
pzi tag list [<citekey>] [--bib NAME]
pzi search [--query <text>] [--author <name>] [--year <int>] [--tag <tag>] [--bib NAME]
pzi bib list
pzi bib set-default <name>
pzi bib update <name> [--dry-run]         # conservative metadata enrichment
pzi bib promote <name> [--dry-run] [--keep-preprint]  # preprint→published promotion
pzi doctor                                # config + translation-server check (JSON)
pzi serve [--host H --port P]             # run local HTTP capture API
```

### HTTP API

`pzi serve` exposes:

- `POST /capture` - body: `{url, bib?, tags?, dry_run?, pdf_url_candidates?, page_title?, canonical_url?, source_url?, abstract_url?, doi?}` → insert/update result
- `POST /attach-pdf-bytes` - body: `{citekey, bib?, source_url?, pdf_base64}` → attach browser-fetched PDF bytes
- `GET /bibs` - configured bibs (used by extension popup dropdown)
- `GET /health` - config + translation-server status

Same ingest pipeline as CLI. CORS responses reflect only allowed local/extension origins.

Security defaults are local-first:

- bind to `127.0.0.1` unless you explicitly set another host
- reject non-local/non-extension browser origins
- cap JSON request bodies with `api_max_body_bytes`
- if `api_auth_token` is set, require `X-Pzi-Token: <token>` or `Authorization: Bearer <token>` on all non-`OPTIONS` requests

For browser extension use, set the same token in the popup's **API token** field.

## Architecture

Pure functions at core, side effects at edges.

**Component tree**

```
cli.py / http_api.py              # boundary: argparse + stdlib http.server
add_service.py                    # add/capture orchestration
pdf_discovery.py                  # pure functional PDF URL discovery + candidates
bib_service.py                    # bib list + default selection
tag_service.py                    # tags, tag search, tag normalization
pdf_service.py                    # PDF retry / attach + PDF metadata extraction
doctor_service.py                 # health/config checks
update_service.py                 # metadata enrichment/update
promote_service.py                # preprint → published promotion
translation_server.py             # Zotero translation-server client + HTTP utils
bib_repository.py                 # filesystem + fcntl lock + write planning + merge
similarity.py                     # exact-identity + fuzzy dedup
bibtex.py                         # record ↔ BibTeX mapping + citekey generation
identifiers.py                    # DOI/URL normalization + classification + year extraction
config.py                         # TOML types, validation, loading, serialization
setup_service.py                  # setup command orchestration
service_templates.py              # single-source helper-service templates
metadata_sources.py               # Crossref, OpenAlex, Semantic Scholar, DOAJ, Europe PMC
```

**Ingest pipeline**

`classify → fetch → normalize → exact-match → (similarity hint | enrich) → attach PDF → plan → write`.

**Data flow (add/capture)**

CLI/HTTP/Extension → `add_input_to_bib()` → `classify_input()` → metadata fetch (translation-server / Crossref / OpenAlex / Semantic Scholar) → merge + overrides → `apply_pdf_discovery()` (7-step pure fallback: translation attachment → candidate URLs → web attachments → browser hook → Crossref/Europe PMC/DOAJ → Unpaywall → arXiv) → `find_exact_match()` / `plan_bib_write()` → `bib_repository.execute_write_plan()` → `.bib` file + PDF.

**Config & multi-library**

`config.toml` holds a list of bibs. `resolve_bib()` selects by explicit `--bib` name/path, or single bib, or the one marked `default = true`.

**Design patterns**

- `TypedDict` everywhere - structural typing, zero boilerplate, immutability by convention.
- Explicit dependency injection - service functions accept `fetch_web`, `fetch_search`, etc. as keyword args with real defaults; tests pass lambdas.
- Pure logic, thin I/O - `pdf_discovery.py` and `bib_repository.py` have no side effects. Network/filesystem lives in `translation_server.py`, `bib_repository.py`, `cli.py`.
- Composable pipeline - `PdfDiscoveryStep = (record, context) → record`. Steps are independently testable and reorderable.

**Design decisions**

| Decision | Why |
|----------|-----|
| `TypedDict` over `@dataclass` | Structural typing, zero boilerplate, easy serialization |
| Multi-bib config | One `config.toml` supports multiple research projects |
| PDF discovery as pure pipeline | Testable, composable, DRY fallback chain |
| Explicit DI defaults | No mocking framework; tests pass lambdas |
| No ORM / no DB | BibTeX is source of truth; grep-able, local-first |
| Translation-server first | Leverages Zotero's 1000+ site translators |
| Browser hook as fallback | Reuses user's authenticated sessions |

**Extension points**

- New metadata source: create `src/pzi/<source>.py`, implement `fetch_<source>_record()`, wire into `add_service._fetch_record_for_input()`.
- New PDF backend: add a step to `pdf_discovery.py`, add to `DEFAULT_DISCOVERY_STEPS`.
- New CLI command: add subparser in `cli.py`, create `src/pzi/<feature>_service.py`, wire in `main()`.
- New HTTP endpoint: add route in `http_api.py.PziHandler`, reuse a service function.
- New extension feature: update `manifest.base.json`, add to `background.js` / `popup.js`, rerun `tools/build_extension.py`.

**Headless-browser fallback**

`pzi init --setup` configures the packaged hook:

```toml
browser_pdf_cmd = "pzi-browser-hook --browser chromium"
```

The hook uses Playwright headless Chromium to load the landing page, inspect PDF-like links and meta tags, click obvious "PDF" / "Download PDF" controls, and return the discovered PDF URL to pzi. Browser binaries install automatically on first use, or manually via `playwright install chromium`.

## Compatibility

Expected coverage for common sources. ✅ = works out-of-box. ⚠️ = needs config (browser profile / FlareSolverr / extension). ❌ = metadata only.

| Source | Type | Meta | PDF |
|--------|------|------|-----|
| arXiv, bioRxiv, medRxiv | Preprint | ✅ | ✅ |
| PLOS ONE, eLife, Frontiers, MDPI, JMLR | OA journal | ✅ | ✅ |
| ACL Anthology, OpenReview | Proceedings | ✅ | ✅ |
| NeurIPS, ICML, AAAI | Proceedings | ✅ | ⚠️ |
| ACM DL, IEEE Xplore, Elsevier, Springer, Wiley | Paywall | ✅ | ⚠️ |
| Google Scholar, DBLP, Crossref, OpenAlex | Aggregator | ✅ | ❌ |
| PubMed Central, Europe PMC, DOAJ | Repository | ✅ | ✅/⚠️ |

**Required config**

- `unpaywall_email` - free sign-up at [unpaywall.org](https://unpaywall.org/products/api), enables OA PDF lookup by DOI
- `semantic_scholar_api_key` - optional, free at [api.semanticscholar.org](https://api.semanticscholar.org/), higher rate limits

For secrets in a password manager, use `_cmd` variants (`unpaywall_email_cmd`, `semantic_scholar_api_key_cmd`) so keys are never written to disk.

## PDF download for paywalled papers

Many publisher sites (ACM, IEEE, Springer) require authentication. pzi supports:

### Option 1: Browser profile (recommended - legal)

Reuse your browser's authenticated session (institutional login, subscriptions):

Find your browser profile path:
- **Chromium** (default): `~/.config/chromium`
- **Chrome**: `~/.config/google-chrome/Default`
- **Firefox**: `~/.mozilla/firefox/xxx.default-release` (add `--browser firefox`)

Then in config:
```toml
# Chromium (default)
browser_pdf_cmd = "pzi-browser-hook --profile ~/.config/chromium"

# Firefox (add --browser flag)
browser_pdf_cmd = "pzi-browser-hook --profile ~/.mozilla/firefox/xxx.default-release --browser firefox"
```


### Option 2: FlareSolverr (gray area)

Bypasses Cloudflare protection. May violate publisher terms of service.

```sh
pzi init --setup --with-flaresolverr --bib ~/bibs/main.bib --force
pzi services up
```

```toml
flaresolverr_url = "http://127.0.0.1:8191"
```

⚠️ **Warning**: When FlareSolverr is used, pzi will emit a warning suggesting you switch to browser profile authentication.

### How it works

PDF download tries in order:
1. **Direct download** - fastest, works for open-access papers
2. **Browser profile** - uses your authenticated session (legal)
3. **FlareSolverr** - bypasses Cloudflare (gray area, warns user)

## Troubleshooting capture flow

Use this map to see which layer is failing.

### 1. Extension cannot talk to pzi

Symptoms:

- popup shows fetch/network error
- no response from capture button

Check:

```sh
pzi serve
curl http://127.0.0.1:8765/health
# with api_auth_token:
curl -H 'X-Pzi-Token: <token>' http://127.0.0.1:8765/health
```

If this fails, `pzi serve` is not running or is on a different host/port.

### 2. Metadata lookup fails before entry creation

Symptoms:

- popup/CLI shows `translation server error`
- no `citekey` returned
- no new BibTeX entry

Check:

```sh
podman run -p 1969:1969 translation-server
pzi doctor
```

Notes:

- browser capture now sends page title / DOI / canonical URL as fallback metadata
- if DOI lookup fails but browser metadata is sufficient, pzi should still insert an entry
- if it still fails, inspect the exact popup JSON and page URL

### 3. Entry created, but no PDF attached

Symptoms:

- `citekey` returned
- BibTeX entry exists
- no `file = {...}` field

Check BibTeX entry:

```sh
grep -n "<citekey>" -A 15 ~/bibs/main.bib
```

Likely causes:

- translator returned no PDF attachment
- browser found no PDF candidate URL
- PDF candidate existed but browser fetch failed
- headless browser hook found nothing

### 4. Browser has access, but backend direct fetch is blocked

Symptoms:

- page works in browser
- CLI/direct backend fetch gets 403

This is normal for many publisher pages. pzi now prefers browser-session PDF fetch for extension flow.

Check that extension was reloaded after updates.

### 5. Browser fetch should work, but PDF still not saved

Symptoms:

- page shows PDF in browser
- capture inserts metadata
- no file saved locally

Check:

- extension reloaded in Firefox
- popup JSON result
- `pzi serve` terminal output
- resulting BibTeX entry
- papers directory contents

```sh
ls -l ~/bibs/papers
```

The browser flow is:

1. extension sends `/capture`
2. pzi creates/updates entry
3. extension fetches PDF bytes in browser session
4. extension sends `/attach-pdf-bytes`
5. pzi stores PDF and patches BibTeX

If step 3 or 4 fails, metadata may succeed while PDF attach does not.

### 6. CLI works for metadata, but not PDF

Symptoms:

- `pzi add ...` inserts entry
- no PDF downloaded

CLI uses public fetch paths plus optional `browser_pdf_cmd`. It does not automatically share your logged-in browser session.

Options:

- configure `browser_pdf_cmd`
- use browser extension flow for authenticated sites
- use manual fallback:

```sh
pzi pdf attach <citekey> <url-or-path>
```

### 7. Fast isolation checklist

Check each layer in order:

1. `pzi serve` running on `:8765`
2. translation-server running on `:1969`
3. extension reloaded
4. browser page actually accessible
5. BibTeX entry created
6. `file = {...}` field written
7. PDF exists in `papers_dir`

If you need to test backend PDF attach path alone:

```sh
python - <<'PY'
import base64, json, urllib.request
pdf = base64.b64encode(b"%PDF-1.4 test").decode("ascii")
body = json.dumps({
    "citekey": "YOUR_CITEKEY",
    "pdf_base64": pdf,
    "source_url": "https://example.com/test.pdf"
}).encode()
req = urllib.request.Request(
    "http://127.0.0.1:8765/attach-pdf-bytes",
    data=body,
    headers={"Content-Type": "application/json"},
    method="POST",
)
print(urllib.request.urlopen(req).read().decode())
PY
```

## Development install

Use dev extras only when hacking on pzi itself:

```sh
git clone https://github.com/mnazaal/pzi
cd pzi
pip install -e ".[dev]"
# or with uv:
uv venv .venv
uv pip install -e ".[dev]"
```

## Tests

```sh
.venv/bin/ruff check src tools
.venv/bin/pyright
.venv/bin/pytest --cov=pzi --cov-report=term-missing -q -k "not browser"
# ~1000 passed, browser tests skipped (need Playwright binaries), coverage gate >=80%
.venv/bin/python -m build
```
