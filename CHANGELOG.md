# Changelog

All notable changes to paperazzi (CLI command `pzi`) are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- The HTTP API auth token is now auto-discovered from `<data-home>/api_token`
  when neither `api_auth_token` nor `api_auth_token_cmd` is set, so a config can
  carry no token reference at all. Resolution precedence: `api_auth_token_cmd` →
  `api_auth_token` → the auto-read token file.
- `api_auth_token_cmd` config option: resolve the HTTP API auth token from a
  command's stdout (e.g. `pass show pzi-token`), matching the existing `*_cmd`
  secret-indirection pattern used for emails and the S2 key.
- `PZI_NODE` env var and `node_path` config option to point pzi at an explicit
  Node.js >=22 binary for the translation-server, instead of PATH auto-detect or
  the portable download. Intended for version-manager users (fnm/nvm/volta/asdf)
  and daemon contexts (systemd) whose PATH does not include the shell's Node
  shims. `PZI_NODE` overrides `node_path`; both override auto-detect. A
  set-but-broken value is a hard error, not a silent fallback to download.

### Changed

- `pzi init --setup` no longer writes the generated API auth token as plaintext
  into `config.toml`. Because users commonly symlink that file into a
  git-tracked dotfiles repo, the inline token was a footgun that leaked a
  secret into version control. Setup now writes the token to a separate `0600`
  file (`<data-home>/api_token`) and writes **nothing** token-related into the
  config — pzi auto-reads that file at runtime from the running user's resolved
  data home. So `config.toml` carries neither the secret nor an absolute home
  path (which would expose a username/directory layout) and is safe to commit
  and portable across machines. Existing configs with a plaintext
  `api_auth_token` continue to work unchanged. **If you ran an older
  `pzi init`, rotate that token: replace the plaintext value (and scrub it from
  any committed history).**
- `pzi init --setup` now writes home-relative `~/...` paths instead of absolute
  ones in the generated `config.toml`: the bib `path`/`papers_dir`, the
  interpreter in `browser_pdf_cmd`, and any Firefox `--profile` are folded to
  `~` when they live under the home directory (paths outside home, e.g. a system
  `/usr/bin/python3`, stay absolute). This keeps a committed config from
  exposing the home layout and makes it consistent with the commented example
  lines. To support this, the browser hook command now expands a leading `~` in
  each token at run time (it is split and run with `shell=False`, so the shell
  never would) — which also makes a hand-written `--profile ~/...` work.
- Default config and data directories now follow the XDG Base Directory spec:
  the config path resolves under `$XDG_CONFIG_HOME` (default `~/.config`) and
  the data home (`pzi_data_home`, cache for Node.js + translation-server) under
  `$XDG_DATA_HOME` (default `~/.local/share`), instead of hardcoding
  `~/.config` / `~/.local/share`. Non-absolute `XDG_*` values are ignored per
  the spec. An explicit `pzi_data_home` in config still takes precedence, and
  when unset the value now respects the injected home directory consistently
  with bib paths. `pzi init --setup` no longer writes a hardcoded
  `pzi_data_home` line (it emits a commented example), so the XDG-aware default
  applies. Chrome/Chromium profile auto-detection likewise honors
  `$XDG_CONFIG_HOME`.

### Fixed

- `pzi server` under systemd (or any non-interactive/no-TTY context) silently
  refused to bootstrap Node.js: `ensure_node` reached the interactive install
  prompt, `input()` raised `EOFError` on the missing stdin, and that was caught
  as "cancelled" — so the translation-server never started and every capture
  failed while `systemctl status` still showed the server "active". When stdin
  is not a TTY, pzi now downloads portable Node.js automatically (as it already
  did for `interactive=False`) instead of prompting.
- `download_node` re-downloaded and re-extracted Node.js on every call even when
  a matching version was already installed: the reuse check compared against
  `detect_node()` (system PATH) rather than the actual cached extraction path.
  It now reuses the previously extracted, runnable binary at
  `<data_home>/node/node-v<version>-<dist>/bin/node`.

- Inbox drain (`pzi inbox drain`) could silently drop a line appended to the
  inbox file (e.g. by the browser extension) while the drain's network calls
  were in flight: the final rewrite used a stale in-memory snapshot from the
  start of the drain. The rewrite now re-reads the file and merges in any
  lines appended after the snapshot was taken, under a short-lived advisory
  lock scoped to just the final read+rewrite.
- The HTTP API accepted requests whose `Host` header named an attacker
  domain even when bound to loopback, which let a DNS-rebinding page (its own
  domain's DNS pointed at 127.0.0.1) reach the API via a plain GET carrying
  no `Origin` header. Requests are now also validated against the server's
  bind host (`api_listen_host`); see `docs/security.md`.
- The HTTP API server only bounded `accept()` on the listening socket, not
  reads on already-accepted connections, so a client that opened a
  connection and trickled bytes (or sent none) could hold a handler thread
  open indefinitely. Each accepted connection now gets a 30s read timeout.
- PDF attach sessions (`/attach-pdf-bytes`, `/attach-pdf-raw`) had a
  get-then-later-consume race: two concurrent requests for the same
  `request_id` could both pass validation before either was marked consumed,
  double-spending a one-shot attach token. The session is now claimed
  (atomically removed) before validation and restored only if that attempt
  doesn't succeed, so a legitimate retry after a bad token or transient
  failure still works, but concurrent racers cannot both proceed.
- `url_safety.public_ip_address` (SSRF guard) treated 100.64.0.0/10
  (carrier-grade NAT) as publicly routable, and could disagree with the
  embedded address of an IPv4-mapped IPv6 literal (`::ffff:127.0.0.1`) on
  some Python patch releases. Now derived from `ip.is_global`, canonicalizing
  IPv4-mapped literals to their embedded address first.
- Writing a `.bib` file at a path that was itself a symlink (e.g. pointing
  into synced cloud storage) silently deleted the symlink and replaced it
  with a regular file, since `os.replace` treats a symlink destination as
  the directory entry to replace rather than the file it points at. Bib
  writes now resolve through the symlink first, so the symlink survives.
- A non-arXiv `eprint` field (e.g. a bioRxiv preprint ID) was classified as
  an arXiv ID whenever it was merely non-empty, regardless of
  `archiveprefix`, fabricating a bogus `arxiv.org` PDF URL. Now gated
  strictly on `archiveprefix` (case-insensitive `arXiv`).
- Better BibTeX `shorttitle(N,M)` citekey/filename templates truncated one
  title word to N characters instead of taking the first N words (each
  optionally truncated to M characters), and an invalid `match`/`replaceFrom`
  regex in a copied Zotero template raised instead of degrading safely.
- `pzi add`'s fallback metadata (e.g. browser-extension page metadata) was
  never applied when the fetched value was an empty list (`authors: []`) —
  only `None`/blank-string values were treated as missing.
- PDF discovery via DOI (Crossref/Europe PMC/DOAJ) always called the real
  network fetchers, bypassing the same dependency-injection seam used by
  every other discovery step; and a single discovery step raising an
  exception aborted the whole sequential discovery chain, unlike the
  parallel path, which already isolated per-step failures.
- The publisher PDF-gateway hostname table matched with an unanchored regex,
  so an unrelated lookalike host (e.g. `evilsciencedirect.com`) could be
  misidentified as a known publisher gateway. Matching is now a proper
  domain/subdomain check. Also: the Authorea/SAGE URL rewrites silently
  returned the unchanged landing-page URL (instead of no PDF found) when
  their expected path substring was absent; the Better BibTeX renderer's
  unrecognized-field fallback looked up the raw filter-suffixed token instead
  of the parsed field name, so any field without a dedicated branch always
  rendered empty; and `arxiv.org` URL detection didn't recognize
  `www.arxiv.org`.
- `pzi add`'s metadata-fetch step caught any exception (not just
  network/parsing failures) and reported it as "translation server error" —
  or silently fell back to manually-provided metadata — which could mask an
  unrelated bug as a network problem. Now scoped to the exception types the
  fetchers actually raise.
- A `.bib` write that failed after the temp file was created but before (or
  during) the atomic rename — e.g. a disk error — left the `.tmp` file behind
  permanently instead of cleaning it up (the original `.bib` was never at
  risk, only the leftover temp file). Now removed on any failure, matching
  the inbox writer's existing behavior.

### Changed

- `.bib`, inbox, and metadata-cache writes now also fsync the containing
  directory after the atomic rename, so the rename itself survives a crash
  immediately after a write (previously only the new file's content was
  guaranteed durable, not the directory entry pointing at it).
- Releases now attach the prebuilt Firefox and Chrome extension zips
  (`paperazzi-capture-firefox.zip`, `paperazzi-capture-chrome.zip`) alongside
  the sdist/wheel, so installing the browser extension no longer requires a
  repo checkout.

### Docs

- Corrected several stale/inaccurate docs: README pipx install syntax for
  the `[playwright]` extra, a false "requires git" claim on the Node.js
  download line, `config.template.toml`'s `browser_engine` value list
  (dropped `chrome`, added `webkit`), the attach-session TTL in
  `docs/security.md` (was documented as 5 min, actually 10 min), the
  `packaging/systemd/pzi.service` `Documentation=` URL, a stale
  `pzi-browser-hook` console-script reference in `tools/`, and the 0.1.0b1
  changelog's inflated "100% coverage, mypy clean" quality claim. Also
  documented `--config PATH`, `pzi pdf retry --failed-only`, previously
  omitted `add`/`init`/`tag`/`export` flags, and shell-completion
  enablement in the README CLI reference; added missing env vars
  (`PZI_NODE_MIRROR`, `PZI_NPM_REGISTRY`,
  `PZI_DISABLE_DESKTOP_BROWSER_FALLBACK`, `PZI_DOWNLOAD_DIR`,
  `PZI_DESKTOP_BROWSER_TIMEOUT`, `PZI_SKIP_BROWSER_HOOK`) to the env-var
  table.
- Clarified that `PZI_BROWSER` (desktop browser fallback) and `pzi init
  --browser`/`browser_engine` (headless Playwright automation browser) are
  independent settings with independent defaults, not the same knob. Also
  documented that `pzi search --json` always returns a JSON array (one result
  per searched library), even for a single default target.

## [0.1.0b2] - 2026-07-02

### Fixed

- Crash when a fallback-sourced record (e.g. browser-extension page metadata)
  supplied `year` as a string: the similarity/dedup check compared it against
  an `int` and raised `TypeError`. Year is now coerced at the point a
  `NormalizedRecord` is produced, with a defensive coercion at the comparison
  site as well.
- DOI normalization no longer keeps a trailing `?query`/`#fragment` from a
  pasted `doi.org` link (`doi.org` forwards query strings to the resolved
  target rather than treating them as part of the DOI). A `doi:` prefix
  (e.g. `doi:10.1234/abc`) is now also recognized.
- `pip install`/`pipx install` guidance in `pzi init --setup`, the browser
  session hook, and the browser PDF hook referenced the wrong package name
  (`pzi[playwright]`, which does not exist) instead of the actual
  distribution name, `paperazzi[playwright]`.

### Changed

- **Breaking, `.bib` format:** `pdf_url` and `abstract_url` are no longer
  packed into the `note` field with `" | "` delimiters and `PDF:`/`Abstract:`
  labels — a user's own note text containing that same shape could corrupt
  the parse, and the packed values weren't readable by other BibTeX tools.
  Each value now has its own field: `pzi-pdf-url`, `pzi-abstract-url`. `note`
  is now pure free text. Entries written by 0.1.0b1 keep their PDF/abstract
  URL as inert text inside `note` on next read (nothing is deleted from the
  file) — re-run `pzi update` or `pzi add` on the affected DOI/URL to
  repopulate the new fields, or move the value over by hand.
- `pzi search` output labels the matched-fields column (`[matched: title,tags]`)
  instead of a bare `[title,tags]`, which read as the same column `pzi entries`
  uses for actual author names.

## [0.1.0b1] - 2026-07-01

First public beta.

### Added

- **CLI**: `init`, `add` (single, bulk `--from-file`, `inbox`), `pdf retry|attach`,
  `tag`, `search`, `check`, `update [--promote]`, `entries`, `delete`,
  `fix clean|dedupe|merge|reindex`, `export`, `import`, `doctor`, `server`.
- **Capture pipeline**: DOI/URL/PDF input → BibTeX entry + PDF download.
  Metadata sources: Zotero translation-server (local child), Crossref, OpenAlex,
  Semantic Scholar, DBLP, OpenReview, Unpaywall, DOAJ, Europe PMC.
- **PDF acquisition**: direct download → browser-profile hook (Playwright, optional
  extra) → FlareSolverr (optional, opt-in).
- **Local HTTP API**: loopback-bound, token-auth optional, for browser extension.
- **Browser extension**: Firefox + Chrome, multi-source capture, search detection,
  onboarding flow.
- **Backend**: Zotero translation-server auto-installed as a local Node.js child
  process; auto-downloaded if missing.
- **BibTeX**: plain `.bib` file + `papers/` dir as sole source of truth; no
  database. Portalocker file lock; aborts on concurrent external edits.
- **Architecture**: pure planning logic separated from side effects; dependency-
  injected fetcher seams; layer-boundary tests enforce no CLI/HTTP/browser
  imports in pure modules.
- **Quality**: ~81% test coverage, pyright clean (no mypy) across ~90 source
  files, ~100 test files.
- **Security**: SSRF guards for FlareSolverr, tar-slip guard for imports,
  loopback-only HTTP binding, optional token auth, documented security model.
- **Shell completions**: bash/zsh completions via `argcomplete`, included in the
  base install.

### Changed

- Playwright is an optional extra (`pip install 'paperazzi[playwright]'`) instead of a
  hard dependency. The base install is lighter; the browser-profile PDF fallback
  is only needed by users who configure `browser_pdf_cmd`.
- `bibtexparser` pin is `>=2.0.0b9,<3` to allow patch-level updates within the v2
  beta series.

### Known Limitations

- APIs can rate-limit; promotion is best-effort.
- Browser extension install is manual.
- Touched `.bib` entries may be re-serialized.
- No native Windows support (WSL2 works).
- No sync, group libraries, or desktop reader (by design).
- Not yet on PyPI; install from GitHub for now.

[Unreleased]: https://github.com/mnazaal/paperazzi/compare/v0.1.0b2...HEAD
[0.1.0b2]: https://github.com/mnazaal/paperazzi/compare/v0.1.0b1...v0.1.0b2
[0.1.0b1]: https://github.com/mnazaal/paperazzi/releases/tag/v0.1.0b1