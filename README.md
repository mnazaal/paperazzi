# pzi

pzi is a local-first paper capture tool that makes a Zotero-style capture workflow easy to use with plain BibTeX. Give it a DOI, URL, or PDF; it writes a BibTeX entry and, when possible, saves the PDF next to your library.

**Status: beta.** pzi is conservative and local-first, but public metadata APIs can rate-limit, promotion is best-effort, browser extension install is still manual, and external `.bib` formatting/comments/macros may not be preserved when entries are rewritten. Report issues at [github.com/mnazaal/pzi/issues](https://github.com/mnazaal/pzi/issues).

pzi can manage BibTeX libraries, but it does not require ownership of them. You can use pzi as your main bibliography workflow, or point it directly at existing `.bib` files from Zotero, Paperpile, LaTeX projects, or hand-managed libraries.

One source of truth: your `.bib` file + a sibling `papers/` dir. No database.

## Quickstart

### Requirements

- Python 3.11+
- `uv` or `pipx` for installation
- Podman if you want `pzi services up` to manage Zotero translation-server
- A repo checkout if you want to build the browser extension

### 1. Install

```sh
uv tool install 'pzi @ git+https://github.com/mnazaal/pzi.git'
# or:
pipx install 'pzi @ git+https://github.com/mnazaal/pzi.git'
# or, with plain pip (some systems may need `pip3`):
pip install --user 'pzi @ git+https://github.com/mnazaal/pzi.git'
```

### 2. Create config and start metadata lookup

```sh
pzi init --setup --bib ~/bibs/main.bib
pzi services up
```

This creates `~/.config/pzi/config.toml` and helper service files configured for `~/bibs/main.bib`. The BibTeX file is created or updated when entries are written.

### 3. Capture a paper from the CLI

```sh
pzi add https://arxiv.org/abs/2301.07041
pzi add 10.1145/1327452.1327492 --tags systems,classic
pzi add ~/Downloads/paper.pdf
```

Expected result:

- BibTeX entries are written to `~/bibs/main.bib`
- PDFs are saved to `~/bibs/papers/` when pzi can find and directly fetch them
- If a publisher blocks direct PDF download, pzi keeps the entry, reports `pdf_status = direct_blocked`, and suggests the browser extension or `browser_pdf_cmd`

### 4. Optional: capture from the browser

Use this for authenticated publisher pages, pages with PDF links visible only in the browser, or quick one-click capture.

```sh
# keep this running while using the extension
# auto-starts translation-server, auto-stops after 30 idle minutes
pzi server --stop-after 30

# from the repo checkout
python tools/build_extension.py
```

**Firefox**: go to `about:debugging` → This Firefox → Load Temporary Add-on → select `dist/firefox/manifest.json`

**Chrome**: open `chrome://extensions`, enable developer mode, click "Load unpacked", select `dist/chrome/`

> **Store status:** The pzi browser extension is not yet listed on the Chrome Web Store or Firefox Add-ons. You must install it as an unpacked/temporary extension. Store submission is planned.

Then open a paper page, click the pzi icon, optionally choose a bib/tags/dry-run, and click **Capture current page**. The extension sends page hints to the local API. If a PDF is visible to your browser, the extension can fetch it with your browser session cookies and attach it to the BibTeX entry. This is the recommended path for authenticated publisher pages and browser-only PDF links.

### 5. Optional: advanced PDF fallback

Prefer the browser extension or `browser_pdf_cmd` for authenticated access. CLI capture is best for metadata and open/direct PDFs; browser capture is best for protected PDFs that need cookies, referrer, JavaScript, or your institutional session. If you need a Cloudflare fallback, opt into FlareSolverr explicitly:

```sh
pzi init --setup --with-flaresolverr --bib ~/bibs/main.bib --force
pzi services up
```

FlareSolverr may violate publisher terms of service; pzi warns when it is used.

---

## `pzi services` vs `pzi server` vs `pzi add`

| | `pzi services` | `pzi server` | `pzi add` |
|---|---|---|---|
| Purpose | Manage helper containers | Start pzi HTTP API for browser capture | Single paper capture |
| For | Explicit container control | Browser extension users | CLI one-shot capture |
| Starts translation-server? | Yes (explicitly) | **Yes** (auto-starts if not running) | **Yes** (auto-starts if not running) |
| Underlying tech | `podman compose` | Python `http.server` | Direct service call |
| Command | `pzi services up` / `down` / `status` | `pzi server [--stop-after N]` | `pzi add <value>` |

**`--stop-after N`**: auto-stops the translation-server container after N idle minutes (no captures in that time). Useful for leaving `pzi server` running while browsing without wasting RAM.

**TL;DR:** `pzi server` for browser extension, `pzi add` for CLI. Both auto-start the translation-server when needed. You rarely need `pzi services` directly.

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
# contact_email_cmd = "pass show research-email"  # preferred: identify to public metadata APIs
# contact_email = "your@email.com"                # plaintext fallback if you do not use a password manager
# unpaywall_email = "your@email.com"        # optional - enables open-access PDF lookup
# unpaywall_email_cmd = "pass show unpaywall-email"  # or resolve via command

# Optional naming templates. PDF names use Zotero 7 file-renaming syntax;
# citekeys support Zotero-style templates and common Better BibTeX formulas.
# citekey_format = "auth.lower + shorttitle(3,3) + year"
# pdf_filename_format = "{{ firstCreator suffix=\" - \" }}{{ year suffix=\" - \" }}{{ title truncate=\"100\" }}"

[[bibs]]
name = "ml"
path = "~/bibs/ml.bib"
default = true

[[bibs]]
name = "sys"
path = "~/bibs/sys.bib"
```

`papers_dir` defaults to `<bib-dir>/papers/` when omitted. Set it per bib to share one PDF directory across libraries, or use `pzi init --setup --papers-dir ~/papers`. If the target PDF filename already exists, pzi reuses it when bytes match; otherwise it keeps existing files untouched and writes `name-1.pdf`, `name-2.pdf`, etc. Suffixing applies after any `pdf_filename_format` template is rendered.

`contact_email` identifies you to public metadata APIs. Prefer `contact_email_cmd` if you keep identity/secrets in a password manager; plaintext `contact_email` is also supported. pzi uses this only for metadata APIs (Crossref User-Agent, OpenAlex `mailto`, and Unpaywall fallback), not arbitrary publisher/PDF downloads.

`unpaywall_email` enables [Unpaywall](https://unpaywall.org) lookups - the same service Zotero uses for "Find Available PDF". When set, pzi will try to find an open-access PDF by DOI for any paper where the translation server returns no attachment. If omitted, pzi uses `contact_email` as the Unpaywall email.

Naming templates are optional. `pdf_filename_format` accepts Zotero-style variables such as `{{ firstCreator }}`, `{{ year }}`, and `{{ title truncate="100" }}` with options like `prefix`, `suffix`, `replaceFrom`, `replaceTo`, `regexOpts`, and `case`. `citekey_format` accepts the same `{{ ... }}` style plus common Better BibTeX formulas such as `auth.lower + shorttitle(3,3) + year`.

### CLI reference

```sh
pzi init [--force]                        # create config from template
pzi init --setup --bib ~/bibs/main.bib [--papers-dir ~/papers]  # config + services + browser fallback
pzi init --setup --with-flaresolverr      # explicit Cloudflare fallback opt-in
pzi services up|down|status               # manage local helper services
pzi browser install [chromium|firefox|chrome]  # install Playwright browser binary
pzi add <doi-or-url-or-pdf> [--tags t1,t2] [--dry-run]
pzi add <doi-or-url-or-pdf> --target <bib-or-name> [--tags t1,t2] [--dry-run]
pzi pdf retry <citekey> [--target <bib-or-name>]
pzi pdf attach <citekey> <url-or-path> [--target <bib-or-name>]
pzi tag add|remove <citekey> <tag...> [--target <bib-or-name>]
pzi tag list [<citekey>] [--target <bib-or-name>]
pzi search [--target <bib-or-name> [<bib-or-name>...]] [--query <text>] [--author <name>] [--year <int>] [--tag <tag>]
pzi update [--target <bib-or-name> [<bib-or-name>...]] [--dry-run]       # conservative metadata enrichment; does not promote preprints
pzi promote [--target <bib-or-name> [<bib-or-name>...]] [--dry-run] [--replace]  # preprint→published promotion
pzi list
pzi set-default <name>
pzi doctor                                # config + translation-server check (JSON)
pzi server [--host H --port P] [--stop-after N]  # HTTP API (auto-starts helpers, auto-stops after idle)
```

Without `--target`, commands operate on the configured default library. `--target` may be a configured library name, configured bib path, or direct `.bib` path. Direct `.bib` targets use `<bib-dir>/papers/` for PDFs. Multiple targets are supported only for `search`, `update`, and `promote` with a single flag: `--target a.bib b.bib`.

`update` only fills missing metadata conservatively. To handle preprints when a published version is found, use `promote`. By default `promote` keeps the preprint and creates a linked published entry. Use `--replace` only when you want to update the preprint entry in place.

For external `.bib` files managed by Zotero, Paperpile, LaTeX projects, or hand edits, use `--dry-run` first and keep a backup or Git history. pzi rewrites entries it touches; known source-preservation limitations:

- Malformed BibTeX (unbalanced braces, unterminated strings) is rejected and must be fixed manually
- Entries with non-standard whitespace or unusual field layouts may be re-serialized on update
- Comments and `@preamble` blocks between entries are preserved on insert/update, but not on delete
- BibTeX `@string` macros are kept as-is but not expanded or validated

### External services and rate limits

| Service | Used for | Key/email |
|---|---|---|
| Zotero translation-server | DOI/URL/page metadata | no key; configured by `translation_server_url` |
| Crossref | DOI/title metadata and PDF links | no key; `contact_email` recommended for polite User-Agent |
| OpenAlex | DOI/title metadata and OA URLs | no key; `contact_email` sent as `mailto` |
| Semantic Scholar | fallback metadata/PDF and promotion fallback | optional `semantic_scholar_api_key_cmd` / `semantic_scholar_api_key` for better limits |
| Unpaywall | open-access PDF by DOI | `unpaywall_email_cmd` / `unpaywall_email`, else `contact_email` |
| DOAJ / Europe PMC | open-access PDF lookup | no key |
| Playwright browser hook | JS/authenticated PDF discovery | no API key; configured by `browser_pdf_cmd` |
| FlareSolverr | Cloudflare fallback | no key; explicit opt-in via `flaresolverr_url` |

Provider failures and rate limits are non-fatal. `promote` reports skip reasons and summary counters so “nothing promoted” is explainable: no candidate, low confidence, already exists, or provider errors. Use `pzi promote --dry-run` before writing.

### Citekeys and promotion

pzi treats existing citekeys as stable external handles because they may already appear in LaTeX, Org, Markdown, notes, or collaborator files. Existing citekeys are never renamed automatically.

- If a new capture is the same paper as an existing entry, pzi reuses the existing citekey.
- If a different paper wants an occupied citekey, pzi gives the new entry a numeric suffix: `smith2024graph2`, then `smith2024graph3`, etc.
- In the default promotion mode, the preprint citekey stays unchanged and the published entry receives its own generated citekey.
- If the published version already exists, pzi skips creating a duplicate and reports the existing published citekey.
- With `--replace`, pzi updates the preprint entry in place and keeps its citekey.

### HTTP API

`pzi server` exposes:

- `POST /capture` - body: `{url, bib?, tags?, dry_run?, pdf_url_candidates?, page_title?, canonical_url?, source_url?, abstract_url?, doi?}` → insert/update result
- `POST /attach-pdf-bytes` - body: `{citekey, bib?, source_url?, pdf_base64}` → attach browser-fetched PDF bytes
- `GET /bibs` - configured bibs (used by extension popup dropdown)
- `GET /health` - config + translation-server status

Same ingest pipeline as CLI. Capture responses include structured PDF fields: `pdf_url`, `pdf_path`, `pdf_status`, `pdf_error`, and `pdf_suggestion`. `pdf_status` is one of `none`, `found`, `direct_saved`, `direct_blocked`, `browser_saved`, or `failed`. CORS responses reflect only allowed local/extension origins.

Security defaults are local-first:

- bind to `127.0.0.1` unless you explicitly set another host
- reject non-local/non-extension browser origins
- cap JSON request bodies with `api_max_body_bytes`
- if `api_auth_token` is set, require `X-Pzi-Token: <token>` or `Authorization: Bearer <token>` on all non-`OPTIONS` requests

For browser extension use, set the same token in the popup's **API token** field.

## Architecture

## Design philosophy

pzi is local-first and BibTeX-native. Your `.bib` file and `papers/` directory are the source of truth; pzi avoids a database and keeps files grep-able, portable, and easy to inspect.

Risky operations are conservative by default. Promotion preserves scholarly provenance by keeping preprints alongside published versions unless `--replace` is requested. Citekeys are stable handles: pzi does not auto-rename existing keys, and new collisions use the common BibTeX numeric suffix convention.

Internally, pzi keeps pure planning logic separate from side effects. Metadata merging, duplicate detection, PDF discovery planning, and write planning are testable units; filesystem, network, browser, and container operations live at the edges.

Pure functions at core, side effects at edges.

**Component tree**

```
cli.py / http_api.py              # boundary: argparse + stdlib http.server
add_service.py                    # add/capture orchestration
pdf_discovery.py                  # pure functional PDF URL discovery + candidates
bib_service.py                    # bib list + default selection
tag_service.py                    # tags, tag filtering support, tag normalization
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

`config.toml` holds a list of bibs. Top-level commands select the single configured bib or the one marked `default = true`. Commands that accept `--target <target>` resolve `<target>` as a configured name, configured path, or direct `.bib` path. Use `pzi list` to inspect configured libraries and `pzi set-default <target>` to change the default.

**Design patterns**

- `TypedDict` everywhere - structural typing, zero boilerplate, immutability by convention.
- Explicit dependency injection - service functions accept `fetch_web`, `fetch_search`, etc. as keyword args with real defaults; tests pass lambdas.
- Pure logic, thin I/O - `pdf_discovery.py` is side-effect free; `bib_repository.py` separates deterministic write planning from locking/filesystem writes. Network/filesystem lives in `translation_server.py`, `bib_repository.py`, `cli.py`.
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
- New CLI command: add subparser in `cli_parser.py`, create `src/pzi/<feature>_service.py`, wire dispatcher in `cli.py`.
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
- `browser_hook` - boolean (default `true`). Set to `false` to skip desktop browser PDF fallback in headless/server environments

For secrets in a password manager, use `_cmd` variants (`unpaywall_email_cmd`, `semantic_scholar_api_key_cmd`) so keys are never written to disk.

### Environment variables

| Variable | Effect |
|----------|--------|
| `PZI_SKIP_AUTO_START` | Set to `1` to skip auto-starting the translation-server container (for CI/testing) |
| `PZI_BROWSER_PDF_CMD` | Override the `browser_pdf_cmd` config value |
| `PZI_BROWSER` | Preferred browser for PDF fallback: `firefox` or `chromium` (default: `firefox`) |
| `PZI_BROWSER_PROFILE` | Browser profile directory override for PDF fallback |

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
- no response after **Capture current page**

Check:

```sh
pzi server
curl http://127.0.0.1:8765/health
# with api_auth_token:
curl -H 'X-Pzi-Token: <token>' http://127.0.0.1:8765/health
```

If this fails, `pzi server` is not running or is on a different host/port.

### 2. Metadata lookup fails before entry creation

Symptoms:

- popup/CLI shows `translation server error`
- no `citekey` returned
- no new BibTeX entry

Check:

```sh
pzi init --setup
pzi services up
pzi doctor
```

Notes:

- browser capture now sends page title / DOI / canonical URL as fallback metadata
- if DOI lookup fails but browser metadata is sufficient, pzi should still insert an entry
- if it still fails, open **Show raw response** and inspect the JSON plus page URL

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
- **Show raw response** result
- `pzi server` terminal output
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

For cross-origin PDF candidates, the extension requests a narrow optional host permission after your click (for example `https://cdn.publisher.com/*`), uses it only for that candidate fetch, and removes it after the attempt. Denying the prompt keeps metadata capture intact but skips that cross-origin PDF fetch.

If step 3 or 4 fails, metadata may succeed while PDF attach does not.

### 6. CLI works for metadata, but not PDF

Symptoms:

- `pzi add ...` inserts entry
- no PDF downloaded

CLI uses public fetch paths plus optional `browser_pdf_cmd`. It does not automatically share your logged-in browser session.

Options:

- configure `browser_pdf_cmd`
- use browser extension flow for authenticated same-origin PDF candidates or when the active tab is the PDF itself
- use manual fallback:

```sh
pzi pdf attach <citekey> <url-or-path>
```

### 7. Fast isolation checklist

Check each layer in order:

1. `pzi server` running on `:8765`
2. translation-server running on `:1969` (auto-started by `pzi server`)
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
.venv/bin/ruff check src tools  # tests are exercised by pytest; legacy tests are not Ruff-clean yet
.venv/bin/pyright
.venv/bin/pytest --cov=pzi --cov-report=term-missing -q
.venv/bin/python tools/build_extension.py
.venv/bin/python -m build
```

### Publish smoke tests

For local publish checks, keep secrets in an untracked `.envrc` (ignored by this
repo). Do not commit it.

```sh
export PZI_LIVE=1
export PZI_CONTACT_EMAIL="you@example.com"
export PZI_UNPAYWALL_EMAIL="you@example.com"
export PZI_S2_API_KEY="..."
export PZI_CHROMIUM_PROFILE="$HOME/.config/chromium"
export PZI_FIREFOX_PROFILE="$HOME/.mozilla/firefox/xxxx.default-release"
```

Then run:

```sh
.venv/bin/python tools/publish_smoke.py --mode local
PZI_LIVE=1 .venv/bin/pytest tests/live -q
.venv/bin/python tools/publish_smoke.py --mode auth
```

The smoke runner prints only `set`/`unset` for secret-backed env vars and refuses
to run if `.envrc` is not ignored or is tracked by git.
