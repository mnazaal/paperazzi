# Changelog

All notable changes to paperazzi (CLI command `pzi`) are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- **Eleven commands now return the exit code they document.** A previous release
  announced that exit codes have one meaning each; these sites never conformed,
  so the guarantee that `1` means "ran fine and has something to report" — and
  never "failed" — did not actually hold. **Scripts that branch on a specific
  code need re-checking; scripts that branch on non-zero are unaffected.**

  | Command | Was | Now |
  |---|---|---|
  | `add --from-file` / `inbox` with some items failed | 1 | 4 |
  | `add` / `inbox` backend not ready, unreadable input file | 1 | 5 |
  | `pdf retry` / `pdf attach` unknown citekey | 5 | 3 |
  | `pdf retry --failed-only` with failures, text mode | 0 | 4 |
  | `fix merge` unknown citekey | 5 | 3 |
  | `tag list` unknown citekey / bad `--target` | 1 | 3 / 5 |
  | `doctor --reinstall-server` failures | 1 | 5 |
  | `import` missing source file | 3 | 5 |
  | `import` unloadable config or unknown `--target` | 4 | 5 |
  | `update` where a record failed | 0 | 4 |
  | `fix reindex` audit that found renames to make | 0 | 1 |

  Two of these were format-dependent rather than merely wrong:
  `pdf retry --failed-only` returned 4 with `--json` and 0 without it for the
  same result, and `fix merge` computed its code separately in each branch.
  Both now share one mapper.

  Root causes, both fixed: `pdf_service` and `dedupe_service` never set the
  structured `reason` field that four runners already branch on, so every
  not-found fell through to the "could not run" default; and `update` recorded
  per-record failures only as free text in each item's `note`, which nothing
  read — so a run in which every record failed still reported success.
  `update` also swallowed `ConcurrentEditError`, the one condition every other
  command reports as 5; it now propagates, because continuing to write after
  losing that race is not safe.

  `import` with an unloadable config or unknown `--target` previously fanned the
  failure out into one error per record, reporting a partly-failed batch when in
  fact nothing had been attempted.

### Added

- **`pzi add` now warns when it inserts a probable duplicate.** A capture with
  no DOI and no arXiv id cannot exact-match an existing entry, so it inserts a
  second one; the fuzzy near-duplicate match was recorded only in the new
  entry's `note` field, which meant the terminal output looked identical to a
  clean capture. It now also prints ``warning: possibly a duplicate of
  <citekey> — compare them with `pzi fix dedupe` `` to stderr, and carries it
  in the `warnings` list of `--json` output. Applies to single and bulk
  (`--from-file`, `inbox`) capture alike.

### Fixed

- **`pzi fix dedupe` never reported a single fuzzy near-duplicate.**
  `find_duplicates` passed each record inside its own candidate corpus, but
  `compute_similarity_hint` returns only the single *best* match and a record
  always scores highest against itself (identical title tokens, full author
  overlap). The self-match therefore won every time and was discarded by the
  `hint != citekey` guard immediately after, so the fuzzy pass was silent for
  every library — the exact-identity pass (DOI / arXiv id / canonical URL) was
  doing all the work. Near-duplicates that carry *different* DOIs, which is
  exactly what the fuzzy pass exists to catch, were invisible. The record is
  now excluded from its own corpus, and a pair is reported once rather than
  once per direction. No test covered `fuzzy_candidates` at all, which is why a
  green suite never caught it.
- **`pzi fix dedupe` exited 0 when its only finding was fuzzy.** The exit code
  was derived from `total_clusters`, which counts exact clusters only. Per the
  documented vocabulary, `1` means "ran fine, has something to report", so a
  library whose sole finding is a near-duplicate now exits `1`. Scripts
  branching on the exit code will see runs flip from `0` to `1` once real
  near-duplicates are detected. `total_clusters` keeps its exact-only meaning.
- **The layering guard could not see `from pzi import <module>`.**
  `tests/test_layer_boundaries.py` parsed `import pzi.x`, `from pzi.x import y`
  and both relative forms, but package-level imports fell through its
  `startswith("pzi.")` check, so 22 import edges — including
  `commands/init.py` → `setup_service` and `errors.py` → `exit_codes`, which
  are written *only* that way — were invisible to all five architectural
  checks. Every one of them was legal, which is why nothing ever failed; a
  future core-module import of a front-end would have passed just as quietly.
  The extractor now reads that form, and the graph drops candidate names that
  are re-exported functions rather than modules.
- **`update --promote` carried a stale confidence-threshold fallback.**
  `promote_service` read `config.get("promote_confidence_threshold", 3)`, a
  leftover from before the move to `score_match`'s 0-100 scale, where the
  documented default is 60 — on the current scale a threshold of 3 is
  effectively no gate at all. It was unreachable (`AppConfig` is a total
  `TypedDict`, so the loader always supplies the key, and a config omitting it
  resolves to 60), so no run was ever affected; the fallback is now gone in
  favour of a plain subscript, and the default itself is a named constant
  (`config.DEFAULT_PROMOTE_CONFIDENCE_THRESHOLD`) rather than the literal `60`
  repeated across the loader and the validator.
- **`NormalizedRecord.similarity_hint` was declared but never written.** The
  fuzzy dedup hint went into `note` prose only. The field now carries the
  matched citekey, which is what the new duplicate warning reads. It is not
  serialized into BibTeX (`record_to_bibtex_entry` projects an explicit
  allowlist), so `.bib` output is unchanged apart from the existing note.

## [0.1.0b3] - 2026-07-26

### Changed

- **Internal: `config.load_and_resolve_bib` is now `config.load_bib_target`**
  and returns a `BibResolutionFailure` (carrying a structured `reason`) instead
  of a bare list of error strings. Affects anything importing it directly.


- **`--json` output is now one envelope for every command**:
  `{command, status, bib_name, items, errors}`, with command-specific fields
  (`imported`, `dry_run`, `total`, …) alongside. Previously there were four
  shapes — `search` emitted a bare array of per-library objects,
  `entries` an envelope, `entries <citekey>` a bare record, and
  `add --from-file` an unlabelled summary — so every consumer needed its own jq
  path. `.items[]` now works everywhere.
- **`authors` is always a list.** `pzi entries --json` emitted a single
  joined string while `export --format json` emitted a list, so the same field
  had two types depending on which command produced it.
- **`pzi search --json` with several `--target` values emits one document**
  instead of one object per library; each item carries its own `bib_name`.
- **`pzi doctor` prints a human-readable summary by default** (`--json` for the
  machine dump) and **exits non-zero when a probe fails** — it used to print
  raw JSON to a human and exit 0 with an unreachable translation-server, which
  made it useless as a health gate.

- **Exit codes now have one meaning each**, documented in the README and
  `pzi --help`. `1` is reserved for "ran successfully and has something to
  report" (no search matches, duplicates found, integrity issues, unverified
  citations), so a command that *could not run* never exits `1` — that is `5`
  (bad config, unknown `--target`, locked bib, permission denied). `2` stays
  usage, `3` is a missing entry, `4` is a partly-failed batch. Scripts that
  treated any non-zero as failure still work; scripts that treated `1` as
  failure need updating.
- **`pzi search` with no matches exits `1` and writes nothing to stdout** (it
  printed `no matches` on stdout and exited `0`). The note moved to stderr.
- **`pzi entries` writes five fixed tab-separated columns** — citekey, year,
  title, authors, and `pdf` when a PDF is attached. The PDF marker used to be
  appended to the authors column without a separator, so `awk -F'\t'` read it as
  part of an author's name. The authors column is also no longer omitted when
  empty, so the column count is stable.
- **`pzi delete` refuses to prompt when stdin is not a terminal**, exiting `2`
  instead of reading EOF, cancelling, and reporting success.
- **`--json` now also emits JSON when the command fails** (`add`, `tag`,
  `check`), instead of printing prose to stderr and no JSON at all.

- **`promote_confidence_threshold` is now a 0-100 match score; the default moves
  from `3` to `60`.** The promotion gate used to score a handful of coarse
  features (title exact/substring, capped author overlap, year proximity) on an
  implicit 0-6 scale, while the explainable 0-100 `score_match` breakdown was
  computed *after* the gate and only decorated the diagnostics — so `pzi update
  --promote` could print `match confidence 0/100 … title_mismatch` and write the
  entry anyway. Both the gate and candidate selection now use the same 0-100
  score. **A `promote_confidence_threshold` set in `config.toml` must be
  restated on the new scale**; values above 100 are now rejected at config
  validation.

### Added

- **`pzi check --jsonl -`** streams one JSON object per entry to stdout (the
  flag previously accepted only a file path); the human table is suppressed so
  the stream stays parseable.
- **`--json` on every command that reports a result**, including the mutating
  ones: `update` (and `update --promote`), `delete`, `import`, `pdf
  attach|retry`, `fix merge`, `fix reindex`, `tag add|remove`, and `doctor`.

- **`PZI_CONFIG`** sets the config file path; `--config` still takes precedence.
- **`pzi import -`** reads BibTeX from stdin, so
  `pzi export --target a | pzi import - --target b` needs no temp file.
- **`--json` on `pzi tag add` and `pzi tag remove`** (previously only `tag list`).

### Security

- **The HTTP API no longer accepts an arbitrary `.bib` path in `bib`.** A direct
  path is a CLI convenience; over the API it let any request that reached it
  make pzi create and write a library anywhere the user can write. Requests are
  confined to libraries declared in `config.toml` (by name or configured path).
- **Binding to a wildcard address (`0.0.0.0`, `::`) is refused.** It started the
  server and then rejected every request, because the Host check that guards
  against DNS rebinding had no bind address to match. Bind to a specific address.
- **`pzi server` states whether auth is enabled at startup.** A token resolves
  from the data home, so a differing `XDG_DATA_HOME` between `pzi init` and the
  server silently yielded no token and served the API unauthenticated.
- **`pzi init` creates `config.toml` with mode `0600`** — it may hold a plaintext
  `api_auth_token` and `*_cmd` hooks that pzi executes.
- **The extension's bot-bypass allowlist now matches on a domain boundary.**
  `evil-nature.com` cleared an allowlist entry of `nature.com`.

### Fixed

- **The browser extension is installable again.** The manifest carried the PEP
  440 project version verbatim (`0.1.0b2`), which Chrome and AMO reject, so the
  v0.1.0b2 zips could not be installed. The version is translated to
  dot-separated integers, with pre-releases ordered below the final release.
- **DOM-based PDF discovery never ran.** The injected function referenced a
  service-worker module name that does not exist in the page, so it threw and
  the failure was swallowed.
- **A page with many PDF links no longer fails the whole capture.** Candidates
  are capped client-side at the limit the server enforces.
- **Re-capturing a paper with a differently-cased or trailing-slash DOI no
  longer creates a duplicate entry.** DOI identities are canonicalized before
  comparison.

- **`pzi fix reindex` renamed the wrong PDF.** The file to rename was derived
  from the old citekey rather than read from the entry, so an unrelated
  `<old_citekey>.pdf` sitting in `papers_dir` was renamed onto the entry while
  the entry's real PDF was left orphaned. The move now comes from the entry's
  own `file =` field, and `--dry-run` prints both paths so a wrong move is
  visible before it happens.
- **`pzi fix reindex` could leave `file =` fields dangling.** PDFs were renamed
  before the `.bib` was written, under a lock that was released in between; if
  the write failed (a concurrent `pzi add` changing the entry count is enough),
  the renames stood while the bib still pointed at the old paths. Renames and
  the write now share one exclusive lock, and a failed write undoes every
  rename.
- **`pzi fix reindex` silently replaced a PDF already at the target path.**
  `os.rename` overwrites its destination; the rename is now refused and reported
  as an error instead.
- **`pzi fix clean` reported quarantined files as orphans forever.** The orphan
  scan descended into `papers_dir/.orphans`, so after the first `--fix` run the
  plain audit exited non-zero permanently.
- **`pzi fix clean --fix` destroyed archived PDFs on a name collision.** A later
  orphan sharing a basename with one already quarantined overwrote it. The
  quarantine directory is an archive: a taken name now gets a numbered suffix
  (`stale-1.pdf`).
- **Conference papers were written with `journal` instead of `booktitle`.**
  Every insert path (`add`, `import`, capture, promote) projected the venue as
  `journal` regardless of entry type, producing `@inproceedings` entries no
  booktitle-requiring citation style can format.
- **`pzi import` retyped every entry as `@article`.** The source entry type was
  read and then dropped before the write, so an imported `@inproceedings`,
  `@incollection`, or `@book` lost its type.
- A PDF download cut short mid-transfer is no longer stored as the paper. Two
  cases: a body with a known `Content-Length` that stops early was completely
  silent (`HTTPResponse.read(amt)` clips to the bytes remaining and returns
  short instead of raising), and a chunked body cut short raised
  `http.client.IncompleteRead`, which derives from neither `OSError` nor
  `ValueError` and so escaped the download error handler as a raw traceback out
  of `pzi add` / `pzi pdf retry`. Reads now reconcile against `Content-Length`
  (skipped when a `Content-Encoding` makes the comparison meaningless), and the
  download path reports both cases as a normal download failure, leaving the
  fallback chain to try the next source.

- **Entry mutations no longer delete BibTeX fields pzi does not model.**
  `pzi tag add/remove`, `pzi update`, and `pzi add` on an entry already in the
  library regenerated the whole `@entry{}` block from the internal record model,
  which carries ~13 field names — so `volume`, `pages`, `publisher`, `editor`,
  `isbn`, `series`, `number`, `month` and any custom field were silently dropped
  on every touch, and `booktitle` was rewritten as `journal` (turning a
  conference paper into a malformed `@article`-shaped entry). The operation
  reported success while destroying data. Updates now merge onto the entry as it
  exists on disk: fields the record model owns are rewritten from the record,
  the entry's type is preserved, and everything else is left byte-for-byte
  alone. Found by the 2026-07-25 audit. Covers `pzi tag`, `pzi update`,
  `pzi add` on an existing entry, `pzi pdf attach`, `pzi pdf retry`
  (including `--failed-only` and the extension's attach route), and
  `pzi fix dedupe --merge`, and now also `pzi update --promote` (see below).

- **`pzi update --promote --keep-preprint` overwrote the preprint instead of
  adding the published version.** The merged published record inherits the
  preprint's `url`, and no metadata provider emits `canonical_url`, so the
  intended insert identity-matched the preprint itself and became an in-place
  update of it — reporting `action=create` with a `published_citekey` that
  existed nowhere in the file, and leaving a dangling
  `note = {Published version: …}`. `--dry-run` previewed the same wrong outcome.
  The published entry is now planned as an unconditional insert, and identity
  that belongs to the preprint (its arXiv/bioRxiv/etc. URL, alongside the
  `arxiv_id` already dropped) no longer carries over to the published record —
  which also stops the published entry from being typed `@unpublished`.

- **A promotion could half-apply, committing an entry behind a reported
  failure.** Keep-mode committed the new entry first and stamped the two
  cross-reference notes after, and only the note path refuses to patch a library
  containing a malformed block — so a single broken entry anywhere in the `.bib`
  left the published entry committed with a `file =` pointing at the PDF the
  rollback had just deleted, while the run reported `created 0,
  skipped_failed 1`. No fault injection needed. The insert and the preprint's
  note are now one atomic batch write, which also refuses up front to patch an
  unparseable library.

- **Promoting no longer blanks fields the published candidate left empty.** The
  merge copied every candidate key including explicit `None` — and
  `_openreview_normalize` always emits `doi: None` — so promoting against those
  providers deleted a populated DOI. Empty candidate values are now treated as
  absent metadata rather than an instruction to clear.

- **`pzi update --promote --replace` no longer retypes every promoted entry
  `@article`.** The in-place branch hardcoded the type and rebuilt the block from
  the record model, dropping unmodelled fields; it now projects onto the entry on
  disk and resolves the type the same way keep-mode does. The translation
  server's item type is also carried into the candidate record, so a promoted
  conference paper becomes `@inproceedings` rather than defaulting to `@article`.

- **A candidate matching on authors alone is no longer promoted.** Three shared
  surnames scored exactly the old default threshold, so an unrelated paper by
  the same group could be written in.

- **A source carrying no author list no longer counts as author disagreement.**
  Several providers return title and venue only, and scoring that identically to
  conflicting authors rejected exact title matches outright. It is now tracked
  as a distinct `author_unknown` flag. For `pzi check` this deliberately does
  *not* become a free pass: an entry whose only corroborating source cannot
  confirm authorship now reports `could_not_verify` rather than `verified` —
  reproducing a real title with invented authors is what a fabricated citation
  looks like, so silence there would be false assurance. It previously reported
  `problematic`, which was a false alarm in the other direction.

## [0.1.0b2] - 2026-07-25

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
- `.bib`, inbox, and metadata-cache writes now also fsync the containing
  directory after the atomic rename, so the rename itself survives a crash
  immediately after a write (previously only the new file's content was
  guaranteed durable, not the directory entry pointing at it).
- Releases now attach the prebuilt Firefox and Chrome extension zips
  (`paperazzi-capture-firefox.zip`, `paperazzi-capture-chrome.zip`) alongside
  the sdist/wheel, so installing the browser extension no longer requires a
  repo checkout.

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

[Unreleased]: https://github.com/mnazaal/paperazzi/compare/v0.1.0b3...HEAD
[0.1.0b3]: https://github.com/mnazaal/paperazzi/compare/v0.1.0b2...v0.1.0b3
[0.1.0b2]: https://github.com/mnazaal/paperazzi/compare/v0.1.0b1...v0.1.0b2
[0.1.0b1]: https://github.com/mnazaal/paperazzi/releases/tag/v0.1.0b1