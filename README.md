# paperazzi

Warning: Paperazzi was built with AI-assistance, and is in beta. Bugs are expected.

paperazzi is a local-first bibliography capture tool that makes a Zotero-style capture workflow easy to use with plain BibTeX. Give it a DOI, URL, or PDF; it writes a BibTeX entry and, when possible, saves the PDF next to your library. Its CLI command is `pzi`.

**Status: beta.** APIs can rate-limit, promotion is best-effort, browser extension install is manual, and touched `.bib` entries may be rewritten. Report issues at [github.com/mnazaal/paperazzi/issues](https://github.com/mnazaal/paperazzi/issues).

paperazzi can manage BibTeX libraries, but it does not require ownership of them. You can use paperazzi as your main bibliography workflow, or point it directly at existing `.bib` files from Zotero, Paperpile, LaTeX projects, or hand-managed libraries.

One source of truth: your `.bib` file + a sibling `papers/` dir. No database.

## Why paperazzi?

paperazzi is for those who want:

- **Plain BibTeX** as source of truth — your `.bib` file is grep-able, git-trackable, and never locked in a database
- **Local-first** — your library is a `.bib` file plus a `papers/` dir; no sync service, no cloud dependency ([what else lands on disk](#what-pzi-writes-to-disk))
- **Zero GUI** — CLI + browser extension is the full interface; no desktop app to install
- **Zotero's translators without Zotero** — paperazzi runs Zotero's translation-server locally to leverage 1000+ site translators, then stores results in your BibTeX files

paperazzi is NOT for those who need:

- A desktop library browser, PDF reader, or annotation tool → use **Zotero**
- Sync across machines or group libraries → use **Zotero** or **Paperpile**
- Native Windows support (WSL2 works but adds friction)

## Quickstart

### Requirements

- Python 3.11+
- `pip`, `uv` or `pipx` for installation
- `git` if you want paperazzi to auto-install the Zotero translation-server
- Node.js 22+ (auto-downloaded if missing)

### 1. Install

paperazzi is not yet on PyPI. Install from GitHub for now:

```sh
# uv (recommended):
uv tool install 'paperazzi @ git+https://github.com/mnazaal/paperazzi.git'
# or pipx:
pipx install 'git+https://github.com/mnazaal/paperazzi.git'
# or plain pip (some systems may need `pip3`):
pip install --user 'paperazzi @ git+https://github.com/mnazaal/paperazzi.git'
```

This installs the `pzi` command. To enable the optional browser-profile PDF
fallback, add the `[playwright]` extra (this installs the `playwright` Python
package; browser binaries download on first use):

```sh
uv tool install 'paperazzi[playwright] @ git+https://github.com/mnazaal/paperazzi.git'
# or:
pipx install 'paperazzi[playwright] @ git+https://github.com/mnazaal/paperazzi.git'
```

### 2. Create config

```sh
pzi init --setup --bib ~/bibs/main.bib
```

This creates `~/.config/pzi/config.toml`, configures `~/bibs/main.bib`, and lets paperazzi launch the translation-server when needed.

### 3. Capture a paper from the CLI

```sh
pzi add https://arxiv.org/abs/2301.07041
pzi add 10.1145/1327452.1327492 --tags systems,classic
pzi add ~/Downloads/paper.pdf
```

Entries are written to `~/bibs/main.bib`; PDFs are saved to `~/bibs/papers/` when fetchable.

Capture many at once from a file of DOIs/URLs (one per line, `#` comments allowed):

```sh
pzi add --from-file urls.txt --tags ml      # or: cat urls.txt | pzi add --from-file -
```

Bulk capture runs sequentially with a polite delay (`--delay`), reuses one
translation-server, prints per-item progress, and writes any failures to
`<input>.failed.txt` so you can re-run just those: `pzi add --from-file urls.failed.txt`.
Reading the list from stdin (`--from-file -`) leaves no input path to name the
file after, so failures go to `pzi-failed.txt` in the current directory;
`--failures-out PATH` overrides either. Interrupting the run with Ctrl-C still
writes the file and prints the summary, so a long batch can be resumed.

For a persistent capture queue, use `pzi inbox <file>`. It processes every
DOI/URL line (lines beginning with `#` are comments; trailing `#tag` and
`@library` tokens set per-line tags and target), adds each one, then **rewrites
the file in place keeping only the lines that failed** — so re-running retries
exactly what is left. Unlike `--from-file` (which appends failures to a separate
`.failed.txt`), the inbox file *is* the queue: drop new links into it over time and
drain whenever you like. `--dry-run` previews without writing to the library or the
inbox file.

### 4. Optional: capture from the browser

Use this for authenticated publisher pages, browser-only PDF links, or one-click capture.

Get the unpacked extension one of two ways:

- **From a release** (no repo checkout needed): download
  `paperazzi-capture-firefox.zip` or `paperazzi-capture-chrome.zip` from the
  newest entry on the [releases
  page](https://github.com/mnazaal/paperazzi/releases) and unzip it. (Not
  `/releases/latest`: every `0.1.0bN` is published as a pre-release, which that
  URL deliberately skips.)
- **From a repo checkout** (tracks `main`): `python tools/build_extension.py`,
  which writes `dist/firefox/` and `dist/chrome/`.

```sh
pzi server --stop-after 30
```

Load the unpacked extension (`<extension-dir>` is the unzipped release folder, or
`dist/firefox`/`dist/chrome` from a repo checkout build):

- **Firefox**: `about:debugging` → This Firefox → Load Temporary Add-on → `<extension-dir>/manifest.json`
- **Chrome**: `chrome://extensions` → Developer mode → Load unpacked → `<extension-dir>/`

In onboarding, keep the default endpoint (`http://127.0.0.1:8765/capture`), set the API token only if configured, then test the connection. Keep `pzi server` running while browsing. Open a paper page, click the paperazzi icon, choose bib/tags/dry-run if needed, then **Capture current page**; or right-click a paper link → **Save to paperazzi**. Entries go to your configured `.bib`; PDFs go to `papers/` when available.

---

## `pzi server` vs `pzi add` vs `pzi doctor`

The translation-server is never a detached daemon you manage by hand. It runs as
a **child** of whichever foreground command needs it, and dies when that command
exits — so there is nothing to "stop" and no PID files.

| | `pzi server` | `pzi add` | `pzi doctor` |
|---|---|---|---|
| Purpose | HTTP API for browser capture | Single paper capture | Inspect / reinstall the backend |
| For | Browser extension users | CLI one-shot capture | Maintenance |
| Backend lifetime | Runs as a child for the server's lifetime | Reuses a running backend, else a short-lived child | n/a |
| Command | `pzi server [--stop-after N]` | `pzi add <value>` | `pzi doctor` / `pzi doctor --reinstall-server` |

Use `pzi server` for the browser extension and `pzi add` for CLI capture; both start the translation-server as needed. `--stop-after N` exits the server after N idle minutes. Stop it with Ctrl-C or `kill`. `pzi doctor` reports backend health; `pzi doctor --reinstall-server` reinstalls the translation-server.

### Running `pzi server` in the background (systemd)

To keep the browser-capture server running without a dedicated terminal, run it as a **user** service. A ready-made unit ships in [`packaging/systemd/pzi.service`](packaging/systemd/pzi.service):

The server refuses to start without an API token, so run `pzi init` first.
`--no-auth` permits that tokenless start; it does *not* switch off a token you
already have — with one configured, every request is still checked.

```sh
mkdir -p ~/.config/systemd/user
cp packaging/systemd/pzi.service ~/.config/systemd/user/
# If `which pzi` isn't ~/.local/bin/pzi, edit ExecStart to the real path.
systemctl --user daemon-reload
systemctl --user enable --now pzi.service
loginctl enable-linger "$USER"        # keep it up when you're logged out
journalctl --user -u pzi -f           # follow logs (no terminal held open)
```

The translation-server runs as a child of `pzi server`, so this one unit covers both. Add `--log-requests` to the `ExecStart` line if you want one journald line per HTTP request (method, path, status, duration) — it is off by default, and the query string is never logged. Don't add `--stop-after` here — the unit uses `Restart=on-failure`, and an idle-exit would just churn-restart.

Socket activation (a `pzi.socket` unit that starts the server on the first
request and lets `--stop-after` reclaim the ~100–150 MB idle footprint) is
**not supported**: `pzi server` always opens its own socket and does not accept
an inherited fd from systemd (`LISTEN_FDS`). The always-on user service above is
the intended deployment. If long-uptime memory growth in the node
translation-server is the concern, a timed restart (`RuntimeMaxSec=` or a
`.timer`) is a more direct fix.

## Reference

### What pzi writes to disk

Your library is a `.bib` file and a `papers/` directory, and those are the only
files you need to back up. pzi also maintains a private data directory —
`$XDG_DATA_HOME/pzi/` (`~/.local/share/pzi/` by default), overridable with
`pzi_data_home`. Everything in it is rebuildable **except** the API token and
the config backups: delete those and a paired browser extension stops working,
and `pzi init --force`'s undo is gone.

| Path | What it is | Size |
|---|---|---|
| `api_token` | The HTTP API token, generated by `pzi init`. **Credential.** | tiny |
| `node/` | A portable Node.js runtime, downloaded on first use. | ~50 MB |
| `ts/` | Zotero's translation-server: three git clones plus `node_modules`. | ~300–500 MB |
| `ts.install.lock` | Guards concurrent installs. | tiny |
| `ts-stderr.log` | The translation-server child's stderr — the first place to look when a capture fails. | grows |
| `metadata-cache/` | Provider responses, only when `metadata_cache_ttl > 0` (off by default). Swept on write and capped. | small |
| `config-backups/` | Timestamped copies of a config replaced by `pzi init --force`. **Not rebuildable.** | tiny |

Beside the library itself, writes leave: `<bib>.lock` and `<inbox>.lock` while a
command holds them, `<bib>.<citekey>.bak` from `delete`/`fix merge`,
`<bib>.promote.bak` from one `update --promote --replace` run, the
`--failures-out` file from a bulk `add`, and `papers/.orphans/` from
`fix clean --fix`.

Outside the data directory, PDF acquisition may briefly write a clone of your
browser profile to `$TMPDIR` (`pzi-chrome-*`) when `browser_pdf_cmd` uses a
Chrome profile — Chrome refuses remote debugging on its default user-data dir,
so it has to be copied. The clone contains your cookie database, is created
mode 0700, and is removed when the session closes; abandoned ones (from a hook
killed by its timeout) are swept on the next launch.


### Config

`pzi init --setup --bib ~/bibs/main.bib` writes `~/.config/pzi/config.toml` and an API token to `<data-home>/api_token` (mode 0600). Re-running `init` reuses the existing token so a paired browser extension keeps working; `--rotate-token` replaces it, which un-pairs the extension until you paste the new one in. `pzi add --force-new` inserts a second entry for a paper pzi considers a duplicate. Common options: `contact_email`, `unpaywall_email`, `browser_pdf_cmd`, `citekey_format`, `pdf_filename_format`, `papers_dir`, `browser_engine` (headless Playwright browser for automated capture, set in `config.toml`; `pzi init --browser` writes `browser_pdf_cmd`, not this, and it is not the same setting as `PZI_BROWSER` below), and multiple `[[bibs]]`. See `src/pzi/config.template.toml` for all options and comments. `papers_dir` defaults to `<bib-dir>/papers/`; use `_cmd` variants for secrets.

### CLI reference

Shared flags: every command accepts `[--config PATH]` to point at a config file other than the default; most library commands also accept `[--target <name|path>]` (`search`/`update` accept multiple: `--target a.bib b.bib`); commands that report a result accept `[--json]` (see below).

**Exit codes.** One meaning per code, so a script can branch on the status alone:

| Code | Meaning |
|---|---|
| 0 | Success |
| 1 | Ran fine and has something to report: no search matches, duplicates or fuzzy near-duplicates found, integrity issues, entries `check` could not verify |
| 2 | Usage error (unknown command, bad or missing arguments, or a refusal to overwrite without `--force`) |
| 3 | Entry not found (unknown citekey) |
| 4 | Batch partly failed — one or more items in a batch failed while at least one succeeded (`add --from-file`, `import`, `inbox drain`, `update`, `update --promote`). A batch in which *nothing* succeeded is `5`. |
| 5 | Could not run: unreadable or invalid config, unknown `--target`, a bib locked by another process or changed out from under a prepared write, permission denied, unreachable service |
| 130 / 141 | Interrupted (SIGINT) / downstream pipe closed (SIGPIPE) |

Note that `1` never means "the command failed" — a failure to run is always `5`.
So `pzi search ... || echo broken` *does* fire on an empty result set (`||`
takes any non-zero status); what `1` tells you is that the command ran fine and
has something to report. To branch on failure alone, test for `5`.

**`--json`.** Every command that reports a result accepts `--json` — except
`export`, `server` and `init` — and emits exactly **one** JSON document
on stdout — including when it fails, so a script
never has to scrape stderr to classify an error. The shape is the same
everywhere:

```json
{"command": "search", "status": "ok", "bib_name": "ml", "items": [], "errors": []}
```

so `.items[]` is the only jq path you need. Command-specific fields (`imported`,
`dry_run`, `total`, …) ride along beside those five keys. `authors` is always a
list, matching `export --format json`.

```sh
pzi search --query graph --json | jq -r '.items[].citekey' \
  | xargs -r -n1 pzi entries --json | jq -r '.items[] | "\(.citekey): \(.title)"'
```

**Pipes.** Empty results write nothing to stdout; the count, warnings, and
placeholder notes go to stderr, so `pzi search --query graph | cut -f1 | xargs -r -n1 pzi entries`
is safe. `pzi entries` writes five tab-separated columns
(citekey, year, title, authors, `pdf` when a PDF is attached).
`pzi import -` reads BibTeX from stdin, so `pzi export --target a | pzi import - --target b`
moves entries between libraries.

```sh
pzi init [--force] [--rotate-token] [--setup --bib PATH] [--papers-dir PATH] [--name NAME] [--browser chromium|firefox]
pzi add <doi|url|pdf> [--tags t1,t2] [--dry-run] [--citekey KEY] [--force-new] [--verbose] [--strict-metadata]
pzi add --from-file <file|-> [--tags t1,t2] [--delay S] [--failures-out PATH]  # bulk
pzi inbox <file> [--dry-run] [--tags t1,t2] [--delay S] [--json]  # drain a file of DOIs/URLs
pzi pdf retry [<citekey>] [--failed-only]     # --failed-only retries every PDF-less entry; not combinable with a citekey
pzi pdf attach <citekey> <url-or-path>
pzi tag add|remove <citekey> <tag...> [--dry-run]
pzi tag list [citekey] [--json]
pzi search [--query <text>] [--author <name>] [--year <int>] [--tag <tag>]
pzi check [--strict] [--report PATH] [--jsonl PATH] [--force] [--json]   # validate references; --force overwrites an existing report
pzi update [--dry-run]                        # fill missing metadata
pzi update --promote [--dry-run] [--replace] [--mark-resolved]  # replace preprints with published versions
pzi entries [--offset N] [--limit N] [--sort citekey|title|year|author]
pzi entries <citekey>                         # show the full record for one entry
pzi entries --stats                           # library statistics
pzi delete <citekey> [--dry-run] [--force]
pzi fix clean [--dry-run] [--fix]            # check integrity; --fix relocates orphan PDFs
pzi fix dedupe
pzi fix merge <citekey_a> <citekey_b> [--dry-run]
pzi fix reindex [--rename-citekeys [--dry-run] [--force]]  # audit citekeys; rename only on explicit opt-in
pzi export [--format bibtex|csv|json|ris] [-o <output>] [--force]
pzi import <source.bib> [--dry-run] [--force-new]
pzi doctor [--config-only] [--reinstall-server]  # health check; --reinstall-server reinstalls the translation-server
pzi server [--host H --port P] [--stop-after N] [--no-auth] [--log-requests]
```
`--strict-metadata` refuses a capture whose metadata does not identify a paper —
a title plus at least one of DOI, author or year — and fails when no provider
answered at all. A provider error that a later provider recovered from is a
warning, not a failure: a Crossref 429 does not fail an add that OpenAlex
completed.

`--dry-run` previews the entry without writing to your library: no `.bib`
change, no PDF downloaded. It is not an offline mode — the metadata cascade
still runs, so the preview reflects what providers actually return, and if
`metadata_cache_ttl` is set those responses are cached (which makes the real
run that follows faster). The previewed entry names the `file =` path a real
run would write, without creating it.


`pzi add` also accepts `--metadata-json PATH|-`, `--cookie-file PATH|-`, `--pdf-candidate URL` (repeatable), and `--page-html PATH|-` — these exist mainly for the browser extension's own capture flow, not typical CLI use. See `pzi add --help` for the full set.

Configured libraries live in your config file (`[[bibs]]` blocks). Choose the default by setting `default = true` on one of them, and inspect them with `pzi doctor`. Use `pzi --version` to print the installed version.

Without `--target`, commands operate on the configured default library. `--target` may be a configured library name, configured bib path, or direct `.bib` path. A direct `.bib` path must **already exist** — pzi will not create one, so `pzi export --target a | pzi import - --target new.bib` needs `new.bib` to be there first (`: > new.bib`), or declared in `config.toml`, where a not-yet-created file does resolve. Direct `.bib` targets use `<bib-dir>/papers/` for PDFs. Multiple targets are supported only for `search` and `update` (including `update --promote`) with a single flag: `--target a.bib b.bib`.

`--json` is accepted by the commands that report a result — including the mutating ones (`add`, `update`, `update --promote`, `tag`, `delete`, `import`, `inbox`, `pdf`, `fix clean|dedupe|merge|reindex`) — and always emits one envelope. Three commands do not accept it: `export` (whose output *is* the document), `server`, and `init` (see the CLI reference above). For `search` and `update`, which accept several `--target` values at once, the run still produces a single document: each item carries the `bib_name` it came from rather than the output splitting per library.

**Shell completion:** `argcomplete` is a base dependency, so tab-completion works once registered for your shell:

```sh
eval "$(register-python-argcomplete pzi)"   # add to ~/.bashrc or ~/.zshrc
```

For external `.bib` files managed by Zotero, Paperpile, LaTeX projects, or hand edits, use `--dry-run` first and keep a backup or Git history. paperazzi rewrites entries it touches; known source-preservation limitations:

- Malformed BibTeX (unbalanced braces, unterminated strings, a `%` comment inside an entry) is rejected and must be fixed manually
- **The first write reformats the whole file**, not only the entry it touches: indentation and whitespace are normalized throughout, and entry types are lowercased (`@Article` → `@article`). This happens once — the result is stable, so later writes only change the entries they touch. Citekeys, field names' capitalization, field order within an entry, line endings, a byte-order mark and the file's permissions are all preserved
- Comments, `@string` macros, and `@preamble` blocks are preserved across insert, update, tag, delete, merge, and reindex (which keeps entry order); `fix clean --fix` never rewrites the `.bib` (it only relocates orphan PDFs)
- BibTeX `@string` macros are kept as-is but not expanded or validated

### External services and rate limits

| Service | Used for | Key/email |
|---|---|---|
| Zotero translation-server | DOI/URL/page metadata | no key; configured by `translation_server_url` |
| Crossref | DOI/title metadata and PDF links | no key; `contact_email` recommended for polite User-Agent |
| OpenAlex | DOI/title metadata and OA URLs | no key; `contact_email` sent as `mailto` |
| Semantic Scholar | fallback metadata/PDF and promotion fallback | optional `semantic_scholar_api_key_cmd` / `semantic_scholar_api_key` for better limits |
| DBLP | CS-conference/journal metadata for promotion and `check` | no key |
| OpenReview | ML-venue (ICLR/NeurIPS/TMLR) metadata for promotion and `check` | no key |
| Unpaywall | open-access PDF by DOI | `unpaywall_email_cmd` / `unpaywall_email`, else `contact_email` |
| DOAJ / Europe PMC | open-access PDF lookup | no key |
| Playwright browser hook | JS/authenticated PDF discovery | no API key; configured by `browser_pdf_cmd` |
| FlareSolverr | Cloudflare fallback | no key; explicit opt-in via `flaresolverr_url` |

Provider failures and rate limits are non-fatal. `update --promote` reports skip reasons and summary counters so “nothing promoted” is explainable: no candidate, low confidence, already exists, or provider errors. Use `pzi update --promote --dry-run` before writing.

### Citekeys and promotion

paperazzi treats citekeys as stable external handles and never renames existing keys automatically.

- Same paper as an existing entry → reuse the existing citekey.
- New paper with an occupied citekey → add a numeric suffix (`smith2024graph-2`).
- Promotion keeps the preprint key by default; `--replace` updates in place.
- `--mark-resolved` tags each promoted preprint (`promoted`) and skips already-tagged entries on later runs, so re-running promotion over a large library only revisits what is new.

### Validate references (`pzi check`)

`pzi check` audits a library against authoritative metadata sources
(Crossref → OpenAlex → DBLP → OpenReview → Semantic Scholar), flagging
fabricated or mismatched references — useful before submitting a paper given
arXiv's 2026 hallucinated-reference policy. It is **read-only** (never writes the
`.bib`) and needs **no translation-server**, so it runs in CI.

Each entry gets one of three verdicts:

- **verified** — every claimed field is positively confirmed by a source
- **could-not-verify** — a record was found but a field could not be confirmed, or
  nothing matched (an abstention — *not* a clean pass)
- **problematic** — positive evidence of a defect (title/author/year mismatch,
  chimeric citation, fabricated author, implausible year)

```sh
pzi check                              # human-readable, problematic entries first
pzi check --report audit.json          # full JSON report
pzi check --strict --jsonl audit.jsonl # one JSON object per entry; CI-friendly
```

`pzi check` **exits 1 whenever any entry is problematic or could not be
verified**, in either mode, so `pzi check || alert` gates on the result without
extra flags (exit `5` still means the audit could not run — see the exit-code
table above).

`--strict` selects *harder checks*: it raises the confidence bar, queries every
source (no early exit), and adds two high-stakes tests — single-edit **title
typos** (a title within one character of the matched record, which whole-word
matching misses) and silently **truncated author lists** (fewer authors than the
record with no `and others` sentinel). `--json` prints the full result to stdout.

### HTTP API

`pzi server` exposes local endpoints for capture, search, listing, detail, export, PDF serving, tags, update, promote, browser PDF discovery/download, inbox drain (`POST /inbox/drain`), and delete. Main extension endpoint: `POST /capture`; health check: `GET /health`.

Security defaults are local-first: bind to `127.0.0.1`, allow local/extension origins, cap body size, and require `X-Pzi-Token` or bearer auth when a token is set. Keep non-loopback binds behind your own network protections.

The token is resolved in this order: `api_auth_token_cmd` (a command whose stdout is the token, e.g. `pass show pzi-token`) → the plaintext `api_auth_token` → an auto-read `<data-home>/api_token` file. `pzi init --setup` writes the token to that `0600` file and puts **nothing** token-related in `config.toml`; pzi reads it at runtime from the running user's resolved data home. So the config carries neither the secret nor an absolute home path, and is safe to commit to dotfiles and portable across machines. `api_auth_token_cmd`, if you set one, runs without a shell (no `~` expansion, no shell operators).

## Architecture

paperazzi is local-first and BibTeX-native. Internally, it keeps pure planning logic separate from side effects and uses this ingest pipeline: `classify → fetch → normalize → match → attach PDF → plan → write`.

Three front-ends — the CLI (`pzi.cli` → `pzi.commands.*`), the local HTTP API (`pzi.http_api`, used by the browser extension), and the extension itself — all converge on one function, `pzi.capture_core.capture_to_bib`, each building the same two frozen request models (`CaptureInput`/`CaptureOptions` in `pzi.capture_models`). Everything below that point, including the `pzi.add_service` core, is shared verbatim, so capture behaves identically however it is triggered. Differences stay above the seam: only the CLI starts the translation-server, and only the HTTP path applies SSRF validation and refuses bibs that are not configured libraries. Network access goes through dependency-injected fetcher seams typed in `pzi.protocols` (translation-server, Crossref/OpenAlex/S2, Unpaywall, PDF/binary), which is what lets the test suite run hermetically without hitting the network. Every module is classified into exactly one of five tiers, enforced by `tests/test_layer_boundaries.py`: **CORE** (`bibtex`, `similarity`, `url_safety`, …) imports no front-end and no browser module even transitively; **PIPELINE** (`pdf_discovery`, `pdf_planning`, `capture_core`) may reach the browser hooks it needs to find a PDF but never a front-end; **SERVICE** may reach both but no front-end; **FRONTEND** is the CLI and HTTP API; **BROWSER** is the browser and server-browser drivers. So "pure modules never import the browser layer" is true of CORE and not of PIPELINE — `pdf_discovery` imports `server_browser` and `browser_pdf` deliberately, and the test permits it. Writes are funneled through `pzi.bib_repository`, which holds a `portalocker` lock across the whole read-modify-write and replaces the file atomically. A write prepared before someone else's edit landed is re-projected onto the library as read under the lock; one whose target entry was deleted or renamed meanwhile is refused (exit 5) rather than applied to whatever now sits at that position.

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
| `PZI_CONFIG` | Config file path. `--config` takes precedence; otherwise this, then the XDG default |
| `PZI_SKIP_AUTO_START` | Set to `1` to skip auto-starting the translation-server (for CI/testing) |
| `PZI_BROWSER_PDF_CMD` | Override the `browser_pdf_cmd` config value |
| `PZI_BROWSER` | Preferred **desktop** browser for the manual "open and watch Downloads/" PDF fallback: `firefox` or `chromium` (default: `firefox`). Independent of `browser_engine`, which picks the **headless** Playwright browser used for automated capture (default: `chromium`) |
| `PZI_BROWSER_PROFILE` | Desktop browser profile directory override for the fallback above |
| `PZI_NODE` | Explicit Node.js >=22 binary for the translation-server (absolute path or a command name on PATH). Overrides PATH auto-detect and the `node_path` config; when set, pzi never prompts or downloads. A broken value is a hard error, not a silent fallback. Use this under systemd/daemons whose PATH lacks your shell's version-manager (fnm/nvm/volta/asdf) shims |
| `PZI_NODE_VERSION` | Pin the Node.js version pzi downloads, as full `X.Y.Z` (a leading `v` is accepted) |
| `PZI_NODE_MIRROR` | Override the Node.js download server (default: `https://nodejs.org/dist`); must be `https://` |
| `PZI_NPM_REGISTRY` | Override the npm registry used when installing the translation-server's dependencies |
| `PZI_DISABLE_DESKTOP_BROWSER_FALLBACK` | Set to `1` to skip the "open in desktop browser and watch Downloads/" PDF fallback |
| `PZI_DOWNLOAD_DIR` | Directory watched for the desktop-browser PDF fallback (default: `~/Downloads`) |
| `PZI_DESKTOP_BROWSER_TIMEOUT` | Seconds to wait for a PDF to appear during the desktop-browser fallback (default: 300; minimum 30) |
| `PZI_SKIP_BROWSER_HOOK` | Set to `1` to skip the `browser_pdf_cmd` headless-browser hook (used by the browser extension's own capture flow) |

Boolean variables above (`PZI_SKIP_AUTO_START`, `PZI_DISABLE_DESKTOP_BROWSER_FALLBACK`, `PZI_SKIP_BROWSER_HOOK`) read `0`, `false`, `no`, `off` and the empty string as off; any other value is on. Unset is off.

## PDF download for paywalled papers

Many publisher sites require authentication. Prefer the browser extension. For CLI fallback, point `browser_pdf_cmd` at an authenticated browser profile:

```toml
browser_pdf_cmd = "... python -m pzi.browser_pdf_hook --profile ~/.config/chromium"
```

For Cloudflare-gated pages, optionally configure FlareSolverr:

```toml
flaresolverr_url = "http://127.0.0.1:8191"
```

FlareSolverr may violate publisher terms of service; paperazzi warns when it is used.

PDF download tries in order (`pdf.fetch_and_store_pdf_with_fallbacks`):
1. **Direct download** — fastest, works for open-access papers
2. **Persistent server browser** — the headless `browser_engine` session, when
   `pzi server` is running and `browser_hook` is on
3. **`browser_pdf_cmd`** — your own command, using a real browser profile
4. **FlareSolverr** — optional Cloudflare fallback (may violate publisher ToS)
5. **Desktop browser + Downloads watcher** — opens the page in your desktop
   browser and waits for the file, for hosts in `desktop_fallback_hosts`

Stages 2 and 3 are skipped when `browser_hook = false`; stage 5 has its own
switch, `PZI_DISABLE_DESKTOP_BROWSER_FALLBACK`. Some stages are also skipped by
origin — a candidate whose host is not the one that blocked you does not get the
full chain.

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

Use dev extras for hacking on paperazzi itself:

```sh
git clone https://github.com/mnazaal/paperazzi
cd paperazzi
pip install -e ".[dev]"
# or with uv:
uv venv .venv
uv pip install -e ".[dev]"
```

The `[dev]` extra includes Playwright. To run the browser integration tests,
install browser binaries:

```sh
.venv/bin/playwright install chromium firefox
pytest -m browser -v
```

## Tests

```sh
.venv/bin/ruff check src tools tests
.venv/bin/pyright  # type-checks src + tools (see [tool.pyright] include)
pytest --cov=pzi --cov-report=term-missing -q
.venv/bin/python tools/build_extension.py
.venv/bin/python -m build
.venv/bin/twine check dist/*.tar.gz dist/*.whl
```

The suite is hermetic: two autouse fixtures in `tests/conftest.py` pin DNS and
refuse any non-loopback connection, so no test in the default run reaches the
network. The exception is `tests/live/`, which is selected only by `PZI_LIVE=1`
and deliberately does use it.

`tests/test_cli_end_to_end.py` covers three behaviours that look untestable and
are not. Each spawns a subprocess, so they are slower than the rest:

| Behaviour | What makes it testable |
|---|---|
| `pzi server` binds, enforces auth, serves | `PZI_SKIP_AUTO_START=1` makes the backend session a no-op, so it never clones translation-server |
| `add --from-file` exits `4` on a partly-failed batch | `tests/stub_translation_server.py` — a loopback stand-in that resolves chosen inputs and refuses the rest, so a *mixed* batch is reproducible |
| `pzi check` exits `5` when nothing is reachable | `unshare -rn` gives a network namespace with no route out and needs no root; skipped where unavailable |

Prefer these over manual checks: anything verified only by hand stops being
verified. If you add a behaviour that seems to need real network or the real
translation-server, check whether one of the three levers above applies first.

One limit to keep in mind: the stub reproduces translation-server's contract as
pzi understands it today, so those tests prove pzi still handles that shape —
not that pzi still works with the real server. The pinned commits in
`ts_backend._TS_REPOS` are what fix the contract; when you bump them, re-run a
capture by hand, because nothing in the suite will tell you the shape moved.
