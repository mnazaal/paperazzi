# pzi

pzi is a local-first bibliography capture tool that makes a Zotero-style capture workflow easy to use with plain BibTeX. Give it a DOI, URL, or PDF; it writes a BibTeX entry and, when possible, saves the PDF next to your library.

**Status: beta.** APIs can rate-limit, promotion is best-effort, browser extension install is manual, and touched `.bib` entries may be rewritten. Report issues at [github.com/mnazaal/pzi/issues](https://github.com/mnazaal/pzi/issues).

pzi can manage BibTeX libraries, but it does not require ownership of them. You can use pzi as your main bibliography workflow, or point it directly at existing `.bib` files from Zotero, Paperpile, LaTeX projects, or hand-managed libraries.

One source of truth: your `.bib` file + a sibling `papers/` dir. No database.

## Why pzi?

pzi is for those who want:

- **Plain BibTeX** as source of truth — your `.bib` file is grep-able, git-trackable, and never locked in a database
- **Local-first** — all data lives in `.bib` + `papers/` dir; no sync service, no cloud dependency
- **Zero GUI** — CLI + browser extension is the full interface; no desktop app to install
- **Zotero's translators without Zotero** — pzi runs Zotero's translation-server locally to leverage 1000+ site translators, then stores results in your BibTeX files

pzi is NOT for those who need:

- A desktop library browser, PDF reader, or annotation tool → use **Zotero**
- Sync across machines or group libraries → use **Zotero** or **Paperpile**
- Native Windows support (WSL2 works but adds friction)

## Quickstart

### Requirements

- Python 3.11+
- `pip`, `uv` or `pipx` for installation
- `git` if you want pzi to auto-install the Zotero translation-server
- Node.js 22+ (auto-downloaded if missing, requires `git` for cloning)

### 1. Install

```sh
uv tool install pzi
# or:
pipx install pzi
# or, with plain pip (some systems may need `pip3`):
pip install --user pzi
# or, from GitHub:
uv tool install 'pzi @ git+https://github.com/mnazaal/pzi.git'
```

### 2. Create config

```sh
pzi init --setup --bib ~/bibs/main.bib
```

This creates `~/.config/pzi/config.toml`, configures `~/bibs/main.bib`, and lets pzi launch the translation-server when needed.

### 3. Capture a paper from the CLI

```sh
pzi add https://arxiv.org/abs/2301.07041
pzi add 10.1145/1327452.1327492 --tags systems,classic
pzi add ~/Downloads/paper.pdf
```

Entries are written to `~/bibs/main.bib`; PDFs are saved to `~/bibs/papers/` when fetchable.

### 4. Optional: capture from the browser

Use this for authenticated publisher pages, browser-only PDF links, or one-click capture.

```sh
python tools/build_extension.py
pzi server --stop-after 30
```

Load the unpacked extension:

- **Firefox**: `about:debugging` → This Firefox → Load Temporary Add-on → `dist/firefox/manifest.json`
- **Chrome**: `chrome://extensions` → Developer mode → Load unpacked → `dist/chrome/`

In onboarding, keep the default endpoint (`http://127.0.0.1:8765/capture`), set the API token only if configured, then test the connection. Keep `pzi server` running while browsing. Open a paper page, click the pzi icon, choose bib/tags/dry-run if needed, then **Capture current page**; or right-click a paper link → **Save to pzi**. Entries go to your configured `.bib`; PDFs go to `papers/` when available.

---

## `pzi server` vs `pzi add` vs `pzi services`

The translation-server is never a detached daemon you manage by hand. It runs as
a **child** of whichever foreground command needs it, and dies when that command
exits — so there is nothing to "stop" and no PID files.

| | `pzi server` | `pzi add` | `pzi services` |
|---|---|---|---|
| Purpose | HTTP API for browser capture | Single paper capture | Inspect / reinstall the backend |
| For | Browser extension users | CLI one-shot capture | Maintenance |
| Backend lifetime | Runs as a child for the server's lifetime | Reuses a running backend, else a short-lived child | n/a |
| Command | `pzi server [--stop-after N]` | `pzi add <value>` | `pzi services status` / `update` |

Use `pzi server` for the browser extension and `pzi add` for CLI capture; both start the translation-server as needed. `--stop-after N` exits the server after N idle minutes. Stop it with Ctrl-C or `kill`.

## Reference

### Config

`pzi init --setup --bib ~/bibs/main.bib` writes `~/.config/pzi/config.toml`. Common options: `contact_email`, `unpaywall_email`, `browser_pdf_cmd`, `citekey_format`, `pdf_filename_format`, `papers_dir`, and multiple `[[bibs]]`. See `src/pzi/config.template.toml` for all options and comments. `papers_dir` defaults to `<bib-dir>/papers/`; use `_cmd` variants for secrets.

### CLI reference

Shared flags: most library commands accept `[--target <name|path>]`; read commands accept `[--json]`.

```sh
pzi init [--force] [--setup --bib PATH] [--papers-dir PATH]
pzi services status|update                 # inspect or reinstall the translation-server
pzi add <doi|url|pdf> [--tags t1,t2] [--dry-run]
pzi pdf retry <citekey>
pzi pdf attach <citekey> <url-or-path>
pzi tag add|remove <citekey> <tag...>
pzi tag list [citekey]
pzi search [--query <text>] [--author <name>] [--year <int>] [--tag <tag>]
pzi update [--dry-run]
pzi promote [--dry-run] [--replace]
pzi list
pzi set-default <name|path>
pzi entries [--offset N] [--limit N] [--sort citekey|title|year|author]
pzi detail <citekey>
pzi delete <citekey> [--dry-run] [--force]
pzi doctor                                # health check (config + translation-server)
pzi bib-stats
pzi clean [--dry-run] [--fix]
pzi dedupe
pzi merge <citekey_a> <citekey_b> [--dry-run]
pzi export [--format bibtex|csv|json|ris] [-o <output>]
pzi import <source.bib> [--dry-run] [--force-new]
pzi reindex [--dry-run] [--force]
pzi config validate [--config <path>]     # validate configuration file
pzi version                               # show installed pzi version
pzi server [--host H --port P] [--stop-after N]
```

Without `--target`, commands operate on the configured default library. `--target` may be a configured library name, configured bib path, or direct `.bib` path. Direct `.bib` targets use `<bib-dir>/papers/` for PDFs. Multiple targets are supported only for `search`, `update`, and `promote` with a single flag: `--target a.bib b.bib`.

Read/query commands (`search`, `entries`, `bib-stats`, `tag list`, `clean`, `dedupe`, `list`, `detail`) accept `--json` for machine-readable output.

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

pzi treats citekeys as stable external handles and never renames existing keys automatically.

- Same paper as an existing entry → reuse the existing citekey.
- New paper with an occupied citekey → add a numeric suffix (`smith2024graph2`).
- Promotion keeps the preprint key by default; `--replace` updates in place.

### HTTP API

`pzi server` exposes local endpoints for capture, search, listing, detail, export, PDF serving, tags, update, promote, browser PDF discovery/download, and delete. Main extension endpoint: `POST /capture`; health check: `GET /health`.

Security defaults are local-first: bind to `127.0.0.1`, allow local/extension origins, cap body size, and require `X-Pzi-Token` or bearer auth when `api_auth_token` is set. Keep non-loopback binds behind your own network protections.

## Architecture

pzi is local-first and BibTeX-native. Internally, it keeps pure planning logic separate from side effects and uses this ingest pipeline: `classify → fetch → normalize → match → attach PDF → plan → write`.

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

### Environment variables

| Variable | Effect |
|----------|--------|
| `PZI_SKIP_AUTO_START` | Set to `1` to skip auto-starting the translation-server (for CI/testing) |
| `PZI_BROWSER_PDF_CMD` | Override the `browser_pdf_cmd` config value |
| `PZI_BROWSER` | Preferred browser for PDF fallback: `firefox` or `chromium` (default: `firefox`) |
| `PZI_BROWSER_PROFILE` | Browser profile directory override for PDF fallback |

## PDF download for paywalled papers

Many publisher sites require authentication. Prefer the browser extension. For CLI fallback, point `browser_pdf_cmd` at an authenticated browser profile:

```toml
browser_pdf_cmd = "... python -m pzi.browser_pdf_hook --profile ~/.config/chromium"
```

For Cloudflare-gated pages, optionally configure FlareSolverr:

```toml
flaresolverr_url = "http://127.0.0.1:8191"
```

FlareSolverr may violate publisher terms of service; pzi warns when it is used.

PDF download tries in order:
1. **Direct download** - fastest, works for open-access papers
2. **Browser profile** - uses your authenticated session
3. **FlareSolverr** - optional Cloudflare fallback

## Troubleshooting capture flow

Fast checks:

```sh
curl http://127.0.0.1:8765/health
curl -H 'X-Pzi-Token: <token>' http://127.0.0.1:8765/health
```

Checklist:

1. `pzi server` is running on `:8765`.
2. `pzi doctor` reports a valid config.
3. Extension was rebuilt and reloaded after source changes.
4. Browser page or PDF is actually accessible.
5. Entry appears in the configured `.bib`.
6. For PDFs, check `file = {...}` in the entry and the configured `papers/` dir.
7. If direct PDF fetch is blocked, open the PDF tab and capture again, or attach manually with `pzi pdf attach <citekey> <url-or-path>`.

## Development install

Use dev extras for hacking on pzi itself:

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
.venv/bin/ruff check src tools tests
.venv/bin/pyright  # type-checks src + tools (see [tool.pyright] include)
pytest --cov=pzi --cov-report=term-missing -q
.venv/bin/python tools/build_extension.py
.venv/bin/python -m build
```
