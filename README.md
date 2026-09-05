# paperazzi

Warning: Paperazzi was built with AI-assistance, and is in beta. Bugs are expected.

paperazzi is a local-first bibliography capture tool that makes a Zotero-style capture workflow easy to use with plain BibTeX. Give it a DOI, URL, or PDF; it writes a BibTeX entry and, when possible, saves the PDF next to your library. Its CLI command is `pzi`.

**Status: beta.** APIs can rate-limit, promotion is best-effort, browser extension install is manual, and touched `.bib` entries may be rewritten. Report issues at [github.com/mnazaal/paperazzi/issues](https://github.com/mnazaal/paperazzi/issues).

paperazzi can manage BibTeX libraries, but it does not require ownership of them. You can use paperazzi as your main bibliography workflow, or point it directly at existing `.bib` files from Zotero, Paperpile, LaTeX projects, or hand-managed libraries.

One source of truth: your `.bib` file + a sibling `papers/` dir. No database.

## Why paperazzi?

paperazzi is for those who want:

- **Plain BibTeX** as source of truth — your `.bib` file is grep-able, git-trackable, and never locked in a database
- **Local-first** — your library is a `.bib` file plus a `papers/` dir; no sync service, no cloud dependency ([what else lands on disk](docs/reference.md#what-pzi-writes-to-disk))
- **Zero GUI** — CLI + browser extension is the full interface; no desktop app to install
- **Zotero's translators without Zotero** — paperazzi runs Zotero's translation-server locally to leverage ~750 site translators, then stores results in your BibTeX files

paperazzi is NOT for those who need:

- A desktop library browser, PDF reader, or annotation tool → use **Zotero**
- Sync across machines or group libraries → use **Zotero** or **Paperpile**
- Native Windows support (WSL2 works but adds friction)

## Quickstart

### Requirements

- Python 3.11.4+
- `pip`, `uv` or `pipx` for installation
- `git` if you want paperazzi to auto-install the Zotero translation-server
- Node.js 22+ (auto-downloaded if missing)

### 1. Install

paperazzi is not published on PyPI yet. Install from GitHub — the project is
called paperazzi but ships as the `pzi` distribution, the same name as the
command:

```sh
# uv (recommended):
uv tool install 'pzi @ git+https://github.com/mnazaal/paperazzi.git'
# or pipx:
pipx install 'git+https://github.com/mnazaal/paperazzi.git'
# or plain pip (some systems may need `pip3`):
pip install --user 'pzi @ git+https://github.com/mnazaal/paperazzi.git'
```

> **Do not run `pip install paperazzi`.** That name on PyPI belongs to an
> unrelated project — "LLM-Based Paper Query System with Evaluation Framework",
> by a different author — so it installs someone else's package, not this one.
> Every command above names the repository explicitly, which is what makes them
> the right ones.

This installs the `pzi` command. To enable the optional browser-profile PDF
fallback, add the `[playwright]` extra (this installs the `playwright` Python
package; browser binaries download on first use):

```sh
uv tool install 'pzi[playwright] @ git+https://github.com/mnazaal/paperazzi.git'
# or:
pipx install 'pzi[playwright] @ git+https://github.com/mnazaal/paperazzi.git'
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
the file in place keeping only the lines that failed** — plus your comments and
blank lines, which are never processed and never dropped — so re-running retries
exactly what is left. Unlike `--from-file` (which writes failures to a separate
`.failed.txt`, replacing that file's previous contents each run), the inbox file
*is* the queue: drop new links into it over time and
drain whenever you like. `--dry-run` previews without writing to the library or the
inbox file.

### 4. Optional: capture from the browser

Use this for authenticated publisher pages, browser-only PDF links, or one-click capture.

Get the unpacked extension one of two ways:

- **From a release** (no repo checkout needed): download
  `paperazzi-capture-firefox.zip` or `paperazzi-capture-chrome.zip` from the
  newest entry on the [releases
  page](https://github.com/mnazaal/paperazzi/releases) and unzip it. (Not
  `/releases/latest`: betas are published as pre-releases, which that URL
  skips. It does not fail — it walks back to the newest release that is *not*
  a pre-release, which for this repo is an early beta from before that marking
  was applied. Following it gets you a stale extension, silently.)
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
request and lets `--stop-after` reclaim the idle footprint — measured at ~31 MB
RSS for `pzi server` alone; the node translation-server child and any persistent
browser session sit on top of that and have not been measured) is
**not supported**: `pzi server` always opens its own socket and does not accept
an inherited fd from systemd (`LISTEN_FDS`). The always-on user service above is
the intended deployment. If long-uptime memory growth in the node
translation-server is the concern, a timed restart (`RuntimeMaxSec=` or a
`.timer`) is a more direct fix.

## Reference

Everything below the quickstart lives in **[docs/reference.md](docs/reference.md)**:
what pzi writes to disk, recovering from an interrupted write, the config keys,
the full CLI reference, external services and rate limits, citekeys and
promotion, `pzi library check`, the HTTP API, the Python API, the architecture,
the versioning promise, and source coverage.

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

Stages 2 and 3 are skipped when `browser_hook = false`. Stages 2, 3 **and 5**
are all skipped when the capture came from the browser extension, which
downloads through its own live session instead. `PZI_SKIP_BROWSER_HOOK` skips
stage 3 only, and stage 5 has its own switch,
`PZI_DISABLE_DESKTOP_BROWSER_FALLBACK`. Two stages are gated on the
candidate's **host** rather than on a switch: stage 5 runs only for hosts in
`desktop_fallback_hosts`, and so does stage 3 when you have not set
`browser_pdf_cmd` yourself — pzi builds one automatically for those hosts only.
An explicit `browser_pdf_cmd` is tried for every candidate. The gate is that
host list, not which host blocked the direct download.

## Troubleshooting capture flow

Fast checks:

```sh
curl -H "X-Pzi-Token: $(cat ~/.local/share/pzi/api_token)" http://127.0.0.1:8765/health
```

Authentication is one gate in front of every route, `/health` included, and
`pzi init` always provisions a token — so an unauthenticated `curl` answers
`401 Unauthorized` even when the server is perfectly healthy. A `401` therefore
tells you the server is up and your token is wrong or missing; connection
refused is the one that means it is not running.

Checklist:

1. `pzi server` is running on `:8765`.
2. `pzi doctor` reports a valid config.
3. Extension was rebuilt and reloaded after source changes.
4. Browser page or PDF is actually accessible.
5. Entry appears in the configured `.bib`.
6. For PDFs, check `file = {...}` in the entry and the configured `papers/` dir.
7. If direct PDF fetch is blocked, open the PDF tab and capture again, or attach manually with `pzi pdf attach <citekey> <url-or-path>`.

### Extension and server versions

The extension and the server ship from one tag and are meant to run as a pair,
but an installed extension outlives a `pzi` upgrade. The extension therefore
compares its own version against the one `GET /health` reports, and **warns
without refusing**: the popup shows a banner naming both versions and every
route keeps working.

That choice is deliberate. Refusing on a mismatch would turn a routine `pzi`
upgrade into a broken capture flow until you remembered to reload the
extension, and an extension that silently stops capturing is a worse failure
than one talking to a slightly newer server. **The fix is to rebuild and reload
the extension** (`python tools/build_extension.py`, then reload it in the
browser). A server too old to report a version produces no warning at all.

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
pytest -m "not browser" --cov=pzi --cov-report=term-missing -q
rm -rf dist/   # both steps below append; a stale wheel would pass twine check
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
| `pzi library check` exits `5` when nothing is reachable | `unshare -rn` gives a network namespace with no route out and needs no root; skipped where unavailable |

Prefer these over manual checks: anything verified only by hand stops being
verified. If you add a behaviour that seems to need real network or the real
translation-server, check whether one of the three levers above applies first.

One limit to keep in mind: the stub reproduces translation-server's contract as
pzi understands it today, so those tests prove pzi still handles that shape —
not that pzi still works with the real server. The pinned commits in
`ts_backend._TS_REPOS` are what fix the contract; when you bump them, re-run a
capture by hand, because nothing in the suite will tell you the shape moved.
