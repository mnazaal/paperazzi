# Remediation plan — 2026-07 review

Derived from the 2026-07-02 full assessment (4-track review + gates: 1338 tests
pass, ruff/pyright clean). Structured as a **fast bug-fix patch first, then the
rest**. Each item cites `file:line` at review time — verify against current code
before editing.

## Guiding principle: no external users yet

As of 2026-07 the package has **no external users**. Breaking changes —
including `.bib` on-disk format changes — are acceptable **as long as they are
clearly documented in the CHANGELOG**. Prefer the clean fix over a back-compat
shim, dual-format reader, or careful migration path. The only library that can
exist in the wild is the maintainer's own, and that can be re-derived or
one-shot migrated.

Corollary — **cut what isn't needed.** Any scaffolding that only earns its keep
under a user base that doesn't exist is itself a defect to fix, not neutral
safety. When implementing an item, prefer the smaller surface: don't add config
keys/override flags speculatively (YAGNI), delete code made dead by a fix rather
than leaving it, and drop migration/back-compat hedging. Applied already to D2
(no new Host override key — derive from `api_listen_host`) and P1.4 (normalize
DOIs on input, no re-normalize-on-touch path). Watch for the same in P2.5
(symlink handling), P4.1 (purity tier), and the note-packing helpers deleted in
P1.2.

## Open decisions (revisit before/at implementation)

- **D1 — note encoding (item P1.2).** **Resolved:** do the clean
  field-separation now (Phase 1), not the lossless-parse stopgap. No users →
  no migration burden; document the `.bib` format change in the CHANGELOG.
  Remaining sub-decision: the exact key name for the PDF *URL* (must not collide
  with the existing `file` on-disk-path field — likely a `pzi-`namespaced key).
- **D2 — Host-header check (item P2.1).** **Resolved:** derive the allowed
  `Host` from the existing `api_listen_host` config — no new "escape hatch" key.
  A loopback bind (the default) accepts only loopback `Host`; if the operator
  has explicitly bound a non-loopback host, that host is allowed. YAGNI: don't
  add a separate override until a real need appears.
- **D3 — purity tier (item P4.1).** **Resolved:** renamed `STRICT_PURE` →
  `CORE`, documented that the tier enforces import layering, not purity.
  Relocation scope: only `pdf_planning.write_pdf_bytes` (and its two private
  helpers) actually warranted moving — it was real, self-contained
  filesystem I/O with an existing impure home (`pdf.py`) already importing
  it. `url_safety` and `bib_serialize`'s I/O is inherent to what those
  modules are for and already disclosed in their docstrings; only
  `capture_models`'s docstring needed a correction, not a code move.

---

## Phase 1 — Fast bug-fix patch (`0.1.0b2`)

Low-risk, user-visible correctness fixes. Ship as a patch release. Each fix
lands with a regression test.

### P1.1 — Crash on string `year` in live capture  *(highest severity)*
- **Where:** `src/pzi/similarity.py:286-290` (`abs(record_year - existing_year)`),
  fed a string by `src/pzi/http_post_routes.py:82` via
  `src/pzi/add_planning.py:47-58`.
- **Fix:** normalize `year` to `int | None` at the merge boundary
  (`merge_fetched_record_with_overrides`) using the existing `_coerce_year`
  (`add_planning.py:110`), so the similarity check never sees a string. Belt-and-
  suspenders: coerce inside `similarity` before subtracting.
- **Test:** capture-path test with `record {"year": "2024"}` vs existing
  `{"year": 2023}` and similar titles → no `TypeError`, hint attached.

### P1.2 — Note truncation / silent data loss  *(breaking .bib format change)*
- **Where:** `src/pzi/bibtex.py:183-209` (`_build_note` packs
  note + `PDF:`/`Abstract:` into one field with `" | "`; `_parse_note_text` /
  `extract_note_field` split it back out).
- **Root cause:** three logically separate values (free-text note, PDF URL,
  abstract URL) share one `note` field, so the delimiter and labels collide with
  user prose.
- **Fix (D1 resolved — clean break, no shim):** move `pdf_url` and
  `abstract_url` out of `note` into their own BibTeX fields; `note` becomes pure
  free text. Removes the entire `" | "` / label-prefix parsing surface, and makes
  the PDF/abstract URLs real fields that other BibTeX tools can read (delivers
  the "plain BibTeX, tool-agnostic" promise for these values). **Implemented as:**
  `pzi-pdf-url` and `pzi-abstract-url`, both dedicated custom fields — `url`
  was *not* reused for `abstract_url` as originally floated here, since `url`
  is already the serialization target for `canonical_url`/`source_url`
  (`bibtex.py:104-106`) and, per `translation_server.py:49-51`, `abstract_url`
  can genuinely differ from those (it tracks the translator-reported item URL
  with no source-url fallback) — reusing `url` would have silently dropped
  that distinction on write.
- **Scope note:** touched `_build_note`, `_parse_note_text`, `extract_note_field`
  and their call sites (`record_to_bibtex_entry`, `bibtex_entry_to_record`,
  plus two direct raw-note readers in `pdf_service.py`'s `retry_pdf` /
  `retry_failed_pdfs` — both switched to reading the already-parsed
  `record["pdf_url"]` instead of re-extracting from the raw `note` field);
  deleted the now-dead packing/splitting helpers. Documented the format
  change in the CHANGELOG (per guiding principle).
- **Test:** round-trip a note containing `" | "` and a leading-`PDF:` segment →
  byte-identical note text; assert PDF/abstract URLs land in their own fields and
  parse back; assert no `PDF:`/`Abstract:` substring appears in serialized `note`.
  **Status: done** (`tests/test_bibtex.py::test_note_pdf_url_abstract_url_round_trip_is_byte_identical`).

### P1.3 — Wrong package name in runtime messages
- **Where:** `src/pzi/commands/init.py:43`, `src/pzi/browser_session.py:235-236`,
  `src/pzi/browser_pdf_hook.py:156-157` (all say `pip install 'pzi[playwright]'`).
- **Fix:** change to the valid distribution form
  `pip install 'paperazzi[playwright]'` (and matching pipx form). Grep for any
  remaining `pzi[playwright]` in shipped code/docs.
- **Test:** assert the emitted message contains `paperazzi[playwright]`
  (extends existing `test_setup_service` / init message tests).

### P1.4 — DOI normalization keeps query/fragment; `doi:` prefix unsupported
- **Where:** `src/pzi/identifiers.py:27,37-47`.
- **Fix:** strip `?query`/`#fragment` from doi.org URLs before capture; accept a
  `doi:`/`doi.org` prefix. Normalize on input; no migration path or
  re-normalize-on-touch hedging (no users → nothing to preserve).
- **Test:** `https://doi.org/10.1234/abc?utm_source=x` → `10.1234/abc`;
  `doi:10.1234/abc` classifies as a DOI.

### P1.5 — `pzi search` `[authors]` column reads as a broken placeholder  *(UX, cheap)*
- **Where:** `src/pzi/cli_render.py:37` renders `matched_fields` bare.
- **Fix:** label it (e.g. `matched: authors`) so it's not confused with the
  author-name column that `pzi entries` shows in the same position.

**Phase 1 exit:** all four bugs + UX nit fixed with tests, ruff/pyright/pytest
green, CHANGELOG `Unreleased` → `0.1.0b2`, version bumped. The CHANGELOG entry
must include a **Changed/Breaking** note for the P1.2 `.bib` note-field format
change (pre-existing entries using the old packed `note` are no longer parsed for
PDF/abstract URLs — document a one-shot re-capture or manual edit).

---

## Phase 2 — Security hardening

### P2.1 — DNS-rebinding reads in default no-token setup  *(main gap)*  — **Status: done**
- **Where:** `src/pzi/http_security.py:123-150` (missing `Origin` allowed),
  `src/pzi/http_api.py` GET path (no `Host` validation).
- **Fix (D2 resolved):** validate `Host` against the value derived from
  `api_listen_host` — loopback bind accepts only loopback `Host`; an explicitly
  configured non-loopback bind accepts that host. No new config key. Keep
  treating a *recognized* `Origin` as today.
- **Test:** loopback bind + `Host: attacker.com` → 403; loopback Host → 200;
  config bound to a LAN host → that Host allowed.

### P2.2 — Per-connection read timeout (slowloris)  — **Status: done**
- **Where:** `src/pzi/http_api.py:391-392` (timeout only on listening socket).
- **Fix:** set a timeout on accepted connection sockets. Implemented via
  `CONNECTION_READ_TIMEOUT_SECONDS = 30` as the handler's `timeout` class
  attribute, which `StreamRequestHandler.setup()` applies to each accepted
  connection socket.

### P2.3 — Attach-session single-use TOCTOU  — **Status: done**
- **Where:** `src/pzi/http_post_routes.py:608-640` (get→validate→attach→consume).
- **Fix:** atomically claim the session at `get` time (pop / mark-in-flight
  inside the store lock). Implemented as `AttachSessionStore.claim()` (pop)
  paired with `.restore()`, called whenever validation or the attach itself
  doesn't succeed — preserves legitimate retry after a bad token or
  transient failure while closing the concurrent-claim race.

### P2.4 — `url_safety` edge cases  — **Status: done**
- **Where:** `src/pzi/url_safety.py:103-116`.
- **Fix:** treat 100.64.0.0/10 (CGNAT) as non-public (prefer `not ip.is_global`);
  guard IPv4-mapped literals for Python 3.11.0–3.11.8. Lower priority — outbound
  paths are already IP-pinned in `safe_http`. Implemented as `ip.is_global and
  not ip.is_multicast` (is_global already excludes CGNAT but not multicast),
  canonicalizing an IPv4-mapped IPv6 literal to its embedded `ipv4_mapped`
  address first so the check is version-independent rather than gated on a
  specific Python patch range.

### P2.5 — Symlinked `.bib` replaced by regular file  — **Status: done**
- **Where:** `src/pzi/bib_repository.py:190,203` (`os.replace` over the link).
- **Fix:** resolve the symlink before writing (write through to the real target),
  or document the behavior. Decide during Phase 2. Resolved as: write through
  the symlink (`os.path.realpath` before the temp-file+`os.replace` dance),
  so the symlink itself survives and continues pointing at the freshly
  written file — no new config knob.

---

## Phase 3 — Docs + test hermeticity

### P3.1 — Data-loss race in inbox drain  *(promoted from bugs: needs a lock)*  — **Status: done**
- **Where:** `src/pzi/inbox_service.py:118-210` (rewrite from stale snapshot, no
  lock).
- **Fix:** hold an advisory lock across read→process→rewrite, or re-read under
  lock and diff before the atomic replace, so lines appended mid-drain survive.
  Implemented as a lock scoped to just the final re-read+rewrite (not the
  whole drain), so external appenders aren't blocked for the drain's full
  duration.
- **Test:** append a line during a simulated drain; assert it survives.
- *(Cross-phase note: this is a correctness bug; if Phase 1 timing allows, pull
  it forward. Kept here because the fix touches the same locking work as P3.)*

### P3.2 — Doc corrections  — **Status: done**
- README:57 pipx extras syntax → `'paperazzi[playwright] @ git+...'`.
- CHANGELOG:34-35 remove/repair "100% coverage, mypy clean, 46 files"
  (real: 80% floor, ~89 files, no mypy).
- `config.template.toml:40` `browser_engine` values → `chromium, firefox, webkit`
  (drop `chrome`), matching `config.py:407`.
- `docs/security.md:104,159` attach-session TTL 5 min → 10 min
  (`http_post_routes.py:49` = 600 s).
- `packaging/systemd/pzi.service:20` `Documentation=` slug `Nazaal/pzi` →
  `mnazaal/paperazzi`.
- README env-var table: add `PZI_NODE_MIRROR`, `PZI_NPM_REGISTRY`,
  `PZI_DISABLE_DESKTOP_BROWSER_FALLBACK`, `PZI_DOWNLOAD_DIR`,
  `PZI_DESKTOP_BROWSER_TIMEOUT`, `PZI_SKIP_BROWSER_HOOK`.
- Document `--config PATH`, `pzi pdf retry --failed-only` (citekey optional), and
  the omitted `add`/`init`/`tag`/`export` flags in the CLI reference.
- Document shell-completion enablement (`register-python-argcomplete pzi`);
  consider a `pzi completions bash|zsh` helper (see P5).
- README:35 drop "requires git" from the Node-download line.
- Remove the `pzi-browser-hook` console-script reference in
  `tools/browser_pdf_hook.py:2` (script isn't in `[project.scripts]`).

### P3.3 — Test hermeticity — **Status: done**
- Added a suite-wide autouse fixture in `tests/conftest.py`: blocks non-loopback
  sockets for non-live tests, sets `PZI_SKIP_AUTO_START=1`, and a `dead_port`
  fixture for per-test dead ephemeral ports, used to replace the hardcoded
  `1969` translation-server port across the ~15 test files where a real
  (unmocked) network path could reach it; files where every `1969` occurrence
  sits behind a fully-mocked fetcher, or asserts the literal default value
  itself, were deliberately left unchanged. Made the Playwright probes in
  `test_browser_pdf_hook_integration.py` and `test_browser_integration.py`
  lazy (autouse skip fixture backed by a shared `functools.lru_cache`d
  `tests/browser_probe.py` helper, instead of launching browsers at module
  import/collection time) and added a `skipif(shutil.which("node") is None)`
  guard to `test_browser_extension_js.py`. Extracted a shared
  `write_app_config` fixture that replaced near-identical `_write_config`
  helpers in 5 files; a second family of raw-TOML config helpers
  (`test_check_service.py`/`test_promote_service.py`) and a few other
  divergent ones were left alone as genuinely not close enough to consolidate
  cleanly.

### P3.4 — Highest-value test additions — **Status: mostly done**
- Two-thread/two-process contention test for `with_bib_lock` (the central
  invariant) — lost-update + stale-lock.
- Crash-injection test for the temp+fsync+rename bib write (original intact,
  no `.tmp` litter).
- Malformed/unicode BibTeX corpus → `failed_blocks` reported, survivors
  preserved.
- Tests for `browser_session_manager.py` and `cli_server.py` (currently zero).

**Implementation notes:** added a 4-thread `update_bib_entry` contention test
(`tests/test_bib_repository.py`) that asserts no lost updates and that no
thread stays stuck past a bounded join timeout (no stale lock). Added a
crash-injection test simulating `os.replace` failure during `write_bib_file`
— this caught a **real bug**: `write_bib_file`/`_write_bib_text_atomic` left
their `.tmp` file behind on any write failure (the original file was always
safe, but the temp file leaked); fixed by wrapping the write+replace in
try/except that unlinks the temp file on any exception, matching the pattern
`inbox_service._write_inbox_atomically` already used. Added a malformed +
unicode BibTeX corpus test proving `read_bib_file` skips an unparseable block
and still returns the valid entries around it (including non-ASCII fields)
without crashing — this is the read-path counterpart to
`_validate_library_parseable`'s existing refuse-to-patch behavior on the
write path. `browser_session_manager.py` got its first test coverage as part
of P4.3 (`tests/test_browser_session_manager.py`). `cli_server.py` coverage
is left for the in-flight P3.3 hermeticity work, which is actively editing
adjacent browser/server test files — deferred to avoid colliding with it.

---

## Phase 4 — Architecture + remaining bugs

### P4.1 — Purity vocabulary vs reality  (D3) — **Status: done**
- **Where:** `tests/test_layer_boundaries.py` `STRICT_PURE` tier lists I/O
  modules; several "pure"-docstringed modules do I/O
  (`pdf_planning.write_pdf_bytes`, `url_safety` DNS, `capture_models.load_page_artifact`,
  `bib_serialize` `Path.resolve()` + arg mutation).
- **Fix:** rename the tier to name what it enforces (no front-end imports), and
  relocate the clearest I/O helpers out of "pure" modules. Update module
  docstrings to match.

**Implementation notes (D3 resolved):** renamed `STRICT_PURE` → `CORE`
throughout `test_layer_boundaries.py`; the module docstring now states
explicitly that the tier enforces import layering (no front-end/browser
reachability), not functional purity — CORE modules may still do real I/O
(DNS, HTTP, single-file disk reads). Relocated the one clear-cut case worth
moving: `write_pdf_bytes`/`resolve_pdf_destination`/`_write_all` (atomic
filesystem writes) moved from `pdf_planning.py` (CORE) to `pdf.py`
(PIPELINE, which already did I/O and was the sole import point for every
caller except a test file). `pdf_planning.py` now only computes destination
paths; its docstring notes where the write helper moved to. Left
`url_safety` (docstring already disclaims purity — "injectable DNS
resolution") and `bib_serialize` (docstring already discloses path
resolution, no code change) as-is; fixed `capture_models.py`'s docstring,
which overclaimed "no network, no BibTeX writes" without mentioning that
`load_page_artifact` reads local disk/stdin.

### P4.2 — Remaining functional bugs — **Status: done**
- `bibtex.py:150-152` — dead `archive_prefix` check treats any `eprint` as arXiv;
  gate on the prefix. Test: bioRxiv eprint → no fabricated arxiv.org URL.
- `format_templates.py:199-205` — Better BibTeX `shorttitle(N,M)` uses one word +
  char-truncation; implement word-count semantics. `:91-103` — wrap
  `match`/`replaceFrom` in try/except so invalid regex degrades safely.
- `add_planning.py:55-57` — fallback fill ignores empty lists (`authors: []`);
  fill when the fetched value is empty, not only `None`/blank string. Test:
  extension `fallback_authors` survives.
- `pdf_discovery.py:312-316` — `doi_pdf_step` bypasses injected seams; route
  through `context["fetch_*"]`. `:71-81` vs `:126-133` — make sequential and
  parallel discovery handle step exceptions symmetrically (don't abort the add).
- Minor: `identifiers.py:115,142` arXiv host-match inconsistency;
  `pdf_discovery.py:490,495` Authorea/SAGE `str.replace` fallthrough sets landing
  page as `pdf_url`; `pdf_acquisition_plan.py:215-217` unanchored host regexes;
  `format_templates.py:185` unknown-field filter uses `part` not `head`.

**Implementation notes:** `bibtex_entry_to_record`'s `arxiv_id` now gates strictly
on `archiveprefix` (case-insensitive `arxiv`), dropping the `or arxiv_id`
fallthrough. `_shorttitle` now selects the first N stopword-filtered title words
(optionally each truncated to M chars) instead of truncating one word;
`_apply_options`' `match`/`replaceFrom` wrap `re.error` and degrade to
empty/unchanged. `merge_fetched_record_with_overrides` treats an empty list
(not just `None`/blank string) as missing. `doi_pdf_step` now reads
`context["fetch_crossref_pdf"|"fetch_europepmc_pdf"|"fetch_doaj_pdf"]` with
fallback to the real fetchers, mirroring `unpaywall_step`.
`apply_pdf_discovery` (sequential) now catches a step's exception and
continues to the next step, matching the parallel path's per-step isolation.
Minor fixes: arXiv host-match now also accepts `www.arxiv.org` and uses
`hostname` consistently (was mixing `hostname`/`netloc`); Authorea/SAGE URL
rewrites return `None` (no PDF found) instead of the unchanged landing URL
when the expected substring is absent; the publisher-gateway table now
matches hostnames by domain-suffix-with-boundary (`_host_matches_domain`)
instead of an unanchored regex that could match an unrelated lookalike host;
the unrecognized-field fallback in the Better BibTeX renderer now looks up
the parsed field name (`head`) instead of the raw filter-suffixed token
(`part`).

### P4.3 — IO-layer cleanups (from services review) — **Status: done**
- `add_service.add_input_to_bib` broad `except Exception` masks bugs as network
  errors — narrow it.
- Parent-directory fsync after rename (durability) in `bib_repository`,
  `inbox_service`, `metadata_cache` — low priority.
- Confirm `browser_manager.close()` is idempotent (called twice on idle
  shutdown).

**Implementation notes:** `add_input_to_bib`'s except clause now catches
`(urllib.error.URLError, OSError, ValueError)` — the actual exception surface
of the translation-server/metadata-provider fetchers (network errors,
timeouts, malformed-JSON `ValueError`s) — instead of bare `Exception`, so an
unrelated bug (`KeyError`, `AttributeError`, ...) now crashes instead of
being silently misreported as "translation server error" or masked by the
manual-record fallback path. Added `pzi.fileio.fsync_parent_dir` (best-effort,
swallows `OSError` — not supported on all platforms) and call it after every
`os.replace` in `bib_repository.write_bib_file` /
`_write_bib_text_atomic`, `inbox_service._write_inbox_atomically`, and
`metadata_cache.MetadataCache.set`. `browser_session.BrowserSession.close()`
and `browser_session_manager.BrowserSessionManager.close()` were already
correctly idempotent (guarded by a `_closed`/`_session is None` check); added
`tests/test_browser_session_manager.py` (previously zero coverage) to lock
this in, including a double-close regression test.

---

## Phase 5 — UX polish (optional, opportunistic) — **Status: done**

- Ship prebuilt extension zips as release artifacts, or add `pzi extension build`
  so users don't need a repo checkout (README §1 vs §4 gap).
- `pzi completions bash|zsh` helper (pairs with P3.2 docs).
- Reconcile `PZI_BROWSER` (firefox default) vs `pzi init --browser` (chromium
  default).
- Document the `pzi search --json` array-wrapping shape.

**Implementation notes:**
- **Extension zips:** `tools/build_extension.py` already produced
  `dist/paperazzi-capture-{firefox,chrome}.zip` locally but nothing wired them
  into a release — `RELEASING.md` step 5 never ran the script and
  `release.yml`'s `github-release` job only globbed `dist/*` for the sdist/wheel.
  Added a `Build browser extension zips` step to the `build` job (runs the
  existing script, no new dependency), and narrowed the release-asset glob from
  `dist/*` to `dist/*.tar.gz dist/*.whl dist/*.zip` — the wider glob would have
  tried (and failed) to upload the unpacked `dist/firefox/`/`dist/chrome/`
  directories as release assets once the new step started producing them.
  README §4 and `RELEASING.md` updated to point at the release zips as the
  no-checkout install path. No `pzi extension build` CLI subcommand added — the
  existing `tools/build_extension.py` plus a shipped release zip covers both
  the maintainer and end-user cases without new CLI surface (YAGNI).
- **Shell completions:** already fully covered by the base `argcomplete`
  dependency + `eval "$(register-python-argcomplete pzi)"`, documented in
  README since P3.2. A dedicated `pzi completions` subcommand would just
  reimplement what `register-python-argcomplete` already does — skipped
  (YAGNI), no code change.
- **`PZI_BROWSER` vs `--browser`/`browser_engine`:** confirmed these are two
  genuinely different settings, not a bug — `browser_engine` (`pzi init
  --browser`, default `chromium`) selects the **headless Playwright** browser
  used for automated capture; `PZI_BROWSER` (default `firefox`) selects the
  **desktop** browser launched for the manual "open and watch Downloads/"
  fallback (confirmed via `pdf.py:393-419`, alongside
  `PZI_DISABLE_DESKTOP_BROWSER_FALLBACK`/`PZI_DOWNLOAD_DIR`, which are
  unambiguously part of the same desktop-fallback feature). Fixed by
  clarifying, not unifying: README's env-var table and Config section now say
  explicitly which subsystem each setting controls and cross-reference each
  other so they read as two independent knobs instead of two conflicting
  defaults for "the browser."
- **`pzi search --json` shape:** documented in README (shared-flags paragraph)
  that it always returns a JSON array of one `{status, bib_name, matches,
  errors}` object per searched library, even for a single default target —
  because `search` is one of only two commands (with `update`) that accept
  multiple `--target` values in one invocation.

---

## Suggested execution order

1. **P1.1 → P1.4** (+ P1.5), release `0.1.0b2`.
2. **P3.1** (inbox race — correctness; pull forward if Phase 1 has slack).
3. **P2.1**, then P2.2–P2.5.
4. **P3.2 / P3.3 / P3.4** (docs + tests together).
5. **P4.1 / P4.2 / P4.3**.
6. **P5** as time allows.

Gates for every phase: `ruff check`, `pyright`, `pytest` green; CHANGELOG
updated; no new `pzi[playwright]`/pre-rename strings introduced.

---

## Plan status: **done**

All phases (1–5) implemented and merged into `[Unreleased]` in CHANGELOG.md.
Final sweep for dead code and stale references (renamed/removed identifiers
from every phase, pre-rename `pzi`→`paperazzi` URLs/package names across
tracked docs, stray `.egg-info` build artifacts) turned up nothing live —
the one stale pre-rename mention found (`src/pzi.egg-info/PKG-INFO`) was a
gitignored, locally-regenerated build artifact, not a tracked reference;
removed it for local hygiene, no code/doc change required. Final gate:
`ruff check src tools tests` clean, bare `pyright` 0 errors, `pytest -m "not
browser"` 1370 passed / 8 skipped / 20 deselected.
